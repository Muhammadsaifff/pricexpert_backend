import logging
import os
import sys
import warnings
from pathlib import Path

# Setup Logging FIRST
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress warnings
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
warnings.filterwarnings('ignore')
logging.getLogger("pydantic").setLevel(logging.WARNING)
logging.getLogger("tensorflow").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Add the project root to Python path - CRITICAL for Render
sys.path.insert(0, str(Path(__file__).parent))

# Debug: Print path info (remove after confirming it works)
logger.info(f"Current working directory: {os.getcwd()}")
logger.info(f"Python path: {sys.path[:3]}")
logger.info(f"Backend folder exists: {os.path.exists('backend')}")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# -------------------------------
# Import DB & Routers
# -------------------------------
try:
    from backend.database.database import engine, Base, init_db
    from backend.database import models
    logger.info("✅ Database imports successful")
except Exception as e:
    logger.error(f"❌ Database import error: {e}")
    raise

# -------------------------------
# Create DB tables
# -------------------------------
try:
    logger.info("Creating database tables...")
    init_db()
    logger.info("✅ Database tables created")
except Exception as e:
    logger.error(f"❌ Database initialization error: {e}")
    raise

# -------------------------------
# Scheduler Setup
# -------------------------------
scheduler = AsyncIOScheduler()
SCHEDULER_INTERVAL_HOURS = 3

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting PriceXpert API on Render...")
    
    try:
        # Import dynamic task runner to avoid circular imports
        from backend.api.endpoints.scraping import run_scheduled_unified_scraping
        from backend.services.notification_service import notification_service
        
        if not scheduler.running:
            # Scraping job
            scheduler.add_job(
                run_scheduled_unified_scraping,
                IntervalTrigger(hours=SCHEDULER_INTERVAL_HOURS),
                id="unified_scraper_auto",
                replace_existing=True
            )
            
            # Daily notifications (new deals)
            scheduler.add_job(
                notification_service.notify_new_deals,
                IntervalTrigger(hours=24),
                id="daily_deals_notifications",
                replace_existing=True
            )
            
            # Weekly digest
            scheduler.add_job(
                notification_service.send_weekly_digest,
                IntervalTrigger(days=7),
                id="weekly_digest",
                replace_existing=True
            )
            
            scheduler.start()
            logger.info(f"✅ Scheduler Started (Scraping: every {SCHEDULER_INTERVAL_HOURS} hours, Notifications: daily/weekly)")
    except Exception as e:
        logger.error(f"❌ Scheduler startup error: {e}")
        # Don't raise - app can still work without scheduler
    
    yield
    
    if scheduler.running:
        scheduler.shutdown()
        logger.info("🛑 Scheduler Stopped")

# -------------------------------
# FastAPI App
# -------------------------------
app = FastAPI(
    title="Price Xpert API",
    description="AI-Based Grocery Price Comparison System",
    version="1.0.0",
    lifespan=lifespan
)

# === CORS ===
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Routers ===
try:
    from backend.api.endpoints import smart_comparison, ai_chatbot, scraping, auth, deals, products, notifications
    
    app.include_router(smart_comparison.router, prefix="/api/v1")
    app.include_router(ai_chatbot.router, prefix="/api/v1")
    app.include_router(scraping.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(deals.router, prefix="/api/v1")
    app.include_router(products.router, prefix="/api/v1")
    app.include_router(notifications.router, prefix="/api/v1")
    logger.info("✅ All routers loaded successfully")
except Exception as e:
    logger.error(f"❌ Router import error: {e}")
    raise

# === Root endpoint ===
@app.get("/")
async def root():
    return {
        "message": "Welcome to PriceXpert AI-Powered Shopping Assistant 🛒",
        "status": "running on Render",
        "scheduler_status": "Running" if scheduler.running else "Stopped",
        "endpoints": {
            "signup": "POST /api/v1/auth/signup",
            "signin": "POST /api/v1/auth/signin",
            "guest": "POST /api/v1/auth/guest",
            "products": "GET /api/v1/products",
            "product_search": "GET /api/v1/products/search",
            "smart_compare": "POST /api/v1/smart-compare",
            "ai_chat": "POST /api/v1/ai-chat",
            "deals": "GET /api/v1/deals",
            "featured_deals": "GET /api/v1/deals/featured",
            "store_deals": "GET /api/v1/deals/store/{store_name}",
            "sentiment": "POST /api/v1/sentiment-test",
            "scraping_trigger": "POST /api/v1/scraping/scheduler/trigger",
            "scraping_status": "GET /api/v1/scraping/scheduler/status"
        }
    }

# Health check endpoint for Render
@app.get("/health")
async def health_check():
    return {"status": "healthy", "scheduler": scheduler.running}
