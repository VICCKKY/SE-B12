import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

import streamlit as st


# ---------------------------------------
# PAGE SETTINGS
# ---------------------------------------
st.set_page_config(
    page_title="Aishwaryam Realty - Property Quest",
    page_icon="🏡",
    layout="wide",
)


# ---------------------------------------
# CUSTOMER WAITING ROOM
# Five browser sessions may use the app at a time. The next twenty sessions
# receive live token numbers. SQLite keeps the queue safe when several
# customers arrive together and also preserves it if Streamlit restarts.
# ---------------------------------------
MAX_ACTIVE_CUSTOMERS = 5
MAX_WAITING_CUSTOMERS = 20
SESSION_TIMEOUT_SECONDS = 90
QUEUE_DATABASE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "customer_queue.db",
)


def queue_connection():
    connection = sqlite3.connect(QUEUE_DATABASE, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_queue (
            session_id TEXT PRIMARY KEY,
            joined_at REAL NOT NULL,
            last_seen REAL NOT NULL
        )
        """
    )
    return connection


def customer_session_id():
    if "customer_session_id" not in st.session_state:
        st.session_state.customer_session_id = str(uuid.uuid4())
    return st.session_state.customer_session_id


def touch_customer_session():
    """Join/refresh the FIFO queue and return this customer's live position."""
    session_id = customer_session_id()
    now = time.time()
    connection = queue_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM customer_queue WHERE last_seen < ?",
            (now - SESSION_TIMEOUT_SECONDS,),
        )

        existing = connection.execute(
            "SELECT 1 FROM customer_queue WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if existing:
            connection.execute(
                "UPDATE customer_queue SET last_seen = ? WHERE session_id = ?",
                (now, session_id),
            )
        else:
            customer_count = connection.execute(
                "SELECT COUNT(*) FROM customer_queue"
            ).fetchone()[0]

            if customer_count >= MAX_ACTIVE_CUSTOMERS + MAX_WAITING_CUSTOMERS:
                connection.commit()
                return {
                    "state": "full",
                    "active_count": MAX_ACTIVE_CUSTOMERS,
                    "waiting_count": MAX_WAITING_CUSTOMERS,
                }

            connection.execute(
                """
                INSERT INTO customer_queue (session_id, joined_at, last_seen)
                VALUES (?, ?, ?)
                """,
                (session_id, now, now),
            )

        ordered_sessions = connection.execute(
            """
            SELECT session_id
            FROM customer_queue
            ORDER BY joined_at ASC, session_id ASC
            """
        ).fetchall()
        connection.commit()
    finally:
        connection.close()

    session_ids = [row[0] for row in ordered_sessions]
    position = session_ids.index(session_id) + 1
    active_count = min(len(session_ids), MAX_ACTIVE_CUSTOMERS)
    waiting_count = max(0, len(session_ids) - MAX_ACTIVE_CUSTOMERS)

    if position <= MAX_ACTIVE_CUSTOMERS:
        return {
            "state": "active",
            "active_slot": position,
            "active_count": active_count,
            "waiting_count": waiting_count,
        }

    return {
        "state": "waiting",
        "token": position - MAX_ACTIVE_CUSTOMERS,
        "active_count": active_count,
        "waiting_count": waiting_count,
    }


@st.cache_resource(show_spinner=False)
def ai_processing_gate():
    # The Mac remains responsive by running at most two heavy AI jobs together.
    return threading.BoundedSemaphore(2)


# ---------------------------------------
# PROPERTY INFORMATION
# Later, we can replace this with
# Venugopal's real property information.
# ---------------------------------------
PROPERTIES = [
    {
        "id": 1,
        "location": "RS Puram",
        "type": "2 BHK Apartment",
        "area": "1200 sq.ft",
        "price_lakhs": 65,
        "status": "Available",
        "description": "A well-connected city apartment close to daily essentials.",
        "image_url": None,
        "maps_url": None,
    },
    {
        "id": 2,
        "location": "Saravanampatti",
        "type": "3 BHK Apartment",
        "area": "1500 sq.ft",
        "price_lakhs": 82,
        "status": "Available",
        "description": "A spacious family apartment in a fast-growing neighbourhood.",
        "image_url": None,
        "maps_url": None,
    },
    {
        "id": 3,
        "location": "Vadavalli",
        "type": "Residential Plot",
        "area": "2400 sq.ft",
        "price_lakhs": 48,
        "status": "Available",
        "description": "A residential plot suited for building a future home.",
        "image_url": None,
        "maps_url": None,
    },
]


# ---------------------------------------
# FIND BUDGET FROM CUSTOMER QUESTION
# Example: "My budget is 50 lakhs"
# ---------------------------------------
def find_budget(question):
    pattern = (
        r"(\d+(?:\.\d+)?)\s*"
        r"(?:lakhs?|லட்சம்|லட்சங்கள்|லட்சத்தில்|லட்சத்திற்குள்|"
        r"லட்சத்திற்கு|லட்சத்துக்குள்)"
    )
    match = re.search(pattern, question.lower())

    if match:
        return float(match.group(1))

    return None


# ---------------------------------------
# FILTER PROPERTIES USING PYTHON
# Python performs the price calculation.
# ---------------------------------------
def filter_properties(question):
    question_lower = question.lower()
    matches = [
        property_item
        for property_item in PROPERTIES
        if property_item["status"] == "Available"
    ]

    budget = find_budget(question)

    if budget is not None:
        matches = [
            property_item
            for property_item in matches
            if property_item["price_lakhs"] <= budget
        ]

    location_aliases = {
        "RS Puram": ["rs puram", "ஆர்.எஸ். புரம்", "ஆர் எஸ் புரம்"],
        "Saravanampatti": ["saravanampatti", "சரவணம்பட்டி"],
        "Vadavalli": ["vadavalli", "வடவள்ளி"],
    }

    requested_locations = [
        location
        for location, aliases in location_aliases.items()
        if any(alias in question_lower for alias in aliases)
    ]

    if requested_locations:
        matches = [
            property_item
            for property_item in matches
            if property_item["location"] in requested_locations
        ]

    plot_terms = ["plot", "குடியிருப்பு மனை", "மனை", "வீட்டு மனை"]
    if any(term in question_lower for term in plot_terms):
        matches = [
            property_item
            for property_item in matches
            if "plot" in property_item["type"].lower()
        ]

    two_bedroom_terms = [
        "2 bhk",
        "2bhk",
        "2 படுக்கையறை",
        "இரண்டு படுக்கையறை",
    ]
    if any(term in question_lower for term in two_bedroom_terms):
        matches = [
            property_item
            for property_item in matches
            if "2 bhk" in property_item["type"].lower()
        ]

    three_bedroom_terms = [
        "3 bhk",
        "3bhk",
        "3 படுக்கையறை",
        "மூன்று படுக்கையறை",
    ]
    if any(term in question_lower for term in three_bedroom_terms):
        matches = [
            property_item
            for property_item in matches
            if "3 bhk" in property_item["type"].lower()
        ]

    return matches, budget


# ---------------------------------------
# LANGUAGE AND TAMIL RESPONSE HELPERS
# Tamil property answers are formatted in Python so that the wording remains
# clear and consistent even when the local language model is very small.
# ---------------------------------------
def is_tamil_question(question):
    return bool(re.search(r"[\u0B80-\u0BFF]", question))


def normalized_text(question):
    return re.sub(r"[^a-z0-9\u0B80-\u0BFF\s]", "", question.lower()).strip()


def detect_intent(question):
    """Separate social conversation from actual property searches."""
    text = normalized_text(question)

    property_terms = [
        "property", "properties", "house", "home", "apartment", "flat",
        "plot", "land", "bhk", "budget", "lakh", "price", "buy",
        "available", "location", "sqft", "square feet", "rs puram",
        "saravanampatti", "vadavalli", "சொத்து", "வீடு", "குடியிருப்பு",
        "மனை", "நிலம்", "விலை", "லட்ச", "வாங்க", "கிடைக்கும்",
        "இடம்", "படுக்கையறை", "ஆர் எஸ் புரம்", "சரவணம்பட்டி", "வடவள்ளி",
    ]

    # A message such as "Hi, I need a house" is a property request rather
    # than only a greeting, so property intent is checked first.
    if any(term in text for term in property_terms):
        return "property"

    exact_greetings = {
        "hi", "hello", "hey", "good morning", "good afternoon",
        "good evening", "வணக்கம்", "ஹாய்", "ஹலோ", "காலை வணக்கம்",
        "மதிய வணக்கம்", "மாலை வணக்கம்",
    }
    if text in exact_greetings:
        return "greeting"

    if any(
        text.startswith(term)
        for term in ["hi ", "hello ", "hey ", "ஹாய் ", "ஹலோ ", "வணக்கம் "]
    ):
        return "greeting"

    if any(term in text for term in ["thank", "thanks", "நன்றி", "ரொம்ப நன்றி"]):
        return "thanks"

    if any(
        term in text
        for term in ["bye", "goodbye", "see you", "பிறகு பார்க்கலாம்", "வருகிறேன்"]
    ):
        return "goodbye"

    if any(
        term in text
        for term in [
            "who are you", "your name", "what can you do",
            "நீங்கள் யார்", "உங்கள் பெயர்", "என்ன செய்ய முடியும்",
        ]
    ):
        return "identity"

    if text in {"help", "உதவி", "உதவி வேண்டும்"}:
        return "help"

    return "conversation"


def social_response(intent, tamil):
    responses = {
        "greeting": {
            True: (
                "வணக்கம்! உங்களைச் சந்திப்பதில் மகிழ்ச்சி. நான் வேணுகோபாலின் "
                "சொத்து ஆலோசனை உதவியாளர். வீடு, அடுக்குமாடிக் குடியிருப்பு "
                "அல்லது மனை குறித்து என்ன தெரிந்துகொள்ள விரும்புகிறீர்கள்?"
            ),
            False: (
                "Hello! Nice to meet you. I’m Venugopal’s property assistant. "
                "Are you looking for a house, an apartment, or a residential plot?"
            ),
        },
        "thanks": {
            True: (
                "மகிழ்ச்சி! உங்களுக்கு உதவியதில் சந்தோஷம். வேறு சொத்து பற்றிய "
                "விவரம் வேண்டுமென்றால் எப்போது வேண்டுமானாலும் கேளுங்கள்."
            ),
            False: (
                "You’re welcome! I’m glad I could help. Feel free to ask about "
                "another property whenever you like."
            ),
        },
        "goodbye": {
            True: "நன்றி! மீண்டும் சந்திப்போம். உங்கள் சொத்துத் தேடல் சிறப்பாக அமைய வாழ்த்துகள்.",
            False: "Thank you! See you again, and best wishes with your property search.",
        },
        "identity": {
            True: (
                "நான் வேணுகோபாலின் சொத்து ஆலோசனை உதவியாளர். கிடைக்கும் "
                "சொத்துகள், விலை, இடம், பரப்பளவு மற்றும் உங்கள் விலை வரம்பிற்குப் "
                "பொருத்தமான வாய்ப்புகள் பற்றித் தெரிவிக்க முடியும்."
            ),
            False: (
                "I’m Venugopal’s property assistant. I can help you explore "
                "available properties by budget, location, type, area, and price."
            ),
        },
        "help": {
            True: (
                "நிச்சயமாக உதவுகிறேன். “50 லட்சத்திற்குள் என்ன சொத்து கிடைக்கும்?”, "
                "“வடவள்ளியில் மனை உள்ளதா?” அல்லது “2 படுக்கையறை வீடு வேண்டும்” "
                "என்று கேட்கலாம்."
            ),
            False: (
                "Of course. Try asking: “What can I buy under 50 lakhs?”, "
                "“Is there a plot in Vadavalli?”, or “I need a 2 BHK apartment.”"
            ),
        },
    }
    return responses[intent][tamil]


def tamil_property_type(property_type):
    translations = {
        "2 BHK Apartment": "2 படுக்கையறைகள் கொண்ட அடுக்குமாடிக் குடியிருப்பு",
        "3 BHK Apartment": "3 படுக்கையறைகள் கொண்ட அடுக்குமாடிக் குடியிருப்பு",
        "Residential Plot": "குடியிருப்பு மனை",
    }
    return translations.get(property_type, property_type)


def tamil_location(location):
    translations = {
        "RS Puram": "ஆர்.எஸ். புரம்",
        "Saravanampatti": "சரவணம்பட்டி",
        "Vadavalli": "வடவள்ளி",
    }
    return translations.get(location, location)


def tamil_status(status):
    return "விற்பனைக்குக் கிடைக்கிறது" if status == "Available" else status


def tamil_area(area):
    return area.replace("sq.ft", "சதுர அடி")


def format_tamil_answer(matching_properties, budget):
    if not matching_properties:
        if budget is not None:
            return (
                f"உங்கள் ₹{budget:g} லட்சம் விலை வரம்பிற்குள், நீங்கள் கேட்ட "
                "வகையிலான சொத்து தற்போது எதுவும் கிடைக்கவில்லை. புதிய "
                "வாய்ப்புகளை அறிய வேணுகோபாலைத் தொடர்புகொள்ளலாம்."
            )

        return (
            "நீங்கள் கேட்ட விவரங்களுக்குப் பொருத்தமான சொத்து தற்போது "
            "கிடைக்கவில்லை. வேறு இடம், விலை வரம்பு அல்லது சொத்து வகையைக் "
            "குறிப்பிட்டு மீண்டும் கேளுங்கள்."
        )

    if len(matching_properties) == 1:
        if budget is not None:
            introduction = (
                f"உங்கள் ₹{budget:g} லட்சம் விலை வரம்பிற்குள் பொருந்தும் "
                "சொத்து இதோ:"
            )
        else:
            introduction = "உங்கள் தேவைக்குப் பொருந்தும் சொத்து இதோ:"
    else:
        if budget is not None:
            introduction = (
                f"உங்கள் ₹{budget:g} லட்சம் விலை வரம்பிற்குள் "
                f"{len(matching_properties)} சொத்துகள் கிடைக்கின்றன:"
            )
        else:
            introduction = (
                f"உங்கள் தேவைக்குப் பொருந்தும் {len(matching_properties)} "
                "சொத்துகள் கிடைக்கின்றன:"
            )

    property_sections = []
    for index, property_item in enumerate(matching_properties, start=1):
        property_sections.append(
            "\n".join(
                [
                    f"**{index}. {tamil_location(property_item['location'])}**",
                    f"- சொத்து வகை: {tamil_property_type(property_item['type'])}",
                    f"- பரப்பளவு: {tamil_area(property_item['area'])}",
                    f"- விலை: ₹{property_item['price_lakhs']} லட்சம்",
                    f"- நிலவரம்: {tamil_status(property_item['status'])}",
                ]
            )
        )

    conclusion = (
        "\n\nமேலும் விவரங்கள் அல்லது பார்வையிடும் நேரம் குறித்து அறிய "
        "வேணுகோபாலைத் தொடர்புகொள்ளலாம்."
    )
    return introduction + "\n\n" + "\n\n".join(property_sections) + conclusion


# ---------------------------------------
# CLOUD + LOCAL AI CONFIGURATION
# Streamlit Community Cloud uses a Gemini API key stored in Streamlit Secrets.
# Local development can continue to use Ollama and faster-whisper as fallbacks.
# ---------------------------------------
def gemini_api_key():
    try:
        secret_key = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        secret_key = None

    return secret_key or os.environ.get("GEMINI_API_KEY")


@st.cache_resource(show_spinner=False)
def gemini_client():
    api_key = gemini_api_key()
    if not api_key:
        return None

    from google import genai

    return genai.Client(api_key=api_key)


@st.cache_resource(show_spinner=False)
def load_speech_model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        "small",
        device="cpu",
        compute_type="int8",
    )


def transcribe_audio(audio_file, language_code):
    client = gemini_client()
    if client is not None:
        from google.genai import types

        mime_type = getattr(audio_file, "type", None) or "audio/wav"
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=[
                (
                    "இந்தக் குரல் பதிவில் பேசப்பட்டதை மட்டும் துல்லியமாக "
                    "எழுத்தாக மாற்றவும். பேசிய மொழியையே பயன்படுத்தவும். "
                    "விளக்கம் அல்லது கூடுதல் பதில் எழுத வேண்டாம்."
                ),
                types.Part.from_bytes(
                    data=audio_file.getvalue(),
                    mime_type=mime_type,
                ),
            ],
        )
        return (response.text or "").strip()

    # Local fallback for development on the owner's Mac.
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
        ) as temporary_file:
            temporary_file.write(audio_file.getvalue())
            temporary_path = temporary_file.name

        model = load_speech_model()
        segments, _ = model.transcribe(
            temporary_path,
            language=language_code,
            beam_size=5,
            vad_filter=True,
        )
        transcript = " ".join(
            segment.text.strip()
            for segment in segments
            if segment.text.strip()
        )
        return transcript.strip()

    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


# ---------------------------------------
# SEND QUESTION TO CLOUD GEMINI OR LOCAL OLLAMA
# ---------------------------------------
def call_qwen(prompt, tamil=False):
    client = gemini_client()
    if client is not None:
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
            )
            return (response.text or "").strip()
        except Exception:
            if tamil:
                return (
                    "செயற்கை நுண்ணறிவு சேவையில் தற்காலிக தாமதம் உள்ளது. "
                    "சிறிது நேரம் கழித்து மீண்டும் முயற்சி செய்யுங்கள்."
                )
            return "The AI service is temporarily busy. Please try again shortly."

    # Local fallback for development on the owner's Mac.
    request_data = json.dumps(
        {
            "model": "qwen2.5:3b",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.2
            },
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["response"].strip()

    except urllib.error.URLError:
        if tamil:
            return (
                "உள்ளூர் செயற்கை நுண்ணறிவு சேவையுடன் தற்போது இணைய முடியவில்லை. "
                "சிறிது நேரம் கழித்து மீண்டும் முயற்சி செய்யுங்கள்."
            )
        return "I could not connect to the local AI. Please try again shortly."

    except Exception as error:
        if tamil:
            return "பதில் உருவாக்கும்போது சிக்கல் ஏற்பட்டது. மீண்டும் முயற்சி செய்யுங்கள்."
        return f"I ran into an error while preparing the answer: {error}"


def conversation_prompt(question, chat_history, tamil):
    recent_messages = chat_history[-6:]
    conversation = "\n".join(
        f"{message['role']}: {message['content']}"
        for message in recent_messages
    )

    if tamil:
        language_instruction = """
Reply in natural, contemporary Tamil that ordinary people can easily understand.
Do not translate English sentence structures literally.
Avoid unnecessary English words. Keep common real-estate terms such as 2 BHK
only when they improve clarity. Use respectful, warm wording.
"""
    else:
        language_instruction = "Reply in clear, friendly, natural English."

    return f"""
You are Venugopal's friendly and professional property assistant in Coimbatore.
Speak like a helpful human, not like a form or a search engine.
You may have brief, friendly small talk, but your main expertise is property.
Do not invent property listings, prices, contact details, or availability.
If the user asks an unrelated factual question, answer briefly when safe and
gently explain that your main role is property assistance.
Keep the response concise, normally two to four sentences.

{language_instruction}

Recent conversation:
{conversation}

Current customer message:
{question}
"""


def ask_qwen(question, matching_properties, budget, chat_history):
    tamil = is_tamil_question(question)
    intent = detect_intent(question)

    if intent in {"greeting", "thanks", "goodbye", "identity", "help"}:
        return social_response(intent, tamil)

    if intent == "conversation":
        prompt = conversation_prompt(question, chat_history, tamil)
        return call_qwen(prompt, tamil=tamil)

    # From this point onward, the message is a genuine property request.
    if tamil:
        return format_tamil_answer(matching_properties, budget)

    if not matching_properties:
        if budget is not None:
            return (
                f"I couldn’t find an available property within ₹{budget:g} "
                "lakhs that matches your request. Would you like to try a "
                "different budget, location, or property type?"
            )

        return (
            "I couldn’t find an available property matching that request. "
            "Would you like to try a different location or property type?"
        )

    property_information = json.dumps(
        matching_properties,
        indent=2,
        ensure_ascii=False,
    )

    prompt = f"""
You are Venugopal's friendly and professional property assistant.
Answer in natural English and speak like a helpful human.
Python has already filtered the listings correctly.
Use ONLY the matching property information below.
Never invent a property, location, price, contact detail, or availability.
Answer the customer's exact question first. Then present the matching details
clearly. End with one useful follow-up question. Keep the response concise.

Matching property information:
{property_information}

Customer question:
{question}

Include the location, property type, area, price, and availability for every
property you recommend.
"""
    return call_qwen(prompt, tamil=False)


# ---------------------------------------
# PROPERTY QUEST GAMIFICATION
# Points are session-only engagement markers and have no monetary value.
# ---------------------------------------
QUEST_MISSIONS = {
    "first_message": ("First Step", "Send your first message", 10),
    "set_budget": ("Budget Ready", "Tell us your price range", 20),
    "choose_location": ("Area Scout", "Choose a preferred location", 20),
    "choose_type": ("Home Style", "Choose a property type", 20),
    "first_match": ("Match Found", "Discover a matching property", 15),
    "use_voice": ("Voice Explorer", "Ask a question by voice", 15),
    "shortlist": ("Shortlist Star", "Save your first property", 25),
}


def initialise_quest():
    defaults = {
        "quest_xp": 0,
        "completed_missions": set(),
        "discovered_properties": set(),
        "shortlist": set(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def complete_mission(mission_key):
    if mission_key in st.session_state.completed_missions:
        return 0

    st.session_state.completed_missions.add(mission_key)
    points = QUEST_MISSIONS[mission_key][2]
    st.session_state.quest_xp += points
    return points


def quest_level(xp):
    levels = [
        (0, "New Explorer", "🧭"),
        (60, "Neighbourhood Scout", "🗺️"),
        (140, "Property Pathfinder", "🏠"),
        (240, "Home Quest Pro", "🏆"),
    ]

    current_index = 0
    for index, (threshold, _, _) in enumerate(levels):
        if xp >= threshold:
            current_index = index

    current_threshold, name, icon = levels[current_index]

    if current_index == len(levels) - 1:
        return name, icon, 1.0, None

    next_threshold = levels[current_index + 1][0]
    progress = (xp - current_threshold) / (next_threshold - current_threshold)
    return name, icon, max(0.0, min(progress, 1.0)), next_threshold


def has_location_preference(question):
    text = normalized_text(question)
    return any(
        term in text
        for term in [
            "rs puram", "saravanampatti", "vadavalli",
            "ஆர் எஸ் புரம்", "சரவணம்பட்டி", "வடவள்ளி",
        ]
    )


def has_property_type_preference(question):
    text = normalized_text(question)
    return any(
        term in text
        for term in [
            "2 bhk", "2bhk", "3 bhk", "3bhk", "apartment", "flat",
            "plot", "land", "house", "home", "படுக்கையறை", "குடியிருப்பு",
            "மனை", "நிலம்", "வீடு",
        ]
    )


def update_quest_progress(question, matching_properties, used_voice=False):
    earned = complete_mission("first_message")

    if find_budget(question) is not None:
        earned += complete_mission("set_budget")

    if has_location_preference(question):
        earned += complete_mission("choose_location")

    if has_property_type_preference(question):
        earned += complete_mission("choose_type")

    if used_voice:
        earned += complete_mission("use_voice")

    if detect_intent(question) == "property" and matching_properties:
        earned += complete_mission("first_match")
        for property_item in matching_properties:
            st.session_state.discovered_properties.add(property_item["id"])

    return earned


def add_to_shortlist(property_id):
    if property_id in st.session_state.shortlist:
        return False

    st.session_state.shortlist.add(property_id)
    complete_mission("shortlist")
    return True


initialise_quest()


# ---------------------------------------
# APP DESIGN
# ---------------------------------------
st.markdown(
    """
    <style>
        :root {
            color-scheme: light;
            --navy: #0B1739;
            --blue: #155EEF;
            --blue-soft: #EAF2FF;
            --green: #087A55;
            --violet: #6D28D9;
            --amber: #F59E0B;
            --ink: #101828;
            --muted: #475467;
            --line: #B9C3D0;
            --page: #F4F7FB;
            --white: #FFFFFF;
        }

        html, body, .stApp {
            font-size: 17px;
        }

        .stApp {
            background:
                radial-gradient(
                    circle at 90% 5%,
                    rgba(21, 94, 239, 0.10),
                    transparent 30rem
                ),
                var(--page);
            color: var(--ink);
        }

        .block-container {
            max-width: 1100px;
            padding-top: 1.75rem;
            padding-bottom: 8rem;
        }

        .stApp p,
        .stApp li,
        .stApp label,
        .stApp span {
            line-height: 1.65;
        }

        .brand {
            display: inline-flex;
            align-items: center;
            gap: 0.55rem;
            color: #D6E4FF;
            font-size: 0.8rem;
            font-weight: 850;
            letter-spacing: 0.14rem;
        }

        .brand::before {
            content: "";
            width: 0.65rem;
            height: 0.65rem;
            border-radius: 50%;
            background: #5FF2B6;
            box-shadow: 0 0 0 5px rgba(95, 242, 182, 0.16);
        }

        .hero {
            padding: 2.35rem;
            margin-bottom: 1.25rem;
            border: 1px solid #26355F;
            border-radius: 22px;
            background:
                linear-gradient(
                    125deg,
                    #071127 0%,
                    #142755 48%,
                    #4C1D95 100%
                );
            box-shadow: 0 18px 55px rgba(11, 23, 57, 0.16);
        }

        .hero h1 {
            margin: 0.65rem 0 0.7rem;
            color: #FFFFFF !important;
            font-size: clamp(2.35rem, 5vw, 3.8rem);
            line-height: 1.04;
            letter-spacing: -0.055em;
        }

        .hero p {
            max-width: 720px;
            margin: 0;
            color: #E5ECF8 !important;
            font-size: 1.05rem;
            line-height: 1.75;
        }

        .trust-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.65rem;
            margin-top: 1.3rem;
        }

        .trust-row span {
            padding: 0.42rem 0.7rem;
            border: 1px solid #52658F;
            border-radius: 999px;
            color: #FFFFFF !important;
            background: rgba(255, 255, 255, 0.07);
            font-size: 0.78rem;
            font-weight: 700;
        }

        [data-testid="stChatMessage"] {
            margin-bottom: 0.8rem;
            padding: 1rem 1.1rem;
            border: 1.5px solid var(--line);
            border-radius: 16px;
            background: var(--white);
            box-shadow: 0 6px 22px rgba(16, 24, 40, 0.06);
        }

        [data-testid="stChatMessage"] p,
        [data-testid="stChatMessage"] li,
        [data-testid="stChatMessage"] span {
            color: var(--ink) !important;
            font-size: 1rem !important;
            line-height: 1.75 !important;
        }

        [data-testid="stChatMessageAvatarUser"] {
            background: var(--navy) !important;
        }

        [data-testid="stChatMessageAvatarAssistant"] {
            background: var(--blue) !important;
        }

        [data-testid="stBottomBlockContainer"] {
            background: rgba(244, 247, 251, 0.96) !important;
            backdrop-filter: blur(10px);
        }

        [data-testid="stChatInput"] {
            min-height: 58px;
            border: 2px solid #667085 !important;
            border-radius: 14px !important;
            background: #FFFFFF !important;
            box-shadow: 0 8px 28px rgba(16, 24, 40, 0.12);
        }

        [data-testid="stChatInput"]:focus-within {
            border-color: var(--blue) !important;
            box-shadow: 0 0 0 4px rgba(21, 94, 239, 0.20) !important;
        }

        [data-testid="stChatInput"] textarea {
            color: #101828 !important;
            background: #FFFFFF !important;
            -webkit-text-fill-color: #101828 !important;
            caret-color: var(--blue) !important;
            font-size: 1rem !important;
        }

        [data-testid="stChatInput"] textarea::placeholder {
            color: #475467 !important;
            opacity: 1 !important;
        }

        [data-testid="stChatInputSubmitButton"] {
            min-width: 44px !important;
            min-height: 44px !important;
            color: #FFFFFF !important;
            background: var(--blue) !important;
            border-radius: 10px !important;
        }

        [data-testid="stSidebar"] {
            background: var(--navy) !important;
            border-right: 1px solid #26355F;
        }

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label {
            color: #FFFFFF !important;
        }

        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
            color: #CFD8EA !important;
            font-size: 0.9rem !important;
        }

        .property-card {
            padding: 1rem 1.05rem;
            margin-bottom: 0.75rem;
            border: 1px solid #52658F;
            border-radius: 12px;
            color: #F7F9FC;
            background: #142755;
            line-height: 1.7;
        }

        .property-card strong {
            display: block;
            margin-bottom: 0.15rem;
            color: #8EC5FF;
            font-size: 1.05rem;
        }

        [data-testid="stSidebar"] [data-testid="stAlert"] {
            border: 1px solid #68D6AE;
            background: #103D35 !important;
        }

        [data-testid="stSidebar"] [data-testid="stAlert"] p {
            color: #E9FFF7 !important;
        }

        button, [role="button"], textarea {
            outline-offset: 3px;
        }

        button:focus-visible,
        [role="button"]:focus-visible,
        textarea:focus-visible {
            outline: 3px solid #FDB022 !important;
        }

        .access-note {
            margin: 0.5rem 0 1.25rem;
            padding: 0.85rem 1rem;
            border-left: 4px solid var(--green);
            color: #344054;
            background: #ECFDF3;
            font-size: 0.9rem;
            line-height: 1.6;
        }

        .quest-strip {
            display: grid;
            grid-template-columns: 1.25fr 0.75fr 0.75fr;
            gap: 0.8rem;
            margin: 0 0 1.25rem;
        }

        .quest-stat {
            min-height: 92px;
            padding: 1rem 1.1rem;
            border: 1px solid #D0D5DD;
            border-radius: 16px;
            color: var(--ink);
            background: #FFFFFF;
            box-shadow: 0 8px 24px rgba(16, 24, 40, 0.06);
        }

        .quest-stat.primary {
            color: #FFFFFF;
            border-color: #5B21B6;
            background: linear-gradient(135deg, #6D28D9, #4338CA);
        }

        .quest-stat small {
            display: block;
            color: #667085;
            font-size: 0.74rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .quest-stat.primary small {
            color: #EDE9FE;
        }

        .quest-stat strong {
            display: block;
            margin-top: 0.3rem;
            font-size: 1.28rem;
            line-height: 1.25;
        }

        .section-kicker {
            margin: 1.4rem 0 0.1rem;
            color: var(--violet);
            font-size: 0.78rem;
            font-weight: 900;
            letter-spacing: 0.12em;
            text-transform: uppercase;
        }

        .property-visual {
            display: grid;
            place-items: center;
            min-height: 126px;
            margin: -0.2rem -0.2rem 0.9rem;
            border-radius: 13px;
            color: #FFFFFF;
            background:
                radial-gradient(circle at 80% 20%, rgba(255,255,255,.25), transparent 28%),
                linear-gradient(135deg, #142755, #6D28D9);
            font-size: 2.8rem;
        }

        .property-price {
            color: #4C1D95;
            font-size: 1.15rem;
            font-weight: 900;
        }

        .property-meta {
            color: #475467;
            font-size: 0.86rem;
            line-height: 1.55;
        }

        .mission-card {
            margin: 0.55rem 0;
            padding: 0.7rem 0.8rem;
            border: 1px solid #52658F;
            border-radius: 11px;
            color: #EEF4FF;
            background: #142755;
            font-size: 0.83rem;
        }

        .mission-card.done {
            border-color: #45D6A4;
            background: #103D35;
        }

        .badge-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin: 0.6rem 0 1rem;
        }

        .badge-chip {
            padding: 0.32rem 0.55rem;
            border: 1px solid #52658F;
            border-radius: 999px;
            color: #D0D5DD !important;
            background: #111F43;
            font-size: 0.72rem;
            font-weight: 750;
        }

        .badge-chip.earned {
            color: #071127 !important;
            border-color: #F8D56B;
            background: #FDE68A;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            border-color: #D0D5DD !important;
            border-radius: 17px !important;
            background: #FFFFFF;
            box-shadow: 0 8px 24px rgba(16, 24, 40, 0.05);
        }

        [data-testid="stChatInputAudioButton"] {
            min-width: 44px !important;
            min-height: 44px !important;
            color: var(--blue) !important;
            border-radius: 10px !important;
        }

        .capacity-banner {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            margin: 0 0 1rem;
            padding: 0.75rem 1rem;
            border: 1px solid #A6F4C5;
            border-radius: 13px;
            color: #054F31;
            background: #ECFDF3;
            font-size: 0.9rem;
            font-weight: 750;
        }

        .waiting-room {
            max-width: 720px;
            margin: 8vh auto 1.5rem;
            padding: 2.2rem;
            border: 1px solid #C7D7FE;
            border-radius: 24px;
            color: var(--ink);
            background: #FFFFFF;
            box-shadow: 0 22px 65px rgba(16, 24, 40, 0.14);
            text-align: center;
        }

        .waiting-icon {
            display: grid;
            place-items: center;
            width: 88px;
            height: 88px;
            margin: 0 auto 1rem;
            border-radius: 24px;
            background: linear-gradient(135deg, #EAF2FF, #EDE9FE);
            font-size: 2.8rem;
        }

        .waiting-room h1 {
            margin: 0.25rem 0 0.65rem;
            color: var(--navy) !important;
            font-size: clamp(1.8rem, 5vw, 2.6rem);
            letter-spacing: -0.035em;
        }

        .waiting-room p {
            margin: 0.4rem auto;
            color: var(--muted) !important;
            font-size: 1rem;
        }

        .token-number {
            display: inline-block;
            min-width: 170px;
            margin: 1.15rem 0;
            padding: 0.9rem 1.2rem;
            border-radius: 16px;
            color: #FFFFFF;
            background: linear-gradient(135deg, #155EEF, #6D28D9);
            font-size: 1.55rem;
            font-weight: 900;
        }

        .queue-pulse {
            display: inline-block;
            width: 0.7rem;
            height: 0.7rem;
            margin-right: 0.35rem;
            border-radius: 50%;
            background: #12B76A;
            animation: queuePulse 1.8s ease-in-out infinite;
        }

        @keyframes queuePulse {
            0%, 100% { opacity: 0.35; transform: scale(0.85); }
            50% { opacity: 1; transform: scale(1.08); }
        }

        @media (max-width: 760px) {
            .block-container {
                padding: 1rem 0.85rem 7rem;
            }

            .hero {
                padding: 1.5rem 1.2rem;
            }

            .hero h1 {
                font-size: 2.25rem;
            }

            .quest-strip {
                grid-template-columns: 1fr;
            }
        }

        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                scroll-behavior: auto !important;
                transition: none !important;
                animation: none !important;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------
# LIVE CAPACITY GATE
# Waiting customers see only this lightweight screen. Their token moves
# automatically as inactive or completed browser sessions leave the queue.
# ---------------------------------------
queue_status = touch_customer_session()


if queue_status["state"] in {"waiting", "full"}:
    @st.fragment(run_every=5)
    def show_waiting_room():
        live_status = touch_customer_session()

        if live_status["state"] == "active":
            st.rerun(scope="app")

        if live_status["state"] == "full":
            st.markdown(
                """
                <div class="waiting-room" role="status" aria-live="polite">
                    <div class="waiting-icon">🏡</div>
                    <h1>தற்போது அனைத்து இடங்களும் நிரம்பியுள்ளன</h1>
                    <p>
                        5 வாடிக்கையாளர்கள் சேவையைப் பயன்படுத்துகின்றனர்;
                        20 பேர் காத்திருப்பு வரிசையில் உள்ளனர்.
                    </p>
                    <div class="token-number">வரிசை நிரம்பியுள்ளது</div>
                    <p>
                        சிறிது நேரம் கழித்து இந்தப் பக்கத்தை மீண்டும் திறக்கவும்.
                        உங்கள் நேரத்திற்கும் புரிதலுக்கும் நன்றி.
                    </p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            return

        token = live_status["token"]
        people_ahead = max(0, token - 1)
        minimum_wait = max(2, token * 2)
        maximum_wait = max(5, token * 5)
        st.markdown(
            f"""
            <div class="waiting-room" role="status" aria-live="polite">
                <div class="waiting-icon">⏳</div>
                <h1>நீங்கள் காத்திருப்பு அறையில் உள்ளீர்கள்</h1>
                <p>
                    தற்போது 5 வாடிக்கையாளர்கள் சேவையைப் பயன்படுத்துகின்றனர்.
                    உங்கள் இடத்தை நாங்கள் பாதுகாப்பாக வைத்திருக்கிறோம்.
                </p>
                <div class="token-number">Token {token}</div>
                <p><strong>உங்களுக்கு முன் {people_ahead} பேர் காத்திருக்கின்றனர்.</strong></p>
                <p>
                    தோராயமான காத்திருப்பு நேரம்: {minimum_wait}–{maximum_wait} நிமிடங்கள்.
                    இந்தப் பக்கத்தை மூடாமல் காத்திருக்கவும்.
                </p>
                <p><span class="queue-pulse"></span> நிலை தானாகப் புதுப்பிக்கப்படுகிறது</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    show_waiting_room()
    st.stop()


@st.fragment(run_every=15)
def keep_active_customer_alive():
    live_status = touch_customer_session()
    if live_status["state"] != "active":
        st.rerun(scope="app")
    st.markdown(
        f"""
        <div class="capacity-banner" role="status">
            <span>🟢 சேவை செயல்பாட்டில் உள்ளது</span>
            <span>{live_status['active_count']} / {MAX_ACTIVE_CUSTOMERS} இடங்கள் பயன்பாட்டில்</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


keep_active_customer_alive()


# ---------------------------------------
# HEADER
# ---------------------------------------
current_xp = st.session_state.quest_xp
level_name, level_icon, level_progress, next_level_xp = quest_level(current_xp)
completed_count = len(st.session_state.completed_missions)

st.markdown(
    f"""
    <div class="hero">
        <div class="brand">AISHWARYAM REALTY · PROPERTY QUEST</div>
        <h1>Turn your property search into a quest.</h1>
        <p>
            Explore verified properties, unlock discovery badges and build
            your shortlist with Venugopal's local property assistant.
        </p>
        <div class="trust-row">
            <span>✓ Verified property data</span>
            <span>✓ Tamil + English</span>
            <span>🎮 {current_xp} Discovery Points</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="quest-strip">
        <div class="quest-stat primary">
            <small>Current level</small>
            <strong>{level_icon} {level_name}</strong>
        </div>
        <div class="quest-stat">
            <small>Properties discovered</small>
            <strong>{len(st.session_state.discovered_properties)} / {len(PROPERTIES)}</strong>
        </div>
        <div class="quest-stat">
            <small>Your shortlist</small>
            <strong>⭐ {len(st.session_state.shortlist)}</strong>
        </div>
    </div>

    <div class="access-note">
        <strong>Your first quest:</strong> Tell the assistant your budget,
        preferred location or property type. Discovery Points are engagement
        markers only and have no monetary value.
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------
# SIDEBAR PROPERTY LIST
# ---------------------------------------
with st.sidebar:
    st.title("🏡 Aishwaryam Realty")
    st.caption("Venugopal · Your property guide")
    st.markdown("**Profile photo placeholder** — Venugopal's photo will appear here.")

    st.subheader(f"{level_icon} {level_name}")
    if next_level_xp:
        st.progress(
            level_progress,
            text=f"{current_xp} XP · Next level at {next_level_xp} XP",
        )
    else:
        st.progress(1.0, text=f"{current_xp} XP · Highest level unlocked")

    st.caption(f"{completed_count} of {len(QUEST_MISSIONS)} achievements unlocked")

    badge_html = []
    for mission_key, (title, _, _) in QUEST_MISSIONS.items():
        earned_class = (
            "badge-chip earned"
            if mission_key in st.session_state.completed_missions
            else "badge-chip"
        )
        badge_html.append(
            f'<span class="{earned_class}">{"✓" if mission_key in st.session_state.completed_missions else "○"} {title}</span>'
        )
    st.markdown(
        '<div class="badge-row">' + "".join(badge_html) + "</div>",
        unsafe_allow_html=True,
    )

    st.subheader("🎯 Quest missions")
    for mission_key in [
        "set_budget",
        "choose_location",
        "choose_type",
        "shortlist",
    ]:
        title, description, points = QUEST_MISSIONS[mission_key]
        is_done = mission_key in st.session_state.completed_missions
        st.markdown(
            f"""
            <div class="mission-card {'done' if is_done else ''}">
                <strong>{'✓' if is_done else '○'} {title}</strong><br>
                {description} · +{points} XP
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.subheader("Available properties")
    st.caption("Current sample catalogue")

    for property_item in PROPERTIES:
        st.markdown(
            f"""
            <div class="property-card">
                <strong>{property_item["location"]}</strong><br>
                {property_item["type"]}<br>
                {property_item["area"]}<br>
                ₹{property_item["price_lakhs"]} lakhs
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.info(
        "Google Sheets, Venugopal's photo and real property images will be "
        "connected in the next data phase."
    )


# ---------------------------------------
# PROPERTY QUEST BOARD
# ---------------------------------------
st.markdown('<div class="section-kicker">Explore and collect</div>', unsafe_allow_html=True)
st.subheader("🗺️ Property Quest Board")
st.caption("Open a property path, compare the details and save your favourites.")

property_columns = st.columns(3)

for column, property_item in zip(property_columns, PROPERTIES):
    with column:
        with st.container(border=True):
            visual_icon = "🏗️" if "Plot" in property_item["type"] else "🏢"
            st.markdown(
                f'<div class="property-visual">{visual_icon}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(f"### {property_item['location']}")
            st.markdown(
                f'<div class="property-price">₹{property_item["price_lakhs"]} lakhs</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f"""
                <div class="property-meta">
                    {property_item['type']} · {property_item['area']}<br>
                    {property_item['description']}
                </div>
                """,
                unsafe_allow_html=True,
            )

            is_saved = property_item["id"] in st.session_state.shortlist
            button_label = "✓ Shortlisted" if is_saved else "⭐ Add to shortlist"
            if st.button(
                button_label,
                key=f"shortlist_{property_item['id']}",
                disabled=is_saved,
                use_container_width=True,
            ):
                if add_to_shortlist(property_item["id"]):
                    st.toast("Property added to your shortlist! +25 XP", icon="🏆")
                    st.rerun()

            if property_item["maps_url"]:
                st.link_button(
                    "📍 Open in Maps",
                    property_item["maps_url"],
                    use_container_width=True,
                )
            else:
                st.caption("📍 Google Maps link will appear here")

if st.session_state.shortlist:
    saved_locations = [
        property_item["location"]
        for property_item in PROPERTIES
        if property_item["id"] in st.session_state.shortlist
    ]
    st.success("⭐ Your shortlist: " + ", ".join(saved_locations))

st.divider()
st.markdown('<div class="section-kicker">Talk to your guide</div>', unsafe_allow_html=True)
st.subheader("💬 Ask Venugopal's Property Guide")


# ---------------------------------------
# CHAT HISTORY
# ---------------------------------------
welcome_message = (
    "வணக்கம்! நான் வேணுகோபாலின் சொத்து ஆலோசனை உதவியாளர். "
    "உங்கள் சொத்துத் தேடல் பயணத்தைத் தொடங்கலாமா? உங்கள் விலை வரம்பு, "
    "விருப்பமான இடம் அல்லது நீங்கள் தேடும் சொத்து வகையைச் சொல்லுங்கள். "
    "ஒவ்வொரு படியிலும் புதிய சாதனைகளைப் பெறலாம்!"
)

if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": welcome_message,
        }
    ]
elif (
    st.session_state.messages
    and st.session_state.messages[0]["role"] == "assistant"
    and st.session_state.messages[0]["content"].startswith("வணக்கம்!")
):
    st.session_state.messages[0]["content"] = welcome_message


for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


# ---------------------------------------
# CUSTOMER CHAT
# ---------------------------------------
chat_submission = st.chat_input(
    "தட்டச்சு செய்யவும் அல்லது மைக்கை அழுத்திப் பேசவும்",
    accept_audio=True,
    audio_sample_rate=16000,
    submit_mode="disable",
)

typed_question = ""
recorded_audio = None
voice_question = None

if chat_submission:
    typed_question = chat_submission.text.strip()
    recorded_audio = chat_submission.audio

if recorded_audio:
    processing_slot = ai_processing_gate().acquire(timeout=120)
    if not processing_slot:
        st.warning(
            "தற்போது மற்ற வாடிக்கையாளர்களின் கோரிக்கைகள் செயலாக்கப்படுகின்றன. "
            "சிறிது நேரம் கழித்து குரல் பதிவை மீண்டும் முயற்சி செய்யுங்கள்."
        )
    else:
        try:
            with st.spinner("உங்கள் பேச்சு எழுத்தாக மாற்றப்படுகிறது..."):
                # None lets Whisper automatically detect Tamil or English.
                voice_question = transcribe_audio(recorded_audio, None)

            if voice_question:
                st.success(f"நீங்கள் கூறியது: {voice_question}")
            else:
                st.warning(
                    "குரல் தெளிவாகப் பதிவாகவில்லை. மீண்டும் முயற்சி செய்யுங்கள்."
                )

        except Exception:
            st.error(
                "குரலை எழுத்தாக மாற்ற முடியவில்லை. சிறிது நேரம் கழித்து "
                "மீண்டும் முயற்சி செய்யுங்கள்."
            )
        finally:
            ai_processing_gate().release()

customer_question = " ".join(
    part for part in [typed_question, voice_question] if part
).strip()

if customer_question:
    st.session_state.messages.append(
        {
            "role": "user",
            "content": customer_question,
        }
    )

    with st.chat_message("user"):
        st.markdown(customer_question)

    matching_properties, customer_budget = filter_properties(
        customer_question
    )

    earned_points = update_quest_progress(
        customer_question,
        matching_properties,
        used_voice=recorded_audio is not None,
    )

    with st.chat_message("assistant"):
        with st.spinner("Searching available properties..."):
            processing_slot = ai_processing_gate().acquire(timeout=120)
            if processing_slot:
                try:
                    answer = ask_qwen(
                        customer_question,
                        matching_properties,
                        customer_budget,
                        st.session_state.messages,
                    )
                finally:
                    ai_processing_gate().release()
            else:
                answer = (
                    "தற்போது மற்ற வாடிக்கையாளர்களின் கோரிக்கைகள் "
                    "செயலாக்கப்படுகின்றன. சிறிது நேரம் கழித்து மீண்டும் "
                    "முயற்சி செய்யுங்கள்."
                    if is_tamil_question(customer_question)
                    else "Other customer requests are being processed. "
                    "Please try again shortly."
                )

        if earned_points:
            if is_tamil_question(customer_question):
                answer += (
                    f"\n\n🏆 **+{earned_points} Discovery Points** — "
                    "உங்கள் சொத்துத் தேடல் பயணம் முன்னேறுகிறது!"
                )
            else:
                answer += (
                    f"\n\n🏆 **+{earned_points} Discovery Points** — "
                    "your property quest is moving forward!"
                )

        st.markdown(answer)

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer,
        }
    )

    st.rerun()
