from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from backend.services.ai_chatbot import ShoppingAssistant
from backend.database.database import get_db  # your DB dependency

router = APIRouter()

# Initialize assistant WITHOUT DB session (models stay in memory)
bilingual_assistant = ShoppingAssistant(db_session=None)

class ChatMessage(BaseModel):
    message: str
    user_id: int | None = None

class BilingualResponse(BaseModel):
    type: str
    message: str
    language: str
    products: List[str] = []
    quantity: Optional[str] = ""
    intent: Optional[str] = ""
    results: Optional[Dict[str, Any]] = None

@router.post("/ai-chat", response_model=BilingualResponse)
async def chat_bilingual(message: ChatMessage, db: Session = Depends(get_db)):
    """
    Chat with bilingual AI shopping assistant.
    Inject DB session per request to avoid NoneType errors.
    """
    try:
        # Inject DB session for this request
        if bilingual_assistant.comparator:
            bilingual_assistant.comparator.db = db
        bilingual_assistant.db = db  # allow saving data
        response = bilingual_assistant.process_message(
        text=message.message,
        user_id=message.user_id         
        )


        intent = response.get("intent", "")
        resp_type = (
            "price_inquiry"
            if intent in ("price_inquiry", "product_search", "comparison")
            else ("budget_response" if intent == "budget" else "general_response")
        )
        processed_response = {
            "type": resp_type,
            "message": response.get("response", "I'm here to help with shopping!"),
            "language": response.get("language", "english"),
            "products": response.get("products", []),
            "quantity": response.get("quantity", ""),
            "intent": intent,
            "results": response.get("search_results"),
        }

        return BilingualResponse(**processed_response)

    except Exception as e:
        print(f"AI processing error: {e}")
        error_response = {
            "type": "error",
            "message": "I encountered an error. Please try again.",
            "language": "english",
            "products": [],
            "quantity": "",
            "intent": "error",
            "results": []
        }
        return BilingualResponse(**error_response)
