import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://www.naheed.pk/',
}

# ── Thread-local sessions (one per thread, avoids contention) ─────────────────
_local = threading.local()


def _get_session() -> requests.Session:
    """Return a thread-local session, creating it on first use."""
    if not hasattr(_local, 'session'):
        session = requests.Session()
        # Aggressive retry only on true server errors; backoff_factor=0 = no sleep
        retries = Retry(
            total=2,
            backoff_factor=0,          # was 1 → up-to-7 s wasted per retry
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(
            pool_connections=1,        # 1 per thread is enough with thread-local sessions
            pool_maxsize=1,
            max_retries=retries,
        )
        session.mount('https://', adapter)
        session.headers.update(HEADERS)
        _local.session = session
    return _local.session


# ── Parsing helpers ───────────────────────────────────────────────────────────

# Pre-compiled selector strings (evaluated once, not per-call)
_PRODUCT_SELECTORS = (
    'a.product-item-link',                # most common on Magento 2
    '.product-item-info a[href*=".html"]',
)
_SUBCAT_SELECTORS = (
    '.category-item a[href]',
    '.categories-grid a[href]',
    '.sidebar a[href]',
    '.block-content a[href]',
)


def _parse_product_links(soup: BeautifulSoup) -> list[str]:
    for selector in _PRODUCT_SELECTORS:
        links = soup.select(selector)
        if links:
            return [a['href'] for a in links if a.get('href')]
    return []


def _parse_subcategory_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    seen = set()
    results = []
    prefix = 'https://www.naheed.pk/'
    for selector in _SUBCAT_SELECTORS:
        for a in soup.select(selector):
            href = a.get('href', '')
            if (
                href
                and href.startswith(prefix)
                and href != base_url
                and '?' not in href
                and href not in seen
            ):
                seen.add(href)
                results.append(href)
        if results:          # stop at first selector that yields something
            break
    return results


def _extract_category_name(soup: BeautifulSoup, url: str) -> str:
    # Breadcrumbs
    for a in reversed(soup.select('.breadcrumbs li a, .breadcrumb li a')):
        text = a.get_text(strip=True)
        if text.lower() not in ('home', ''):
            return text
    # H1
    h1 = soup.find('h1', id='page-title-heading')
    if h1:
        span = h1.find('span')
        if span:
            return span.get_text(strip=True)
    # URL slug fallback
    return url.rstrip('/').split('/')[-1].replace('-', ' ').title()


# ── Core page fetcher ─────────────────────────────────────────────────────────

def _fetch(url: str, timeout: int = 15):
    """GET url, return (status_code, BeautifulSoup | None)."""
    try:
        resp = _get_session().get(url, timeout=timeout)
        if resp.status_code == 200:
            # lxml is 3-5× faster than html.parser; fall back if not installed
            try:
                soup = BeautifulSoup(resp.content, 'lxml')
            except Exception:
                soup = BeautifulSoup(resp.content, 'html.parser')
            return resp.status_code, soup
        return resp.status_code, None
    except requests.exceptions.Timeout:
        logger.warning(f"  Timeout: {url}")
        return -1, None
    except requests.exceptions.RequestException as exc:
        logger.warning(f"  Request error: {url} → {exc}")
        return -2, None


# ── Category crawler (iterative, no recursion) ────────────────────────────────

def crawl_category(category_url: str) -> list[dict]:
    """
    Crawl one category (and any discovered sub-categories) returning
    [{"url": ..., "category": ...}, ...].
    Uses an explicit queue instead of recursion so stack depth is O(1).
    """
    results: list[dict] = []
    seen_product_urls: set[str] = set()

    # Queue of (url, display_name_or_None) to process
    queue: deque[tuple[str, str | None]] = deque([(category_url, None)])
    visited_cat_urls: set[str] = {category_url}

    while queue:
        cat_url, display_name = queue.popleft()
        slug = cat_url.rstrip('/').split('/')[-1]
        page = 1
        consecutive_empty = 0
        cat_display = display_name  # filled on first successful page

        logger.info(f"🏁 Harvesting: {slug} ({cat_url})")

        while True:
            target = f"{cat_url}?p={page}" if page > 1 else cat_url
            status, soup = _fetch(target)

            if status == 404:
                logger.warning(f"   404: {target}")
                break

            if soup is None:
                consecutive_empty += 1
                if consecutive_empty >= 2:   # was 3 — fail faster
                    break
                page += 1
                continue

            # Resolve display name once
            if cat_display is None:
                cat_display = _extract_category_name(soup, cat_url)

            product_hrefs = _parse_product_links(soup)

            if not product_hrefs:
                # On the first page, maybe this is a hub with sub-categories
                if page == 1:
                    subcats = _parse_subcategory_links(soup, cat_url)
                    for sub in subcats:
                        if sub not in visited_cat_urls:
                            visited_cat_urls.add(sub)
                            queue.append((sub, None))
                    if subcats:
                        logger.info(f"   Queued {len(subcats)} sub-categories from {slug}")
                        break   # nothing to page through here

                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
                page += 1
                continue

            added = 0
            for href in product_hrefs:
                if href not in seen_product_urls:
                    seen_product_urls.add(href)
                    results.append({"url": href, "category": cat_display})
                    added += 1

            if added == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
            else:
                consecutive_empty = 0

            page += 1
            if page > 200:
                logger.warning(f"   Hit 200-page limit for {slug}")
                break

        logger.info(f"✅ Done {slug}: {len(results)} URLs so far")

    return results


# ── Public entry point ────────────────────────────────────────────────────────

def start_harvest(categories: list[str], workers: int = 20) -> list[dict]:
    """
    Crawl all category URLs in parallel.

    Bump `workers` to 20-30 for I/O-bound scraping — the old default of 5
    left 75-85 % of available concurrency unused.
    """
    all_results: list[dict] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(crawl_category, url): url for url in categories}
        for future in as_completed(futures):
            cat_url = futures[future]
            try:
                result = future.result()
                all_results.extend(result)
                logger.info(f"Completed: {cat_url} ({len(result)} products)")
            except Exception as exc:
                logger.error(f"❌ Harvester failed for {cat_url}: {exc}")

    # Deduplicate on URL (last write wins for category label)
    unique = list({item['url']: item for item in all_results}.values())
    logger.info(f"🏁 Total unique product URLs: {len(unique)}")
    return unique