# pricexpert/api/endpoints/auth.py

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from backend.database.database import get_db
from backend.services.auth_service import AuthService
from backend.database import crud
from pydantic import BaseModel
from typing import Optional
import re
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# Pydantic models for request/response
class UserCreate(BaseModel):
    name: str
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    language: Optional[str] = None
    budget: Optional[float] = None

class UserResponse(BaseModel):
    id: int
    name: str
    email: str

    class Config:
        orm_mode = True

class AuthResponse(BaseModel):
    success: bool
    message: str
    user: Optional[UserResponse] = None


# Request body for Google sign-in (UPDATED - now expects Firebase ID token)
class GoogleLoginRequest(BaseModel):
    token: str  # This is now the Firebase ID token, not the Google OAuth token

# Password validation
def validate_password(password: str) -> bool:
    """Basic password validation"""
    if len(password) < 6:
        return False
    return True

# Email validation (additional to Pydantic)
def validate_email(email: str) -> bool:
    """Basic email validation"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

@router.post("/auth/signup", response_model=AuthResponse)
async def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    """Create a new user account"""
    try:
        user = AuthService.register_user(
            db=db,
            name=user_data.name,
            email=user_data.email,
            password=user_data.password
        )

        return AuthResponse(
            success=True,
            message="Account created successfully",
            user=UserResponse(
                id=user.id,
                name=user.name,
                email=user.email
            )
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Signup error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/auth/signin", response_model=AuthResponse)
async def signin(user_data: UserLogin, db: Session = Depends(get_db)):
    """Authenticate user login"""
    try:
        user = AuthService.authenticate_user(
            db=db,
            email=user_data.email,
            password=user_data.password
        )

        if not user:
            raise HTTPException(
                status_code=401,
                detail="Invalid email or password"
            )

        return AuthResponse(
            success=True,
            message="Login successful",
            user=UserResponse(
                id=user.id,
                name=user.name,
                email=user.email
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signin error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/auth/guest", response_model=AuthResponse)
async def guest_login():
    """Allow guest access without authentication"""
    return AuthResponse(
        success=True,
        message="Guest access granted",
        user=None  # No user data for guest
    )

@router.get("/auth/get-user", response_model=AuthResponse)
async def get_user(user_id: int = Query(...), db: Session = Depends(get_db)):
    """Get user details by user_id"""
    try:
        user = crud.get_user_by_id(db, user_id)
        
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        return AuthResponse(
            success=True,
            message="User retrieved successfully",
            user=UserResponse(
                id=user.id,
                name=user.name,
                email=user.email
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get user error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.put("/auth/update-user", response_model=AuthResponse)
async def update_user(user_id: int = Query(...), user_data: Optional[UserUpdate] = None, db: Session = Depends(get_db)):
    """Update user profile"""
    try:
        user = crud.get_user_by_id(db, user_id)
        
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        # Prepare update data
        update_data = {}
        if user_data:
            if user_data.name:
                update_data['name'] = user_data.name
            if user_data.email:
                # Validate email format if provided
                if not validate_email(user_data.email):
                    raise HTTPException(status_code=400, detail="Invalid email format")
                update_data['email'] = user_data.email
            if user_data.language:
                update_data['language'] = user_data.language
            if user_data.budget is not None:
                update_data['budget'] = user_data.budget

        if not update_data:
            raise HTTPException(
                status_code=400,
                detail="At least one field must be provided for update"
            )

        # Update user
        updated_user = crud.update_user(db, user_id, **update_data)

        return AuthResponse(
            success=True,
            message="User updated successfully",
            user=UserResponse(
                id=updated_user.id,
                name=updated_user.name,
                email=updated_user.email
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update user error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/auth/google", response_model=AuthResponse)
async def google_signin(data: GoogleLoginRequest, db: Session = Depends(get_db)):
    """
    Sign in or register with Google using Firebase ID token.
    
    This endpoint expects a Firebase ID token (not a Google OAuth token).
    The token is obtained from Firebase Auth after successful Google Sign-In.
    """
    try:
        # Verify the Firebase ID token and get/create user
        # This uses the Firebase Admin SDK to verify the token securely
        user = AuthService.verify_google_token_and_login(db, data.token)

        return AuthResponse(
            success=True,
            message="Google sign-in successful",
            user=UserResponse(
                id=user.id,
                name=user.name,
                email=user.email
            )
        )
        
    except ValueError as e:
        # This catches invalid/expired tokens from the verification
        logger.warning(f"Google sign-in failed: {e}")
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        logger.error(f"Google sign-in error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/auth/logout", response_model=AuthResponse)
async def logout(user_id: int = Query(...)):
    """Logout user (clears session on client side)"""
    # Note: For Firebase, actual token revocation should be handled client-side
    # This endpoint exists for API consistency
    return AuthResponse(
        success=True,
        message="Logout successful"
    )

# Optional: Add a health check endpoint for debugging
@router.get("/auth/health")
async def health_check():
    """Health check endpoint for authentication service"""
    return {
        "status": "healthy",
        "service": "authentication",
        "firebase_sdk_initialized": bool(AuthService._check_firebase_initialized())
    }