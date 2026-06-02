import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import or_
from backend.database.database import get_db
from backend.database.models import (
    User, UserNotificationPreferences, Notification,
    FCMToken, Product, UserInterest
)
from backend.database.crud import (
    log_user_interest,
    get_user_interests,
    was_notification_recently_sent,
    log_notification_delivered,
    _make_product_key,
)

logger = logging.getLogger(__name__)

# ─── Cooldown constants ───────────────────────────────────────────────────────
_COOLDOWN_PRICE_DROP_H = 24       # hours between repeated price-drop alerts
_COOLDOWN_DEAL_H = 48             # hours between repeated deal alerts
_BATCH_THRESHOLD = 3              # min notifications before switching to batch summary
_SIGNIFICANT_DROP_PCT = 5.0       # % discount that qualifies as "significant"

class NotificationService:
    def __init__(self):
        # Initialize Firebase Admin SDK if credentials are available
        try:
            import firebase_admin
            from firebase_admin import credentials, messaging

            if not firebase_admin._apps:
                # Try to load Firebase credentials from environment
                firebase_creds_path = os.getenv('FIREBASE_CREDENTIALS_PATH')
                if firebase_creds_path and os.path.exists(firebase_creds_path):
                    cred = credentials.Certificate(firebase_creds_path)
                    firebase_admin.initialize_app(cred)
                    logger.info("Firebase Admin SDK initialized")
                else:
                    logger.warning("Firebase credentials not found. Push notifications disabled.")
        except ImportError:
            logger.warning("firebase-admin not installed. Push notifications disabled.")

    async def send_push_notification(self, token: str, title: str, body: str, data: Optional[Dict[str, str]] = None):
        """Send push notification to a specific FCM token"""
        try:
            import firebase_admin
            from firebase_admin import messaging

            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                data=data or {},
                token=token,
            )

            response = messaging.send(message)
            logger.info(f"Push notification sent successfully: {response}")
            return True
        except Exception as e:
            logger.error(f"Failed to send push notification: {e}")
            return False

    async def send_notification_to_user(self, user_id: int, title: str, message: str,
                                      notification_type: str = "system",
                                      product_id: Optional[int] = None,
                                      data: Optional[Dict[str, Any]] = None):
        """Send notification to a user (both in-app and push)"""
        db = next(get_db())

        try:
            # Check user preferences
            preferences = db.query(UserNotificationPreferences).filter(
                UserNotificationPreferences.user_id == user_id
            ).first()

            if not preferences:
                # Create default preferences
                preferences = UserNotificationPreferences(user_id=user_id)
                db.add(preferences)
                db.commit()
                db.refresh(preferences)

            # Check if user wants this type of notification
            should_send = self._should_send_notification(preferences, notification_type)
            if not should_send:
                return

            # Create in-app notification
            notification = Notification(
                user_id=user_id,
                title=title,
                message=message,
                notification_type=notification_type,
                related_product_id=product_id,
                data=json.dumps(data) if data else None
            )
            db.add(notification)
            db.commit()

            # Send push notification if enabled
            if preferences.push_notifications:
                tokens = db.query(FCMToken).filter(
                    FCMToken.user_id == user_id,
                    FCMToken.is_active == True
                ).all()

                push_data = {
                    "type": notification_type,
                    "notification_id": str(notification.id)
                }
                if product_id:
                    push_data["product_id"] = str(product_id)
                if data:
                    push_data.update({k: str(v) for k, v in data.items()})

                for token in tokens:
                    await self.send_push_notification(
                        token.token, title, message, push_data
                    )

            # Update last notification sent
            preferences.last_notification_sent = datetime.utcnow()
            db.commit()

        except Exception as e:
            logger.error(f"Error sending notification to user {user_id}: {e}")
            db.rollback()
        finally:
            db.close()

    def _should_send_notification(self, preferences: UserNotificationPreferences, notification_type: str) -> bool:
        """Check if notification should be sent based on user preferences"""
        if notification_type == "deal" and not preferences.deals_notifications:
            return False
        if notification_type == "discount" and not preferences.discount_notifications:
            return False
        if notification_type == "price_drop" and not preferences.price_drop_notifications:
            return False

        # Check frequency
        if preferences.notification_frequency == "realtime":
            return True
        elif preferences.notification_frequency == "daily":
            if preferences.last_notification_sent:
                return datetime.utcnow() - preferences.last_notification_sent > timedelta(hours=24)
            return True
        elif preferences.notification_frequency == "weekly":
            if preferences.last_notification_sent:
                return datetime.utcnow() - preferences.last_notification_sent > timedelta(days=7)
            return True

        return True

    async def send_bulk_notifications(self, user_ids: List[int], title: str, message: str,
                                    notification_type: str = "system",
                                    product_id: Optional[int] = None,
                                    data: Optional[Dict[str, Any]] = None):
        """Send notification to multiple users"""
        for user_id in user_ids:
            await self.send_notification_to_user(
                user_id, title, message, notification_type, product_id, data
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Interest tracking (called from endpoints on every user action)
    # ─────────────────────────────────────────────────────────────────────────

    def track_interaction(
        self,
        user_id: int,
        product_name: str,
        interaction_type: str,
        brand: str = "",
        category: str = "",
        quantity: str = "",
    ) -> bool:
        """Record a user interaction and update their interest profile."""
        db = next(get_db())
        try:
            log_user_interest(
                db, user_id, product_name, interaction_type,
                brand=brand, category=category, quantity=quantity,
            )
            return True
        except Exception as e:
            logger.error(f"track_interaction failed for user {user_id}: {e}")
            return False
        finally:
            db.close()

    # ─────────────────────────────────────────────────────────────────────────
    # Personalized price-drop + deal notifications
    # ─────────────────────────────────────────────────────────────────────────

    async def notify_price_drops(self, product_ids: List[int]):
        """
        For each product in the list, find users who have shown interest in it
        and notify them if:
          - they haven't been notified recently about this product, OR
          - the price dropped further since the last notification.
        Multiple alerts are batched into a summary when >= _BATCH_THRESHOLD.
        """
        db = next(get_db())
        try:
            for product_id in product_ids:
                product = db.query(Product).filter(Product.id == product_id).first()
                if not product or product.out_of_stock:
                    continue

                price = product.discounted_price or product.old_price
                if not price:
                    continue

                product_key = _make_product_key(
                    product.product_name,
                    product.brand or "",
                    product.quantity or "",
                )

                # Find all users who interacted with this product key
                interested = (
                    db.query(UserInterest)
                    .filter(UserInterest.product_key == product_key)
                    .all()
                )

                for interest in interested:
                    uid = interest.user_id
                    if was_notification_recently_sent(
                        db, uid, product_key, "price_drop",
                        cooldown_hours=_COOLDOWN_PRICE_DROP_H,
                        current_price=price,
                    ):
                        continue

                    await self.send_notification_to_user(
                        uid,
                        f"💰 Price Drop: {product.product_name}",
                        f"{product.product_name} is now Rs. {price:.0f} at {product.store}",
                        "price_drop",
                        product_id,
                        {"store": product.store, "price": str(price)},
                    )
                    log_notification_delivered(
                        db, uid, product_key, "price_drop",
                        price=price, store=product.store,
                    )
        except Exception as e:
            logger.error(f"notify_price_drops error: {e}")
        finally:
            db.close()

    async def notify_new_deals(self, limit: int = 10):
        """
        Find recently-scraped deals and notify only users who have previously
        searched or viewed those products. Batches alerts into a summary
        when a user qualifies for >= _BATCH_THRESHOLD alerts at once.
        """
        db = next(get_db())
        try:
            yesterday = datetime.utcnow() - timedelta(days=1)
            recent_deals = (
                db.query(Product)
                .filter(Product.save_amount > 0, Product.scraped_at > yesterday)
                .order_by(Product.save_amount.desc())
                .limit(limit)
                .all()
            )

            # Group qualifying deals per user so we can batch
            user_deals: Dict[int, List[Product]] = {}

            for deal in recent_deals:
                pkey = _make_product_key(
                    deal.product_name, deal.brand or "", deal.quantity or ""
                )
                interested = (
                    db.query(UserInterest)
                    .filter(UserInterest.product_key == pkey)
                    .all()
                )
                for interest in interested:
                    uid = interest.user_id
                    if was_notification_recently_sent(
                        db, uid, pkey, "deal",
                        cooldown_hours=_COOLDOWN_DEAL_H,
                    ):
                        continue
                    user_deals.setdefault(uid, []).append(deal)

            for uid, deals in user_deals.items():
                await self._dispatch_deal_alerts(db, uid, deals)

        except Exception as e:
            logger.error(f"notify_new_deals error: {e}")
        finally:
            db.close()

    async def _dispatch_deal_alerts(self, db: Session, user_id: int, deals: List):
        """Send individual or batched deal alerts to a user."""
        if len(deals) >= _BATCH_THRESHOLD:
            # Batch summary
            names = ", ".join(d.product_name for d in deals[:3])
            extra = f" and {len(deals) - 3} more" if len(deals) > 3 else ""
            await self.send_notification_to_user(
                user_id,
                f"🔥 {len(deals)} New Deals for You",
                f"{names}{extra} have new discounts available.",
                "deal",
            )
            for deal in deals:
                pkey = _make_product_key(
                    deal.product_name, deal.brand or "", deal.quantity or ""
                )
                log_notification_delivered(
                    db, user_id, pkey, "deal",
                    price=deal.discounted_price, store=deal.store,
                )
        else:
            for deal in deals:
                pkey = _make_product_key(
                    deal.product_name, deal.brand or "", deal.quantity or ""
                )
                price = deal.discounted_price or deal.old_price
                await self.send_notification_to_user(
                    user_id,
                    f"🔥 Hot Deal: {deal.product_name}",
                    f"Save Rs. {deal.save_amount:.0f} on {deal.product_name} at {deal.store}",
                    "deal",
                    deal.id,
                    {"store": deal.store, "savings": str(deal.save_amount)},
                )
                log_notification_delivered(
                    db, user_id, pkey, "deal",
                    price=price, store=deal.store,
                )

    async def notify_personalized_price_drops(self, user_id: int):
        """
        For a single user, scan all their interests against current DB prices
        and send alerts where the price has meaningfully dropped.
        Called on-demand (e.g. after a scraping job finishes).
        """
        db = next(get_db())
        try:
            interests = get_user_interests(db, user_id)
            qualifying: List[Tuple[Product, UserInterest]] = []

            for interest in interests:
                # Find cheapest in-stock product matching this key
                # Use ILIKE on product_name (the key prefix)
                base_name = interest.product_key.split(" ")[0]  # first word
                candidates = (
                    db.query(Product)
                    .filter(
                        Product.product_name.ilike(f"%{base_name}%"),
                        Product.out_of_stock == False,
                    )
                    .order_by(
                        (Product.discounted_price).asc()
                    )
                    .limit(5)
                    .all()
                )
                for p in candidates:
                    p_key = _make_product_key(
                        p.product_name, p.brand or "", p.quantity or ""
                    )
                    if p_key != interest.product_key:
                        continue
                    price = p.discounted_price or p.old_price
                    if not price:
                        continue
                    if was_notification_recently_sent(
                        db, user_id, p_key, "price_drop",
                        cooldown_hours=_COOLDOWN_PRICE_DROP_H,
                        current_price=price,
                    ):
                        continue
                    qualifying.append((p, interest))
                    break   # one cheapest per product key

            if not qualifying:
                return

            if len(qualifying) >= _BATCH_THRESHOLD:
                names = ", ".join(p.product_name for p, _ in qualifying[:3])
                extra = f" and {len(qualifying) - 3} more" if len(qualifying) > 3 else ""
                await self.send_notification_to_user(
                    user_id,
                    f"💰 {len(qualifying)} Price Drops on Your Watchlist",
                    f"{names}{extra} are now cheaper.",
                    "price_drop",
                )
                for p, _ in qualifying:
                    pkey = _make_product_key(p.product_name, p.brand or "", p.quantity or "")
                    price = p.discounted_price or p.old_price
                    log_notification_delivered(
                        db, user_id, pkey, "price_drop",
                        price=price, store=p.store,
                    )
            else:
                for p, _ in qualifying:
                    pkey = _make_product_key(p.product_name, p.brand or "", p.quantity or "")
                    price = p.discounted_price or p.old_price
                    await self.send_notification_to_user(
                        user_id,
                        f"💰 Price Drop: {p.product_name}",
                        f"{p.product_name} is now Rs. {price:.0f} at {p.store}",
                        "price_drop",
                        p.id,
                        {"store": p.store, "price": str(price)},
                    )
                    log_notification_delivered(
                        db, user_id, pkey, "price_drop",
                        price=price, store=p.store,
                    )
        except Exception as e:
            logger.error(f"notify_personalized_price_drops error for user {user_id}: {e}")
        finally:
            db.close()

    async def send_weekly_digest(self):
        """Send weekly digest of top deals and discounts"""
        db = next(get_db())

        try:
            # Get users who want weekly digest
            users = db.query(User).join(UserNotificationPreferences).filter(
                UserNotificationPreferences.weekly_digest == True
            ).all()

            # Get top deals from last week
            week_ago = datetime.utcnow() - timedelta(days=7)
            top_deals = db.query(Product).filter(
                Product.save_amount > 0,
                Product.scraped_at > week_ago
            ).order_by(Product.save_amount.desc()).limit(5).all()

            if top_deals:
                deals_text = "\n".join([
                    f"• {deal.product_name} - Save {deal.save_amount} PKR at {deal.store}"
                    for deal in top_deals
                ])

                title = "📊 Your Weekly Price Digest"
                message = f"Here are the best deals from the past week:\n\n{deals_text}"

                await self.send_bulk_notifications(
                    [user.id for user in users],
                    title, message, "system"
                )

        except Exception as e:
            logger.error(f"Error sending weekly digest: {e}")
        finally:
            db.close()

    def register_fcm_token(self, user_id: int, token: str, device_type: str = "android"):
        """Register FCM token for a user"""
        db = next(get_db())

        try:
            # Check if token already exists
            existing_token = db.query(FCMToken).filter(FCMToken.token == token).first()

            if existing_token:
                # Update existing token
                existing_token.user_id = user_id
                existing_token.device_type = device_type
                existing_token.is_active = True
                existing_token.last_used = datetime.utcnow()
            else:
                # Create new token
                fcm_token = FCMToken(
                    user_id=user_id,
                    token=token,
                    device_type=device_type
                )
                db.add(fcm_token)

            db.commit()
            return True

        except Exception as e:
            logger.error(f"Error registering FCM token: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def unregister_fcm_token(self, token: str):
        """Unregister FCM token"""
        db = next(get_db())

        try:
            db.query(FCMToken).filter(FCMToken.token == token).update({"is_active": False})
            db.commit()
            return True
        except Exception as e:
            logger.error(f"Error unregistering FCM token: {e}")
            return False
        finally:
            db.close()


# Global notification service instance
notification_service = NotificationService()