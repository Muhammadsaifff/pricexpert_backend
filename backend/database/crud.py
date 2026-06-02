from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_, and_, func
from datetime import datetime
import logging
from backend.database import models
from uuid import uuid4
import re
from fuzzywuzzy import fuzz
from functools import lru_cache
from datetime import timedelta
from backend.services.quantity_normalizer import (
    normalize_quantity,
    split_search_query,
    quantities_match,
)

# Setup logging
logger = logging.getLogger(__name__)

# ==================== GROCERY NORMALIZATION DICTIONARY ====================

# Common grocery item mappings (variations to standard names)
GROCERY_NORMALIZATION = {
    # Dairy products
    'milk': 'milk',
    'milt': 'milk',
    'milkk': 'milk',
    'doodh': 'milk',
    'fresh milk': 'milk',
    'pasteurized milk': 'milk',

    'yogurt': 'yogurt',
    'yoghurt': 'yogurt',
    'dahi': 'yogurt',
    'curd': 'yogurt',

    'butter': 'butter',
    'buter': 'butter',
    'butterr': 'butter',
    'makhan': 'butter',

    'cheese': 'cheese',
    'cheeze': 'cheese',
    'cheddar': 'cheese',
    'mozzarella': 'cheese',

    'cream': 'cream',
    'fresh cream': 'cream',
    'whipping cream': 'cream',

    # Beverages
    'water': 'water',
    'mineral water': 'water',
    'drinking water': 'water',

    'juice': 'juice',
    'fresh juice': 'juice',
    'fruit juice': 'juice',

    'soft drink': 'soft drink',
    'soda': 'soft drink',
    'coke': 'soft drink',
    'pepsi': 'soft drink',
    'soda pop': 'soft drink',

    'tea': 'tea',
    'chai': 'tea',
    'green tea': 'tea',
    'black tea': 'tea',

    'coffee': 'coffee',
    'instant coffee': 'coffee',
    'ground coffee': 'coffee',

    # Fruits
    'apple': 'apple',
    'appple': 'apple',
    'apples': 'apple',
    'red apple': 'apple',
    'green apple': 'apple',

    'banana': 'banana',
    'bananna': 'banana',
    'bananas': 'banana',
    'kela': 'banana',

    'orange': 'orange',
    'oranges': 'orange',
    'santara': 'orange',
    'kinnow': 'orange',

    'grape': 'grape',
    'grapes': 'grape',
    'angoor': 'grape',

    'mango': 'mango',
    'mangoes': 'mango',
    'aam': 'mango',

    'strawberry': 'strawberry',
    'strawberries': 'strawberry',

    'pineapple': 'pineapple',
    'ananas': 'pineapple',

    'watermelon': 'watermelon',
    'tarbooz': 'watermelon',

    # Vegetables
    'tomato': 'tomato',
    'tamatar': 'tomato',
    'tomatos': 'tomato',
    'tomatoes': 'tomato',

    'onion': 'onion',
    'pyaz': 'onion',
    'onions': 'onion',

    'potato': 'potato',
    'aloo': 'potato',
    'potatoes': 'potato',

    'carrot': 'carrot',
    'gajar': 'carrot',
    'carrots': 'carrot',

    'cucumber': 'cucumber',
    'kheera': 'cucumber',

    'spinach': 'spinach',
    'palak': 'spinach',

    'cabbage': 'cabbage',
    'band gobi': 'cabbage',
    'patta gobi': 'cabbage',

    'cauliflower': 'cauliflower',
    'phool gobi': 'cauliflower',

    'peas': 'peas',
    'matar': 'peas',

    'beans': 'beans',
    'sem': 'beans',
    'green beans': 'beans',

    'broccoli': 'broccoli',
    'hari gobi': 'broccoli',

    'capsicum': 'capsicum',
    'bell pepper': 'capsicum',
    'shimla mirch': 'capsicum',

    # Grains & Staples
    'rice': 'rice',
    'chawal': 'rice',
    'basmati rice': 'rice',
    'brown rice': 'rice',

    'wheat': 'wheat',
    'gehun': 'wheat',
    'wheat flour': 'wheat',
    'atta': 'wheat',

    'flour': 'flour',
    'maida': 'flour',
    'all purpose flour': 'flour',

    'bread': 'bread',
    'roti': 'bread',
    'brown bread': 'bread',
    'white bread': 'bread',

    'pasta': 'pasta',
    'noodles': 'pasta',
    'spaghetti': 'pasta',

    'cereal': 'cereal',
    'corn flakes': 'cereal',
    'breakfast cereal': 'cereal',

    'oats': 'oats',
    'oatmeal': 'oats',
    'rolled oats': 'oats',

    # Proteins
    'chicken': 'chicken',
    'chiken': 'chicken',
    'murgh': 'chicken',
    'broiler chicken': 'chicken',

    'beef': 'beef',
    'gai ka gosht': 'beef',

    'mutton': 'mutton',
    'lamb': 'mutton',
    'bakra gosht': 'mutton',

    'fish': 'fish',
    'machli': 'fish',
    'fresh fish': 'fish',

    'egg': 'egg',
    'eggs': 'egg',
    'ande': 'egg',
    'anda': 'egg',

    # Snacks & Packaged Foods
    'chips': 'chips',
    'crisps': 'chips',
    'potato chips': 'chips',

    'biscuit': 'biscuit',
    'biscuits': 'biscuit',
    'cookie': 'biscuit',
    'cookies': 'biscuit',

    'chocolate': 'chocolate',
    'choclate': 'chocolate',
    'chocolates': 'chocolate',

    'cake': 'cake',
    'cupcake': 'cake',
    'sponge cake': 'cake',

    'ice cream': 'ice cream',
    'icecream': 'ice cream',
    'frozen dessert': 'ice cream',

    # Condiments & Spices
    'salt': 'salt',
    'table salt': 'salt',
    'sendha namak': 'salt',

    'sugar': 'sugar',
    'cheeni': 'sugar',
    'white sugar': 'sugar',
    'brown sugar': 'sugar',

    'oil': 'oil',
    'cooking oil': 'oil',
    'vegetable oil': 'oil',
    'olive oil': 'olive oil',

    'spice': 'spice',
    'masala': 'spice',
    'garam masala': 'spice',

    'ketchup': 'ketchup',
    'tomato ketchup': 'ketchup',

    'sauce': 'sauce',
    'soy sauce': 'soy sauce',
    'chili sauce': 'chili sauce',

    # Cleaning & Household
    'detergent': 'detergent',
    'washing powder': 'detergent',
    'laundry detergent': 'detergent',

    'soap': 'soap',
    'hand soap': 'soap',
    'bath soap': 'soap',
    'sabun': 'soap',

    'shampoo': 'shampoo',
    'sgampoo': 'shampoo',
    'sampoo': 'shampoo',
    'shampo': 'shampoo',

    'conditioner': 'conditioner',
    'conditoner': 'conditioner',

    'toothpaste': 'toothpaste',
    'tooth paste': 'toothpaste',
    'dant manjan': 'toothpaste',

    # Personal Care
    'lotion': 'lotion',
    'body lotion': 'lotion',
    'moisturizer': 'lotion',

    'cream': 'cream',
    'face cream': 'cream',
    'cold cream': 'cream',

    # Baby Products
    'diaper': 'diaper',
    'diapers': 'diaper',
    'baby diaper': 'diaper',

    'baby food': 'baby food',
    'infant formula': 'baby food',

    # Pet Food
    'pet food': 'pet food',
    'dog food': 'pet food',
    'cat food': 'pet food',
}

# Category mappings
CATEGORY_NORMALIZATION = {
    'dairy': ['milk', 'yogurt', 'butter', 'cheese', 'cream'],
    'fruits': ['apple', 'banana', 'orange', 'grape', 'mango', 'strawberry', 'pineapple', 'watermelon'],
    'vegetables': ['tomato', 'onion', 'potato', 'carrot', 'cucumber', 'spinach', 'cabbage', 'cauliflower'],
    'grains': ['rice', 'wheat', 'flour', 'bread', 'pasta', 'cereal', 'oats'],
    'proteins': ['chicken', 'beef', 'mutton', 'fish', 'egg'],
    'beverages': ['water', 'juice', 'soft drink', 'tea', 'coffee'],
    'snacks': ['chips', 'biscuit', 'chocolate', 'cake', 'ice cream'],
    'condiments': ['salt', 'sugar', 'oil', 'spice', 'ketchup', 'sauce'],
    'household': ['detergent', 'soap', 'shampoo', 'conditioner', 'toothpaste'],
    'personal_care': ['lotion', 'cream'],
}

# ==================== GROCERY SEARCH HELPERS ====================

def normalize_grocery_term(search_term: str) -> tuple[str, str | None]:
    """
    Normalize grocery search term to standard form

    Returns: (normalized_term, detected_category)
    """
    search_lower = search_term.lower().strip()

    # Check for exact match in normalization dictionary
    if search_lower in GROCERY_NORMALIZATION:
        normalized = GROCERY_NORMALIZATION[search_lower]
        logger.info(f"🛒 Normalized grocery term: '{search_term}' -> '{normalized}'")

        # Detect category
        category = None
        for cat, items in CATEGORY_NORMALIZATION.items():
            if normalized in items:
                category = cat
                break

        return normalized, category

    # Check for partial matches (e.g., "fresh milk" -> "milk")
    for variation, standard in GROCERY_NORMALIZATION.items():
        if variation in search_lower:
            normalized = standard
            logger.info(f"🛒 Normalized partial match: '{search_term}' -> '{normalized}'")

            # Detect category
            category = None
            for cat, items in CATEGORY_NORMALIZATION.items():
                if normalized in items:
                    category = cat
                    break

            return normalized, category

    # Check for common prefixes/suffixes
    common_foods = ['fresh', 'organic', 'natural', 'pure', 'premium', 'family pack']
    for food in common_foods:
        if search_lower.startswith(food + ' '):
            remaining = search_lower[len(food)+1:]
            if remaining in GROCERY_NORMALIZATION:
                normalized = GROCERY_NORMALIZATION[remaining]
                return normalized, None

    return search_term, None

def get_related_grocery_items(term: str) -> list[str]:
    """Get related grocery items for a given term"""
    related = []

    # Find category
    category = None
    for cat, items in CATEGORY_NORMALIZATION.items():
        if term in items:
            category = cat
            break

    # Return all items in same category
    if category:
        related = CATEGORY_NORMALIZATION[category]

    return [item for item in related if item != term]

def expand_search_with_related_terms(search_term: str) -> list[str]:
    """Expand search term to include related grocery items ONLY if no brand is detected"""
    normalized, category = normalize_grocery_term(search_term)

    search_terms = [normalized]

    # Add original term for fuzzy matching
    if search_term != normalized:
        search_terms.append(search_term)

    # IMPORTANT: Only expand to related items if the search is GENERIC (no brand name)
    # If user says "slice juice", don't expand to all juices
    # But if user says just "juice", it's OK to expand
    has_brand_indicator = _detect_brand_indicator(search_term)
    
    if category and not has_brand_indicator:
        # Only expand if NO brand is mentioned
        related = get_related_grocery_items(normalized)
        search_terms.extend(related[:3])  # Add top 3 related items
        logger.info(f"🔍 Expanding '{search_term}' to related items: {search_terms}")
    elif has_brand_indicator:
        logger.info(f"🏷️  Brand detected in '{search_term}' - NOT expanding search to avoid showing all alternatives")

    # Remove duplicates while preserving order
    seen = set()
    unique_terms = []
    for term in search_terms:
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)

    return unique_terms

def _detect_brand_indicator(search_term: str) -> bool:
    """
    Detect if search contains a brand name (not just a generic category)
    
    Returns True if brand is likely mentioned:
    - "slice juice" → True (Slice is a brand)
    - "juice" → False (generic)
    - "olpers milk" → True (Olpers is a brand)
    - "milk" → False (generic)
    """
    search_lower = search_term.lower().strip()
    
    # Known brand names (non-exhaustive, but covers common ones)
    known_brands = [
        # Juice brands
        'slice', 'mango', 'orange', 'citrus', 'pulpy', 'lemonada', 'sherbet',
        'minute maid', 'tropica', 'sunfresh', 'appy', 'real',
        
        # Milk brands
        'olpers', 'engro', 'haleeb', 'nestle', 'fortified', 'purewhite',
        'rainbow', 'crown', 'hazara',
        
        # Other beverage brands
        'coca cola', 'coke', 'pepsi', 'sprite', 'fanta', 'seven up',
        '7up', 'miranda', 'dew', 'mountain dew', 'sting', 'lahore',
        
        # Water brands
        'nestle pure life', 'aquafina', 'eva', 'sama', 'aqua', 'pure life',
        
        # Tea/Coffee brands
        'lipton', 'tetley', 'nescafe', 'nespresso', 'cafe coffee',
        
        # Common product brands
        'dawat', 'sunsilk', 'head & shoulders', 'dove', 'lux', 'safeguard'
    ]
    
    # Count how many tokens match known brands
    search_tokens = search_lower.split()
    brand_matches = sum(1 for token in search_tokens if token in known_brands)
    
    # If any token is a known brand, consider it brand-specific
    if brand_matches > 0:
        return True
    
    # Also check if search has multiple words (likely brand + category)
    # e.g., "slice juice" (2 words), "olpers milk" (2 words)
    if len(search_tokens) > 1:
        # Get the category
        normalized, category = normalize_grocery_term(search_term)
        
        # If normalized to a category AND has multiple words,
        # it's likely "Brand Category" format
        if category:
            # Check if last word is the category and other words are brand
            last_word = search_tokens[-1]
            if last_word in normalized or category in last_word.lower():
                return True  # "brand category" pattern detected
    
    return False

def _extract_brand_from_search(search_term: str) -> str | None:
    """
    Extract brand name from search query
    
    Examples:
    - "slice juice" → "slice"
    - "olpers 500ml" → "olpers"
    - "juice" → None
    """
    if not _detect_brand_indicator(search_term):
        return None
    
    search_lower = search_term.lower().strip()
    search_tokens = search_lower.split()
    
    # Known brands list from _detect_brand_indicator
    known_brands = [
        'slice', 'mango', 'orange', 'citrus', 'pulpy', 'lemonada', 'sherbet',
        'minute maid', 'tropica', 'sunfresh', 'appy', 'real',
        'olpers', 'engro', 'haleeb', 'nestle', 'fortified', 'purewhite',
        'rainbow', 'crown', 'hazara',
        'coca cola', 'coke', 'pepsi', 'sprite', 'fanta', 'seven up',
        '7up', 'miranda', 'dew', 'mountain dew', 'sting', 'lahore',
        'nestle pure life', 'aquafina', 'eva', 'sama', 'aqua', 'pure life',
        'lipton', 'tetley', 'nescafe', 'nespresso', 'cafe coffee',
        'dawat', 'sunsilk', 'head & shoulders', 'dove', 'lux', 'safeguard'
    ]
    
    # Look for known brands in the search term
    for brand in known_brands:
        if brand in search_lower:
            return brand
    
    # If no known brand found but brand is detected, 
    # return first token (assuming "Brand Category" format)
    if len(search_tokens) > 1:
        return search_tokens[0]  # Return first word as likely brand
    
    return None

# ==================== USER CRUD OPERATIONS ====================

def create_user(db: Session, name: str, email: str, password: str = None, language: str = "en", budget: float = 0.0):
    """Create a new user"""
    try:
        # Check if user already exists
        existing_user = db.query(models.User).filter(models.User.email == email).first()
        if existing_user:
            raise ValueError("User with this email already exists")

        db_user = models.User(
            name=name,
            email=email,
            password=password,
            language=language,
            budget=budget
        )

        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    except IntegrityError:
        db.rollback()
        raise ValueError("User with this email already exists")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create user {email}: {e}")
        raise

def get_user_by_email(db: Session, email: str):
    """Get user by email"""
    return db.query(models.User).filter(models.User.email == email).first()

def get_user_by_id(db: Session, user_id: int):
    """Get user by ID"""
    return db.query(models.User).filter(models.User.id == user_id).first()

def update_user(db: Session, user_id: int, **kwargs):
    """Update user information"""
    try:
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)

        db.commit()
        db.refresh(user)
        return user
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update user {user_id}: {e}")
        raise

def delete_user(db: Session, user_id: int):
    """Delete user"""
    try:
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            raise ValueError("User not found")

        db.delete(user)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete user {user_id}: {e}")
        raise

# ==================== SEARCH QUERY CRUD OPERATIONS ====================

def create_search_query(db: Session, user_id: int, query_text: str):
    """Create a search query record"""
    try:
        db_query = models.SearchQuery(
            user_id=user_id,
            query_text=query_text
        )

        db.add(db_query)
        db.commit()
        db.refresh(db_query)
        return db_query
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create search query for user {user_id}: {e}")
        raise

def get_user_search_queries(db: Session, user_id: int, limit: int = 10):
    """Get recent search queries for a user"""
    return db.query(models.SearchQuery).filter(
        models.SearchQuery.user_id == user_id
    ).order_by(models.SearchQuery.created_at.desc()).limit(limit).all()

# ==================== SCRAPING JOB CRUD OPERATIONS ====================

def create_scraping_job(
    db: Session,
    store: str = None,
    store_type: str = "Unknown",
    use_test_config: bool = True,
    status: str = "queued",
    stage: str = "queued",
    urls_count: int = 0,
    products_count: int = 0,
    message: str = None,
    error: str = None,
    started_at=None,
    completed_at=None,
    job_data: dict = None
):
    """Create a new scraping job record"""
    try:
        if job_data:
            store = job_data.get('store', store)
            store_type = job_data.get('store_type', store_type)
            use_test_config = job_data.get('use_test_config', use_test_config)
            status = job_data.get('status', status)
            stage = job_data.get('stage', stage)
            urls_count = job_data.get('urls_count', urls_count)
            products_count = job_data.get('products_count', products_count)
            message = job_data.get('message', message)
            error = job_data.get('error', error)
            started_at = job_data.get('started_at', started_at)
            completed_at = job_data.get('completed_at', completed_at)
            job_id = job_data.get('id')
        else:
            job_id = None

        if not job_id:
            job_id = str(uuid4())

        db_job = models.ScrapingJob(
            id=job_id,
            store=store,
            store_type=store_type,
            status=status,
            stage=stage,
            use_test_config=use_test_config,
            urls_count=urls_count,
            products_count=products_count,
            message=message,
            error=error,
            started_at=started_at,
            completed_at=completed_at
        )

        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        return db_job
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create scraping job: {e}")
        raise

def get_scraping_job(db: Session, job_id: str):
    """Get a scraping job by ID"""
    return db.query(models.ScrapingJob).filter(models.ScrapingJob.id == job_id).first()

def update_scraping_job_status(
    db: Session,
    job_id: str,
    status: str = None,
    stage: str = None,
    message: str = None,
    error: str = None,
    urls_count: int = None,
    products_count: int = None,
    start_job: bool = False,
    complete_job: bool = False
):
    """Update the status and stage of a scraping job"""
    job = get_scraping_job(db, job_id)
    if not job:
        return None

    if status:
        job.status = status
    if stage:
        job.stage = stage
    if message:
        job.message = message
    if error:
        job.error = error
    if urls_count is not None:
        job.urls_count = urls_count
    if products_count is not None:
        job.products_count = products_count

    if start_job and not job.started_at:
        job.started_at = datetime.utcnow()

    if complete_job:
        job.completed_at = datetime.utcnow()
        if not status:
            job.status = "completed"

    db.commit()
    db.refresh(job)
    return job

def get_active_jobs(db: Session, store: str = None):
    """Get all active (queued/running) scraping jobs"""
    query = db.query(models.ScrapingJob).filter(
        models.ScrapingJob.status.in_(["queued", "running"])
    )

    if store:
        query = query.filter(models.ScrapingJob.store == store)

    return query.order_by(models.ScrapingJob.created_at).all()

def list_scraping_jobs(db: Session, limit: int = 100):
    """List recent scraping jobs"""
    return db.query(models.ScrapingJob).order_by(
        models.ScrapingJob.created_at.desc()
    ).limit(limit).all()

# ==================== PRODUCT CRUD OPERATIONS ====================

def save_scraped_data(db: Session, scraped_data, store: str, job_id: str = None):
    """Save or update scraped product data"""
    saved_count, updated_count = 0, 0

    if isinstance(scraped_data, dict):
        scraped_data = [scraped_data]
    elif hasattr(scraped_data, "to_dict"):
        scraped_data = scraped_data.to_dict('records')

    for raw_product in scraped_data:
        db_data = map_raw_to_db(raw_product, store, job_id)

        if not db_data.get('product_url'):
            logger.warning(f"Skipping product without URL: {db_data.get('product_name')}")
            continue

        try:
            product = models.Product(**db_data)
            db.add(product)
            db.commit()
            db.refresh(product)
            saved_count += 1

        except IntegrityError:
            db.rollback()
            existing = db.query(models.Product).filter(
                models.Product.product_url == db_data['product_url'],
                models.Product.store == db_data['store']
            ).first()
            if existing:
                updated = False
                for key, value in db_data.items():
                    if key in ["product_url", "store"]:
                        continue
                    if getattr(existing, key) != value:
                        setattr(existing, key, value)
                        updated = True
                if updated:
                    db.commit()
                    db.refresh(existing)
                    updated_count += 1

        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save product: {e}")

    return {"saved_count": saved_count, "updated_count": updated_count, "total": len(scraped_data)}

def map_raw_to_db(product_data: dict, store: str, job_id: str = None):
    """Map raw scraped data to database model fields"""

    def safe_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def get_field(primary_key: str, fallback_key: str = None):
        value = product_data.get(primary_key)
        if value is None and fallback_key:
            value = product_data.get(fallback_key)
        return value

    raw_timestamp = get_field("Timestamp", "timestamp")
    if isinstance(raw_timestamp, str):
        try:
            scraped_at = datetime.strptime(raw_timestamp, "%A, %B %d, %Y %I:%M %p PKT")
        except (ValueError, TypeError):
            scraped_at = datetime.utcnow()
    elif isinstance(raw_timestamp, datetime):
        scraped_at = raw_timestamp
    else:
        scraped_at = datetime.utcnow()

    raw_out_of_stock = product_data.get('out_of_stock', 'no')
    is_out_of_stock = True if raw_out_of_stock.lower() == 'yes' else False

    # Normalize quantity to a canonical form (e.g. '1L' → '1000ml', '1kg' → '1000g')
    raw_qty = get_field('Quantity', 'quantity') or ""
    normalized_qty = normalize_quantity(raw_qty) or raw_qty

    return {
        'product_name': get_field('Product name', 'product_name') or "Unknown Product",
        'brand': get_field('Brand', 'brand') or "Unknown Brand",
        'category': get_field('Category', 'category') or "Misc",
        'quantity': normalized_qty,
        'old_price': safe_float(get_field('Old price', 'old_price')),
        'discounted_price': safe_float(get_field('Discounted price', 'discounted_price')),
        'save_amount': safe_float(get_field('Save amount', 'save_amount')),
        'store': store or get_field('Store', 'store') or "Unknown Store",
        "out_of_stock": is_out_of_stock,
        'product_url': get_field('Product URL', 'product_url'),
        'image_url': get_field('Image URL', 'image_url') or "",
        'job_id': job_id,
        'scraped_at': scraped_at
    }

# ==================== ADVANCED GROCERY SEARCH ====================

def search_grocery_products(db: Session, search_term: str, limit: int = 500,
                           min_score: int = 30, prioritize_in_stock: bool = True,
                           expand_search: bool = True):
    """
    Specialized search for grocery products with normalization and expansion

    Features:
    - Normalizes grocery terms (milt -> milk, sgampoo -> shampoo)
    - Detects product categories
    - Expands search to related items
    - Handles typos and variations
    - Prioritizes grocery-relevant results
    """
    if not search_term or not search_term.strip():
        return []

    original_term = search_term
    search_term = search_term.strip().lower()

    logger.info(f"🛒 Grocery search for: '{search_term}'")

    # Split quantity out of the query so it doesn't pollute token matching
    base_search_term, required_qty = split_search_query(search_term)
    if required_qty:
        logger.info(f"📏 Quantity detected: '{required_qty}' — searching for '{base_search_term}'")
    search_term = base_search_term or search_term

    # Step 1: Normalize the search term (grocery typo / Urdu alias handling)
    normalized_term, detected_category = normalize_grocery_term(search_term)

    if normalized_term != search_term:
        logger.info(f"📝 Normalized: '{search_term}' -> '{normalized_term}'")
        search_term = normalized_term

    # Step 2: Expand search terms if enabled
    search_terms = [search_term]
    if expand_search:
        search_terms = expand_search_with_related_terms(search_term)
        if len(search_terms) > 1:
            logger.info(f"🔍 Expanded search terms: {search_terms}")

    # Step 3: Search with multiple terms and combine results
    all_results = {}

    for term in search_terms:
        results = _perform_grocery_search(
            db, term, limit * 2, min_score, prioritize_in_stock, detected_category
        )

        # Add results with weights (first term gets higher weight)
        weight = 1.0 if term == search_term else 0.7
        for product in results:
            if product.id not in all_results:
                all_results[product.id] = {'product': product, 'score': 0}
            all_results[product.id]['score'] += weight * getattr(product, '_search_score', 50)

    # Step 4: Sort by combined score
    sorted_results = sorted(
        all_results.values(),
        key=lambda x: x['score'],
        reverse=True
    )

    candidates = [item['product'] for item in sorted_results]

    # Step 5: Quantity post-filter — keep only products with matching quantity.
    # Falls back to unfiltered results when none match (graceful degradation).
    if required_qty:
        qty_matched = [p for p in candidates if quantities_match(p.quantity, required_qty)]
        logger.info(f"📏 Quantity filter '{required_qty}': {len(qty_matched)}/{len(candidates)} matched")
        if qty_matched:
            final_results = qty_matched[:limit]
            logger.info(f"✅ Found {len(final_results)} grocery products for '{original_term}'")
            return final_results

    final_results = candidates[:limit]
    logger.info(f"✅ Found {len(final_results)} grocery products for '{original_term}'")
    return final_results

def _perform_grocery_search(db: Session, search_term: str, limit: int,
                           min_score: int, prioritize_in_stock: bool,
                           category_filter: str = None):
    """
    Internal function to perform actual grocery search
    """
    search_tokens = search_term.split()

    # Build query with category boost if available
    query = db.query(models.Product)

    if category_filter:
        # Boost products in detected category
        query = query.filter(
            or_(
                models.Product.category.ilike(f"%{category_filter}%"),
                *[models.Product.product_name.ilike(f"%{token}%") for token in search_tokens]
            )
        )
    else:
        # Standard search
        conditions = []
        for token in search_tokens:
            token_pattern = f"%{token}%"
            conditions.append(or_(
                models.Product.product_name.ilike(token_pattern),
                models.Product.brand.ilike(token_pattern),
                models.Product.category.ilike(token_pattern)
            ))

        if conditions:
            query = query.filter(or_(*conditions))

    candidates = query.limit(limit * 3).all()
    
    # BRAND FILTERING: If search mentions a brand, filter to only that brand
    # This prevents "slice juice" from returning all juices
    brand_filter = _extract_brand_from_search(search_term)
    if brand_filter:
        logger.info(f"🏷️  Brand filter detected: '{brand_filter}' - strict matching enabled")
        candidates = [p for p in candidates if _check_word_boundary((p.brand or '').lower(), brand_filter.lower())]
        if not candidates:
            logger.warning(f"⚠️  No products found for brand '{brand_filter}', falling back to all results")
            candidates = query.limit(limit * 3).all()

    # Score candidates
    scored_products = []

    for product in candidates:
        score = _calculate_grocery_relevance_score(
            product, search_term, search_tokens, prioritize_in_stock, category_filter, brand_filter
        )

        # Attach score to product for later use
        setattr(product, '_search_score', score)

        if score >= min_score:
            scored_products.append((score, product))

    # Fuzzy fallback if needed
    if len(scored_products) < limit:
        additional_candidates = db.query(models.Product).limit(limit * 5).all()

        for product in additional_candidates:
            if product not in [p for _, p in scored_products]:
                score = _calculate_fuzzy_score(product, search_term, search_tokens)
                if score >= min_score:
                    setattr(product, '_search_score', score)
                    scored_products.append((score, product))

    scored_products.sort(key=lambda x: (-x[0]))
    return [product for score, product in scored_products[:limit]]

def _calculate_grocery_relevance_score(product, search_term: str, search_tokens: list,
                                      prioritize_in_stock: bool, category_filter: str = None) -> int:
    """
    Calculate relevance score specifically for grocery items
    """
    name = (product.product_name or '').lower()
    brand = (product.brand or '').lower()
    category = (product.category or '').lower()

    # Field weights for grocery items
    WEIGHT_NAME = 5
    WEIGHT_BRAND = 2
    WEIGHT_CATEGORY = 3  # Category is more important for groceries

    total_score = 0

    # 1. Category match (important for groceries)
    if category_filter and category_filter in category:
        total_score += 200

    # 2. Exact matches
    if name == search_term:
        total_score += 300 * WEIGHT_NAME

    # 3. Token matching with grocery-specific logic
    token_matches = 0
    for token in search_tokens:
        if token in name:
            token_matches += 1
            total_score += 50 * WEIGHT_NAME
        if token in brand:
            token_matches += 0.5
            total_score += 30 * WEIGHT_BRAND
        if token in category:
            token_matches += 0.8
            total_score += 40 * WEIGHT_CATEGORY

    # Token coverage bonus
    if search_tokens:
        coverage = token_matches / len(search_tokens)
        total_score += coverage * 150

    # 4. Word boundary matches (avoid partial matches)
    for token in search_tokens:
        if _check_word_boundary(name, token):
            total_score += 40 * WEIGHT_NAME

    # 5. Stock status (important for groceries)
    if prioritize_in_stock and not getattr(product, 'out_of_stock', False):
        total_score += 60

    # 6. Price competitiveness for groceries
    if product.discounted_price and product.old_price and product.old_price > 0:
        discount_percent = (product.old_price - product.discounted_price) / product.old_price
        if discount_percent > 0.2:  # >20% discount
            total_score += 25

    # 7. Brand recognition bonus (common grocery brands)
    popular_brands = ['nestle', 'unilever', 'procter', 'p&g', 'kissan', 'mitchells', 'shezan']
    for popular_brand in popular_brands:
        if popular_brand in brand:
            total_score += 30
            break

    return min(1000, total_score)

def _check_word_boundary(text: str, token: str) -> bool:
    """Check if token appears as a whole word"""
    pattern = r'\b' + re.escape(token) + r'\b'
    return bool(re.search(pattern, text))

def _calculate_fuzzy_score(product, search_term: str, search_tokens: list) -> int:
    """Fallback fuzzy matching for edge cases"""
    name = (product.product_name or '').lower()
    brand = (product.brand or '').lower()
    category = (product.category or '').lower()

    scores = []

    # Token set ratio
    scores.append(fuzz.token_set_ratio(search_term, name))
    scores.append(fuzz.token_set_ratio(search_term, brand) * 0.6)
    scores.append(fuzz.token_set_ratio(search_term, category) * 0.4)

    # Partial ratio
    scores.append(fuzz.partial_ratio(search_term, name) * 0.8)

    # Individual token matching
    if len(search_tokens) > 1:
        token_scores = []
        for token in search_tokens:
            token_scores.append(fuzz.partial_ratio(token, name))
        if token_scores:
            avg_token_score = sum(token_scores) / len(token_scores)
            scores.append(avg_token_score * 0.6)

    max_score = max(scores) if scores else 0
    return int(max_score * 0.6)

# ==================== MAIN SEARCH FUNCTION (BACKWARD COMPATIBLE) ====================

def search_products(db: Session, search_term: str, limit: int = 500,
                   min_score: int = 30, prioritize_in_stock: bool = True,
                   use_grocery_normalization: bool = True):
    """
    Main search function - automatically uses grocery normalization for food items
    """
    if not search_term or not search_term.strip():
        return []

    # Check if this looks like a grocery search
    grocery_keywords = ['milk', 'bread', 'rice', 'chicken', 'egg', 'butter',
                        'cheese', 'yogurt', 'fruit', 'vegetable', 'juice',
                        'water', 'tea', 'coffee', 'shampoo', 'soap', 'oil',
                        'sugar', 'salt', 'flour', 'spice', 'sgampoo', 'milt']

    is_likely_grocery = any(keyword in search_term.lower() for keyword in grocery_keywords)

    if use_grocery_normalization and is_likely_grocery:
        # Use specialized grocery search
        return search_grocery_products(db, search_term, limit, min_score, prioritize_in_stock)
    else:
        # Use standard search for non-grocery items
        return search_products_standard(db, search_term, limit, min_score, prioritize_in_stock)

def search_products_standard(db: Session, search_term: str, limit: int = 500,
                            min_score: int = 30, prioritize_in_stock: bool = True):
    """
    Standard product search (backward compatible).

    When the query contains a quantity token (e.g. '1L', '500g') it is
    stripped for the DB ILIKE search and then used to post-filter results so
    that equivalent quantities ('1L', '1000ml', '1 Litre') all match.
    """
    if not search_term or not search_term.strip():
        return []

    # Split quantity from base term (e.g. 'Pepsi 1L' → ('pepsi', '1000ml'))
    base_term, required_qty = split_search_query(search_term.strip())
    search_term_lower = base_term.lower() if base_term else search_term.strip().lower()
    search_tokens = search_term_lower.split()

    # SQL filtering on base term only
    conditions = []
    for token in search_tokens:
        token_pattern = f"%{token}%"
        conditions.append(or_(
            models.Product.product_name.ilike(token_pattern),
            models.Product.brand.ilike(token_pattern),
            models.Product.category.ilike(token_pattern)
        ))

    initial_candidates = db.query(models.Product).filter(
        and_(*conditions) if conditions else True
    ).limit(limit * 3).all()

    if len(initial_candidates) < limit and len(search_tokens) > 1:
        initial_candidates = db.query(models.Product).filter(
            or_(*conditions)
        ).limit(limit * 3).all()

    # Score candidates
    scored_products = []
    for product in initial_candidates:
        score = _calculate_advanced_relevance_score(
            product, search_term_lower, search_tokens, prioritize_in_stock
        )
        if score >= min_score:
            scored_products.append((score, product))

    # Fuzzy fallback
    if len(scored_products) < limit:
        additional_candidates = db.query(models.Product).limit(limit * 5).all()
        for product in additional_candidates:
            if product not in [p for _, p in scored_products]:
                score = _calculate_fuzzy_score(product, search_term_lower, search_tokens)
                if score >= min_score:
                    scored_products.append((score, product))

    scored_products.sort(key=lambda x: (-x[0]))
    results = [product for _score, product in scored_products[:limit * 2]]

    # Quantity post-filter: prefer products whose quantity matches the search qty.
    # If nothing matches (e.g. old un-normalised rows), fall back to all results.
    if required_qty:
        qty_matched = [p for p in results if quantities_match(p.quantity, required_qty)]
        if qty_matched:
            return qty_matched[:limit]

    return results[:limit]

def _calculate_grocery_relevance_score(product, search_term: str, search_tokens: list,
                                      prioritize_in_stock: bool, category_filter: str = None,
                                      brand_filter: str = None) -> int:
    """
    Calculate relevance score specifically for grocery items
    
    Args:
        brand_filter: If provided, only products with this brand get high scores
    """
    name = (product.product_name or '').lower()
    brand = (product.brand or '').lower()
    category = (product.category or '').lower()

    # Field weights for grocery items
    WEIGHT_NAME = 5
    WEIGHT_BRAND = 2
    WEIGHT_CATEGORY = 3  # Category is more important for groceries

    total_score = 0
    
    # BRAND FILTERING: If brand filter specified, heavily boost matching brands
    if brand_filter:
        brand_filter_lower = brand_filter.lower()
        if _check_word_boundary(brand, brand_filter_lower):
            total_score += 500  # High boost for brand match
        else:
            total_score -= 200  # Penalize non-matching brands

    # 1. Category match (important for groceries)
    if category_filter and category_filter in category:
        total_score += 200

    # 2. Exact matches
    if name == search_term:
        total_score += 300 * WEIGHT_NAME
    # Prefix match
    if name.startswith(search_term):
        total_score += 150 * WEIGHT_NAME
    elif brand.startswith(search_term):
        total_score += 100 * WEIGHT_BRAND

    # Token matches
    token_matches = 0
    token_match_quality = 0

    for token in search_tokens:
        token_len = len(token)

        if _check_word_boundary(name, token):
            token_matches += 1
            token_match_quality += min(50, token_len * 5)
            total_score += 40 * WEIGHT_NAME

        if _check_word_boundary(brand, token):
            token_matches += 0.8
            total_score += 30 * WEIGHT_BRAND

        if _check_word_boundary(category, token):
            token_matches += 0.6
            total_score += 20 * WEIGHT_CATEGORY

    if search_tokens:
        coverage = min(1.0, token_matches / len(search_tokens))
        total_score += coverage * 200

    total_score += min(100, token_match_quality)

    # Contains matches
    if search_term in name:
        total_score += 30 * WEIGHT_NAME
    if search_term in brand:
        total_score += 20 * WEIGHT_BRAND
    if search_term in category:
        total_score += 10 * WEIGHT_CATEGORY

    # Stock boost
    if prioritize_in_stock and not getattr(product, 'out_of_stock', False):
        total_score += 50

    # Discount boost
    if product.discounted_price and product.old_price and product.old_price > 0:
        discount_percent = (product.old_price - product.discounted_price) / product.old_price
        if discount_percent > 0.3:
            total_score += 30
        elif discount_percent > 0.1:
            total_score += 15

    return min(1000, total_score)

# ==================== PRODUCT QUERIES ====================

def get_products(db: Session, skip: int = 0, limit: int = 100, store: str = None):
    """Return products with optional store filter"""
    try:
        query = db.query(models.Product)
        if store:
            query = query.filter(models.Product.store.ilike(f"%{store}%"))

        query = query.order_by(models.Product.scraped_at.desc())

        if skip:
            query = query.offset(skip)
        if limit:
            query = query.limit(limit)

        return query.all()
    except Exception as e:
        logger.error(f"Failed to fetch products: {e}")
        return []

def get_random_products(db: Session, limit: int = 10):
    """Return random products"""
    try:
        query = db.query(models.Product).order_by(func.random()).limit(limit)
        return query.all()
    except Exception as e:
        logger.error(f"Failed to fetch random products: {e}")
        return []

def get_product_by_id(db: Session, product_id: int):
    """Get a single product by ID"""
    return db.query(models.Product).filter(models.Product.id == product_id).first()

def get_products_by_store(db: Session, store: str, limit: int = 100):
    """Get products from a specific store"""
    return db.query(models.Product).filter(
        models.Product.store.ilike(f"%{store}%")
    ).limit(limit).all()

def get_products_by_brand(db: Session, brand: str, limit: int = 100):
    """Get products by brand"""
    return db.query(models.Product).filter(
        models.Product.brand.ilike(f"%{brand}%")
    ).limit(limit).all()

def get_products_by_category(db: Session, category: str, limit: int = 100):
    """Get products by category"""
    return db.query(models.Product).filter(
        models.Product.category.ilike(f"%{category}%")
    ).limit(limit).all()

# ==================== DEALS AND DISCOUNTS ====================

def get_discounted_products(db: Session, limit: int = 20, store: str = None, category: str = None):
    """Get products that have discounts"""
    try:
        query = db.query(models.Product).filter(
            models.Product.old_price.isnot(None),
            models.Product.discounted_price.isnot(None),
            models.Product.old_price > models.Product.discounted_price
        )

        if store:
            query = query.filter(models.Product.store.ilike(f"%{store}%"))
        if category:
            query = query.filter(models.Product.category.ilike(f"%{category}%"))

        query = query.order_by(
            (models.Product.old_price - models.Product.discounted_price).desc()
        )

        return query.limit(limit).all()
    except Exception as e:
        logger.error(f"Failed to get discounted products: {e}")
        return []

def get_top_discounts(db: Session, limit: int = 20, min_discount_percent: float = 20):
    """Get products with highest discount percentages"""
    try:
        query = db.query(models.Product).filter(
            models.Product.old_price.isnot(None),
            models.Product.discounted_price.isnot(None),
            models.Product.old_price > 0,
            models.Product.discounted_price > 0,
            ((models.Product.old_price - models.Product.discounted_price) / models.Product.old_price * 100) >= min_discount_percent
        )

        query = query.order_by(
            ((models.Product.old_price - models.Product.discounted_price) / models.Product.old_price).desc()
        )

        return query.limit(limit).all()
    except Exception as e:
        logger.error(f"Failed to get top discounts: {e}")
        return []

# ==================== SIMILAR PRODUCTS ====================

def get_similar_products(db: Session, product_id: int, limit: int = 10):
    """Find similar products based on category, brand, and price range"""
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        return []

    similar_query = db.query(models.Product).filter(
        models.Product.category == product.category,
        models.Product.id != product_id
    )

    if similar_query.count() < limit:
        similar_query = similar_query.union(
            db.query(models.Product).filter(
                models.Product.brand == product.brand,
                models.Product.id != product_id
            )
        )

    price_field = product.discounted_price if product.discounted_price else product.old_price
    if price_field and price_field > 0:
        price_range = price_field * 0.3
        similar_query = similar_query.filter(
            or_(
                models.Product.discounted_price.between(price_field - price_range, price_field + price_range),
                models.Product.old_price.between(price_field - price_range, price_field + price_range)
            )
        )

    return similar_query.limit(limit).all()

# ==================== STATISTICS AND ANALYTICS ====================

def get_product_count(db: Session, store: str = None):
    """Get total number of products"""
    query = db.query(models.Product)
    if store:
        query = query.filter(models.Product.store.ilike(f"%{store}%"))
    return query.count()

def get_unique_stores(db: Session):
    """Get list of unique stores"""
    stores = db.query(models.Product.store).distinct().all()
    return [store[0] for store in stores if store[0]]

def get_unique_categories(db: Session, limit: int = 50):
    """Get list of unique categories"""
    categories = db.query(models.Product.category).distinct().limit(limit).all()
    return [category[0] for category in categories if category[0]]

def get_price_range(db: Session, store: str = None):
    """Get min and max prices for products"""
    query = db.query(models.Product)
    if store:
        query = query.filter(models.Product.store.ilike(f"%{store}%"))

    min_price = query.filter(models.Product.discounted_price > 0).order_by(models.Product.discounted_price).first()
    max_price = query.filter(models.Product.discounted_price > 0).order_by(models.Product.discounted_price.desc()).first()

    return {
        'min': min_price.discounted_price if min_price else 0,
        'max': max_price.discounted_price if max_price else 0
    }

# ==================== SEARCH CACHE (OPTIONAL) ====================

class SearchCache:
    """Simple TTL cache for search results"""
    def __init__(self, ttl_seconds: int = 300):
        self.cache = {}
        self.ttl = timedelta(seconds=ttl_seconds)

    def get(self, key: str):
        if key in self.cache:
            result, timestamp = self.cache[key]
            if datetime.utcnow() - timestamp < self.ttl:
                return result
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value):
        self.cache[key] = (value, datetime.utcnow())

    def clear(self):
        self.cache.clear()

_search_cache = SearchCache(ttl_seconds=60)

def search_products_with_cache(db: Session, search_term: str, **kwargs):
    """Cached version of search_products"""
    cache_key = f"{search_term}_{kwargs.get('limit', 500)}_{kwargs.get('prioritize_in_stock', True)}_{kwargs.get('min_score', 30)}"

    cached_result = _search_cache.get(cache_key)
    if cached_result is not None:
        logger.debug(f"Cache hit for search: {search_term}")
        return cached_result

    result = search_products(db, search_term, **kwargs)
    _search_cache.set(cache_key, result)

    return result

def clear_search_cache():
    """Clear the search cache"""
    _search_cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# User Interest Tracking
# ─────────────────────────────────────────────────────────────────────────────

def _make_product_key(product_name: str, brand: str = "", quantity: str = "") -> str:
    """
    Build a stable, lowercase product key for deduplication.
    e.g. ("Pepsi Cola", "Pepsi", "1000ml") → "pepsi cola 1000ml"
    """
    parts = [product_name.lower().strip()]
    if quantity:
        q = quantity.lower().strip()
        if q and q not in parts[0]:
            parts.append(q)
    return " ".join(parts)


def log_user_interest(
    db: Session,
    user_id: int,
    product_name: str,
    interaction_type: str,           # "search" | "view" | "comparison" | "favorite"
    brand: str = "",
    category: str = "",
    quantity: str = "",
) -> models.UserInterest:
    """
    Upsert a UserInterest row.
    On conflict (user_id + product_key) it increments the relevant counter.
    Returns the updated/created row.
    """
    from backend.services.quantity_normalizer import normalize_quantity

    norm_qty = normalize_quantity(quantity) if quantity else ""
    key = _make_product_key(product_name, brand, norm_qty)

    existing = (
        db.query(models.UserInterest)
        .filter(
            models.UserInterest.user_id == user_id,
            models.UserInterest.product_key == key,
        )
        .first()
    )

    counter_map = {
        "search": "search_count",
        "view": "view_count",
        "comparison": "comparison_count",
        "favorite": "favorite_count",
    }
    counter_field = counter_map.get(interaction_type, "view_count")

    if existing:
        setattr(existing, counter_field, getattr(existing, counter_field) + 1)
        existing.last_interacted_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    interest = models.UserInterest(
        user_id=user_id,
        product_key=key,
        product_name=product_name,
        brand=brand or None,
        category=category or None,
        quantity=norm_qty or None,
    )
    setattr(interest, counter_field, 1)
    db.add(interest)
    db.commit()
    db.refresh(interest)
    return interest


def get_user_interests(db: Session, user_id: int, min_interactions: int = 1):
    """Return all UserInterest rows for a user that meet the minimum interaction threshold."""
    from sqlalchemy import case
    weight = (
        models.UserInterest.search_count
        + models.UserInterest.view_count * 2
        + models.UserInterest.comparison_count * 3
        + models.UserInterest.favorite_count * 5
    )
    return (
        db.query(models.UserInterest)
        .filter(models.UserInterest.user_id == user_id)
        .filter(weight >= min_interactions)
        .order_by(weight.desc())
        .all()
    )


def get_user_interest_keys(db: Session, user_id: int) -> list:
    """Return just the product_key strings for a user's interests (fast lookup)."""
    rows = db.query(models.UserInterest.product_key).filter(
        models.UserInterest.user_id == user_id
    ).all()
    return [r[0] for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Notification Delivery Log (duplicate prevention)
# ─────────────────────────────────────────────────────────────────────────────

def was_notification_recently_sent(
    db: Session,
    user_id: int,
    product_key: str,
    notification_type: str,
    cooldown_hours: int = 24,
    current_price: float = None,
) -> bool:
    """
    Returns True when an identical notification was already sent within the
    cooldown window AND the price hasn't changed since then.
    """
    since = datetime.utcnow() - timedelta(hours=cooldown_hours)
    log = (
        db.query(models.NotificationDeliveryLog)
        .filter(
            models.NotificationDeliveryLog.user_id == user_id,
            models.NotificationDeliveryLog.product_key == product_key,
            models.NotificationDeliveryLog.notification_type == notification_type,
            models.NotificationDeliveryLog.sent_at >= since,
        )
        .order_by(models.NotificationDeliveryLog.sent_at.desc())
        .first()
    )
    if not log:
        return False
    # Allow re-notification if price has dropped further
    if current_price is not None and log.price_at_delivery is not None:
        if current_price < log.price_at_delivery - 1:   # at least Re 1 cheaper
            return False
    return True


def log_notification_delivered(
    db: Session,
    user_id: int,
    product_key: str,
    notification_type: str,
    price: float = None,
    store: str = None,
):
    """Append a delivery log entry so future calls to was_notification_recently_sent work."""
    entry = models.NotificationDeliveryLog(
        user_id=user_id,
        product_key=product_key,
        notification_type=notification_type,
        price_at_delivery=price,
        store=store,
    )
    db.add(entry)
    db.commit()
