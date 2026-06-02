import requests
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# ---------------------------
# CONSTANTS
# ---------------------------
BASE_URL = "https://www.chaseupgrocery.com"
REST_ID = "55525"
REST_BR_ID = "56246"

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
    "app-name": "chaseup",
    "rest-id": REST_ID,
}


# ---------------------------
# SESSION SETUP (Selenium bootstrap)
# ---------------------------
def _get_session_cookies():
    """
    Visit ChaseUp website with Selenium to get valid session cookies.
    """
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--disable-images')
    options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    driver = webdriver.Chrome(options=options)
    cookies = {}

    try:
        driver.get(BASE_URL)
        time.sleep(3)

        # Handle location popup
        try:
            driver.execute_script("""
                var btns = document.querySelectorAll('button, div[role="button"], a');
                btns.forEach(b => {
                    var text = b.innerText || '';
                    if (text.includes('Karachi')) b.click();
                });
            """)
            time.sleep(1)
            driver.execute_script("""
                var btns = document.querySelectorAll('button, div[role="button"]');
                btns.forEach(b => {
                    var text = b.innerText || '';
                    if (text.includes('Select') || text.includes('Confirm') || text.includes('Continue')) b.click();
                });
            """)
            time.sleep(2)
        except Exception:
            pass

        for c in driver.get_cookies():
            cookies[c['name']] = c['value']

        if 'brId' not in cookies:
            cookies['brId'] = REST_BR_ID

    except Exception as e:
        print(f"   Warning: Session bootstrap error: {e}")
        cookies = {"brId": REST_BR_ID}
    finally:
        driver.quit()

    return cookies


def _get_api_session(cookies):
    """Create a requests session with the bootstrapped cookies."""
    session = requests.Session()
    session.headers.update(API_HEADERS)
    session.cookies.update(cookies)
    return session


# ---------------------------
# PRODUCT HARVESTING
# ---------------------------
def harvest_subsection(session, sub_section_id, section_name="Unknown", per_page=30):
    """
    Fetch all products from a given sub_section_id using pagination.
    """
    all_products = []
    page = 1
    max_pages = 100

    while page <= max_pages:
        url = f"{BASE_URL}/api/items-by-subsection"
        params = {
            "restId": REST_ID,
            "rest_brId": REST_BR_ID,
            "sub_section_id": str(sub_section_id),
            "delivery_type": "0",
            "source": "",
            "brand_name": "",
            "min_price": "0",
            "max_price": "",
            "sort_by": "",
            "sort": "",
            "page_no": str(page),
            "per_page": str(per_page),
            "start": str((page - 1) * per_page),
            "limit": str(per_page),
        }

        try:
            r = session.get(url, params=params, timeout=15)
            data = r.json()
            products = data.get('data', [])

            if not products:
                break

            for p in products:
                p['_category'] = section_name
                p['_sub_section_id'] = sub_section_id

            all_products.extend(products)
            print(f"      [ChaseUp] {section_name} | Page {page}: {len(products)} products")

            if len(products) < per_page:
                break

            page += 1
            time.sleep(0.3)

        except Exception as e:
            print(f"      [ChaseUp] Error on page {page}: {e}")
            break

    return all_products


def start_harvest(categories, workers=3):
    """
    Main entry point for ChaseUp harvester.
    """
    print("   [ChaseUp] Bootstrapping session...")
    cookies = _get_session_cookies()
    session = _get_api_session(cookies)

    # Normalize input format
    normalized = []
    for cat in categories:
        if isinstance(cat, dict):
            normalized.append({
                "sub_section_id": cat.get("sub_section_id", cat.get("id")),
                "name": cat.get("name", cat.get("section_name", "Unknown")),
                "menu": cat.get("menu", ""),
            })
        elif isinstance(cat, (int, str)):
            normalized.append({
                "sub_section_id": int(cat),
                "name": f"Section_{cat}",
                "menu": "",
            })

    print(f"   [ChaseUp] Harvesting {len(normalized)} sub-sections...")
    all_products = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for cat in normalized:
            future = executor.submit(
                harvest_subsection,
                session,
                cat["sub_section_id"],
                cat.get("name", "Unknown"),
            )
            futures[future] = cat

        for future in as_completed(futures):
            cat = futures[future]
            try:
                products = future.result()
                all_products.extend(products)
                print(f"   [ChaseUp] Done: {cat['name']}: {len(products)} products")
            except Exception as e:
                print(f"   [ChaseUp] {cat['name']} failed: {e}")

    # Deduplicate by product ID
    unique = {p.get('id', p.get('name')): p for p in all_products}
    results = list(unique.values())

    print(f"\n   [ChaseUp] Harvest complete: {len(results)} unique products")

    def _build_url(p):
        slug = p.get('slug', '')
        pid = str(p.get('id', ''))
        if slug and pid and pid not in slug:
            return f"{BASE_URL}/product/{slug}-{pid}"
        elif slug:
            return f"{BASE_URL}/product/{slug}"
        elif pid:
            return f"{BASE_URL}/product/{pid}"
        return ''

    return [{**p, "url": _build_url(p)} for p in results]
