# app/services/scraping/static_scrapers/qne/parser.py

import re
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

# Database
try:
    from app.database.database import SessionLocal
    from app.database import crud
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# --- CONFIG ---
BASE_DOMAIN = "https://qne.com.pk"
QUANTITY_PATTERN = r'(?i)(?:\b\d+(?:\.\d+)?\s*[xX]\s*)?\b\d+(?:\.\d+)?\s*(?:kg|gm|g|ml|ltr|liter|liters|litres|l|lb|pound|pounds|oz|pcs|pieces|piece|pack|packs|pk|pair|pairs|sachet|ply|metres|meters|feet|inches|portions|portion|s)\b(?:\s+(?:Bottle|Can|Jar|Pouch|Bowl|Bag|Carton|Box|Packet))?'

# ---------------------------
# HELPERS
# ---------------------------
def extract_quantity(text):
    if not text: return None
    matches = re.findall(QUANTITY_PATTERN, text)
    if matches:
        return ", ".join([m.strip() for m in matches])
    return None

def get_pkt_timestamp():
    return datetime.now(timezone(timedelta(hours=5))).strftime("%A, %B %d, %Y %I:%M %p PKT")

# ---------------------------
# PARSE SINGLE PRODUCT 
# ---------------------------
def parse_item(session, item):
    """
    Parses a product directly from the JSON payload provided by the new harvester.
    No network request required.
    """
    url = item['url']
    fallback_cat = item.get('category', 'Uncategorized').replace('-', ' ').title()
    
    product_json = item.get('product_json')
    if not product_json:
        print(f"[{url}] ⚠️ Missing product_json payload. Skipping.")
        return None

    try:
        # Product Name
        name = product_json.get('title', 'Unknown')

        # Brand
        brand = product_json.get('vendor', 'Generic')
        if not brand or str(brand).strip() == '':
            brand = 'Generic'

        # Variants for Pricing and Stock
        variants = product_json.get('variants', [])
        if not variants:
            print(f"[{url}] No variants found.")
            return None
            
        # Use first variant
        variant = variants[0]

        # Prices
        current_price = float(variant.get('price', 0))
        compare_at = variant.get('compare_at_price')
        old_price = float(compare_at) if compare_at else current_price
        
        save_amount = max(old_price - current_price, 0)

        # Stock
        out_of_stock = "No" if variant.get('available') else "Yes"

        # Quantity
        quantity = extract_quantity(name)

        # Category
        category = fallback_cat

        # Image
        images = product_json.get('images', [])
        image_url = images[0].get('src').split('?')[0] if images and images[0].get('src') else None

        # Timestamp
        timestamp = get_pkt_timestamp()

        # Output payload matching the required schema
        return {
            'Product name': name,
            'Brand': brand,
            'Category': category,
            'Quantity': quantity,
            'Old price': int(old_price) if old_price.is_integer() else old_price,
            'Discounted price': int(current_price) if current_price.is_integer() else current_price,
            'Save amount': int(save_amount) if save_amount.is_integer() else save_amount,
            'Store': 'QnE',
            'Out of stock': out_of_stock,
            'Product URL': url,
            'Timestamp': timestamp,
            'Image URL': image_url
        }

    except Exception as e:
        print(f"[{url}] Parsing error: {e}")
        import traceback
        traceback.print_exc()
        return None

# ---------------------------
# RUN PARSER
# ---------------------------
def run_parser(items, workers=5, store='qne', job_id=None):
    results = []
    failed_urls = []

    # Still using threading wrapper to match API, but execution is instant
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # Pass None for session since we don't need network IO in parser anymore
        futures = {executor.submit(parse_item, None, item): item for item in items}

        for future in futures:
            original_item = futures[future]
            parsed_data = future.result()
            if parsed_data:
                results.append(parsed_data)
            else:
                failed_urls.append(original_item['url'])

    # Save to DB
    if results and DB_AVAILABLE:
        db = SessionLocal()
        try:
            crud.save_scraped_data(db, results, store, job_id)
        finally:
            db.close()

    return results, failed_urls
