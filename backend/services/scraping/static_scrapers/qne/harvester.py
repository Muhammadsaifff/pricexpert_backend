import requests
import time

BASE_DOMAIN = "https://qne.com.pk"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

def get_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session

def start_harvest(category_list):
    session = get_session()
    harvested_data = []

    print(f"[QnE] Starting API JSON Harvest on {len(category_list)} categories...")

    for cat_url in category_list:
        page = 1
        cat_slug = cat_url.split('/')[-1]
        products_seen_in_category = set()

        while True:
            # Shopify API endpoint for collection products
            target_url = f"{BASE_DOMAIN}/collections/{cat_slug}/products.json?page={page}&limit=250"

            try:
                response = session.get(target_url, timeout=15)
                if response.status_code != 200:
                    print(f"   [QnE] Status {response.status_code} at {cat_slug} page {page}. Next category.")
                    break

                data = response.json()
                products = data.get('products', [])
                
                if not products:
                    print(f"   [QnE] No new products found on Page {page}. Stopping.")
                    break

                new_products = 0

                for product in products:
                    product_handle = product.get('handle')
                    if not product_handle:
                        continue
                        
                    full_url = f"{BASE_DOMAIN}/products/{product_handle}"
                    
                    if full_url not in products_seen_in_category:
                        # Pass the ENTIRE JSON product context to the parser
                        harvested_data.append({
                            "url": full_url,
                            "category": cat_slug,
                            "product_json": product  # <--- MAGIC HAPPENS HERE
                        })
                        products_seen_in_category.add(full_url)
                        new_products += 1

                print(f"   [QnE] {cat_slug} | Page {page} | Found {new_products} NEW products")

                if new_products == 0:
                    break

                page += 1
                time.sleep(0.5)

            except Exception as e:
                print(f"   [QnE] Error on {target_url}: {e}")
                break

    print(f"\n[QnE] Harvesting Complete! Collected {len(harvested_data)} URLs.")
    return harvested_data