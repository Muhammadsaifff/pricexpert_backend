import re
import os
from typing import Dict, List, Any, Optional, Tuple
import torch
from transformers import pipeline, AutoTokenizer, AutoModelForSequenceClassification
from backend.services.smart_comparison import SmartPriceComparator
from backend.services.quantity_normalizer import normalize_quantity
from backend.database.models import SearchQuery

class ShoppingAssistant:

    def __init__(self, db_session=None, user=None):
        print("🔄 Loading AI models...")

        self.user = user
        self.db = db_session  
        self.comparator = SmartPriceComparator(db_session)
        self.pending_items = ""                # store terms when waiting for budget
        print("✅ SmartPriceComparator initialized!")

        self._load_urdu_model()
        self._load_english_classifier()
        self._init_urdu_patterns()
        print("🎯 Bilingual Shopping Assistant ready!")

    def _load_urdu_model(self):
        try:
            self.urdu_tokenizer = AutoTokenizer.from_pretrained(
                "callmesan/ModernBERT-large-roman-urdu-fine-grained"
            )
            self.urdu_model = AutoModelForSequenceClassification.from_pretrained(
                "callmesan/ModernBERT-large-roman-urdu-fine-grained"
            )
            self.urdu_pipe = pipeline(
                "text-classification",
                model=self.urdu_model,
                tokenizer=self.urdu_tokenizer,
                device=0 if torch.cuda.is_available() else -1
            )
            print("✅ Roman Urdu model loaded!")
        except Exception as e:
            print(f"❌ Failed to load Roman Urdu model: {e}")
            self.urdu_pipe = None

    def _load_english_classifier(self):
        try:
            self.english_classifier = pipeline(
                "zero-shot-classification",
                model="facebook/bart-large-mnli",
                device=0 if torch.cuda.is_available() else -1
            )
            print("✅ English classifier loaded!")
        except Exception as e:
            print(f"❌ Failed to load English classifier: {e}")
            self.english_classifier = None

    def _init_urdu_patterns(self):
        self.urdu_intent_patterns = {
            'price_inquiry': [
                r'kitne?\s*(ki|ka|ke)?\s*(hai|hain|he)',
                r'qeemat\s*(kya|kitni|bataiye)',
                r'daam\s*(kya|kitna|bataiye)',
                r'rate\s*(kya|kitna|bataiye)',
                r'(sasti|mehngi|cheap)',
                r'price\s*(bataiye|batao|kya)',
            ],
            'product_search': [
                r'(kahan|kidhar)\s*(milegi|milay|miltaa?)',
                r'(dhoond|dhundo|talaash)',
                r'(chahiye|chaiye)',
                r'(available|stock)\s*(hai|hain)?',
            ],
            'comparison': [
                r'compare\s*(karo|karein|kijiye)',
                r'(muqabla|moqabla)',
                r'(konsi|kaun\s*si)\s*(sasti|mehngi|behtar)',
                r'(best|behtareen)\s*(deal|offer)',
            ],
            'budget': [
                r'budget\s*(\d+)',
                r'(\d+)\s*rupay\s*mein',
                r'(\d+)\s*(rs|rupees?)\s*(mein|ke\s*andar)',
                r'kitne\s*mein\s*aa\s*jaega',
            ],
            'deals': [
                r'(offer|discount|deal)\s*(hai|hain|batao)?',
                r'(sale|sasta)\s*(hai|hain)?',
                r'(bachao|saving)',
            ]
        }

        # Greeting patterns in Urdu and English
        self.greeting_patterns = {
            'urdu': {
                'greetings': [
                    r'\b(assalam\s+o\s+alaikum|assalamu\s+alaikum|salam|salam\s+alaikum)\b',
                    r'\b(adaab|aadaab)\b',
                    r'\b(khus\s*?raho|khush\s*?raho)\b',
                ],
                'how_are_you': [
                    r'\b(kia\s+haal\s+hein|kya\s+haal\s+hain|kaise\s+ho)\b',
                    r'\b(aap\s+kaise\s+hain)\b',
                    r'\b(state|halat|haalat|kaisa\s+chal\s+raha)\b',
                    r'\b(tussi\s+theek\s+ho|thik\s+ho)\b',
                ],
                'thanks': [
                    r'\b(shukriya|shukria|sukran|dhanyavaad|meherbani)\b',
                    r'\b(thanks|thank\s+you)\b',
                ],
                'goodbye': [
                    r'\b(khuda\s+hafiz|allah\s+hafiz|bye|alvida)\b',
                    r'\b(phir\s+milenge|phr\s+milenge)\b',
                    r'\b(jate\s+raho|roindaa)\b',
                ]
            },
            'english': {
                'greetings': [
                    r'\b(hello|hi|hey|greetings)\b',
                    r'\b(howdy|what\'?s\s+up)\b',
                ],
                'how_are_you': [
                    r'\b(how\s+are\s+you|how\s+are\s+ya)\b',
                    r'\b(whats?\s+up|sup)\b',
                    r'\b(how\'?s\s+it\s+going)\b',
                ],
                'thanks': [
                    r'\b(thanks|thank\s+you|appreciate)\b',
                    r'\b(grateful|acknowledge)\b',
                ],
                'goodbye': [
                    r'\b(bye|goodbye|farewell|see\s+you)\b',
                    r'\b(later|ttyl|catch\s+you)\b',
                ]
            }
        }

    # ------------------- Greeting Detection -------------------
    def is_greeting_query(self, text: str) -> tuple[bool, str, str]:
        """
        Detect if the message is a greeting/general query.
        Returns (is_greeting, response_message, language)
        """
        text_lower = text.lower().strip()
        
        # Detect language first
        language = self.detect_language(text)
        
        if language == 'urdu':
            patterns = self.greeting_patterns['urdu']
        else:
            patterns = self.greeting_patterns['english']
        
        # Check for greetings
        for pattern in patterns.get('greetings', []):
            if re.search(pattern, text_lower):
                if language == 'urdu':
                    responses = [
                        "Walaikum Assalam! Khush amdeed 😊",
                        "Assalamu Alaikum! Main yahan aapki madad ke liye hoon.",
                        "Adaab! Kya main aapki koi madad kar sakta/sakti hoon?",
                    ]
                else:
                    responses = [
                        "Hello! Welcome to PriceXpert 😊",
                        "Hi there! I'm here to help you find the best prices.",
                        "Hey! How can I assist you with your shopping today?",
                    ]
                import random
                return True, random.choice(responses), language
        
        # Check for "how are you" queries
        for pattern in patterns.get('how_are_you', []):
            if re.search(pattern, text_lower):
                if language == 'urdu':
                    responses = [
                        "Main bilkul theek hoon, shukriya pooch ne ke liye! Aap kaisa/kaisi ho? Main aapko best prices dhondne mein madad kar sakta/sakti hoon! 😊",
                        "Alhamdulillah theek hoon! Aap kaise ho? Aapko kya shopping chahiye?",
                    ]
                else:
                    responses = [
                        "I'm doing great, thanks for asking! 😊 How can I help you find the best deals today?",
                        "All good here! Ready to help you save money on your shopping. What are you looking for?",
                    ]
                import random
                return True, random.choice(responses), language
        
        # Check for thanks
        for pattern in patterns.get('thanks', []):
            if re.search(pattern, text_lower):
                if language == 'urdu':
                    responses = [
                        "Khush rahiye! Agar aapko aur madad chahiye toh mujhe bataiye.",
                        "Aapka shukriya! Kya main aapki aur koi madad kar sakta/sakti hoon?",
                    ]
                else:
                    responses = [
                        "You're welcome! Let me know if you need anything else.",
                        "Happy to help! Feel free to ask if you need more assistance.",
                    ]
                import random
                return True, random.choice(responses), language
        
        # Check for goodbye
        for pattern in patterns.get('goodbye', []):
            if re.search(pattern, text_lower):
                if language == 'urdu':
                    responses = [
                        "Khuda Hafiz! Aapko ek acha din! 👋",
                        "Phir milenge! Allah aapko khush rakhay.",
                        "Bye! Shopping ka mazaa lijiye!",
                    ]
                else:
                    responses = [
                        "Goodbye! Have a great day! 👋",
                        "See you later! Happy shopping!",
                        "Take care! Come back soon!",
                    ]
                import random
                return True, random.choice(responses), language
        
        return False, "", language

    # ------------------- Language Detection -------------------
    def detect_language(self, text: str) -> str:
        text_lower = text.lower()
        urdu_indicators = [
            'sasti', 'sasta', 'kam', 'km', 'kitne', 'qeemat', 'daam', 
            'kahan', 'milegi', 'milayga', 'milaygi', 'dhoond', 'talaash',
            'shukriya', 'shukria', 'meherbani', 'bhai', 'bhae', 'janab',
            'ki', 'ka', 'ke', 'ko', 'mein', 'se', 'par', 'per', 'ho',
            'chahiye', 'chaiye', 'chaheye', 'chahta', 'chahti', 'chahte',
            'batao', 'bataiye', 'bataye', 'batado', 'kar', 'karo', 'karein',
            'hai', 'hain', 'he', 'ho', 'raha', 'rahe', 'rahi', 'rahay'
        ]
        urdu_count = sum(1 for word in urdu_indicators if word in text_lower)
        english_indicators = ['where', 'price', 'cheap', 'expensive', 'best', 'cheapest', 'show', 'me',
                             'store', 'available', 'availability', 'buy', 'low', 'reasonable',
                             'purchase', 'cost', 'comparison', 'compare','can', 'i', 'find']
        english_count = sum(1 for word in english_indicators if word in text_lower)

        urdu_patterns = [
            r'\b(mein|se|ka|ki|ke|ko)\b',
            r'\b(chahiye|chahta|chahti)\b',
            r'\b(kitne|kahan|kya)\b',
            r'\b(batao|bataiye)\b',
            r'\b(milayga|milaygi|milega|milegi)\b'
        ]
        for pattern in urdu_patterns:
            if re.search(pattern, text_lower):
                urdu_count += 2

        return 'urdu' if urdu_count > english_count else 'english'

    # ------------------- Extract Search Terms -------------------
    def extract_search_terms(self, text: str) -> str:
        """
        Extract the core product / brand term from a conversational query.
        Only removes unambiguous grammar and intent words; preserves product
        names, brand names, sizes, and useful descriptors so the fuzzy search
        engine gets clean but complete input.
        """
        pure_filler = {
            # Urdu grammar particles
            'ka', 'ki', 'ke', 'ko', 'se', 'par', 'per', 'pe',
            'mein', 'mai', 'main',
            'hai', 'hain', 'ho', 'tha', 'thi', 'thay',
            'raha', 'rahi', 'rahe', 'rahay',
            'ab', 'sab', 'sub', 'phr', 'phir', 'bhi',
            # Urdu intent / question words
            'kitne', 'kitni', 'kitna', 'kitnay',
            'kahan', 'kidhar', 'kaise', 'kia', 'kya',
            'konsi', 'kaun', 'kis',
            # Urdu personal pronouns / social words
            'mujhe', 'mjhe', 'mere', 'meri', 'mera',
            'hum', 'humein', 'aap', 'tum',
            'bhai', 'bhae', 'janab', 'sir',
            'acha', 'achi', 'achay', 'haan', 'han', 'ji',
            # Urdu shopping verbs / filler
            'chahiye', 'chaiye', 'chahta', 'chahti', 'chahte',
            'lena', 'leni', 'lelo', 'len', 'lain',
            'milayga', 'milaygi', 'milega', 'milegi', 'milta', 'milti',
            'dhoond', 'dhoondo', 'talaash',
            'batao', 'bataiye', 'bataye', 'batado',
            'shukriya', 'shukria', 'meherbani',
            # English grammar / pure filler
            'where', 'what', 'how', 'which', 'when',
            'can', 'could', 'would', 'should', 'will',
            'please', 'plz', 'tell', 'me', 'the', 'a', 'an', 'is', 'are',
            'this', 'that', 'its', 'their',
            'find', 'show', 'get', 'buy', 'purchase', 'looking',
            'want', 'need', 'like', 'have',
            'and', 'or', 'with', 'for', 'to', 'just', 'some',
            'in', 'at', 'of', 'from', 'has',
            'i', 'my', 'we', 'you', 'any', 'also',
        }

        words = re.findall(r'\b[\w]+\b', text.lower())
        filtered = [w for w in words if w not in pure_filler and len(w) > 1]
        search_term = ' '.join(filtered).strip()

        # Fallback: if nothing useful survived, return cleaned full text
        if len(search_term) < 2:
            search_term = re.sub(r'[^\w\s]', ' ', text).strip()

        return search_term

    # ------------------- Quantity Extraction -------------------
    def extract_quantity(self, text: str) -> str:
        match = re.search(r'\b\d+\.?\d*\s*(ml|l|litre|liter|g|kg|gram|kilo|pc|pack|pcs|piece|sachet|tablet)\b', text.lower())
        return match.group() if match else ""

    # ------------------- Budget Extraction -------------------
    def extract_budget(self, text: str) -> float:
        patterns = [
            r'(\d+)\s*rupay\s*mein',
            r'(\d+)\s*(rs|rupees?)\s*mein',
            r'budget\s*(\d+)',
            r'(\d+)\s*tak',
            r'(\d+)\s*under',
            r'(\d+)\s*se\s*kam',
            r'within\s*(\d+)'
        ]
        for pattern in patterns:
            match = re.search(pattern, text.lower())
            if match:
                return float(match.group(1))
        return 0.0

    # ------------------- Intent Classification -------------------
    def classify_intent(self, text: str, language: str) -> str:
        text_lower = text.lower()
        price_patterns = ['kitne', 'qeemat', 'daam', 'price', 'cost', 'rate', 'sasta', 'sasti', 'mehnga', 'mehngi', 'cheap', 'expensive']
        location_patterns = ['kahan', 'kidhar', 'milega', 'milegi', 'milayga', 'milaygi', 'available', 'where', 'find', 'get', 'buy']
        comparison_patterns = ['compare', 'comparison', 'muqabla', 'moqabla', 'best', 'better', 'cheapest', 'sab se sasta']
        budget_patterns = ['budget', 'rupay mein', 'rupees mein', 'kam paise', 'under', 'within', 'tak', 'se kam']

        if any(p in text_lower for p in comparison_patterns):
            return 'comparison'
        elif any(p in text_lower for p in price_patterns):
            return 'price_inquiry'
        elif any(p in text_lower for p in location_patterns):
            return 'product_search'
        elif any(p in text_lower for p in budget_patterns):
            return 'budget'

        if language == 'urdu' and self.urdu_pipe:
            try:
                result = self.urdu_pipe(text)
                label_map = {'price':'price_inquiry','search':'product_search','location':'product_search','availability':'product_search'}
                model_label = result[0]['label'].lower()
                intent = label_map.get(model_label, 'general_inquiry')
                if intent == 'general_inquiry' and text_lower.strip():
                    # when model is unsure but text exists, treat as search
                    intent = 'product_search'
                return intent
            except Exception as e:
                print(f"Urdu model inference failed: {e}")
        # fallback default: if text contains something treat as product search
        return 'product_search' if text_lower.strip() else 'general_inquiry'

    # ------------------- Main Message Processor -------------------
    def process_message(self, text: str, user_id: int | None = None) -> Dict[str, Any]:
        print(f"\n🔍 Processing query: '{text}'")

        # 🔹 CHECK FOR GREETINGS FIRST
        is_greeting, greeting_response, language = self.is_greeting_query(text)
        if is_greeting:
            print(f"😊 Greeting detected in {language}")
            return {
                "type": "greeting_response",
                "language": language,
                "search_term": "",
                "products": [],
                "quantity": "",
                "budget": None,
                "intent": "greeting",
                "response": greeting_response,
                "search_results": None
            }

        # 🔹 USER INTEGRATION: Detect language
        language = self.detect_language(text)
        if self.user and self.user.language:
            language = self.user.language

        print(f"🌐 Detected language: {language}")

        search_term = self.extract_search_terms(text)
        print(f"🔎 Search term: '{search_term}'")

        quantity = self.extract_quantity(text)

        budget = self.extract_budget(text)

        # 🔹 USER INTEGRATION: Use saved budget if not mentioned
        if budget <= 0 and self.user and self.user.budget:
            budget = self.user.budget

        intent = self.classify_intent(text, language)

        # if classifier couldn't decide but we at least have some search term,
        # assume user wants to look for a product rather than returning a generic response.
        if intent == 'general_inquiry' and search_term:
            print("🔁 Overriding intent to 'product_search' since search_term provided")
            intent = 'product_search'

        # --- Multiple items: search each one separately ---
        multi_items = bool(re.search(r'\band\b', text.lower()) or ',' in text)
        if multi_items and intent in ['price_inquiry', 'product_search', 'comparison']:
            raw_parts = re.split(r'\band\b|,', text, flags=re.IGNORECASE)
            item_terms = [self.extract_search_terms(p.strip()) for p in raw_parts if p.strip()]
            valid_terms = [t for t in item_terms if len(t) > 1]
            if len(valid_terms) > 1:
                print(f"📦 Multi-item query detected: {valid_terms}")
                return self._handle_multi_item_query(valid_terms, language, budget, intent)

        print(f"📏 Quantity: {quantity}")
        print(f"💰 Budget: {budget if budget > 0 else 'Not specified'}")
        print(f"🎯 Intent: {intent}")

        # handle general inquiries without doing search
        if intent == 'general_inquiry':
            generic = (
                "Mujhe maaf kijiye, main samajh nahi paaya. Kya aap kisi product ke baare mein pooch rahe hain?" if language=='urdu'
                else "I'm not sure I understood. Could you ask about a product or let me know what you're looking for?"
            )
            return {
                "type": "general_response",
                "language": language,
                "search_term": search_term,
                "products": [],
                "quantity": quantity,
                "budget": budget if budget > 0 else None,
                "intent": intent,
                "response": generic,
                "search_results": None
            }

        # 🔹 USER INTEGRATION: Save search history
        if self.db and self.user:
            search_entry = SearchQuery(
                user_id=self.user.id,
                query_text=text
            )
            self.db.add(search_entry)
            self.db.commit()

        search_result = self.comparator.searchandcompare_products(search_term)
        print(f"📊 Search result status: {search_result['status']}")

        response_message = ""
        if search_result['status'] == 'success':
            if intent == 'budget' and budget > 0:
                response_message = self._format_budget_response(search_result, budget, language)
            elif intent == 'comparison':
                response_message = self._format_comparison_response(search_result, language, quantity=quantity)
            else:
                # price_inquiry, product_search, or any other intent → rich price list
                response_message = self._format_price_response(search_result, language, quantity=quantity)
        else:
            # Product not found – include suggestions when available
            suggestions = search_result.get('suggestions', [])
            if language == 'urdu':
                response_message = f"Maaf kijiye, '{search_term}' nahi mila."
                if suggestions:
                    response_message += f" Kya aap yeh mante hain: {', '.join(suggestions[:3])}?"
                else:
                    response_message += " Kripya product ka naam dobara likhein."
            else:
                response_message = f"Sorry, I couldn't find '{search_term}'."
                if suggestions:
                    response_message += f" Did you mean: {', '.join(suggestions[:3])}?"
                else:
                    response_message += " Please try a different product name or spelling."

        product_names = []
        if search_result['status'] == 'success' and search_result['results']:
            for family in search_result['results'].values():
                for size_name in family.keys():
                    product_names.append(size_name)

        return {
            "type": "product_search_response",
            "language": language,
            "search_term": search_term,
            "products": product_names[:5],
            "quantity": quantity,
            "budget": budget if budget > 0 else None,
            "intent": intent,
            "response": response_message.strip(),
            "search_results": search_result if search_result['status'] == 'success' else None
        }


    # ------------------- Multi-Item Helpers -------------------
    def _handle_multi_item_query(
        self,
        item_terms: List[str],
        language: str,
        budget: float,
        intent: str,
    ) -> Dict[str, Any]:
        """Search for each item separately and return a combined summary."""
        summaries: List[str] = []
        found_products: List[str] = []

        for term in item_terms[:4]:          # cap at 4 items
            result = self.comparator.searchandcompare_products(term)
            if result['status'] == 'success':
                summaries.append(self._get_product_one_liner(result, language))
                found_products.append(term)
            else:
                suggestions = result.get('suggestions', [])
                if language == 'urdu':
                    msg = f"❓ '{term}' nahi mila"
                    if suggestions:
                        msg += f" (kya '{suggestions[0]}' mante hain?)"
                else:
                    msg = f"❓ '{term}' not found"
                    if suggestions:
                        msg += f" (did you mean '{suggestions[0]}'?)"
                summaries.append(msg)

        header = (
            f"Aapke {len(item_terms)} products ki prices:\n"
            if language == 'urdu'
            else f"Prices for {len(item_terms)} products:\n"
        )
        return {
            "type": "product_search_response",
            "language": language,
            "search_term": ', '.join(item_terms),
            "products": found_products,
            "quantity": "",
            "budget": budget if budget > 0 else None,
            "intent": intent,
            "response": header + '\n'.join(summaries),
            "search_results": None,
        }

    def _get_product_one_liner(self, search_result: Dict, language: str) -> str:
        """Single-line best-deal summary used inside multi-item responses."""
        best_price, best_store, best_family, best_size = float('inf'), '', '', ''
        total_in_stock = 0
        for family, sizes in search_result['results'].items():
            for size_key, data in sizes.items():
                cheapest = data.get('cheapest_in_stock')
                total_in_stock += data.get('in_stock_count', 0)
                if cheapest:
                    price = cheapest.get('discounted_price') or cheapest.get('old_price', float('inf'))
                    if price and price < best_price:
                        best_price = price
                        best_store = cheapest.get('store', '')
                        best_family = family
                        best_size = size_key
        if not best_store:
            term = search_result.get('search_term', '?')
            return (f"❌ {term} – stock mein nahi" if language == 'urdu'
                    else f"❌ {term} – out of stock everywhere")
        stores_label = f"{total_in_stock} store{'s' if total_in_stock != 1 else ''}"
        if language == 'urdu':
            return (f"🛒 {best_family.title()} ({best_size}): "
                    f"Rs. {best_price:.0f} — {best_store} ({stores_label} mein available)")
        else:
            return (f"🛒 {best_family.title()} ({best_size}): "
                    f"Rs. {best_price:.0f} — cheapest at {best_store} ({stores_label})")

    # ------------------- Price & Budget Response Formatters -------------------
    def _format_price_response(
        self, search_result: Dict, language: str, quantity: str = ""
    ) -> str:
        """
        Rich structured store-by-store price list for the best matching product.
        Shows all in-stock stores ranked cheapest → most expensive, savings tip,
        and a brief mention of out-of-stock stores.
        Handles requested-quantity mismatches gracefully.
        """
        if search_result['status'] != 'success' or not search_result['results']:
            return (
                "Maaf kijiye, yeh product nahi mila."
                if language == 'urdu'
                else "Sorry, I couldn't find this product."
            )

        results = search_result['results']
        normalized_qty = normalize_quantity(quantity) if quantity else None

        # ── Pick the best-matching size group ────────────────────────────────
        chosen_family: Optional[str] = None
        chosen_size: Optional[str] = None
        chosen_data: Optional[Dict] = None
        best_price = float('inf')

        for family_name, sizes in results.items():
            for size_key, size_data in sizes.items():
                cheapest = size_data.get('cheapest_in_stock')
                if not cheapest:
                    continue
                price = cheapest.get('discounted_price') or cheapest.get('old_price', float('inf'))
                if not price:
                    continue
                if normalized_qty:
                    if normalize_quantity(size_key) == normalized_qty and price < best_price:
                        best_price, chosen_family, chosen_size, chosen_data = (
                            price, family_name, size_key, size_data)
                else:
                    if price < best_price:
                        best_price, chosen_family, chosen_size, chosen_data = (
                            price, family_name, size_key, size_data)

        # Quantity mismatch – fall back and note it
        qty_mismatch_note = ""
        if normalized_qty and chosen_data is None:
            best_price = float('inf')
            for family_name, sizes in results.items():
                for size_key, size_data in sizes.items():
                    cheapest = size_data.get('cheapest_in_stock')
                    if not cheapest:
                        continue
                    price = cheapest.get('discounted_price') or cheapest.get('old_price', float('inf'))
                    if price and price < best_price:
                        best_price, chosen_family, chosen_size, chosen_data = (
                            price, family_name, size_key, size_data)
            if chosen_data:
                qty_mismatch_note = (
                    f"⚠️ '{quantity}' nahi mili – '{chosen_size}' dikha raha hoon:\n"
                    if language == 'urdu'
                    else f"⚠️ '{quantity}' not found – showing '{chosen_size}' instead:\n"
                )

        if chosen_data is None:
            return (
                "Yeh product abhi kisi bhi store mein stock mein nahi hai."
                if language == 'urdu'
                else "This product is currently out of stock at all stores."
            )

        in_stock  = chosen_data.get('in_stock_stores', [])
        out_of_stock = chosen_data.get('out_of_stock_stores', [])
        price_range  = chosen_data.get('price_range', {})

        if not in_stock:
            return (
                "Yeh product abhi stock mein nahi hai."
                if language == 'urdu'
                else "This product is currently out of stock."
            )

        lines: List[str] = []

        # Header
        store_word = "store" if language == 'english' else "store"
        if language == 'urdu':
            lines.append(
                f"🔍 *{chosen_family.title()}* ({chosen_size}) "
                f"— {len(in_stock)} {store_word} mein available:\n"
            )
        else:
            lines.append(
                f"🔍 *{chosen_family.title()}* ({chosen_size}) "
                f"— available at {len(in_stock)} {store_word}(s):\n"
            )

        if qty_mismatch_note:
            lines.append(qty_mismatch_note)

        # ── Ranked store list ─────────────────────────────────────────────────
        rank_emojis = ['🏆', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣']
        for i, entry in enumerate(in_stock[:6]):
            price    = entry.get('discounted_price') or entry.get('old_price', 0)
            old_price = entry.get('old_price', 0)
            save_amt  = entry.get('save_amount', 0)
            store_name = entry.get('store', 'Unknown')
            emoji = rank_emojis[i] if i < len(rank_emojis) else '•'
            line = f"{emoji} {store_name} – Rs. {price:.0f}"
            if save_amt and float(save_amt) > 0:
                line += f"  ✂️ Save Rs. {float(save_amt):.0f}"
            elif old_price and old_price > price:
                line += f"  (was Rs. {old_price:.0f})"
            lines.append(line)

        # ── Out-of-stock mention ──────────────────────────────────────────────
        if out_of_stock:
            oos_names = [s.get('store', '') for s in out_of_stock[:3]]
            if language == 'urdu':
                lines.append(f"\n❌ Yahan available nahi: {', '.join(oos_names)}")
            else:
                lines.append(f"\n❌ Out of stock at: {', '.join(oos_names)}")

        # ── Savings tip ───────────────────────────────────────────────────────
        p_min = price_range.get('min')
        p_max = price_range.get('max')
        if p_min and p_max and p_max > p_min:
            diff = p_max - p_min
            best_s = in_stock[0].get('store', '') if in_stock else ''
            if language == 'urdu':
                lines.append(f"\n💡 {best_s} se khareedne par Rs. {diff:.0f} ki bachat!")
            else:
                lines.append(f"\n💡 Buy at {best_s} to save Rs. {diff:.0f} vs the most expensive option!")

        # ── Other families hint ───────────────────────────────────────────────
        others = [f.title() for f in results if f != chosen_family]
        if others:
            if language == 'urdu':
                lines.append(f"\n📦 Related: {', '.join(others[:3])}")
            else:
                lines.append(f"\n📦 Also found: {', '.join(others[:3])}")

        return '\n'.join(lines)

    def _format_comparison_response(
        self, search_result: Dict, language: str, quantity: str = ""
    ) -> str:
        """
        Dedicated formatter for comparison intent.
        Shows every size variant of the product with a full ranked store list,
        a price-range tip, and out-of-stock mentions.
        """
        if search_result['status'] != 'success' or not search_result['results']:
            return (
                "Maaf kijiye, compare karne ke liye koi product nahi mila."
                if language == 'urdu'
                else "Sorry, no products found to compare."
            )

        results = search_result['results']
        total_options = sum(
            data.get('total_stores', 0)
            for sizes in results.values()
            for data in sizes.values()
        )

        lines: List[str] = []
        if language == 'urdu':
            lines.append(f"📊 Price Comparison — {total_options} options mile:\n")
        else:
            lines.append(f"📊 Price Comparison — {total_options} option(s) found:\n")

        rank_emojis = ['🏆', '2️⃣', '3️⃣', '4️⃣', '5️⃣']
        for family_name, sizes in results.items():
            for size_key, size_data in sizes.items():
                in_stock     = size_data.get('in_stock_stores', [])
                out_of_stock = size_data.get('out_of_stock_stores', [])
                if not in_stock and not out_of_stock:
                    continue

                lines.append(f"🛍️ *{family_name.title()} ({size_key})*")

                for i, store in enumerate(in_stock[:5]):
                    price      = store.get('discounted_price') or store.get('old_price', 0)
                    save_amt   = store.get('save_amount', 0)
                    store_name = store.get('store', 'Unknown')
                    emoji = rank_emojis[i] if i < len(rank_emojis) else '•'
                    line = f"  {emoji} {store_name}: Rs. {price:.0f}"
                    if save_amt and float(save_amt) > 0:
                        line += f" (Save Rs. {float(save_amt):.0f})"
                    lines.append(line)

                if out_of_stock:
                    oos_names = [s.get('store', '') for s in out_of_stock[:3]]
                    lines.append(f"  ❌ Out of stock: {', '.join(oos_names)}")

                p_range = size_data.get('price_range', {})
                if p_range.get('min') and p_range.get('max') and p_range['max'] > p_range['min']:
                    diff   = p_range['max'] - p_range['min']
                    best_s = in_stock[0].get('store', '') if in_stock else ''
                    if language == 'urdu':
                        lines.append(f"  💡 {best_s} mein buy karo — Rs. {diff:.0f} ki bachat!")
                    else:
                        lines.append(f"  💡 Buy at {best_s} — save Rs. {diff:.0f} vs priciest!")

                lines.append("")

        return '\n'.join(lines).strip()

    def _format_budget_response(
        self, search_result: Dict, budget: float, language: str
    ) -> str:
        """Format response for budget-constrained search."""
        affordable: List[Dict] = []
        too_expensive: List[Dict] = []

        for family_name, sizes_data in search_result['results'].items():
            best_price  = float('inf')
            best_entry: Optional[Dict] = None
            for size_key, product_data in sizes_data.items():
                cheapest = product_data.get('cheapest_in_stock')
                if cheapest:
                    price = cheapest.get('discounted_price') or cheapest.get('old_price', 0)
                    if price and price < best_price:
                        best_price = price
                        best_entry = {
                            'family': family_name,
                            'size': size_key,
                            'store': cheapest.get('store', ''),
                            'price': price,
                        }
            if best_entry:
                (affordable if best_price <= budget else too_expensive).append(best_entry)

        if not affordable:
            lines: List[str] = []
            if language == 'urdu':
                lines.append(f"Rs. {budget:.0f} ke budget mein koi product available nahi mila.")
                if too_expensive:
                    min_p = min(p['price'] for p in too_expensive)
                    lines.append(f"Sab se sasta option Rs. {min_p:.0f} ka hai.")
            else:
                lines.append(f"No products found within Rs. {budget:.0f}.")
                if too_expensive:
                    min_p = min(p['price'] for p in too_expensive)
                    lines.append(f"The cheapest available option starts at Rs. {min_p:.0f}.")
            return '\n'.join(lines)

        # Deduplicate by family, keep cheapest per family
        affordable.sort(key=lambda x: x['price'])
        seen: set = set()
        unique: List[Dict] = []
        for p in affordable:
            if p['family'] not in seen:
                seen.add(p['family'])
                unique.append(p)
                if len(unique) >= 5:
                    break

        lines = []
        if language == 'urdu':
            lines.append(f"✅ Rs. {budget:.0f} ke andar yeh options hain:\n")
            for i, p in enumerate(unique, 1):
                lines.append(f"{i}. {p['family'].title()} ({p['size']}) — {p['store']}: Rs. {p['price']:.0f}")
        else:
            lines.append(f"✅ Within Rs. {budget:.0f}, here are your options:\n")
            for i, p in enumerate(unique, 1):
                lines.append(f"{i}. {p['family'].title()} ({p['size']}) — {p['store']}: Rs. {p['price']:.0f}")

        if too_expensive:
            exp_names = ', '.join(p['family'].title() for p in too_expensive[:3])
            if language == 'urdu':
                lines.append(f"\n⬆️ Budget se bahar: {exp_names}")
            else:
                lines.append(f"\n⬆️ Over your budget: {exp_names}")

        return '\n'.join(lines)
