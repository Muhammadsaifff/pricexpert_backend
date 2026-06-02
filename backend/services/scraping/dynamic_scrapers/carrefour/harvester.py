import time
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# ---------------------------
# CONSTANTS
# ---------------------------
BASE_URL = "https://www.carrefour.pk"
STORE_ID = "mafpak"
LANG = "en"


def _get_selenium_driver():
    """Create a headless Chrome driver configured to avoid bot detection."""
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # Needs a real user agent
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    
    # Execute CDP command to further mask Selenium
    driver.execute_cdp_cmd('Network.setUserAgentOverride', {
        "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver


# ---------------------------
# RSC STREAM PARSER
# ---------------------------
def _extract_products_from_html(html_text):
    """
    Extract product data from Carrefour HTML page.
    """
    products = []
    pagination = {}

    try:
        # 1. Unescape the entire HTML document to reveal nested JSON strings
        unescaped = html_text.replace('\\"', '"').replace('\\\\', '\\')
        
        # We know from the debug log that the products are right after '"products":['
        # Let's extract the raw json string by finding the start and bracket counting
        prod_start = unescaped.find('"products":[')
        if prod_start != -1:
            bracket_start = unescaped.find('[', prod_start)
            depth = 0
            pos = bracket_start
            in_string = False
            escape_next = False
            
            for c in unescaped[bracket_start:]:
                if escape_next:
                    escape_next = False
                elif c == '\\':
                    escape_next = True
                elif c == '"':
                    in_string = not in_string
                elif not in_string:
                    if c == '[': depth += 1
                    elif c == ']': depth -= 1
                
                if not in_string and depth == 0:
                    break
                pos += 1
                
            try:
                arr_str = unescaped[bracket_start:pos+1]
                # Fix unicode escapes before parsing
                arr_str = arr_str.replace('\\u0026', '&')
                products = json.loads(arr_str)
            except Exception as e:
                pass
                
        # 2. Extract pagination similarly
        pag_start = unescaped.find('"pagination":{')
        if pag_start != -1:
            bracket_start = unescaped.find('{', pag_start)
            depth = 0
            pos = bracket_start
            in_string = False
            escape_next = False
            
            for c in unescaped[bracket_start:]:
                if escape_next:
                    escape_next = False
                elif c == '\\':
                    escape_next = True
                elif c == '"':
                    in_string = not in_string
                elif not in_string:
                    if c == '{': depth += 1
                    elif c == '}': depth -= 1
                
                if not in_string and depth == 0:
                    break
                pos += 1
                
            try:
                pag_str = unescaped[bracket_start:pos+1]
                pag_str = pag_str.replace('\\u0026', '&')
                pagination = json.loads(pag_str)
            except Exception:
                pass

    except Exception as e:
        print(f"      [ERROR] Exception during parsing: {e}")

    return products, pagination


# ---------------------------
# PRODUCT HARVESTING
# ---------------------------
def harvest_single_category(category):
    """
    Fetch all products from a Carrefour category page using Selenium.
    Extracts products from RSC stream in the page source.
    """
    cat_id = category.get('id', '')
    cat_name = category.get('name', 'Unknown')
    cat_url_path = category.get('url', '')
    all_products = []

    print(f"    Harvesting Carrefour: {cat_name}")

    driver = _get_selenium_driver()
    page = 0
    max_pages = 50

    try:
        while page < max_pages:
            # Construct the URL
            if cat_url_path.startswith('/c/'):
                full_url = f"{BASE_URL}/{STORE_ID}/{LANG}{cat_url_path}"
            elif cat_url_path.startswith('/n/c/'):
                full_url = f"{BASE_URL}/{STORE_ID}/{LANG}{cat_url_path}"
            else:
                full_url = f"{BASE_URL}/{STORE_ID}/{LANG}/c/{cat_id}"

            if page > 0:
                full_url += f"?currentPage={page}"

            # Visit the page
            driver.get(full_url)
            time.sleep(3)  # Wait for RSC payloads to load executing JS

            html = driver.page_source
            
            # Anti-bot check
            if len(html) < 500 and "<html>" in html:
                print(f"      [WARN] Hit bot protection on {cat_name} page {page}. Retrying after sleep...")
                time.sleep(5)
                driver.get(full_url)
                time.sleep(4)
                html = driver.page_source

            products, pagination = _extract_products_from_html(html)

            if not products:
                if page == 0:
                    print(f"      [WARN] No products found in HTML for {cat_name}")
                break

            # Enrich products with category info
            for p in products:
                p['_category_id'] = cat_id
                p['_category_name'] = cat_name
                p['_page'] = page

            all_products.extend(products)

            total_pages = pagination.get('totalPages', 1)
            total_results = pagination.get('totalResults', 0)
            print(f"       {cat_name} | Page {page + 1}/{total_pages}: {len(products)} products (total: {total_results})")

            # Check if we should keep paginating
            if page + 1 >= total_pages or len(products) == 0:
                break

            page += 1
            
    except Exception as e:
        print(f"      [ERROR] Error on {cat_name}: {e}")
    finally:
        driver.quit()

    print(f"   [SUCCESS] Finished {cat_name}: {len(all_products)} products")
    return all_products


def start_harvest(categories, workers=2):
    """
    Main entry point for Carrefour harvester. Uses Selenium, so keep workers low.
    """
    all_products = []

    print(f"    Harvesting {len(categories)} Carrefour categories with Selenium...")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(harvest_single_category, cat): cat
            for cat in categories
        }

        for future in as_completed(futures):
            cat = futures[future]
            try:
                products = future.result()
                all_products.extend(products)
            except Exception as e:
                print(f"   [ERROR] Harvester thread failed for {cat.get('name', '?')}: {e}")

    # Deduplicate by EAN/ID
    unique = {}
    for p in all_products:
        key = p.get('ean', p.get('id', ''))
        if key:
            unique[key] = p

    results = list(unique.values())
    print(f"\n    Harvest complete: {len(results)} unique Carrefour products")
    return results
