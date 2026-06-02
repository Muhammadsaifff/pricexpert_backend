# pricexpert/services/auth_service.py

import logging
import firebase_admin
from firebase_admin import credentials, auth
from sqlalchemy.orm import Session
from backend.database import crud
from backend.database.models import User
from typing import Optional
import re
import bcrypt
import os

logger = logging.getLogger(__name__)

# Initialize Firebase Admin SDK once when the service starts
# You can set the path via environment variable or use a default path
def initialize_firebase_admin():
    """Initialize Firebase Admin SDK if not already initialized"""
    if not firebase_admin._apps:
        # Try to get the service account path from environment variable
        firebase_credentials_path = os.getenv('FIREBASE_SERVICE_ACCOUNT_PATH', 'service-account-key.json')
        
        if os.path.exists(firebase_credentials_path):
            cred = credentials.Certificate(firebase_credentials_path)
            firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin SDK initialized successfully")
        else:
            logger.warning(f"Firebase service account key not found at {firebase_credentials_path}")
            logger.warning("Google authentication will not work without it")

# Initialize on module load
initialize_firebase_admin()

class AuthService:
    """Service layer for authentication operations"""

    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None

    @staticmethod
    def validate_password(password: str) -> bool:
        """Validate password strength"""
        if len(password) < 6:
            return False
        return True

    @staticmethod
    def register_user(db: Session, name: str, email: str, password: str) -> User:
        """Register a new user"""
        try:
            # Validate input
            if not name or not name.strip():
                raise ValueError("Name is required")

            if not AuthService.validate_email(email):
                raise ValueError("Invalid email format")

            if not AuthService.validate_password(password):
                raise ValueError("Password must be at least 6 characters long")

            # Hash the password
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

            # Create user
            user = crud.create_user(
                db=db,
                name=name.strip(),
                email=email.lower().strip(),
                password=hashed_password
            )

            logger.info(f"User registered successfully: {email}")
            return user

        except ValueError as e:
            logger.warning(f"User registration failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during user registration: {e}")
            raise

    @staticmethod
    def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
        """Authenticate user credentials"""
        try:
            # Get user by email
            user = crud.get_user_by_email(db, email.lower().strip())

            if not user:
                logger.warning(f"Authentication failed: user not found - {email}")
                return None

            # Verify password
            if not user.password or not bcrypt.checkpw(password.encode('utf-8'), user.password.encode('utf-8')):
                logger.warning(f"Authentication failed: invalid password - {email}")
                return None

            logger.info(f"User authenticated successfully: {email}")
            return user

        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}")
            return None

    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
        """Get user by ID"""
        try:
            return crud.get_user_by_id(db, user_id)
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None

    @staticmethod
    def update_user_profile(db: Session, user_id: int, **kwargs) -> Optional[User]:
        """Update user profile information"""
        try:
            # Validate email if provided
            if 'email' in kwargs and not AuthService.validate_email(kwargs['email']):
                raise ValueError("Invalid email format")

            user = crud.update_user(db, user_id, **kwargs)
            logger.info(f"User profile updated: {user_id}")
            return user

        except ValueError as e:
            logger.warning(f"User profile update failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error updating user profile: {e}")
            raise

    @staticmethod
    def record_search_query(db: Session, user_id: int, query_text: str):
        """Record a user's search query"""
        try:
            if not query_text or not query_text.strip():
                return None

            search_query = crud.create_search_query(
                db=db,
                user_id=user_id,
                query_text=query_text.strip()
            )

            logger.debug(f"Search query recorded for user {user_id}: {query_text}")
            return search_query

        except Exception as e:
            logger.error(f"Error recording search query: {e}")
            return None

    @staticmethod
    def verify_google_token_and_login(db: Session, firebase_id_token: str) -> User:
        """
        Verify Firebase ID token from Google Sign-In and login/register user.
        This is the SECURE method that replaces the old insecure login_or_register_google_user.
        """
        try:
            # Check if Firebase Admin SDK is initialized
            if not firebase_admin._apps:
                raise Exception("Firebase Admin SDK not initialized. Please check your service account configuration.")
            
            # Verify the Firebase ID token
            decoded_token = auth.verify_id_token(firebase_id_token)
            
            # Extract user info from the verified token
            email = decoded_token.get('email')
            name = decoded_token.get('name')
            firebase_uid = decoded_token.get('uid')
            
            if not email:
                raise ValueError("No email found in Firebase token")
            
            if not name:
                # Fallback to email username if name not provided
                name = email.split('@')[0]
            
            email = email.lower().strip()
            
            # Check if user exists in your database
            user = crud.get_user_by_email(db, email)
            
            if user:
                # User exists - update their name if it's changed and log the login
                if user.name != name:
                    # Update name if it changed (optional)
                    crud.update_user(db, user.id, name=name)
                    logger.info(f"Updated user name for {email} from Google")
                
                logger.info(f"Google user logged in: {email} (Firebase UID: {firebase_uid})")
                return user
            else:
                # Create new user (password is None for Google users)
                user = crud.create_user(
                    db=db, 
                    name=name, 
                    email=email, 
                    password=None  # Google users don't have a password
                )
                logger.info(f"New user created via Google sign-in: {email} (Firebase UID: {firebase_uid})")
                return user
                
        except auth.InvalidIdTokenError as e:
            logger.error(f"Invalid Firebase ID token: {e}")
            raise ValueError("Invalid authentication token. Please sign in again.")
        except auth.ExpiredIdTokenError as e:
            logger.error(f"Expired Firebase ID token: {e}")
            raise ValueError("Authentication token has expired. Please sign in again.")
        except auth.RevokedIdTokenError as e:
            logger.error(f"Revoked Firebase ID token: {e}")
            raise ValueError("Authentication token has been revoked. Please sign in again.")
        except auth.CertificateFetchError as e:
            logger.error(f"Certificate fetch error: {e}")
            raise ValueError("Unable to verify authentication. Please try again later.")
        except Exception as e:
            logger.error(f"Error in Google authentication: {e}")
            raise

    @staticmethod
    def get_user_search_history(db: Session, user_id: int, limit: int = 10):
        """Get user's recent search queries"""
        try:
            return crud.get_user_search_queries(db, user_id, limit)
        except Exception as e:
            logger.error(f"Error getting search history for user {user_id}: {e}")
            return []