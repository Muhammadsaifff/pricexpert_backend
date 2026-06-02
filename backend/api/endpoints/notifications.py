from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from backend.database.database import get_db
from backend.database.models import (
    User, UserNotificationPreferences, Notification, FCMToken
)
from backend.database.crud import log_user_interest, get_user_interests
from backend.services.notification_service import notification_service

router = APIRouter()


# ── Pydantic models ───────────────────────────────────────────────────────────
class TrackInteractionRequest(BaseModel):
    user_id: int
    product_name: str
    interaction_type: str           # search | view | comparison | favorite
    brand: Optional[str] = ""
    category: Optional[str] = ""
    quantity: Optional[str] = ""

# Pydantic models
class NotificationPreferencesUpdate(BaseModel):
    deals_notifications: Optional[bool] = None
    discount_notifications: Optional[bool] = None
    price_drop_notifications: Optional[bool] = None
    weekly_digest: Optional[bool] = None
    push_notifications: Optional[bool] = None
    email_notifications: Optional[bool] = None
    notification_frequency: Optional[str] = None

class FCMTokenRegister(BaseModel):
    token: str
    device_type: Optional[str] = "android"

class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    notification_type: str
    related_product_id: Optional[int]
    is_read: bool
    sent_at: datetime
    data: Optional[str]

# Get user notifications
@router.get("/notifications", response_model=List[NotificationResponse])
async def get_user_notifications(
    user_id: int,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    notifications = db.query(Notification).filter(
        Notification.user_id == user_id
    ).order_by(Notification.sent_at.desc()).offset(skip).limit(limit).all()

    return notifications

# Mark notification as read
@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    user_id: int,
    db: Session = Depends(get_db)
):
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user_id
    ).first()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.is_read = True
    notification.read_at = datetime.utcnow()
    db.commit()

    return {"message": "Notification marked as read"}

# Get notification preferences
@router.get("/notifications/preferences")
async def get_notification_preferences(
    user_id: int,
    db: Session = Depends(get_db)
):
    preferences = db.query(UserNotificationPreferences).filter(
        UserNotificationPreferences.user_id == user_id
    ).first()

    if not preferences:
        # Create default preferences
        preferences = UserNotificationPreferences(user_id=user_id)
        db.add(preferences)
        db.commit()
        db.refresh(preferences)

    return {
        "success": True,
        "preferences": {
            "deals_notifications": preferences.deals_notifications,
            "discount_notifications": preferences.discount_notifications,
            "price_drop_notifications": preferences.price_drop_notifications,
            "weekly_digest": preferences.weekly_digest,
            "push_notifications": preferences.push_notifications,
            "email_notifications": preferences.email_notifications,
            "notification_frequency": preferences.notification_frequency,
        }
    }

# Update notification preferences
@router.put("/notifications/preferences")
async def update_notification_preferences(
    preferences_update: NotificationPreferencesUpdate,
    user_id: int,
    db: Session = Depends(get_db)
):
    preferences = db.query(UserNotificationPreferences).filter(
        UserNotificationPreferences.user_id == user_id
    ).first()

    if not preferences:
        preferences = UserNotificationPreferences(user_id=user_id)
        db.add(preferences)

    # Update fields
    update_data = preferences_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(preferences, field, value)

    preferences.updated_at = datetime.utcnow()
    db.commit()

    return {"success": True, "message": "Preferences updated successfully"}

# Register FCM token
@router.post("/notifications/fcm-token")
async def register_fcm_token(
    token_data: FCMTokenRegister,
    user_id: int,
    db: Session = Depends(get_db)
):
    success = notification_service.register_fcm_token(
        user_id, token_data.token, token_data.device_type or "android"
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to register FCM token")

    return {"message": "FCM token registered successfully"}

# Unregister FCM token
@router.delete("/notifications/fcm-token")
async def unregister_fcm_token(
    token: str,
    user_id: int,
    db: Session = Depends(get_db)
):
    # Verify token belongs to user
    fcm_token = db.query(FCMToken).filter(
        FCMToken.token == token,
        FCMToken.user_id == user_id
    ).first()

    if not fcm_token:
        raise HTTPException(status_code=404, detail="FCM token not found")

    success = notification_service.unregister_fcm_token(token)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to unregister FCM token")

    return {"message": "FCM token unregistered successfully"}

# Send test notification (for development)
@router.post("/notifications/test")
async def send_test_notification(
    user_id: int,
    background_tasks: BackgroundTasks
):
    background_tasks.add_task(
        notification_service.send_notification_to_user,
        user_id,
        "Test Notification",
        "This is a test notification to verify your notification setup.",
        "system"
    )

    return {"success": True, "message": "Test notification queued"}

# Admin endpoints for bulk notifications
@router.post("/admin/notifications/price-drops")
async def notify_price_drops(
    product_ids: List[int],
    background_tasks: BackgroundTasks
):
    background_tasks.add_task(
        notification_service.notify_price_drops,
        product_ids
    )
    return {"message": "Price drop notifications queued"}

@router.post("/admin/notifications/new-deals")
async def notify_new_deals(
    background_tasks: BackgroundTasks,
    limit: int = 10
):
    background_tasks.add_task(
        notification_service.notify_new_deals,
        limit
    )
    return {"message": "New deals notifications queued"}

@router.post("/admin/notifications/weekly-digest")
async def send_weekly_digest(
    background_tasks: BackgroundTasks
):
    background_tasks.add_task(
        notification_service.send_weekly_digest
    )
    return {"message": "Weekly digest notifications queued"}


# ── Interest tracking ────────────────────────────────────────────────────────

@router.post("/notifications/track-interaction")
async def track_user_interaction(
    body: TrackInteractionRequest,
    db: Session = Depends(get_db),
):
    """
    Record a user interaction (search / view / comparison / favorite).
    Called by the Flutter app whenever the user interacts with a product.
    """
    valid_types = {"search", "view", "comparison", "favorite"}
    if body.interaction_type not in valid_types:
        raise HTTPException(
            status_code=422,
            detail=f"interaction_type must be one of {valid_types}",
        )

    log_user_interest(
        db,
        user_id=body.user_id,
        product_name=body.product_name,
        interaction_type=body.interaction_type,
        brand=body.brand or "",
        category=body.category or "",
        quantity=body.quantity or "",
    )
    return {"success": True}


@router.get("/notifications/interests")
async def get_user_interest_profile(
    user_id: int,
    db: Session = Depends(get_db),
):
    """Return the interest profile for a user (for debugging / transparency)."""
    interests = get_user_interests(db, user_id)
    return {
        "success": True,
        "interests": [
            {
                "product_name": i.product_name,
                "product_key": i.product_key,
                "brand": i.brand,
                "category": i.category,
                "quantity": i.quantity,
                "search_count": i.search_count,
                "view_count": i.view_count,
                "comparison_count": i.comparison_count,
                "favorite_count": i.favorite_count,
                "last_interacted_at": i.last_interacted_at,
            }
            for i in interests
        ],
    }


# ── Personalized price-drop check ────────────────────────────────────────────

@router.post("/notifications/personalized/check")
async def trigger_personalized_check(
    user_id: int,
    background_tasks: BackgroundTasks,
):
    """
    Trigger an on-demand personalized price-drop scan for a single user.
    Typically called after a scraping job finishes.
    """
    background_tasks.add_task(
        notification_service.notify_personalized_price_drops,
        user_id,
    )
    return {"success": True, "message": "Personalized check queued"}
