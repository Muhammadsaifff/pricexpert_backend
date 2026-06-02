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
    Parse a Carrefour product from the RSC JSON data.
    All data is already available in the item dict.
    """
    try:
        # 1️⃣ Product Name
        name = item.get('name', '')
        if not name:
            return None

        # 2️⃣ Brand
        brand_info = item.get('brand', {})
        brand = 'Generic'
        if isinstance(brand_info, dict):
            brand = brand_info.get('name', 'Generic')
        elif isinstance(brand_info, str):
            brand = brand_info
        if not brand:
            brand = 'Generic'

        # 3️⃣ Category
        cat_list = item.get('category', [])
        category = item.get('_category_name', 'Unknown')
        if cat_list and isinstance(cat_list, list):
            # Use the deepest category level
            deepest = max(cat_list, key=lambda x: x.get('level', 0))
            category = deepest.get('name', category)

        # 4️⃣ Quantity
        qty_matches = RE_QUANTITY.finditer(name)
        results = [m.group(0) for m in qty_matches]
        quantity = results[-1] if results else None

        # 5️⃣ Price
        price_info = item.get('price', {})
        old_price = 0
        final_price = 0

        # Helper to extract max old price and min final price from a price block
        def extract_float(val):
            if isinstance(val, dict):
                return float(val.get('value', 0) or val.get('gross', 0) or 0)
            try:
                return float(val or 0)
            except (ValueError, TypeError):
                return 0.0

        def parse_price_block(block):
            op, fp = 0, 0
            if isinstance(block, dict):
                p_val = extract_float(block.get('price', 0))
                op = extract_float(block.get('originalPrice', p_val))
                if op == 0:
                    op = p_val
                fp = p_val
                
                # Check explicit discount dict inside the block
                discount = block.get('discount', {})
                if discount and isinstance(discount, dict):
                    disc_price = extract_float(discount.get('price', 0))
                    if disc_price > 0:
                        fp = disc_price
                
                # basePrice represents the per-unit price (e.g., per liter or per kg), NOT the undiscounted price.
                # Do not use it for discount calculation.
                bp = extract_float(block.get('basePrice', 0))
                # if bp > op: op = bp # REMOVED: Incorrectly setting per-liter price as old price
            elif isinstance(block, (int, float, str)):
                op = extract_float(block)
                fp = extract_float(block)
            return op, fp

        old_price, final_price = parse_price_block(price_info)

        # Check in 'offers' array because some discounts are contextual offers
        offers = item.get('offers', [])
        if isinstance(offers, list):
            for offer in offers:
                if isinstance(offer, dict):
                    offer_price_block = offer.get('price', offer)
                    o_op, o_fp = parse_price_block(offer_price_block)
                    if o_fp > 0 and o_fp < final_price:
                        final_price = o_fp
                    if o_op > old_price:
                        old_price = o_op

        # Guarantee old_price >= final_price
        if old_price < final_price:
            old_price = final_price

        save_amount = max(old_price - final_price, 0)

        # Convert to int if whole numbers
        if old_price == int(old_price):
            old_price = int(old_price)
        if final_price == int(final_price):
            final_price = int(final_price)
        if save_amount == int(save_amount):
            save_amount = int(save_amount)

        # 6️⃣ Stock Status
        out_of_stock = "No"

        # Direct root-level flags
        if str(item.get('isOutOfStock', '')).lower() == 'true':
            out_of_stock = "Yes"
        elif str(item.get('sellable', '')).lower() == 'false':
            out_of_stock = "Yes"

        # Check stock dict
        stock_info = item.get('stock')
        if isinstance(stock_info, dict):
            status = stock_info.get('stockLevelStatus')
            stock_level = stock_info.get('stockLevel')

            if status == 'outOfStock':
                out_of_stock = "Yes"
            elif status and status != 'inStock' and status != 'lowStock':
                out_of_stock = "Yes"

            if stock_level is not None:
                try:
                    if float(stock_level) <= 0:
                        out_of_stock = "Yes"
                except (ValueError, TypeError):
                    pass

        # Check availability dict
        availability = item.get('availability')
        if out_of_stock == "No" and isinstance(availability, dict):
            avail_flag = availability.get('isAvailable')
            if avail_flag is False or str(avail_flag).lower() == 'false':
                out_of_stock = "Yes"

        # 7️⃣ Image URL
        links = item.get('links', {})
        img_url = ''
        if isinstance(links, dict):
            images = links.get('images', [])
            if images:
                img = images[0] if isinstance(images, list) else images
                if isinstance(img, dict):
                    img_url = img.get('href', '')
                elif isinstance(img, str):
                    img_url = img

        # 8️⃣ Product URL
        product_url = ''
        if isinstance(links, dict):
            product_link = links.get('productUrl', {})
            if isinstance(product_link, dict):
                href = product_link.get('href', '')
                if href:
                    product_url = f"https://www.carrefour.pk{href}" if href.startswith('/') else href
            elif isinstance(product_link, str):
                product_url = f"https://www.carrefour.pk{product_link}" if product_link.startswith('/') else product_link

        if not product_url:
            ean = item.get('ean', '')
            product_id = item.get('id', '')
            if ean:
                product_url = f"https://www.carrefour.pk/mafpak/en/p/{ean}"

        # 9️⃣ Timestamp (PKT)
        timestamp = datetime.now(timezone(timedelta(hours=5))).strftime("%A, %B %d, %Y %I:%M %p PKT")

        return {
            "Product name": name,
            "Brand": brand,
            "Category": category,
            "Quantity": quantity,
            "Old price": old_price,
            "Discounted price": final_price,
            "Save amount": save_amount,
            "Store": "Carrefour",
            "Out of stock": out_of_stock,
            "Product URL": product_url,
            "Timestamp": timestamp,
            "Image URL": img_url,
        }

    except Exception as e:
        print(f"   [ERROR] Error parsing Carrefour product: {e}")
        return None


# ---------------------------
# RUNNER
# ---------------------------
def start_parsing(items, workers=8):
    """
    Parse a list of Carrefour product dicts (already containing full data from harvester).

    items: List of dicts from harvester
    workers: Number of concurrent threads
    """
    results = []
    failed_urls = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parse_item, None, item): item for item in items}

        for future in tqdm(as_completed(futures), total=len(items), desc="[INFO] Parsing Carrefour"):
            parsed = future.result()
            if parsed:
                results.append(parsed)
            else:
                original = futures[future]
                failed_urls.append(original.get('name', 'unknown'))

    print(f"\n   [SUCCESS] Parsed {len(results)} Carrefour products, {len(failed_urls)} failed.")
    return pd.DataFrame(results), failed_urls
