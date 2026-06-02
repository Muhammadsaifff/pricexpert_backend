import os
import sys
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict

# 1. FastAPI & Scheduler Imports
from fastapi import FastAPI, BackgroundTasks, HTTPException
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# 2. Ensure package root is on path (should already be since we run from workspace root)
#    no explicit append needed unless running from a different directory

# 3. Import the scraping logic DIRECTLY from your application
#    (We don't need subprocess anymore, we use the code you already wrote!)
try:
    from backend.api.endpoints.scraping import run_naheed_scraping, run_qne_scraping
except ImportError as e:
    print(f"❌ Import Error: {e}")
    print("   Ensure you are running this from the root directory containing the backend package.")
    sys.exit(1)

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
INTERVAL_HOURS = 3
LOG_FILE = "scheduler.log"

# ==========================================
# 📝 LOGGING SETUP
# ==========================================
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Global State
state = {
    "scheduler_active": False,
    "last_run": "Never",
    "next_scheduled_run": "Calculating...",
    "active_jobs": {}
}

# Initialize Scheduler
scheduler = AsyncIOScheduler()

# ==========================================
# 🔄 THE UNIFIED SCRAPING JOB
# ==========================================
async def run_unified_job():
    """
    This function triggers both Naheed and QnE scrapers sequentially.
    It simulates what 'subprocess' used to do, but natively.
    """
    job_id = str(uuid.uuid4())
    logger.info(f"⏰ SCHEDULER: Starting Unified Scrape (ID: {job_id})")
    print(f"\n⚡ [{datetime.now().strftime('%H:%M')}] Scheduler triggered scraping job...")

    try:
        # 1. Run Naheed
        print("   > Starting Naheed...")
        # use_test_config=False means PRODUCTION mode
        await run_naheed_scraping(job_id=f"{job_id}_naheed", use_test_config=False)
        
        # 2. Run QnE
        print("   > Starting QnE...")
        await run_qne_scraping(job_id=f"{job_id}_qne")

        # Update State
        state["last_run"] = datetime.now().strftime('%Y-%m-%d %I:%M %p')
        logger.info(f"✅ SCHEDULER: Job {job_id} Completed Successfully.")
        print("   ✅ All Scrapers Finished.")

    except Exception as e:
        logger.error(f"❌ SCHEDULER ERROR: {str(e)}")
        print(f"   ❌ Error: {e}")

# ==========================================
# 🚀 LIFESPAN (STARTUP/SHUTDOWN)
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- STARTUP ---
    print(f"🚀 SCHEDULER API ACTIVE | Interval: Every {INTERVAL_HOURS} Hours")
    
    # Schedule the job
    scheduler.add_job(
        run_unified_job, 
        IntervalTrigger(hours=INTERVAL_HOURS), 
        id="unified_scraper",
        replace_existing=True
    )
    scheduler.start()
    
    # Update State info
    state["scheduler_active"] = True
    next_run = scheduler.get_job("unified_scraper").next_run_time
    if next_run:
        state["next_scheduled_run"] = next_run.strftime('%I:%M %p')
    
    yield
    
    # --- SHUTDOWN ---
    scheduler.shutdown()
    print("🛑 Scheduler shut down.")

# ==========================================
# 🌐 API ENDPOINTS
# ==========================================
app = FastAPI(lifespan=lifespan, title="Scraper Scheduler Service")

@app.get("/")
def home():
    """View Scheduler Status"""
    # Refresh next run time for display
    if scheduler.get_job("unified_scraper"):
        next_run = scheduler.get_job("unified_scraper").next_run_time
        state["next_scheduled_run"] = next_run.strftime('%I:%M %p') if next_run else "Paused"
    
    return {
        "message": "Scheduler is running",
        "config": {"interval_hours": INTERVAL_HOURS},
        "status": state
    }

@app.post("/trigger")
async def trigger_now(background_tasks: BackgroundTasks):
    """Force the scraper to run IMMEDIATELY (Manual Trigger)"""
    background_tasks.add_task(run_unified_job)
    return {"message": "Scraper job triggered in background"}

@app.post("/stop")
def stop_scheduler():
    """Pause the automatic timer"""
    scheduler.pause()
    state["scheduler_active"] = False
    return {"message": "Scheduler paused"}

@app.post("/resume")
def resume_scheduler():
    """Resume the automatic timer"""
    scheduler.resume()
    state["scheduler_active"] = True
    return {"message": "Scheduler resumed"}

@app.get("/logs")
def get_logs(lines: int = 50):
    """View logs in browser"""
    if not os.path.exists(LOG_FILE):
        return {"logs": ["Log file empty"]}
    try:
        with open(LOG_FILE, "r") as f:
            return {"logs": f.readlines()[-lines:]}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    # Run the Scheduler API on port 8001 to avoid conflict with Main API (8000)
    uvicorn.run(app, host="0.0.0.0", port=8001)