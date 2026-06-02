import re
import json
import time
import random
import requests
import logging
import pandas as pd
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

logger = logging.getLogger(__name__)

# ---------------------------
# CONFIG
# ---------------------------
UNITS = r'kg|gm|g|ml|ltr|liter|liters|litres|l|lb|pound|pounds|oz|pcs|pieces|piece|pack|packs|pk|pair|pairs|sachet|ply|metres|meters|feet|inches|s'
CONTAINERS = r'Bottle|Can|Jar|Pouch|Bowl|Bag'
RE_QUANTITY = re.compile(
    r'(?i)(\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?(?:\s*[xX]\s*)?\s*[-]?\s*(?:' +
    UNITS + r')\b(?:\s+(?:' + CONTAINERS + r'))?)|(?:\bEconomy\s+Pack\b|\bTea\s+Bag\b)'
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.naheed.pk/'
}

# ---------------------------
# NETWORK SESSION
# ---------------------------
def get_session(workers=10):
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(pool_connections=workers + 5, pool_maxsize=workers + 5, max_retries=retries)
    session.mount('https://', adapter)
    session.headers.update(HEADERS)
    return session


def get_with_retry(session, url, max_attempts=5):
    for attempt in range(max_attempts):
        try:
            res = session.get(url, timeout=20)
            if res.status_code == 429:
                wait = 2 ** attempt + random.random()
                logger.warning(f"[{url}] 429 Too Many Requests. Sleeping {wait:.1f}s")
                time.sleep(wait)
                continue
            if res.status_code == 404:
                logger.warning(f"[{url}] 404 Not Found — skipping")
                return None
            res.raise_for_status()
            return res
        except requests.exceptions.RequestException as e:
            wait = 2 ** attempt + random.random()
            logger.warning(f"[{url}] Request failed (attempt {attempt+1}/{max_attempts}): {e}. Retrying in {wait:.1f}s")
            time.sleep(wait)
    return None


# ---------------------------
# PRODUCT PARSING
# ---------------------------
def parse_item(session, item):
    url = item['url']
    category_fallback = item.get('category', 'Unknown')

    try:
        # Reduced delay — retry logic handles rate limiting
        time.sleep(random.uniform(0.3, 1.0))

        res = get_with_retry(session, url)
        if not res:
            return None

        soup = BeautifulSoup(res.text, 'html.parser')

        # 1️⃣ Product Name
        name = None
        h1 = soup.find('h1', class_='page-title')
        if h1:
            span = h1.find('span')
            name = span.text.strip() if span else h1.text.strip()
        if not name:
            meta = soup.find('meta', property="og:title")
            if meta:
                name = meta.get('content')
        if not name:
            logger.debug(f"No product name found: {url}")
            return None

        # 2️⃣ Brand
        brand = None
        # Primary: brand-name div
        b_div = soup.find(class_='brand-name')
        if b_div:
            a = b_div.find('a')
            brand = a.text.strip() if a else b_div.text.strip()

        # Fallback: product attribute table
        if not brand:
            attr_rows = soup.select('.additional-attributes-wrapper tr, .product-attributes tr, table.data tr')
            for row in attr_rows:
                th = row.find('th')
                td = row.find('td')
                if th and td and 'brand' in th.get_text(strip=True).lower():
                    brand = td.get_text(strip=True)
                    break

        # Fallback: meta tag
        if not brand:
            brand_meta = soup.find('meta', attrs={'itemprop': 'brand'})
            if brand_meta:
                brand = brand_meta.get('content', '')

        # 3️⃣ Quantity
        qty_matches = RE_QUANTITY.finditer(name)
        quantity = None
        results = [m.group(0) for m in qty_matches]
        if results:
            quantity = results[-1]

        # 4️⃣ Price
        old_price, discounted_price, save_amount = None, None, 0
        final = soup.find(attrs={"data-price-type": "finalPrice"})
        old = soup.find(attrs={"data-price-type": "oldPrice"})

        if final:
            try:
                discounted_price = float(final['data-price-amount'])
            except (ValueError, KeyError):
                pass
        if old:
            try:
                old_price = float(old['data-price-amount'])
            except (ValueError, KeyError):
                pass

        # Fallback: price-box text
        if discounted_price is None:
            box = soup.find(class_='price-box')
            if box:
                nums = re.findall(r'\d{1,3}(?:,\d{3})*(?:\.\d+)?', box.text)
                if nums:
                    discounted_price = float(nums[0].replace(',', ''))

        # Fallback: meta itemprop price
        if discounted_price is None:
            price_meta = soup.find('meta', attrs={'itemprop': 'price'})
            if price_meta:
                try:
                    discounted_price = float(price_meta.get('content', '0'))
                except ValueError:
                    pass

        if old_price is None:
            old_price = discounted_price
        if old_price and discounted_price:
            save_amount = max(0, old_price - discounted_price)

        # Convert to int if whole numbers
        if old_price is not None and isinstance(old_price, float) and old_price.is_integer():
            old_price = int(old_price)
        if discounted_price is not None and isinstance(discounted_price, float) and discounted_price.is_integer():
            discounted_price = int(discounted_price)
        if isinstance(save_amount, float) and save_amount.is_integer():
            save_amount = int(save_amount)

        # 5️⃣ Stock
        stock = "No"
        stock_unavail = soup.find(class_='stock unavailable')
        if stock_unavail:
            stock = "Yes"
        elif soup.find(class_='stock available'):
            stock = "No"
        elif "Out of stock" in soup.get_text() and "In stock" not in soup.get_text():
            stock = "Yes"

        # 6️⃣ Image
        # Primary: MagicZoom Plus (New Naheed Gallery)
        img_url = None
        main_img_zoom = soup.select_one('a.MagicZoom')
        if main_img_zoom:
            img_url = main_img_zoom.get('href')

        if not img_url:
            thumb_zoom = soup.select_one('a.mt-thumb-switcher')
            if thumb_zoom:
                img_url = thumb_zoom.get('href')

        # Secondary: parse Magento's x-magento-init JSON (Standard fallback)
        if not img_url:
            for script in soup.find_all('script', type='text/x-magento-init'):
                txt = script.string or ''
                if '"full"' in txt and 'media/catalog' in txt:
                    try:
                        data = json.loads(txt)
                        # Navigate into nested structure to find gallery data
                        for key, val in data.items():
                            if isinstance(val, dict):
                                for subkey, subval in val.items():
                                    if isinstance(subval, dict) and 'data' in subval:
                                        for item in subval['data']:
                                            if isinstance(item, dict) and item.get('full'):
                                                img_url = item['full']
                                                break
                                    if img_url:
                                        break
                            if img_url:
                                break
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        pass
                if img_url:
                    break

        # Fallback: scan any script block for media/catalog image URLs (covers other Magento JSON patterns)
        if not img_url:
            for script in soup.find_all('script'):
                txt = script.string or ''
                if 'media/catalog/product' in txt:
                    matches = re.findall(r'https://[^"\'\\]+media/catalog/product/[^"\'\\]+\.(?:jpg|jpeg|png|webp)', txt)
                    if matches:
                        img_url = matches[0]
                        break

        # Fallback: fotorama / gallery img tags (data-src for lazy-loaded images)
        if not img_url:
            gallery_img = soup.select_one('.fotorama__img, .gallery-placeholder img, img.gallery-placeholder__image')
            if gallery_img:
                img_url = gallery_img.get('data-src') or gallery_img.get('src')

        # Last resort: og:image — only if it's not a placeholder
        if not img_url:
            img_tag = soup.find('meta', property="og:image")
            if img_tag:
                candidate = img_tag.get('content', '')
                if candidate and 'placeholder' not in candidate:
                    img_url = candidate

        # 7️⃣ Timestamp (PKT)
        timestamp = datetime.now(timezone(timedelta(hours=5))).strftime("%A, %B %d, %Y %I:%M %p PKT")

        # 8️⃣ Return dict
        return {
            "Product name": name,
            "Brand": brand,
            "Category": category_fallback,
            "Quantity": quantity,
            "Old price": old_price,
            "Discounted price": discounted_price,
            "Save amount": save_amount,
            "Store": "Naheed",
            "Out of stock": stock,
            "Product URL": url,
            "Timestamp": timestamp,
            "Image URL": img_url
        }

    except Exception as e:
        logger.error(f"Error parsing {url}: {e}", exc_info=True)
        return None


# ---------------------------
# RUNNER
# ---------------------------
def start_parsing(items, workers=8):
    """
    items: List of dicts, each must have 'url' and optional 'category'
    workers: Number of concurrent threads
    """
    results = []
    failed_urls = []
    session = get_session(workers)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(parse_item, session, item): item for item in items}

        for future in tqdm(as_completed(futures), total=len(items), desc="⚡ Parsing"):
            item = future.result()
            if item:
                results.append(item)
            else:
                failed_urls.append(futures[future]['url'])

    logger.info(f"[SUCCESS] Parsed {len(results)} products successfully, {len(failed_urls)} failed.")
    print(f"\n[SUCCESS] Parsed {len(results)} products successfully, {len(failed_urls)} failed.")
    return pd.DataFrame(results), failed_urls
