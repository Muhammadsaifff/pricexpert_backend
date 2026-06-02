import os
import json
import uuid
import asyncio
import logging
import random
import time
import requests
from datetime import datetime
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from sqlalchemy.orm import Session

# Database Imports
from backend.database.database import get_db, SessionLocal
from backend.database import crud, models
from backend.database.models import APIResponse

# Scraper Imports
from backend.services.scraping.dynamic_scrapers.naheed import harvester as naheed_harvester
from backend.services.scraping.dynamic_scrapers.naheed import parser as naheed_parser
#from backend.services.scraping.dynamic_scrapers.metro import harvester as metro_harvester
#from backend.services.scraping.dynamic_scrapers.metro import parser as metro_parser
from backend.services.scraping.static_scrapers.qne import harvester as qne_harvester
from backend.services.scraping.static_scrapers.qne import parser as qne_parser
from backend.services.scraping.dynamic_scrapers.imtiaz import harvester as imtiaz_harvester
from backend.services.scraping.dynamic_scrapers.imtiaz import parser as imtiaz_parser
from backend.services.scraping.dynamic_scrapers.carrefour import harvester as carrefour_harvester
from backend.services.scraping.dynamic_scrapers.carrefour import parser as carrefour_parser
from backend.services.scraping.dynamic_scrapers.binhashim import harvester as binhashim_harvester
from backend.services.scraping.dynamic_scrapers.binhashim import parser as binhashim_parser
from backend.services.scraping.dynamic_scrapers.chaseup import harvester as chaseup_harvester
from backend.services.scraping.dynamic_scrapers.chaseup import parser as chaseup_parser

router = APIRouter(prefix="/scraping", tags=["scraping"])

# === CONFIGURATION ===
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(BASE_DIR, 'services', 'scraping', 'configs')

# ⚡ OPTIMIZED STORE-SPECIFIC CONFIGURATION
STORE_WORKER_CONFIG = {
    'naheed': 12,
    'metro': 6,
    'qne': 4,
    'imtiaz': 6,
    'carrefour': 2,
    'binhashim': 6,
    'chaseup': 6
}

# Store-specific delays (seconds)
STORE_DELAYS = {
    'naheed': 0.2,
    'metro': 0.5,
    'qne': 0.8,
    'imtiaz': 0.3,
    'carrefour': 1.0,
    'binhashim': 0.3,
    'chaseup': 0.3
}

# Max retry attempts per store
STORE_RETRIES = {
    'naheed': 2,
    'metro': 3,
    'qne': 5,
    'imtiaz': 3,
    'carrefour': 3,
    'binhashim': 3,
    'chaseup': 3
}

# Calculate total for unified mode
MAX_GLOBAL_WORKERS = sum(STORE_WORKER_CONFIG.values())

# Job Tracking (In-Memory)
scraping_jobs: Dict[str, Dict] = {}

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- Helpers -----------------

def load_config(store_type: str, store_name: str, config_file: str):
    """Load configuration JSON"""
    config_path = os.path.join(CONFIG_DIR, store_type, store_name, config_file)
    if not os.path.exists(config_path):
        config_path = os.path.join(CONFIG_DIR, store_type, config_file)
    if not os.path.exists(config_path):
        config_path = os.path.join(CONFIG_DIR, config_file)
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        return json.load(f)

def get_shared_session():
    """Creates an optimized session with store-aware settings"""
    session = requests.Session()
    
    retry_strategy = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        respect_retry_after_header=True
    )
    
    adapter = HTTPAdapter(
        pool_connections=MAX_GLOBAL_WORKERS * 2,
        pool_maxsize=MAX_GLOBAL_WORKERS * 2,
        max_retries=retry_strategy,
        pool_block=False
    )
    
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    
    session.headers.update({
        'User-Agent': random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    })
    
    return session

def apply_intelligent_delay(store_name: str, last_request_time: Dict[str, float]):
    """Apply smart delay based on store and request timing"""
    current_time = time.time()
    
    if store_name in last_request_time:
        time_since_last = current_time - last_request_time[store_name]
        min_delay = STORE_DELAYS.get(store_name, 0.5)
        
        if time_since_last < min_delay:
            extra_wait = min_delay - time_since_last + random.uniform(0, 0.2)
            time.sleep(extra_wait)
    
    last_request_time[store_name] = time.time()

# ----------------- Enhanced Batch Processing -----------------

def process_store_in_batches(items, parser_func, session, store_name: str, 
                           batch_size: int = 100, progress_callback=None):
    """Process items in batches with detailed progress tracking"""
    results = []
    failed_items = []
    
    # Create batches
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    total_batches = len(batches)
    
    logger.info(f"   📦 Processing {store_name}: {len(items)} items in {total_batches} batches")
    logger.info(f"   ⚙️  {store_name} config: Workers={STORE_WORKER_CONFIG[store_name]}, Delay={STORE_DELAYS[store_name]}s")
    
    start_time = time.time()
    
    for batch_num, batch in enumerate(batches, 1):
        batch_start = time.time()
        logger.info(f"   🔄 {store_name} - Starting batch {batch_num}/{total_batches} ({len(batch)} items)")
        
        # Process batch with store-specific concurrency
        with ThreadPoolExecutor(max_workers=STORE_WORKER_CONFIG[store_name]) as executor:
            batch_futures = []
            last_request_time = {}
            
            for idx, item in enumerate(batch):
                if isinstance(item, dict):
                    item['_store'] = store_name
                
                # Apply delay
                apply_intelligent_delay(store_name, last_request_time)
                
                # Submit task
                future = executor.submit(parser_func, session, item)
                batch_futures.append(future)
            
            # Collect results with progress tracking
            completed = 0
            for future in as_completed(batch_futures):
                completed += 1
                try:
                    result = future.result(timeout=30)
                    if result:
                        results.append(result)
                        if completed % 50 == 0:  # Log every 50 items
                            logger.info(f"      {store_name}: Processed {completed}/{len(batch)} items in batch {batch_num}")
                except Exception as e:
                    logger.warning(f"   ⚠️ {store_name} item failed: {str(e)[:100]}")
                    failed_items.append(str(e))
        
        batch_time = time.time() - batch_start
        batch_rate = len(batch) / batch_time if batch_time > 0 else 0
        cumulative_rate = len(results) / (time.time() - start_time) if (time.time() - start_time) > 0 else 0
        
        logger.info(f"   ✅ {store_name} - Batch {batch_num}/{total_batches} completed: "
                   f"{len(results)}/{len(items)} total ({len(results)/len(items)*100:.1f}%) | "
                   f"Speed: {batch_rate:.1f} items/sec (avg: {cumulative_rate:.1f} items/sec)")
        
        if progress_callback:
            progress_callback(batch_num, total_batches, batch_rate)
        
        # Brief pause between batches
        if batch_num < total_batches:
            batch_pause = random.uniform(1, 3)
            logger.info(f"   ⏸️  {store_name} - Pausing {batch_pause:.1f}s before next batch...")
            time.sleep(batch_pause)
    
    total_time = time.time() - start_time
    logger.info(f"   🎉 {store_name} - COMPLETED: {len(results)}/{len(items)} items parsed "
               f"in {total_time:.1f}s ({len(results)/total_time:.1f} items/sec)")
    
    return results, failed_items

# ----------------- Core Logic with Enhanced Logging -----------------

async def run_unified_scraping_process(job_id: str, use_test_config: bool = False):
    """
    The Optimized Master Scraper Logic with parallel store processing.
    """
    try:
        # Update Status
        scraping_jobs[job_id] = {
            "job_id": job_id,
            "store": "unified",
            "status": "running",
            "stage": "harvesting",
            "started_at": datetime.now().isoformat(),
            "use_test_config": use_test_config,
            "urls_count": 0,
            "products_count": 0,
            "naheed": {"harvested": 0, "parsed": 0},
            "metro": {"harvested": 0, "parsed": 0},
            "qne": {"harvested": 0, "parsed": 0},
            "imtiaz": {"harvested": 0, "parsed": 0},
            "carrefour": {"harvested": 0, "parsed": 0},
            "binhashim": {"harvested": 0, "parsed": 0},
            "chaseup": {"harvested": 0, "parsed": 0}
        }
        
        logger.info(f"🚀 {'='*60}")
        logger.info(f"🚀 Job {job_id}: Starting Optimized Unified Scrape")
        logger.info(f"🚀 Test Mode: {use_test_config}")
        logger.info(f"🚀 {'='*60}")
        
        # --- Stage 1: Parallel Harvesting ---
        scraping_jobs[job_id]["stage"] = "harvesting"
        logger.info(f"\n📡 STAGE 1: HARVESTING - Gathering product URLs from all stores\n")
        
        async def harvest_store(store_name: str, harvester_func, config_file: str, 
                               store_type: str = 'dynamic', workers: int = 3):
            """Harvest a single store"""
            try:
                config = config_file if isinstance(config_file, list) else load_config(
                    store_type, store_name, 
                    f"{store_name}_test_categories.json" if use_test_config else f"{store_name}_categories.json"
                )
                
                logger.info(f"   🌐 Harvesting {store_name.upper()}...")
                harvest_start = time.time()
                
                if store_name == 'naheed':
                    items = await asyncio.to_thread(harvester_func, config)
                elif store_name == 'metro':
                    items = await asyncio.to_thread(harvester_func, config, workers=workers)
                elif store_name == 'qne':
                    items = await asyncio.to_thread(harvester_func, config)
                elif store_name in ['imtiaz', 'carrefour', 'binhashim', 'chaseup']:
                    items = await asyncio.to_thread(harvester_func, config, workers=workers)
                else:
                    items = []
                
                harvest_time = time.time() - harvest_start
                logger.info(f"   ✅ {store_name.upper()}: {len(items)} URLs harvested in {harvest_time:.1f}s ({len(items)/harvest_time:.1f} URLs/sec)")
                return store_name, items
                
            except Exception as e:
                logger.error(f"   ❌ {store_name.upper()} Harvest Failed: {e}")
                return store_name, []
        
        # Harvest all stores in parallel
        harvest_tasks = [
            harvest_store('naheed', naheed_harvester.start_harvest, 'naheed'),
            # harvest_store('metro', metro_harvester.start_harvest, 'metro', workers=2),
            harvest_store('qne', qne_harvester.start_harvest, 'qne', 'static', 1),
            harvest_store('imtiaz', imtiaz_harvester.start_harvest, 'imtiaz', workers=2),
            harvest_store('carrefour', carrefour_harvester.start_harvest, 'carrefour', workers=2),
            harvest_store('binhashim', binhashim_harvester.start_harvest, 'binhashim', workers=2),
            harvest_store('chaseup', chaseup_harvester.start_harvest, 'chaseup', workers=2)
        ]
        
        harvest_results = await asyncio.gather(*harvest_tasks)
        
        # Organize harvested items
        store_items = {}
        total_urls = 0
        
        logger.info(f"\n📊 HARVEST SUMMARY:")
        for store_name, items in harvest_results:
            store_items[store_name] = items
            scraping_jobs[job_id][store_name]["harvested"] = len(items)
            total_urls += len(items)
            logger.info(f"   📍 {store_name.upper():12}: {len(items):5} URLs")
        
        scraping_jobs[job_id]["urls_count"] = total_urls
        logger.info(f"\n   ✅ TOTAL URLs Harvested: {total_urls}\n")
        
        # --- Stage 2: Parallel Parsing with Smart Batching ---
        scraping_jobs[job_id]["stage"] = "parsing"
        logger.info(f"🔧 STAGE 2: PARSING - Extracting product data from {total_urls} URLs\n")
        
        # Create shared session
        session = get_shared_session()
        
        # Store-specific parser functions
        parser_map = {
            'naheed': naheed_parser.parse_item,
            # 'metro': metro_parser.parse_item,
            'qne': qne_parser.parse_item,
            'imtiaz': imtiaz_parser.parse_item,
            'carrefour': carrefour_parser.parse_item,
            'binhashim': binhashim_parser.parse_item,
            'chaseup': chaseup_parser.parse_item
        }
        
        async def parse_store_parallel(store_name: str, items: List):
            """Parse a single store's items with detailed progress"""
            if not items:
                logger.info(f"   ⏭️  {store_name.upper()}: No items to parse")
                return store_name, [], []
            
            logger.info(f"\n   🚀 Starting {store_name.upper()} parsing: {len(items)} items")
            logger.info(f"   ⚙️  {store_name.upper()} - Workers: {STORE_WORKER_CONFIG[store_name]}, Batch size: {150 if store_name == 'naheed' else 100 if store_name == 'metro' else 50}")
            
            def progress_callback(batch_num, total_batches, batch_rate):
                logger.info(f"     📊 {store_name.upper()} Progress: Batch {batch_num}/{total_batches} ({batch_num/total_batches*100:.0f}%) @ {batch_rate:.1f} items/sec")
            
            parse_start = time.time()
            
            # Process with intelligent batching
            results, failed = await asyncio.to_thread(
                process_store_in_batches,
                items,
                parser_map[store_name],
                session,
                store_name,
                batch_size=150 if store_name == 'naheed' else 100 if store_name == 'metro' else 50,
                progress_callback=progress_callback
            )
            
            parse_time = time.time() - parse_start
            success_rate = (len(results) / len(items) * 100) if items else 0
            
            scraping_jobs[job_id][store_name]["parsed"] = len(results)
            logger.info(f"\n   ✅ {store_name.upper()} PARSING COMPLETE:")
            logger.info(f"      📈 Success: {len(results)}/{len(items)} ({success_rate:.1f}%)")
            logger.info(f"      ⏱️  Time: {parse_time:.1f}s ({len(results)/parse_time:.1f} items/sec)")
            logger.info(f"      ❌ Failed: {len(failed)} items")
            
            if len(results) > 0:
                logger.info(f"      📝 Sample: {results[0]['Product name'][:50]}...")
            
            return store_name, results, failed
        
        # Parse all stores in parallel
        parse_tasks = []
        for store_name, items in store_items.items():
            if items:
                parse_tasks.append(parse_store_parallel(store_name, items))
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🔄 Starting parallel parsing for {len(parse_tasks)} stores...")
        logger.info(f"{'='*60}\n")
        
        parse_results = await asyncio.gather(*parse_tasks)
        
        # Organize parsed results
        all_results = []
        all_failed = []
        store_results = {}
        
        logger.info(f"\n📊 PARSING SUMMARY:")
        for store_name, results, failed in parse_results:
            store_results[store_name] = results
            all_results.extend(results)
            all_failed.extend(failed)
            logger.info(f"   📍 {store_name.upper():12}: {len(results):5} products parsed")
        
        logger.info(f"\n   ✅ TOTAL Products Parsed: {len(all_results)} from {len(parse_tasks)} stores\n")
        
        # --- Stage 3: Optimized Database Saving ---
        scraping_jobs[job_id]["stage"] = "saving"
        logger.info(f"💾 STAGE 3: SAVING - Storing {len(all_results)} products to database\n")
        
        # Save in batches to database
        db = SessionLocal()
        try:
            saved_counts = {}
            updated_counts = {}
            
            save_start = time.time()
            
            # Save each store's results
            for store_name, results in store_results.items():
                if results:
                    logger.info(f"   💿 Saving {store_name.upper()}: {len(results)} products...")
                    save_result = crud.save_scraped_data(db, results, store_name, job_id)
                    saved_counts[store_name] = save_result['saved_count']
                    updated_counts[store_name] = save_result['updated_count']
                    logger.info(f"      ✅ {store_name.upper()}: {save_result['saved_count']} saved, {save_result['updated_count']} updated")
            
            save_time = time.time() - save_start
            
            # Create job record
            job_data = {
                'id': job_id,
                'store': 'unified',
                'store_type': 'hybrid',
                'status': 'completed',
                'stage': 'completed',
                'use_test_config': use_test_config,
                'urls_count': total_urls,
                'products_count': len(all_results),
                'message': f"Naheed: {len(store_results.get('naheed', []))} "
                          f"QnE: {len(store_results.get('qne', []))} "
                          f"Imtiaz: {len(store_results.get('imtiaz', []))} "
                          f"Carrefour: {len(store_results.get('carrefour', []))} "
                          f"BinHashim: {len(store_results.get('binhashim', []))} "
                          f"ChaseUp: {len(store_results.get('chaseup', []))}",
                'started_at': datetime.fromisoformat(scraping_jobs[job_id]["started_at"]),
                'completed_at': datetime.now()
            }
            
            crud.create_scraping_job(db, job_data=job_data)
            
            # Update job status
            scraping_jobs[job_id]["status"] = "completed"
            scraping_jobs[job_id]["products_count"] = len(all_results)
            scraping_jobs[job_id]["completed_at"] = datetime.now().isoformat()
            scraping_jobs[job_id]["message"] = job_data['message']
            
            total_saved = sum(saved_counts.values())
            total_updated = sum(updated_counts.values())
            
            logger.info(f"\n{'='*60}")
            logger.info(f"🎉 JOB {job_id} COMPLETED SUCCESSFULLY!")
            logger.info(f"{'='*60}")
            logger.info(f"📊 FINAL SUMMARY:")
            logger.info(f"   🔗 URLs Harvested: {total_urls}")
            logger.info(f"   📦 Products Parsed: {len(all_results)}")
            logger.info(f"   💾 Database: {total_saved} saved, {total_updated} updated")
            logger.info(f"   ⏱️  Total Time: {save_time + (time.time() - scraping_jobs[job_id]['started_at'].replace('T', ' ').split('.')[0] if isinstance(scraping_jobs[job_id]['started_at'], str) else 0):.1f}s")
            logger.info(f"{'='*60}\n")
            
        except Exception as e:
            logger.error(f"   ❌ Database Error: {e}")
            scraping_jobs[job_id]["status"] = "failed"
            scraping_jobs[job_id]["error"] = str(e)
        finally:
            db.close()
            session.close()
        
        # Cleanup
        scraping_jobs[job_id]["stage"] = "completed"
        
    except Exception as e:
        logger.error(f"❌ Job {job_id} Critical Fail: {e}")
        import traceback
        traceback.print_exc()
        scraping_jobs[job_id] = scraping_jobs.get(job_id, {})
        scraping_jobs[job_id]["status"] = "failed"
        scraping_jobs[job_id]["error"] = str(e)

# ----------------- Scheduler Entry Point -----------------

async def run_scheduled_unified_scraping():
    """
    Optimized scheduled scraping with intelligent rate limiting.
    """
    job_id = f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    scraping_jobs[job_id] = {
        "job_id": job_id,
        "store": "unified",
        "status": "queued",
        "created_at": datetime.now().isoformat(),
        "type": "scheduled"
    }
    
    # Run in Production Mode (False) or Test Mode (True) based on time
    current_hour = datetime.now().hour
    use_test_config = current_hour >= 22 or current_hour <= 6
    
    await run_unified_scraping_process(job_id, use_test_config=use_test_config)

# ----------------- API Endpoints -----------------

@router.post("/start/unified", response_model=APIResponse)
async def start_unified_scraping(
    background_tasks: BackgroundTasks,
    use_test_config: bool = False,
    max_workers_override: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Start optimized unified scraping"""
    job_id = str(uuid.uuid4())
    
    # Apply worker override if provided
    if max_workers_override:
        STORE_WORKER_CONFIG['naheed'] = min(max_workers_override, 15)
        STORE_WORKER_CONFIG['metro'] = min(max_workers_override // 2, 8)
        STORE_WORKER_CONFIG['qne'] = min(max_workers_override // 3, 6)
        STORE_WORKER_CONFIG['imtiaz'] = min(max_workers_override // 2, 8)
        STORE_WORKER_CONFIG['carrefour'] = min(max_workers_override // 4, 3)
        STORE_WORKER_CONFIG['binhashim'] = min(max_workers_override // 2, 8)
        STORE_WORKER_CONFIG['chaseup'] = min(max_workers_override // 2, 8)
    
    scraping_jobs[job_id] = {
        "job_id": job_id,
        "store": "unified",
        "status": "queued",
        "use_test_config": use_test_config,
        "created_at": datetime.now().isoformat(),
        "stage": "queued",
        "urls_count": 0,
        "products_count": 0,
        "max_workers": max_workers_override
    }
    
    background_tasks.add_task(run_unified_scraping_process, job_id, use_test_config)
    
    logger.info(f"📋 Job {job_id} created and queued")
    
    return APIResponse(
        success=True,
        message="Optimized unified scraping job started",
        data={
            "job_id": job_id,
            "config": {
                "naheed_workers": STORE_WORKER_CONFIG['naheed'],
                "metro_workers": STORE_WORKER_CONFIG['metro'],
                "qne_workers": STORE_WORKER_CONFIG['qne'],
                "imtiaz_workers": STORE_WORKER_CONFIG['imtiaz'],
                "carrefour_workers": STORE_WORKER_CONFIG['carrefour'],
                "binhashim_workers": STORE_WORKER_CONFIG['binhashim'],
                "chaseup_workers": STORE_WORKER_CONFIG['chaseup'],
                "use_test_config": use_test_config
            }
        }
    )

@router.post("/start/individual", response_model=APIResponse)
async def start_individual_scraping(
    background_tasks: BackgroundTasks,
    store: str,
    use_test_config: bool = False,
    db: Session = Depends(get_db)
):
    """Scrape individual store only"""
    if store not in ['naheed', 'metro', 'qne', 'imtiaz', 'carrefour', 'binhashim', 'chaseup']:
        raise HTTPException(status_code=400, detail="Invalid store name")
    
    job_id = f"{store}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    scraping_jobs[job_id] = {
        "job_id": job_id,
        "store": store,
        "status": "queued",
        "use_test_config": use_test_config,
        "created_at": datetime.now().isoformat()
    }
    
    # Run individual store scraping
    async def run_single_store():
        try:
            scraping_jobs[job_id]["status"] = "running"
            logger.info(f"🚀 Starting individual scrape for {store}")
            
            # Load config
            config_file = f"{store}_test_categories.json" if use_test_config else f"{store}_categories.json"
            config_path = os.path.join(CONFIG_DIR, 'dynamic' if store != 'qne' else 'static', store, config_file)
            
            with open(config_path, 'r') as f:
                categories = json.load(f)
            
            # Harvest
            logger.info(f"   📡 Harvesting {store}...")
            if store == 'naheed':
                items = naheed_harvester.start_harvest(categories)
            #elif store == 'metro':
             #   items = metro_harvester.start_harvest(categories, workers=3)
            elif store == 'qne':
                items = qne_harvester.start_harvest(categories)
            else:
                harvester = globals()[f"{store}_harvester"]
                target_workers = 2 if store == 'carrefour' else 3
                items = harvester.start_harvest(categories, workers=target_workers)
            
            logger.info(f"   ✅ {store}: {len(items)} items harvested")
            
            # Parse
            logger.info(f"   🔧 Parsing {store}...")
            session = get_shared_session()
            results, failed = await asyncio.to_thread(
                process_store_in_batches,
                items,
                globals()[f"{store}_parser"].parse_item,
                session,
                store,
                batch_size=100
            )
            
            logger.info(f"   ✅ {store}: {len(results)} items parsed")
            
            # Save to DB
            logger.info(f"   💾 Saving to database...")
            db = SessionLocal()
            save_result = crud.save_scraped_data(db, results, store, job_id)
            db.close()
            
            scraping_jobs[job_id]["status"] = "completed"
            scraping_jobs[job_id]["products_count"] = len(results)
            scraping_jobs[job_id]["completed_at"] = datetime.now().isoformat()
            
            logger.info(f"🎉 {store} scraping completed: {len(results)} products saved")
            
        except Exception as e:
            logger.error(f"❌ {store} scraping failed: {e}")
            scraping_jobs[job_id]["status"] = "failed"
            scraping_jobs[job_id]["error"] = str(e)
    
    background_tasks.add_task(run_single_store)
    
    return APIResponse(
        success=True,
        message=f"Started {store} scraping",
        data={"job_id": job_id}
    )

@router.post("/scheduler/trigger", response_model=APIResponse)
async def trigger_scheduler_manually(background_tasks: BackgroundTasks):
    """Manually trigger the scheduled job"""
    background_tasks.add_task(run_scheduled_unified_scraping)
    logger.info("🕐 Manual scheduler trigger activated")
    return APIResponse(
        success=True, 
        message="Optimized scheduled job triggered",
        data={"time": datetime.now().isoformat()}
    )

@router.get("/status/{job_id}", response_model=APIResponse)
async def get_scraping_status(job_id: str, db: Session = Depends(get_db)):
    """Get detailed scraping status"""
    # Check Memory
    if job_id in scraping_jobs:
        job = scraping_jobs[job_id]
        # Calculate progress percentage if running
        if job.get("status") == "running":
            if job.get("stage") == "harvesting":
                progress = "Harvesting in progress..."
            elif job.get("stage") == "parsing":
                total_parsed = sum(job.get(store, {}).get("parsed", 0) for store in ['naheed', 'metro', 'qne', 'imtiaz', 'carrefour', 'binhashim', 'chaseup'])
                total_harvested = job.get("urls_count", 1)
                progress = f"Parsing: {total_parsed}/{total_harvested} products ({total_parsed/total_harvested*100:.1f}%)"
            elif job.get("stage") == "saving":
                progress = "Saving to database..."
            else:
                progress = "Processing..."
            
            job["progress"] = progress
        
        return APIResponse(
            success=True, 
            message="Job status from memory",
            data={"job": job}
        )
    
    # Check DB
    db_job = crud.get_scraping_job(db, job_id)
    if db_job:
        job_dict = {k: v for k, v in db_job.__dict__.items() if not k.startswith('_')}
        for k, v in job_dict.items():
            if isinstance(v, datetime): 
                job_dict[k] = v.isoformat()
        
        return APIResponse(
            success=True, 
            message="Job found in database",
            data={"job": job_dict}
        )
    
    raise HTTPException(status_code=404, detail="Job not found")

@router.get("/jobs", response_model=APIResponse)
async def list_jobs(db: Session = Depends(get_db), limit: int = 10):
    """List recent jobs with details"""
    db_jobs = crud.get_scraping_jobs(db, limit=limit)
    jobs_list = []
    
    for job in db_jobs:
        job_data = {
            "id": job.id,
            "store": job.store,
            "status": job.status,
            "urls_count": job.urls_count,
            "products_count": job.products_count,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None
        }
        jobs_list.append(job_data)
    
    return APIResponse(
        success=True, 
        message=f"Retrieved {len(jobs_list)} jobs", 
        data={"jobs": jobs_list}
    )

@router.get("/stats", response_model=APIResponse)
async def get_scraping_stats(db: Session = Depends(get_db)):
    """Get scraping statistics"""
    stats = {
        "store_workers": STORE_WORKER_CONFIG,
        "store_delays": STORE_DELAYS,
        "total_workers": MAX_GLOBAL_WORKERS,
        "active_jobs": len([j for j in scraping_jobs.values() if j.get("status") == "running"]),
        "queued_jobs": len([j for j in scraping_jobs.values() if j.get("status") == "queued"]),
        "memory_jobs": len(scraping_jobs),
        "total_products_today": sum(j.get("products_count", 0) for j in scraping_jobs.values() if j.get("completed_at", "").startswith(datetime.now().strftime("%Y-%m-%d")))
    }
    
    return APIResponse(
        success=True,
        message="Scraping system statistics",
        data=stats
    )