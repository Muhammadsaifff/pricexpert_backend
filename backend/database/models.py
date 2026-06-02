# pricexpert/database/models.py

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, UniqueConstraint, ForeignKey
from sqlalchemy.sql import func
from .database import Base
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List, Any, Dict

# ------------------- Product Model -------------------
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    product_name = Column(String, index=True)
    brand = Column(String, nullable=True)
    category = Column(String, nullable=True)
    quantity = Column(String, nullable=True)
    old_price = Column(Float, nullable=True)
    discounted_price = Column(Float)
    save_amount = Column(Float, nullable=True)
    store = Column(String, index=True)
    out_of_stock = Column(Boolean, default=False)
    product_url = Column(String, unique=True, nullable=True)
    image_url = Column(String, nullable=True)
    job_id = Column(String, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow)
    timestamp = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('product_url', name='uq_product_url'),
    )

    def __repr__(self):
        return f"<Product {self.product_name} - {self.store}>"


# ------------------- ScrapingJob Model -------------------
class ScrapingJob(Base):
    __tablename__ = "scraping_jobs"

    id = Column(String, primary_key=True, index=True)
    store = Column(String, nullable=False)
    store_type = Column(String, nullable=False)
    status = Column(String, default="queued")          # queued, running, completed, failed
    stage = Column(String, default="queued")           # queued, harvesting, parsing, completed
    use_test_config = Column(Boolean, default=True)
    urls_count = Column(Integer, default=0)
    products_count = Column(Integer, default=0)
    message = Column(String, nullable=True)
    error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<ScrapingJob {self.id} - {self.store} - {self.status}>"


# ------------------- Pydantic Models -------------------
class ProductResponse(BaseModel):
    id: int
    product_name: Optional[str]
    brand: Optional[str]
    category: Optional[str]
    quantity: Optional[str]
    old_price: Optional[float]
    discounted_price: Optional[float]
    save_amount: Optional[float]
    store: Optional[str]
    out_of_stock: Optional[bool]
    product_url: Optional[str]
    image_url: Optional[str]

    class Config:
        orm_mode = True


class APIResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

from sqlalchemy.orm import relationship

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.database.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    email = Column(String, unique=True)
    password = Column(String, nullable=True)  # Hashed password, nullable for Google users
    language = Column(String)
    budget = Column(Float)

    search_queries = relationship("SearchQuery", back_populates="user")


class SearchQuery(Base):
    __tablename__ = "search_queries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    query_text = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Add this relationship
    user = relationship("User", back_populates="search_queries")


# ------------------- Notification Models -------------------
class UserNotificationPreferences(Base):
    __tablename__ = "user_notification_preferences"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    deals_notifications = Column(Boolean, default=True)
    discount_notifications = Column(Boolean, default=True)
    price_drop_notifications = Column(Boolean, default=True)
    weekly_digest = Column(Boolean, default=True)
    push_notifications = Column(Boolean, default=True)
    email_notifications = Column(Boolean, default=False)
    notification_frequency = Column(String, default="daily")  # daily, weekly, realtime
    last_notification_sent = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="notification_preferences")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    notification_type = Column(String, nullable=False)  # deal, discount, price_drop, system
    related_product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    data = Column(String, nullable=True)  # JSON string for additional data
    is_read = Column(Boolean, default=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="notifications")
    product = relationship("Product")


class FCMToken(Base):
    __tablename__ = "fcm_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    token = Column(String, unique=True, nullable=False)
    device_type = Column(String, nullable=True)  # android, ios, web
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="fcm_tokens")


class UserInterest(Base):
    """
    Lightweight interest profile built from user behaviour.
    One row per (user, normalized_product_key) — upserted on each interaction.
    """
    __tablename__ = "user_interests"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Canonical product identity used for matching (brand + name + quantity normalised)
    product_key = Column(String, nullable=False)   # e.g. "pepsi cola 1000ml"
    product_name = Column(String, nullable=False)
    brand = Column(String, nullable=True)
    category = Column(String, nullable=True)
    quantity = Column(String, nullable=True)        # canonical qty, e.g. "1000ml"
    # Interaction counters — any non-zero value means "interested"
    search_count = Column(Integer, default=0)
    view_count = Column(Integer, default=0)
    comparison_count = Column(Integer, default=0)
    favorite_count = Column(Integer, default=0)
    last_interacted_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="interests")

    __table_args__ = (
        UniqueConstraint("user_id", "product_key", name="uq_user_interest"),
    )


class NotificationDeliveryLog(Base):
    """
    Record of every notification that was actually delivered.
    Used to enforce per-product cooldowns and prevent duplicate spam.
    """
    __tablename__ = "notification_delivery_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_key = Column(String, nullable=False)   # same key as UserInterest
    notification_type = Column(String, nullable=False)  # price_drop, deal, discount, featured
    price_at_delivery = Column(Float, nullable=True)    # price snapshot so we re-notify on change
    store = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="delivery_logs")


# Update User model to include relationships
User.search_queries = relationship("SearchQuery", back_populates="user")
User.notification_preferences = relationship("UserNotificationPreferences", back_populates="user")
User.notifications = relationship("Notification", back_populates="user")
User.fcm_tokens = relationship("FCMToken", back_populates="user")
User.interests = relationship("UserInterest", back_populates="user")
User.delivery_logs = relationship("NotificationDeliveryLog", back_populates="user")
