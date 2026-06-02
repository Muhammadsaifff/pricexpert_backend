import re
from fuzzywuzzy import fuzz
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from backend.database import crud, models
from backend.services.quantity_normalizer import normalize_quantity, extract_and_normalize
import logging

logger = logging.getLogger(__name__)

class SmartPriceComparator:
    def __init__(self, db: Session):
        self.db = db
        # Cache for normalized terms to avoid repeated processing
        self.normalization_cache = {}

    def extract_product_family(self, product_name: str) -> str:
        """Extract the main product name without size/quantity using advanced regex
        
        Now PRESERVES brand name to differentiate between "Slice juice" and "Mango juice"
        """
        patterns = [
            r'\s*\d+(?:\.\d+)?\s*(g|gm|gram|kg|kilo|ml|l|litre|liter|pc|pcs|piece|sachet)\b',
            r'\s*\d+\s*x',
            r'x\s*\d+',
            r'\b\d+\b',
            r'\s*\(?pack\s+of\s+\d+\)?',
            # Additional patterns for grocery items
            r'\s*\d+\s*(%|percent)\b',
            r'\s*\(.*?\)',  # Remove parenthetical content
        ]
        
        family_name = product_name.lower()
        for pattern in patterns:
            family_name = re.sub(pattern, ' ', family_name, flags=re.IGNORECASE)
        
        # Remove extra whitespace
        family_name = " ".join(family_name.split())
        
        # Remove ONLY specific filler words, NOT brand words
        filler_words = ['deal', 'offer', 'pack', 'bundle', 'fresh', 'organic', 'natural', 'premium', 'classic', 'original']
        for word in filler_words:
            family_name = re.sub(r'\b' + word + r'\b', ' ', family_name, flags=re.IGNORECASE)
        
        # Clean up extra spaces again
        family_name = " ".join(family_name.split())
        return family_name.strip()


    def normalize_grocery_term(self, term: str) -> str:
        """
        Normalize grocery search term using CRUD's grocery normalization
        """
        # Check cache first
        if term in self.normalization_cache:
            return self.normalization_cache[term]
        
        # Use CRUD's grocery normalization
        normalized, category = crud.normalize_grocery_term(term)
        
        # Cache the result
        self.normalization_cache[term] = normalized
        
        if normalized != term:
            logger.info(f"🛒 Normalized grocery term: '{term}' -> '{normalized}' (category: {category})")
        
        return normalized

    def _get_all_close_matches(
        self, 
        name: str, 
        candidates: List[str], 
        threshold: int = 60,
        use_grocery_boost: bool = True
    ) -> List[tuple]:
        """
        Find ALL matches above threshold, sorted by similarity
        
        Enhanced with:
        - Brand-aware matching (e.g., "slice juice" won't match "mango juice")
        - Stricter similarity requirements
        - Grocery-specific boosting
        """
        matches = []
        
        # Pre-normalize the search name if it's a grocery term
        if use_grocery_boost:
            normalized_name = self.normalize_grocery_term(name)
        else:
            normalized_name = name
        
        for candidate in candidates:
            # For brand-aware matching, prioritize exact word matches
            # Check if all content words in search are in candidate
            search_words = set(normalized_name.lower().split())
            candidate_words = set(candidate.lower().split())
            
            # Count matching words (this ensures brand names match)
            matching_words = search_words.intersection(candidate_words)
            word_match_ratio = len(matching_words) / max(len(search_words), 1)
            
            # Calculate base similarity using token_set_ratio
            ratio = fuzz.token_set_ratio(normalized_name, candidate)
            
            # If search has 2+ words (e.g., "slice juice"), require strong word overlap
            if len(search_words) >= 2:
                # At least 50% of search words must be in candidate
                if word_match_ratio < 0.5:
                    ratio = max(0, ratio - 30)  # Heavily penalize poor word matches
                else:
                    ratio = min(100, ratio + 10)  # Boost good word matches
            
            # Apply grocery boost if enabled
            if use_grocery_boost:
                # Boost if candidate contains grocery-related terms
                grocery_keywords = ['milk', 'bread', 'rice', 'chicken', 'egg', 'butter', 
                                   'cheese', 'yogurt', 'fruit', 'vegetable', 'juice', 'drink']
                for keyword in grocery_keywords:
                    if keyword in candidate and keyword in normalized_name:
                        ratio = min(100, ratio + 10)  # Boost by 10%
                        break
            
            if ratio >= threshold:
                matches.append((candidate, ratio))
        
        # Sort by score descending (best matches first)
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    def searchandcompare_products(
        self, 
        search_term: str, 
        use_grocery_normalization: bool = True,
        expand_search: bool = True,
        min_score: int = 30
    ) -> Dict[str, Any]:
        """
        Search for products and return ALL matching families with complete details
        
        ENHANCED FEATURES:
        - Automatic grocery term normalization (sgampoo -> shampoo)
        - Search expansion to related items
        - Better handling of partial matches
        - Returns ALL matching product families
        - Shows multiple product variants for each family
        """
        print(f"\n🔍 Searching for: '{search_term}'")
        
        # Step 1: Normalize the search term if grocery normalization is enabled
        normalized_term = search_term
        detected_category = None
        
        if use_grocery_normalization:
            normalized_term, detected_category = crud.normalize_grocery_term(search_term)
            if normalized_term != search_term.lower():
                print(f"📝 Normalized term: '{search_term}' -> '{normalized_term}'")
                if detected_category:
                    print(f"📂 Detected category: {detected_category}")
        
        # Step 2: Perform search with enhanced parameters
        if use_grocery_normalization and expand_search:
            # Use specialized grocery search for better results
            db_products = crud.search_grocery_products(
                self.db, 
                search_term=normalized_term,
                limit=500,
                min_score=min_score,
                prioritize_in_stock=True,
                expand_search=expand_search
            )
        else:
            # Use standard search
            db_products = crud.search_products(
                self.db, 
                search_term=normalized_term,
                limit=500,
                min_score=min_score,
                prioritize_in_stock=True,
                use_grocery_normalization=use_grocery_normalization
            )

        if not db_products:
            # Try with original term if normalization didn't help
            if normalized_term != search_term:
                print(f"⚠️ No results with normalized term, trying original: '{search_term}'")
                db_products = crud.search_products(
                    self.db, 
                    search_term=search_term,
                    limit=500,
                    min_score=min_score,
                    prioritize_in_stock=True,
                    use_grocery_normalization=False
                )
            
            if not db_products:
                # Get suggestions for the user
                suggestions = crud.get_search_suggestions(self.db, search_term)
                return {
                    "status": "not_found",
                    "message": f"Product '{search_term}' not found in any store",
                    "search_term": search_term,
                    "normalized_term": normalized_term,
                    "detected_category": detected_category,
                    "suggestions": suggestions[:5] if suggestions else [],
                    "results": {}
                }

        print(f"✅ Found {len(db_products)} total products matching '{search_term}'")
        
        # Log first few products for debugging
        if db_products:
            print(f"📦 Sample products: {[p.product_name[:50] for p in db_products[:3]]}")

        # Group products by extracted family name
        product_families = {}
        for p in db_products:
            family = self.extract_product_family(p.product_name)
            if family not in product_families:
                product_families[family] = []
            
            product_families[family].append({
                'id': p.id,
                'store': p.store,
                'product_name': p.product_name,
                'brand': p.brand,
                'quantity': p.quantity,
                'old_price': p.old_price,
                'discounted_price': p.discounted_price,
                'save_amount': p.save_amount,
                'product_url': p.product_url,
                'image_url': p.image_url,
                'out_of_stock': p.out_of_stock,
                'product_family': family,
                'scraped_at': p.scraped_at
            })

        print(f"📦 Grouped into {len(product_families)} product families")
        
        # Show some families for debugging
        family_list = list(product_families.keys())[:10]
        print(f"📦 Families: {family_list}...")

        # Extract search family with normalization
        search_family = self.extract_product_family(normalized_term)
        print(f"🔎 Search family extracted as: '{search_family}'")
        
        # Get ALL matching families with improved matching
        matching_families_with_scores = self._get_all_close_matches(
            search_family, 
            list(product_families.keys()),
            threshold=55,  # Lower threshold for grocery items
            use_grocery_boost=use_grocery_normalization
        )

        if not matching_families_with_scores:
            # Try with original search family
            original_search_family = self.extract_product_family(search_term)
            if original_search_family != search_family:
                print(f"🔄 Trying with original search family: '{original_search_family}'")
                matching_families_with_scores = self._get_all_close_matches(
                    original_search_family, 
                    list(product_families.keys()),
                    threshold=55,
                    use_grocery_boost=use_grocery_normalization
                )
            
            if not matching_families_with_scores:
                return {
                    "status": "not_found",
                    "message": f"No matches found for '{search_term}'",
                    "search_term": search_term,
                    "normalized_term": normalized_term,
                    "detected_category": detected_category,
                    "suggestions": crud.get_search_suggestions(self.db, search_term)[:5],
                    "results": {}
                }

        matching_families = [fam for fam, score in matching_families_with_scores]
        print(f"🎯 Found {len(matching_families)} matching families:")
        for fam, score in matching_families_with_scores[:10]:  # Show top 10
            print(f"   ✓ '{fam}' (match: {score}%)")

        # Organize results for ALL matching families
        organized_results = {}
        total_matches = 0

        for family_name in matching_families:
            products_in_family = product_families[family_name]
            print(f"\n📊 Processing family: '{family_name}' ({len(products_in_family)} products)")

            # Group by canonical quantity so that '1L', '1000ml', '1 Litre'
            # all map to the same size key ('1000ml') and are compared together.
            size_groups = {}
            for product in products_in_family:
                # Prefer the stored (already-normalised) quantity field;
                # fall back to extracting from the product name.
                raw_qty = product.get('quantity') or ''
                size_key = (
                    normalize_quantity(raw_qty)
                    or extract_and_normalize(product['product_name'])
                    or product['product_name']
                )

                if size_key not in size_groups:
                    size_groups[size_key] = []
                size_groups[size_key].append(product)

            organized_sizes = {}
            for size_name, stores in size_groups.items():
                # Sort by price (cheapest first)
                stores.sort(key=lambda x: x['discounted_price'] or x['old_price'] or float('inf'))
                
                # Separate in-stock and out-of-stock
                in_stock_stores = [s for s in stores if not s['out_of_stock']]
                out_of_stock_stores = [s for s in stores if s['out_of_stock']]
                
                # Get cheapest in-stock option
                cheapest_in_stock = in_stock_stores[0] if in_stock_stores else None
                
                # Calculate price range
                prices = [s['discounted_price'] or s['old_price'] for s in stores if s['discounted_price'] or s['old_price']]
                price_range = {
                    'min': min(prices) if prices else None,
                    'max': max(prices) if prices else None
                }
                
                organized_sizes[size_name] = {
                    'stores': stores,
                    'in_stock_stores': in_stock_stores,
                    'out_of_stock_stores': out_of_stock_stores,
                    'cheapest_in_stock': cheapest_in_stock,
                    'total_stores': len(stores),
                    'in_stock_count': len(in_stock_stores),
                    'price_range': price_range,
                    'best_deal': cheapest_in_stock
                }
                total_matches += len(stores)
            
            organized_results[family_name] = organized_sizes

        print(f"\n✅ FINAL RESULT: {len(matching_families)} families, {total_matches} total products")

        # Get additional suggestions for related products
        related_suggestions = []
        if len(matching_families) < 3 and detected_category:
            # Suggest other items in same category
            related_suggestions = crud.get_related_grocery_items(normalized_term)
            related_suggestions = [s for s in related_suggestions if s != normalized_term][:5]

        # Return comprehensive results
        return {
            "status": "success",
            "search_term": search_term,
            "normalized_term": normalized_term,
            "detected_category": detected_category,
            "was_corrected": search_term.lower() != normalized_term,
            "matched_families_count": len(matching_families),
            "matched_families": matching_families,
            "matched_families_with_scores": dict(matching_families_with_scores[:10]),  # Top 10 with scores
            "total_matches": total_matches,
            "results": organized_results,
            "related_suggestions": related_suggestions,
            "search_method": "grocery" if use_grocery_normalization else "standard"
        }

    def get_best_deal_for_product(self, search_term: str) -> Dict[str, Any]:
        """
        Simplified method to get just the best deal for a product
        """
        result = self.searchandcompare_products(search_term)
        
        if result["status"] != "success":
            return result
        
        # Find the absolute best deal across all families
        best_deal = None
        best_price = float('inf')
        
        for family, sizes in result["results"].items():
            for size, size_data in sizes.items():
                if size_data["cheapest_in_stock"]:
                    deal = size_data["cheapest_in_stock"]
                    price = deal['discounted_price'] or deal['old_price']
                    if price and price < best_price:
                        best_price = price
                        best_deal = deal
        
        return {
            "status": "success",
            "search_term": search_term,
            "normalized_term": result["normalized_term"],
            "best_deal": best_deal,
            "best_price": best_price if best_price != float('inf') else None,
            "total_options": result["total_matches"],
            "families_found": result["matched_families"]
        }

    def compare_specific_products(self, product_urls: List[str]) -> Dict[str, Any]:
        """
        Compare specific products by their URLs
        """
        products = []
        for url in product_urls:
            # Query product by URL
            product = self.db.query(models.Product).filter(
                models.Product.product_url == url
            ).first()
            if product:
                products.append(product)
        
        if not products:
            return {
                "status": "not_found",
                "message": "No products found for the provided URLs"
            }
        
        # Organize comparison
        comparison = {
            "status": "success",
            "products_count": len(products),
            "products": []
        }
        
        for product in products:
            comparison["products"].append({
                "name": product.product_name,
                "store": product.store,
                "brand": product.brand,
                "price": product.discounted_price or product.old_price,
                "original_price": product.old_price,
                "discount": product.save_amount,
                "in_stock": not product.out_of_stock,
                "url": product.product_url
            })
        
        # Sort by price
        comparison["products"].sort(key=lambda x: x["price"] or float('inf'))
        comparison["cheapest"] = comparison["products"][0] if comparison["products"] else None
        
        return comparison