from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, List
from backend.database.database import get_db
from backend.database import crud, models
from backend.database.models import ProductResponse, APIResponse

router = APIRouter(prefix="/deals", tags=["deals"])

@router.get("/", response_model=APIResponse)
async def get_deals(
    db: Session = Depends(get_db),
    limit: int = 10,
    store: Optional[str] = None,
    category: Optional[str] = None
):
    """Get current deals/discounts across all stores"""
    try:
        # Get products with discounts (where old_price > discounted_price)
        # Temporarily remove out_of_stock filter to debug
        deals = crud.get_discounted_products(db, limit=limit, store=store, category=category)

        # Convert to response format with discount calculations
        deals_response = []
        for deal in deals:
            discount_percentage = 0.0
            if deal.old_price and deal.old_price > 0 and deal.discounted_price:
                discount_percentage = ((deal.old_price - deal.discounted_price) / deal.old_price) * 100

            deals_response.append({
                "id": deal.id,
                "name": deal.product_name,
                "oldPrice": f"PKR {deal.old_price:.0f}" if deal.old_price else None,
                "newPrice": f"PKR {deal.discounted_price:.0f}" if deal.discounted_price else "N/A",
                "discount": f"-{discount_percentage:.0f}%" if discount_percentage > 0 else None,
                "saveAmount": f"PKR {deal.save_amount:.0f}" if deal.save_amount else None,
                "store": deal.store,
                "category": deal.category,
                "quantity": deal.quantity,
                "image": deal.image_url or f"https://via.placeholder.com/100x100?text={deal.product_name[:1] if deal.product_name else 'D'}",
                "product_url": deal.product_url,
                "out_of_stock": deal.out_of_stock
            })

        return APIResponse(
            success=True,
            message=f"Found {len(deals_response)} deals",
            data={"deals": deals_response}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching deals: {str(e)}")

@router.get("/featured", response_model=APIResponse)
async def get_featured_deals(
    db: Session = Depends(get_db),
    limit: int = 6
):
    """Get featured deals (highest discount percentages)"""
    try:
        # Get all discounted products and sort by discount percentage
        deals = crud.get_discounted_products(db, limit=100)  # Get more to sort

        # Calculate discount percentages and sort
        deals_with_percentage = []
        for deal in deals:
            if deal.old_price and deal.old_price > 0 and deal.discounted_price:
                discount_percentage = ((deal.old_price - deal.discounted_price) / deal.old_price) * 100
                deals_with_percentage.append((deal, discount_percentage))

        # Sort by discount percentage (highest first)
        deals_with_percentage.sort(key=lambda x: x[1], reverse=True)

        # Take top deals
        featured_deals = deals_with_percentage[:limit]

        # Convert to response format
        deals_response = []
        for deal, discount_percentage in featured_deals:
            deals_response.append({
                "id": deal.id,
                "name": deal.product_name,
                "oldPrice": f"PKR {deal.old_price:.0f}" if deal.old_price else None,
                "newPrice": f"PKR {deal.discounted_price:.0f}" if deal.discounted_price else "N/A",
                "discount": f"-{discount_percentage:.0f}%" if discount_percentage > 0 else None,
                "saveAmount": f"PKR {deal.save_amount:.0f}" if deal.save_amount else None,
                "store": deal.store,
                "category": deal.category,
                "quantity": deal.quantity,
                "image": deal.image_url or f"https://via.placeholder.com/100x100?text={deal.product_name[:1] if deal.product_name else 'D'}",
                "product_url": deal.product_url,
                "out_of_stock": deal.out_of_stock
            })

        return APIResponse(
            success=True,
            message=f"Found {len(deals_response)} featured deals",
            data={"deals": deals_response}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching featured deals: {str(e)}")

@router.get("/store/{store_name}", response_model=APIResponse)
async def get_deals_by_store(
    store_name: str,
    db: Session = Depends(get_db),
    limit: int = 20
):
    """Get deals for a specific store"""
    try:
        deals = crud.get_discounted_products(db, store=store_name, limit=limit)

        deals_response = []
        for deal in deals:
            discount_percentage = 0.0
            if deal.old_price and deal.old_price > 0 and deal.discounted_price:
                discount_percentage = ((deal.old_price - deal.discounted_price) / deal.old_price) * 100

            deals_response.append({
                "id": deal.id,
                "name": deal.product_name,
                "oldPrice": f"PKR {deal.old_price:.0f}" if deal.old_price else None,
                "newPrice": f"PKR {deal.discounted_price:.0f}" if deal.discounted_price else "N/A",
                "discount": f"-{discount_percentage:.0f}%" if discount_percentage > 0 else None,
                "saveAmount": f"PKR {deal.save_amount:.0f}" if deal.save_amount else None,
                "store": deal.store,
                "category": deal.category,
                "quantity": deal.quantity,
                "image": deal.image_url or f"https://via.placeholder.com/100x100?text={deal.product_name[:1] if deal.product_name else 'D'}",
                "product_url": deal.product_url,
                "out_of_stock": deal.out_of_stock
            })

        return APIResponse(
            success=True,
            message=f"Found {len(deals_response)} deals at {store_name}",
            data={"deals": deals_response, "store": store_name}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching deals for {store_name}: {str(e)}")