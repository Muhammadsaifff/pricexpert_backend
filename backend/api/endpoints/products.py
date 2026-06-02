from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from backend.database.database import get_db
from backend.database import crud, models
from backend.database.models import ProductResponse, APIResponse

router = APIRouter(prefix="/products", tags=["products"])

@router.get("/", response_model=APIResponse)
async def get_products(
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    store: str = None,
    category: str = None
):
    """Get all products with filtering and pagination"""
    products = crud.get_products(db, skip=skip, limit=limit, store=store)

    # Additional category filtering
    if category:
        products = [p for p in products if p.category and category.lower() in p.category.lower()]

    # Map ORM products to frontend-friendly dicts
    product_dicts = []
    for p in products:
        product_dicts.append({
            "id": p.id,
            "name": p.product_name,
            "brand": p.brand,
            "category": p.category,
            "price": p.discounted_price if p.discounted_price is not None else p.old_price,
            "old_price": p.old_price,
            "store": p.store,
            "image": p.image_url,
            "url": p.product_url,
            "out_of_stock": p.out_of_stock,
        })

    return APIResponse(
        success=True,
        message=f"Retrieved {len(product_dicts)} products",
        data={"products": product_dicts}
    )

@router.get("/search", response_model=APIResponse)
async def search_products(
    query: str = Query(..., min_length=2, description="Search query for product names"),
    db: Session = Depends(get_db),
    limit: int = 50
):
    """Search products by name"""
    products = crud.search_products(db, search_term=query)

    product_dicts = []
    for p in products:
        product_dicts.append({
            "id": p.id,
            "name": p.product_name,
            "brand": p.brand,
            "category": p.category,
            "price": p.discounted_price if p.discounted_price is not None else p.old_price,
            "old_price": p.old_price,
            "store": p.store,
            "image": p.image_url,
            "url": p.product_url,
            "out_of_stock": p.out_of_stock,
        })

    return APIResponse(
        success=True,
        message=f"Found {len(product_dicts)} products for '{query}'",
        data={"products": product_dicts, "query": query}
    )

@router.get("/featured", response_model=APIResponse)
async def get_featured_products(
    db: Session = Depends(get_db),
    limit: int = 10
):
    """Get random featured products for homepage"""
    products = crud.get_random_products(db, limit=limit)

    product_dicts = []
    for p in products:
        product_dicts.append({
            "id": p.id,
            "name": p.product_name,
            "brand": p.brand,
            "category": p.category,
            "price": p.discounted_price if p.discounted_price is not None else p.old_price,
            "old_price": p.old_price,
            "store": p.store,
            "image": p.image_url,
            "url": p.product_url,
            "out_of_stock": p.out_of_stock,
        })

    return APIResponse(
        success=True,
        message=f"Retrieved {len(product_dicts)} featured products",
        data={"products": product_dicts}
    )

@router.get("/{product_id}", response_model=APIResponse)
async def get_product(
    product_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific product by ID"""
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return APIResponse(
        success=True,
        message="Product retrieved successfully",
        data={"product": ProductResponse.from_orm(product)}
    )

@router.get("/store/{store_name}", response_model=APIResponse)
async def get_products_by_store(
    store_name: str,
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100
):
    """Get products by store name"""
    products = crud.get_products(db, skip=skip, limit=limit, store=store_name)

    product_dicts = []
    for p in products:
        product_dicts.append({
            "id": p.id,
            "name": p.product_name,
            "brand": p.brand,
            "category": p.category,
            "price": p.discounted_price if p.discounted_price is not None else p.old_price,
            "old_price": p.old_price,
            "store": p.store,
            "image": p.image_url,
            "url": p.product_url,
            "out_of_stock": p.out_of_stock,
        })

    return APIResponse(
        success=True,
        message=f"Retrieved {len(product_dicts)} products from {store_name}",
        data={"products": product_dicts, "store": store_name}
    )
