import re
import pandas as pd
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ---------------------------
# CONFIG
# ---------------------------
STORE_NAME = "ChaseUp"
BASE_URL = "https://www.chaseupgrocery.com"

UNITS = r'kg|gm|g|ml|ltr|liter|liters|litres|l|lb|pound|pounds|oz|pcs|pieces|piece|pack|packs|pk|pair|pairs|sachet|ply|metres|meters|feet|inches|s'
CONTAINERS = r'Bottle|Can|Jar|Pouch|Bowl|Bag|Carton|Box|Packet'
RE_QUANTITY = re.compile(
    r'(?i)(\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?(?:\s*[xX]\s*)?\s*[-]?\s*(?:' +
    UNITS + r')\b(?:\s+(?:' + CONTAINERS + r'))?)|(?:\bEconomy\s+Pack\b|\bTea\s+Bag\b)'
)


def parse_item(session, item):
    """Parse a ChaseUp product from the Blink API JSON data."""
    try:
        name = item.get('name', '')
        if not name:
            return None

        # Brand — from API field only (NOT from search_tags)
        # ChaseUp search_tags format is "ProductName,Brand" — using tags[1]
        # for category would give the brand name, which is wrong.
        brand = item.get('brand_name', '')
        if not brand or brand == '0':
            # Fallback: try search_tags last element (which is brand for ChaseUp)
            search_tags = item.get('search_tags', '')
            tags = [t.strip() for t in search_tags.split(',') if t.strip()]
            # ChaseUp tags: [ProductName, Brand]
            if len(tags) >= 2 and tags[-1] != name:
                brand = tags[-1]
            else:
                brand = 'Generic'

        # Category — use _category from harvester (navigation-based) ONLY.
        # ChaseUp search_tags do NOT contain category info — they only have
        # [ProductName, Brand], so search_tags must NOT be used for category.
        category = item.get('_category', 'Unknown')

        # Quantity
        qty_matches = RE_QUANTITY.finditer(name)
        results = [m.group(0) for m in qty_matches]
        quantity = results[-1] if results else None

        # Prices — ChaseUp Blink API has two layers:
        #   Root level: price, base_price, discount_price
        #   dish_branch_status: price, base_price, discount_price (branch-specific)
        # IMPORTANT: discount_price is the FINAL discounted selling price,
        #            NOT a discount amount to subtract.
        branch = item.get('dish_branch_status') or {}
        
        # Branch-level prices take priority (this is what the website shows)
        branch_price = float(branch.get('price', 0) or 0)
        branch_discount = float(branch.get('discount_price', 0) or 0)
        branch_base = float(branch.get('base_price', 0) or 0)
        
        # Root-level prices as fallback
        root_price = float(item.get('price', 0) or 0)
        root_discount = float(item.get('discount_price', 0) or 0)
        root_base = float(item.get('base_price', 0) or 0)
        
        # Use branch values if available, otherwise root
        price = branch_price if branch_price > 0 else root_price
        discount_price = branch_discount if branch_discount > 0 else root_discount
        base_price = branch_base if branch_base > 0 else root_base

        # Determine old price and final price
        # discount_price IS the final selling price (not an amount to subtract)
        if discount_price > 0 and discount_price < price:
            old_price = price
            final_price = discount_price
        elif base_price > 0 and price > 0 and base_price > price:
            old_price = base_price
            final_price = price
        elif price > 0:
            old_price = price
            final_price = price
        elif base_price > 0:
            old_price = base_price
            final_price = base_price
        else:
            old_price = 0
            final_price = 0

        save_amount = max(old_price - final_price, 0)

        if old_price == int(old_price): old_price = int(old_price)
        if final_price == int(final_price): final_price = int(final_price)
        if save_amount == int(save_amount): save_amount = int(save_amount)

        # Stock status — check branch status first (branch can override availability)
        if branch and branch.get('status', 1) == 0:
            out_of_stock = "Yes"
        else:
            availability = item.get('availability', 1)
            out_of_stock = "Yes" if availability == 0 else "No"

        # URLs — ChaseUp frontend requires {slug}-{id} format
        slug = item.get('slug', '')
        product_id = str(item.get('id', ''))
        if slug and product_id and product_id not in slug:
            product_url = f"{BASE_URL}/product/{slug}-{product_id}"
        elif slug:
            product_url = f"{BASE_URL}/product/{slug}"
        elif product_id:
            product_url = f"{BASE_URL}/product/{product_id}"
        else:
            product_url = ''

        img_url = item.get('img_url', '')

        # Timestamp (PKT)
        timestamp = datetime.now(timezone(timedelta(hours=5))).strftime("%A, %B %d, %Y %I:%M %p PKT")

        return {
            "Product name": name,
            "Brand": brand,
            "Category": category,
            "Quantity": quantity,
            "Old price": old_price,
            "Discounted price": final_price,
            "Save amount": save_amount,
            "Store": STORE_NAME,
            "Out of stock": out_of_stock,
            "Product URL": product_url,
            "Timestamp": timestamp,
            "Image URL": img_url,
        }

    except Exception as e:
        print(f"   Error parsing ChaseUp product: {e}")
        return None


def start_parsing(items, workers=8):
    """Parse a list of ChaseUp product dicts."""
    results = []
    failed_urls = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parse_item, None, item): item for item in items}
        for future in tqdm(as_completed(futures), total=len(items), desc="Parsing ChaseUp"):
            parsed = future.result()
            if parsed:
                results.append(parsed)
            else:
                original = futures[future]
                failed_urls.append(original.get('name', 'unknown'))

    print(f"\n   Parsed {len(results)} ChaseUp products, {len(failed_urls)} failed.")
    return pd.DataFrame(results), failed_urls
