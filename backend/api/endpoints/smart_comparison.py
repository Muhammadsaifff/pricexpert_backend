from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text, func, desc
from datetime import datetime

from backend.database.database import get_db
from backend.database.models import Product, ScrapingJob
from backend.database import crud
from backend.services.smart_comparison import SmartPriceComparator  # Updated import

router = APIRouter()


# ----------------- Request/Response Models -----------------
class SearchRequest(BaseModel):
    products: List[str]
    include_out_of_stock: bool = False
    use_grocery_normalization: bool = True  # NEW: Enable grocery normalization
    expand_search: bool = True  # NEW: Expand to related items
    min_score: int = 30  # NEW: Minimum relevance score


class BudgetRequest(BaseModel):
    """Request model for budget-based shopping"""
    products: List[str]
    budget: float
    prefer_cheapest: bool = True
    use_grocery_normalization: bool = True  # NEW: Enable grocery normalization


class SearchResponse(BaseModel):
    status: str
    search_results: Dict[str, Any]
    message: str = ""
    corrections_applied: Dict[str, str] = {}  # NEW: Track corrections
    suggestions: Dict[str, List[str]] = {}  # NEW: Search suggestions


class BudgetResponse(BaseModel):
    status: str
    within_budget: bool
    total_cost: float
    remaining_budget: float
    shopping_list: List[Dict[str, Any]]
    message: str = ""
    corrections_applied: Dict[str, str] = {}  # NEW: Track corrections


class ScrapingJobResponse(BaseModel):
    """Response model for scraping job data"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


# ----------------- API Endpoints -----------------
@router.post("/smart-compare", response_model=SearchResponse)
def smart_compare_prices(request: SearchRequest, db: Session = Depends(get_db)):
    """Compare prices across stores for multiple products with grocery normalization and typo correction"""
    if not request.products:
        raise HTTPException(status_code=400, detail="Products list cannot be empty")

    comparator = SmartPriceComparator(db)
    all_results = {}
    corrections_applied = {}
    suggestions = {}

    for product_name in request.products:
        # Use enhanced search with grocery normalization
        results = comparator.searchandcompare_products(
            search_term=product_name,
            use_grocery_normalization=request.use_grocery_normalization,
            expand_search=request.expand_search,
            min_score=request.min_score
        )
        
        all_results[product_name] = results
        
        # Track corrections
        if results.get("was_corrected"):
            corrections_applied[product_name] = results.get("normalized_term", product_name)
        
        # Track suggestions if few results
        if results.get("status") == "not_found" or results.get("matched_families_count", 0) < 2:
            if results.get("suggestions"):
                suggestions[product_name] = results.get("suggestions", [])[:5]

    return SearchResponse(
        status="success",
        search_results=all_results,
        message=f"Compared {len(request.products)} products" + 
                (f" (corrected: {len(corrections_applied)} terms)" if corrections_applied else ""),
        corrections_applied=corrections_applied,
        suggestions=suggestions
    )


@router.post("/budget-shop", response_model=BudgetResponse)
def budget_shopping(request: BudgetRequest, db: Session = Depends(get_db)):
    """🆕 Get optimal shopping list within budget with grocery normalization"""
    if not request.products:
        raise HTTPException(status_code=400, detail="Products list cannot be empty")
    if request.budget <= 0:
        raise HTTPException(status_code=400, detail="Budget must be greater than 0")

    shopping_list = []
    total_cost = 0.0
    comparator = SmartPriceComparator(db)
    corrections_applied = {}

    for product_name in request.products:
        # Use enhanced search
        result = comparator.searchandcompare_products(
            search_term=product_name,
            use_grocery_normalization=request.use_grocery_normalization,
            expand_search=True,
            min_score=30
        )
        
        # Track corrections
        if result.get("was_corrected"):
            corrections_applied[product_name] = result.get("normalized_term", product_name)
        
        # Find the cheapest in-stock product across all matching families
        cheapest_product = None
        cheapest_price = float('inf')
        
        if result['status'] == 'success' and result.get('results'):
            for family, sizes in result['results'].items():
                for size, details in sizes.items():
                    # Check in-stock products
                    if details.get('cheapest_in_stock'):
                        product = details['cheapest_in_stock']
                        price = product.get('discounted_price') or product.get('old_price', 0)
                        
                        # If we have price and it's cheaper, or we're prioritizing price
                        if price and price < cheapest_price:
                            cheapest_price = price
                            cheapest_product = product
                    
                    # Also check all in-stock stores if cheapest_in_stock isn't set
                    elif details.get('in_stock_stores'):
                        for store_product in details['in_stock_stores']:
                            price = store_product.get('discounted_price') or store_product.get('old_price', 0)
                            if price and price < cheapest_price:
                                cheapest_price = price
                                cheapest_product = store_product
            
            if cheapest_product:
                item_cost = cheapest_product.get('discounted_price') or cheapest_product.get('old_price', 0)
                shopping_list.append({
                    "product": product_name,
                    "normalized_name": result.get('normalized_term', product_name),
                    "matched_name": cheapest_product.get('product_name', 'Unknown'),
                    "status": "found",
                    "price": item_cost,
                    "store": cheapest_product.get('store', 'Unknown'),
                    "product_url": cheapest_product.get('product_url', ''),
                    "save_amount": cheapest_product.get('save_amount', 0),
                    "in_stock": not cheapest_product.get('out_of_stock', True),
                    "category": result.get('detected_category', 'Unknown')
                })
                total_cost += item_cost
            else:
                # No in-stock products found across any family
                shopping_list.append({
                    "product": product_name,
                    "normalized_name": result.get('normalized_term', product_name),
                    "status": "out_of_stock",
                    "price": 0,
                    "store": None,
                    "suggestions": result.get('suggestions', [])[:3]
                })
        else:
            # Product not found
            shopping_list.append({
                "product": product_name,
                "normalized_name": result.get('normalized_term', product_name),
                "status": "not_found",
                "price": 0,
                "store": None,
                "suggestions": result.get('suggestions', [])[:3]
            })

    # Sort shopping list by status and price
    shopping_list.sort(key=lambda x: (
        0 if x['status'] == 'found' else 1,  # Found items first
        x['price'] if x['status'] == 'found' else float('inf')  # Cheapest first
    ))

    return BudgetResponse(
        status="success",
        within_budget=total_cost <= request.budget,
        total_cost=total_cost,
        remaining_budget=request.budget - total_cost,
        shopping_list=shopping_list,
        message=f"Budget: Rs. {request.budget:.2f} | Total: Rs. {total_cost:.2f}" +
                (f" | Corrected: {len(corrections_applied)} terms" if corrections_applied else ""),
        corrections_applied=corrections_applied
    )


@router.get("/search/{product_name}")
def search_single_product(
    product_name: str, 
    use_grocery_normalization: bool = Query(True, description="Enable grocery term normalization"),
    expand_search: bool = Query(True, description="Expand search to related items"),
    min_score: int = Query(30, ge=0, le=100, description="Minimum relevance score"),
    db: Session = Depends(get_db)
):
    """Search for a single product across all stores with grocery normalization"""
    if len(product_name) < 2:
        raise HTTPException(status_code=400, detail="Search term must be at least 2 characters")

    comparator = SmartPriceComparator(db)
    result = comparator.searchandcompare_products(
        search_term=product_name,
        use_grocery_normalization=use_grocery_normalization,
        expand_search=expand_search,
        min_score=min_score
    )
    
    return result


@router.get("/grocery/search")
def search_grocery_items(
    q: str = Query(..., min_length=1, description="Grocery item to search"),
    expand: bool = Query(True, description="Expand to related items"),
    category_filter: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    """
    Specialized grocery search with normalization and expansion
    """
    # Normalize the search term
    normalized, category = crud.normalize_grocery_term(q)
    
    # Perform grocery-specific search
    results = crud.search_grocery_products(
        db,
        search_term=q,
        limit=limit,
        expand_search=expand,
        min_score=30,
        prioritize_in_stock=True
    )
    
    # Apply category filter if specified
    if category_filter and results:
        results = [p for p in results if category_filter.lower() in (p.category or '').lower()]
    
    # Get related suggestions
    related = crud.get_related_grocery_items(normalized) if expand else []
    
    return {
        "status": "success",
        "original_query": q,
        "normalized_query": normalized,
        "detected_category": category,
        "category_filter_applied": category_filter,
        "total_results": len(results),
        "results": results,
        "related_suggestions": related[:10],
        "search_expanded": expand
    }


@router.get("/grocery/categories")
def get_grocery_categories(db: Session = Depends(get_db)):
    """
    Get all available grocery categories with product counts
    """
    # Get unique categories from products
    categories = crud.get_unique_categories(db, limit=100)
    
    # Count products per category
    category_counts = {}
    for category in categories:
        if category and category != "Misc":
            count = db.query(Product).filter(
                Product.category.ilike(f"%{category}%")
            ).count()
            category_counts[category] = count
    
    # Sort by count descending
    sorted_categories = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
    
    return {
        "status": "success",
        "total_categories": len(sorted_categories),
        "categories": [{"name": cat, "product_count": count} for cat, count in sorted_categories]
    }


@router.get("/grocery/popular")
def get_popular_grocery_items(
    limit: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None, description="Filter by category"),
    db: Session = Depends(get_db)
):
    """
    Get popular grocery items based on search frequency and discounts
    """
    # Get discounted products first
    discounted = crud.get_discounted_products(db, limit=limit, category=category)
    
    # If not enough, get random products
    if len(discounted) < limit:
        remaining = limit - len(discounted)
        random_products = crud.get_random_products(db, limit=remaining)
        
        # Combine and remove duplicates
        all_products = discounted + random_products
        seen = set()
        unique_products = []
        for p in all_products:
            if p.id not in seen:
                seen.add(p.id)
                unique_products.append(p)
        
        results = unique_products[:limit]
    else:
        results = discounted[:limit]
    
    return {
        "status": "success",
        "total_results": len(results),
        "category_filter": category,
        "results": results
    }


@router.post("/grocery/bulk-compare")
def bulk_grocery_compare(
    items: List[str],
    use_normalization: bool = Query(True),
    db: Session = Depends(get_db)
):
    """
    Bulk compare multiple grocery items at once
    """
    comparator = SmartPriceComparator(db)
    results = {}
    summary = {
        "total_items": len(items),
        "items_found": 0,
        "items_not_found": 0,
        "total_best_price": 0,
        "corrections": {}
    }
    
    for item in items:
        search_result = comparator.searchandcompare_products(
            search_term=item,
            use_grocery_normalization=use_normalization,
            expand_search=True
        )
        
        if search_result.get("was_corrected"):
            summary["corrections"][item] = search_result.get("normalized_term")
        
        # Find best deal
        best_deal = None
        best_price = float('inf')
        
        if search_result.get('status') == 'success' and search_result.get('results'):
            for family, sizes in search_result['results'].items():
                for size, details in sizes.items():
                    if details.get('cheapest_in_stock'):
                        product = details['cheapest_in_stock']
                        price = product.get('discounted_price') or product.get('old_price', 0)
                        if price and price < best_price:
                            best_price = price
                            best_deal = product
            
            if best_deal:
                summary["items_found"] += 1
                summary["total_best_price"] += best_price
                results[item] = {
                    "found": True,
                    "normalized": search_result.get('normalized_term'),
                    "best_price": best_price,
                    "best_deal": best_deal,
                    "alternatives_count": search_result.get('total_matches', 0)
                }
            else:
                summary["items_not_found"] += 1
                results[item] = {
                    "found": False,
                    "normalized": search_result.get('normalized_term'),
                    "message": "No in-stock products found",
                    "suggestions": search_result.get('suggestions', [])
                }
        else:
            summary["items_not_found"] += 1
            results[item] = {
                "found": False,
                "normalized": search_result.get('normalized_term'),
                "message": "Product not found",
                "suggestions": search_result.get('suggestions', [])
            }
    
    summary["average_price"] = summary["total_best_price"] / summary["items_found"] if summary["items_found"] > 0 else 0
    
    return {
        "status": "success",
        "summary": summary,
        "results": results
    }


# Keep your existing scraping job endpoints as they are
@router.get("/scraping-jobs/latest", response_model=ScrapingJobResponse)
def get_latest_scraping_job(db: Session = Depends(get_db)):
    """Get the most recent completed scraping job."""
    # Your existing code remains exactly the same
    try:
        latest_job = db.query(ScrapingJob)\
            .filter(ScrapingJob.status == 'completed')\
            .filter(ScrapingJob.products_count > 0)\
            .order_by(desc(ScrapingJob.created_at))\
            .first()
        
        if not latest_job:
            latest_job = db.query(ScrapingJob)\
                .filter(ScrapingJob.status == 'completed')\
                .order_by(desc(ScrapingJob.created_at))\
                .first()
        
        if not latest_job:
            return ScrapingJobResponse(
                success=False,
                message="No completed scraping jobs found",
                data=None
            )
        
        stores_summary = f"{latest_job.store}: {latest_job.products_count}" if latest_job.store else ""
        
        job_data = {
            "id": latest_job.id,
            "store": latest_job.store,
            "store_type": latest_job.store_type,
            "status": latest_job.status,
            "stage": latest_job.stage,
            "use_test_config": latest_job.use_test_config,
            "urls_count": latest_job.urls_count,
            "products_count": latest_job.products_count,
            "message": latest_job.message,
            "error": latest_job.error,
            "created_at": latest_job.created_at.isoformat() if latest_job.created_at else None,
            "started_at": latest_job.started_at.isoformat() if latest_job.started_at else None,
            "completed_at": latest_job.completed_at.isoformat() if latest_job.completed_at else None,
            "stores_summary": stores_summary,
            "total_products": latest_job.urls_count,
            "completed_products": latest_job.products_count,
        }
        
        return ScrapingJobResponse(
            success=True,
            data=job_data,
            message="Latest scraping job retrieved successfully"
        )
        
    except Exception as e:
        print(f"Error fetching latest scraping job: {e}")
        return ScrapingJobResponse(
            success=False,
            message=f"Error retrieving scraping job: {str(e)}",
            data=None
        )


@router.get("/scraping-jobs", response_model=Dict[str, Any])
def get_scraping_jobs(
    limit: int = Query(10, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status (queued, running, completed, failed)"),
    store: Optional[str] = Query(None, description="Filter by store name"),
    db: Session = Depends(get_db)
):
    """Get list of scraping jobs with optional filtering."""
    # Your existing code remains exactly the same
    try:
        query = db.query(ScrapingJob)
        
        if status:
            query = query.filter(ScrapingJob.status == status)
        if store:
            query = query.filter(ScrapingJob.store == store)
        
        jobs = query.order_by(desc(ScrapingJob.created_at)).limit(limit).all()
        
        job_list = []
        for job in jobs:
            stores_summary = f"{job.store}: {job.products_count}" if job.store else ""
            
            job_list.append({
                "id": job.id,
                "store": job.store,
                "store_type": job.store_type,
                "status": job.status,
                "stage": job.stage,
                "use_test_config": job.use_test_config,
                "urls_count": job.urls_count,
                "products_count": job.products_count,
                "message": job.message,
                "error": job.error,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "stores_summary": stores_summary,
                "total_products": job.urls_count,
                "completed_products": job.products_count,
            })
        
        return {
            "success": True,
            "data": job_list,
            "total": len(job_list),
            "message": f"Retrieved {len(job_list)} scraping jobs"
        }
        
    except Exception as e:
        print(f"Error fetching scraping jobs: {e}")
        return {
            "success": False,
            "data": [],
            "message": f"Error retrieving scraping jobs: {str(e)}"
        }


@router.get("/scraping-jobs/stores/summary", response_model=Dict[str, Any])
def get_scraping_stores_summary(db: Session = Depends(get_db)):
    """Get summary of scraping jobs by store."""
    # Your existing code remains exactly the same
    try:
        stores = db.query(ScrapingJob.store).distinct().all()
        stores = [s[0] for s in stores if s[0]]
        
        summary = {}
        for store in stores:
            job_count = db.query(ScrapingJob).filter(ScrapingJob.store == store).count()
            
            latest = db.query(ScrapingJob)\
                .filter(ScrapingJob.store == store)\
                .order_by(desc(ScrapingJob.created_at))\
                .first()
            
            summary[store] = {
                "total_jobs": job_count,
                "latest_job": {
                    "id": latest.id if latest else None,
                    "status": latest.status if latest else None,
                    "products_count": latest.products_count if latest else 0,
                    "created_at": latest.created_at.isoformat() if latest and latest.created_at else None,
                }
            }
        
        return {
            "success": True,
            "data": summary,
            "message": f"Retrieved summary for {len(stores)} stores"
        }
        
    except Exception as e:
        print(f"Error fetching stores summary: {e}")
        return {
            "success": False,
            "data": {},
            "message": f"Error retrieving stores summary: {str(e)}"
        }


# NEW: Health check endpoint for search functionality
@router.get("/search/health")
def search_health_check(db: Session = Depends(get_db)):
    """Check if search functionality is working properly"""
    test_terms = ["milk", "sgampoo", "milt", "buter"]
    results = {}
    
    for term in test_terms:
        normalized, category = crud.normalize_grocery_term(term)
        results[term] = {
            "normalized": normalized,
            "category": category,
            "was_corrected": term != normalized
        }
    
    return {
        "status": "healthy",
        "grocery_normalization": results,
        "product_count": crud.get_product_count(db)
    }