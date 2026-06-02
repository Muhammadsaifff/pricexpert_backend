import re
import pandas as pd
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ---------------------------
# CONFIG
# ---------------------------
UNITS = r'kg|gm|g|ml|ltr|liter|liters|litres|l|lb|pound|pounds|oz|pcs|pieces|piece|pack|packs|pk|pair|pairs|sachet|ply|metres|meters|feet|inches|s'
CONTAINERS = r'Bottle|Can|Jar|Pouch|Bowl|Bag|Carton|Box|Packet'
RE_QUANTITY = re.compile(
    r'(?i)(\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?(?:\s*[xX]\s*)?\s*[-]?\s*(?:' +
    UNITS + r')\b(?:\s+(?:' + CONTAINERS + r'))?)|(?:\bEconomy\s+Pack\b|\bTea\s+Bag\b)'
)


# ---------------------------
# PRODUCT PARSING
# ---------------------------
def parse_item(session, item):
    """
    Parse an Imtiaz product from the API response data.
    The item dict already contains ALL product data from the harvester
    (no need to visit individual product pages).
    """
    try:
        # 1. Product Name
        name = item.get('name', '')
        if not name:
            return None

        # 2. Brand — from API field, fallback to search_tags[0]
        brand = item.get('brand_name', '')
        if not brand or brand == '0':
            search_tags = (item.get('search_tags') or '')
            tags = [t.strip() for t in search_tags.split(',') if t.strip()]
            # Imtiaz search_tags: "Brand,ParentCategory,SubCategory,SubSubCategory"
            if tags and tags[0] != name:
                brand = tags[0]
            else:
                brand = 'Generic'

        # 3. Category — use _category (from harvester navigation) as primary,
        #    enriched by search_tags subcategory when available
        category = item.get('_category', 'Unknown')
        search_tags = (item.get('search_tags') or '')
        tags = [t.strip() for t in search_tags.split(',') if t.strip()]
        # Imtiaz tags: [Brand, ParentCategory, SubCategory, SubSubCategory]
        # Use SubCategory (tags[2]) when available for more specific categorization
        if len(tags) >= 3 and tags[2]:
            category = tags[2]

        # 4. Quantity
        qty_matches = RE_QUANTITY.finditer(name)
        results = [m.group(0) for m in qty_matches]
        quantity = results[-1] if results else None

        # 5. Price — Blink API semantics:
        #    price = original/list price
        #    discount_price = the FINAL discounted selling price (NOT a discount amount)
        #    base_price = rarely used, alternative original price
        #    dish_branch_status = branch-specific overrides
        branch = item.get('dish_branch_status') or {}
        
        # Branch-level prices take priority
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
        # discount_price IS the final selling price
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

        # Convert to int if whole numbers
        if old_price and old_price == int(old_price):
            old_price = int(old_price)
        if final_price and final_price == int(final_price):
            final_price = int(final_price)
        if save_amount and save_amount == int(save_amount):
            save_amount = int(save_amount)

        # 6. Stock Status — check branch status first
        out_of_stock = "No"
        if branch and branch.get('status', 1) == 0:
            out_of_stock = "Yes"
        else:
            availability = item.get('availability', 1)
            stock_val = item.get('stock')
            if availability == 0 or (stock_val is not None and stock_val == 0):
                out_of_stock = "Yes"

        # 7. Image URL
        img_url = item.get('img_url', '')

        # 8. Product URL
        slug = item.get('slug')
        item_id = item.get('id')
        name = item.get('name', '')
        product_url = item.get('url', '')
        
        if not product_url or 'None' in product_url:
            if not slug and name:
                slug = re.sub(r'[^a-zA-Z0-9]+', '-', name).strip('-').lower()
                
            if slug and item_id:
                actual_slug = f"{slug}-{item_id}" if not slug.endswith(f"-{item_id}") else slug
            elif slug:
                actual_slug = slug
            else:
                actual_slug = str(item_id)
            product_url = f"https://shop.imtiaz.com.pk/product/{actual_slug}"

        # 9. Timestamp (PKT)
        timestamp = datetime.now(timezone(timedelta(hours=5))).strftime("%A, %B %d, %Y %I:%M %p PKT")

        return {
            "Product name": name,
            "Brand": brand,
            "Category": category,
            "Quantity": quantity,
            "Old price": old_price,
            "Discounted price": final_price,
            "Save amount": save_amount,
            "Store": "Imtiaz",
            "Out of stock": out_of_stock,
            "Product URL": product_url,
            "Timestamp": timestamp,
            "Image URL": img_url,
        }

    except Exception as e:
        print(f"   Error parsing Imtiaz product: {e}")
        return None


# ---------------------------
# RUNNER
# ---------------------------
def start_parsing(items, workers=8):
    """
    Parse a list of Imtiaz product dicts (already containing full data from API).
    """
    results = []
    failed_urls = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parse_item, None, item): item for item in items}

        for future in tqdm(as_completed(futures), total=len(items), desc="Parsing Imtiaz"):
            parsed = future.result()
            if parsed:
                results.append(parsed)
            else:
                original = futures[future]
                failed_urls.append(original.get('url', original.get('name', 'unknown')))

    print(f"\n   Parsed {len(results)} Imtiaz products, {len(failed_urls)} failed.")
    return pd.DataFrame(results), failed_urls
