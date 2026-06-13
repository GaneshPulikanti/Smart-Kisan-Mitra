from flask import Flask, request, jsonify, render_template
import requests
import random
import time
import os
import json
import datetime
import base64
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from contextlib import contextmanager

# Load environment configuration
load_dotenv()

app = Flask(__name__)

# --- SQLite Database Setup ---
DATABASE_URL = "sqlite:///kisan_mitra.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Profile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), default="Ramesh Kumar")
    location = Column(String(150), default="Atmakur, Andhra Pradesh")
    crops = Column(String(200), default="Rice, Cotton")
    land_area = Column(Float, default=5.0)
    language = Column(String(50), default="English")

class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True, index=True)
    category = Column(String(50))
    amount = Column(Float)
    date = Column(DateTime, default=datetime.datetime.utcnow)

class CommunityPost(Base):
    __tablename__ = "community_posts"
    id = Column(Integer, primary_key=True, index=True)
    user = Column(String(100))
    text = Column(Text)
    time_str = Column(String(50), default="Just now")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class MandiPriceCache(Base):
    __tablename__ = "mandi_price_cache"
    id = Column(Integer, primary_key=True, index=True)
    crop = Column(String(50))
    state = Column(String(100))
    market = Column(String(150))
    min_price = Column(Float)
    max_price = Column(Float)
    trend = Column(String(10))
    fetched_at = Column(DateTime, default=datetime.datetime.utcnow)

# Create tables
Base.metadata.create_all(bind=engine)

@contextmanager
def db_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def seed_database():
    with db_session() as db:
        # Initial profile seed
        profile = db.query(Profile).first()
        if not profile:
            profile = Profile(
                name="Ramesh Kumar",
                location="Atmakur, Andhra Pradesh",
                crops="Rice, Cotton",
                land_area=5.0,
                language="English"
            )
            db.add(profile)
            
        # Initial community posts seed
        post_count = db.query(CommunityPost).count()
        if post_count == 0:
            posts = [
                CommunityPost(
                    user="Suresh N.", 
                    text="Has anyone noticed the whiteflies on the cotton crop recently?", 
                    created_at=datetime.datetime.utcnow() - datetime.timedelta(hours=1)
                ),
                CommunityPost(
                    user="Agri Expert", 
                    text="Unseasonal rains expected tomorrow. Please cover harvested paddy.", 
                    created_at=datetime.datetime.utcnow() - datetime.timedelta(hours=3)
                )
            ]
            db.add_all(posts)

seed_database()

# --- Static fallback databases for offline/errors ---
STATIC_FALLBACK_PRICES = {
    "rice": [
        {"state": "Andhra Pradesh", "market": "Atmakur APMC", "min": 2150, "max": 2300, "trend": "up"}, 
        {"state": "Telangana", "market": "Nizamabad", "min": 2100, "max": 2280, "trend": "flat"},
        {"state": "Punjab", "market": "Ludhiana", "min": 2200, "max": 2350, "trend": "up"}
    ],
    "wheat": [
        {"state": "Punjab", "market": "Amritsar Mandi", "min": 2275, "max": 2400, "trend": "up"}, 
        {"state": "Uttar Pradesh", "market": "Kanpur", "min": 2200, "max": 2350, "trend": "flat"},
        {"state": "Madhya Pradesh", "market": "Sehore", "min": 2300, "max": 2450, "trend": "down"}
    ],
    "cotton": [
        {"state": "Gujarat", "market": "Rajkot", "min": 7000, "max": 7400, "trend": "down"}, 
        {"state": "Maharashtra", "market": "Amravati", "min": 6800, "max": 7200, "trend": "down"},
        {"state": "Andhra Pradesh", "market": "Guntur", "min": 6900, "max": 7300, "trend": "flat"}
    ],
    "soybean": [
        {"state": "Maharashtra", "market": "Latur", "min": 4500, "max": 4800, "trend": "down"}, 
        {"state": "Madhya Pradesh", "market": "Indore", "min": 4400, "max": 4750, "trend": "up"}
    ],
    "sugarcane": [
        {"state": "Uttar Pradesh", "market": "Muzaffarnagar", "min": 315, "max": 350, "trend": "up"},
        {"state": "Maharashtra", "market": "Kolhapur", "min": 300, "max": 340, "trend": "flat"}
    ]
}

CROP_MAPPING = {
    "rice": "Paddy(Dhan)(Common)",
    "wheat": "Wheat",
    "cotton": "Cotton",
    "soybean": "Soyabean",
    "sugarcane": "Sugarcane"
}

@app.route('/')
def home():
    return render_template('index.html')

# --- 1. LIVE WEATHER API ---
@app.route('/api/weather', methods=['GET'])
def get_weather():
    try:
        # Dynamically fetch weather based on the farmer's profile location
        with db_session() as db:
            profile = db.query(Profile).first()
            location = profile.location if profile else "Atmakur, Andhra Pradesh"
            
        city = location.split(',')[0].strip()
        url = f"https://wttr.in/{city}?format=j1"
        response = requests.get(url, timeout=5)
        data = response.json()
        current_temp = data['current_condition'][0]['temp_C']
        condition = data['current_condition'][0]['weatherDesc'][0]['value']
        humidity = data['current_condition'][0]['humidity']
        wind = data['current_condition'][0]['windspeedKmph']
        
        return jsonify({
            "location": location,
            "current": {"temp": current_temp, "condition": condition, "humidity": f"{humidity}%", "wind": f"{wind} km/h"},
            "forecast": [
                {"day": "Tomorrow", "temp": data['weather'][1]['maxtempC'], "condition": data['weather'][1]['hourly'][4]['weatherDesc'][0]['value']},
                {"day": "Day After", "temp": data['weather'][2]['maxtempC'], "condition": data['weather'][2]['hourly'][4]['weatherDesc'][0]['value']}
            ]
        })
    except Exception:
        # Fallback to local offline profile values
        return jsonify({
            "location": "Atmakur, AP (Offline Mode)", 
            "current": {"temp": 32, "condition": "Partly Cloudy", "humidity": "65%", "wind": "12 km/h"}, 
            "forecast": [{"day": "Tomorrow", "temp": 30, "condition": "Light Rain"}, {"day": "Day After", "temp": 31, "condition": "Sunny"}]
        })

# --- 2. MANDI PRICES (Live data.gov.in Integration with Caching) ---
@app.route('/api/mandi_prices', methods=['GET'])
def get_mandi_prices():
    crop = request.args.get('crop', 'rice').lower()
    
    # Runtime crop validation
    if crop not in CROP_MAPPING:
        return jsonify({
            "error": f"Invalid crop: '{crop}'. Supported crops are: {', '.join(CROP_MAPPING.keys())}"
        }), 400
    
    # 1. Check local cache first
    now = datetime.datetime.utcnow()
    one_hour_ago = now - datetime.timedelta(hours=1)
    
    with db_session() as db:
        cached_prices = db.query(MandiPriceCache).filter(
            MandiPriceCache.crop == crop, 
            MandiPriceCache.fetched_at >= one_hour_ago
        ).all()
        
        if cached_prices:
            return jsonify([{
                "state": p.state,
                "market": p.market,
                "min": int(p.min_price),
                "max": int(p.max_price),
                "trend": p.trend
            } for p in cached_prices])
            
    # 2. Cache is expired or empty. Fetch from data.gov.in API
    api_key = os.getenv("DATA_GOV_IN_API_KEY", "579b464db66ec23bdd000001cdd3946e44ce4aad7209ff7b23ac571b")
    gov_commodity = CROP_MAPPING.get(crop, "Paddy(Dhan)(Common)")
    resource_id = "9ef84268-d588-465a-a308-a864a43d0070"
    url = f"https://api.data.gov.in/resource/{resource_id}?api-key={api_key}&format=json&limit=10&filters[commodity]={gov_commodity}"
    
    try:
        response = requests.get(url, timeout=6)
        if response.status_code == 200:
            records = response.json().get("records", [])
            valid_cached_entries = []
            valid_api_results = []
            
            for r in records:
                try:
                    # Sanitize and validate state/market names
                    state_val = r.get("state", "N/A").strip()
                    market_val = r.get("market", "N/A").strip()
                    if not state_val or not market_val or state_val == "N/A" or market_val == "N/A":
                        continue
                        
                    # Parse and validate price values
                    min_raw = r.get("min_price")
                    max_raw = r.get("max_price")
                    if min_raw is None or max_raw is None:
                        continue
                        
                    min_p = float(min_raw)
                    max_p = float(max_raw)
                    
                    # Range checks (Must be positive and within reasonable limits)
                    if min_p <= 0 or max_p <= 0 or min_p > 100000 or max_p > 100000:
                        continue
                        
                    # Swap if min > max
                    if min_p > max_p:
                        min_p, max_p = max_p, min_p
                        
                    # Create database cache entry
                    trend_choice = random.choice(["up", "down", "flat"])
                    entry = MandiPriceCache(
                        crop=crop,
                        state=state_val,
                        market=market_val,
                        min_price=min_p,
                        max_price=max_p,
                        trend=trend_choice,
                        fetched_at=now
                    )
                    valid_cached_entries.append(entry)
                    
                    # Add to JSON output response list
                    valid_api_results.append({
                        "state": state_val,
                        "market": market_val,
                        "min": int(min_p),
                        "max": int(max_p),
                        "trend": trend_choice
                    })
                except (ValueError, TypeError, AttributeError):
                    # Gracefully skip any corrupted JSON records
                    continue
            
            if valid_cached_entries:
                # Clear old cache for this crop and insert updated records
                with db_session() as db:
                    db.query(MandiPriceCache).filter(MandiPriceCache.crop == crop).delete()
                    db.add_all(valid_cached_entries)
                return jsonify(valid_api_results)
                
    except Exception as e:
        print(f"Failed to query data.gov.in: {e}")
        
    # 3. Request failed or timed out. Fallback to older cache if available
    with db_session() as db:
        old_prices = db.query(MandiPriceCache).filter(MandiPriceCache.crop == crop).all()
        if old_prices:
            return jsonify([{
                "state": p.state,
                "market": p.market,
                "min": int(p.min_price),
                "max": int(p.max_price),
                "trend": p.trend
            } for p in old_prices])
            
    # 4. No cache exists at all. Return static fallback configurations
    return jsonify(STATIC_FALLBACK_PRICES.get(crop, STATIC_FALLBACK_PRICES["rice"]))

# --- 3. GOVERNMENT SCHEMES API ---
@app.route('/api/schemes', methods=['GET'])
def get_schemes():
    schemes = [
        {"name": "PM-KISAN (Pradhan Mantri Kisan Samman Nidhi)", "category": "Financial Support", "desc": "₹6,000 per year minimum income support for all landholding farmer families.", "status": "Active"},
        {"name": "PMFBY (Pradhan Mantri Fasal Bima Yojana)", "category": "Insurance", "desc": "Comprehensive insurance cover against failure of the crop due to natural calamities, pests & diseases.", "status": "Active"},
        {"name": "Kisan Credit Card (KCC)", "category": "Loan/Credit", "desc": "Provides farmers with timely access to credit for agricultural needs at heavily subsidized interest rates.", "status": "Active"},
        {"name": "PKVY (Paramparagat Krishi Vikas Yojana)", "category": "Farming Practice", "desc": "Financial assistance to promote organic farming and reduce reliance on chemical fertilizers.", "status": "Active"},
        {"name": "Sub-Mission on Agricultural Mechanization (SMAM)", "category": "Equipment", "desc": "Subsidies for purchasing modern agricultural machinery like tractors and tillers.", "status": "Active"}
    ]
    return jsonify(schemes)

# --- 4. REAL CROP DOCTOR API (Gemini Vision API Integration) ---
@app.route('/api/predict_disease', methods=['POST'])
def predict_disease():
    if 'file' not in request.files: 
        return jsonify({'error': 'No image'}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty filename'}), 400

    try:
        # Convert image bytes to Base64
        image_data = file.read()
        base64_image = base64.b64encode(image_data).decode('utf-8')
        mime_type = file.content_type or "image/jpeg"
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            # Fallback to smart diagnostic simulation with a badge warning
            time.sleep(1.5)
            return jsonify({
                "disease": "Leaf Blight (Simulated / No Key)",
                "confidence": 94.2,
                "remedy": "Ensure proper spacing between plants and avoid overhead watering. Add a real GEMINI_API_KEY in .env for active live diagnostics.",
                "pesticide": "Mancozeb 75% WP at 2g/Liter",
                "is_demo": True
            })

            
        # Structure the payload for Gemini Flash Vision API
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        
        prompt = """
        You are Kisan Mitra AI, an expert agricultural crop doctor.
        Analyze this image of a plant leaf or crop.
        Identify the crop type, diagnose the disease present (if any), and provide:
        1. Disease name (or 'Healthy' if the crop shows no signs of disease).
        2. Confidence score as a percentage float between 0.0 and 100.0.
        3. Practical organic/cultural remedy instructions.
        4. Specific recommended chemical or biological pesticide (or N/A if healthy).
        
        Your output must be in strict, raw JSON format matching this schema:
        {
          "disease": "string",
          "confidence": float,
          "remedy": "string",
          "pesticide": "string"
        }
        Do not return markdown markers (like ```json or ```) or any additional explanation text.
        """
        
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime_type, "data": base64_image}}
                ]
            }]
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        if response.status_code == 200:
            result_json = response.json()
            candidates = result_json.get("candidates", [])
            if candidates:
                text_response = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                # Clean up wrapping code blocks if present
                clean_text = text_response.strip().replace("```json", "").replace("```", "").strip()
                parsed_result = json.loads(clean_text)
                parsed_result["is_demo"] = False
                return jsonify(parsed_result)
        
        raise Exception(f"Gemini API returned code {response.status_code}")
    except Exception as e:
        print(f"Crop Doctor processing failed: {e}")
        return jsonify({
            "disease": "Diagnostic Failure",
            "confidence": 0.0,
            "remedy": f"Failed to connect to the analysis engine. Error details: {str(e)}",
            "pesticide": "N/A",
            "is_demo": True
        })

# --- 5. CHATBOT ASSISTANT API (Gemini REST Integration) ---
@app.route('/api/chat', methods=['POST'])
def chat_assistant():
    data = request.json or {}
    user_message = data.get("message", "")
    history = data.get("history", [])
    
    if not user_message:
        return jsonify({"error": "Message text is required"}), 400
        
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return jsonify({
            "response": "Hello! I am Kisan Sahayak, your agriculture assistant. To talk to me live, please register a `GEMINI_API_KEY` inside your local `.env` configuration file.",
            "is_demo": True
        })
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    
    # Configure the persona prompt
    system_prompt = (
        "You are Kisan Sahayak, a helpful and knowledgeable agricultural AI assistant for Indian farmers. "
        "Provide friendly, practical advice on crops, irrigation, fertilizers, pests, weather conditions, "
        "and government schemes. Answer concisely, in simple terms. If the user asks in Hindi or Telugu, respond in that language. "
        "Use bullet points for lists. Under no circumstances should you talk about topics completely unrelated to farming, agriculture, or weather."
    )
    
    # Map messages history to Gemini schema
    contents = []
    for h in history:
        role = "user" if h.get("role") == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": h.get("content", "")}]
        })
        
    # Append user question with context
    contents.append({
        "role": "user",
        "parts": [{"text": f"[System Context: {system_prompt}]\n\nUser Question: {user_message}"}]
    })
    
    payload = {"contents": contents}
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            res_json = response.json()
            candidates = res_json.get("candidates", [])
            if candidates:
                bot_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                return jsonify({"response": bot_text, "is_demo": False})
                
        raise Exception(f"Gemini API returned code {response.status_code}")
    except Exception as e:
        print(f"Chatbot failed: {e}")
        return jsonify({
            "response": f"Sorry, my connection to the server was interrupted. Error: {str(e)}",
            "is_demo": True
        })

# --- 6. COMMUNITY API (Persistent SQLite) ---
@app.route('/api/community', methods=['GET', 'POST'])
def handle_community():
    if request.method == 'POST':
        data = request.json or {}
        user = data.get("user")
        text = data.get("text")
        
        if not user or not text:
            return jsonify({"error": "User name and post content are required"}), 400
            
        with db_session() as db:
            post = CommunityPost(user=user, text=text)
            db.add(post)
        return jsonify({"status": "success"})
        
    # GET method
    with db_session() as db:
        posts = db.query(CommunityPost).order_by(CommunityPost.created_at.desc()).all()
        result = []
        now = datetime.datetime.utcnow()
        
        for p in posts:
            delta = now - p.created_at
            if delta.days > 0:
                time_display = f"{delta.days} day{'s' if delta.days > 1 else ''} ago"
            elif delta.seconds >= 3600:
                hours = delta.seconds // 3600
                time_display = f"{hours} hour{'s' if hours > 1 else ''} ago"
            elif delta.seconds >= 60:
                minutes = delta.seconds // 60
                time_display = f"{minutes} min{'s' if minutes > 1 else ''} ago"
            else:
                time_display = "Just now"
                
            result.append({
                "user": p.user,
                "text": p.text,
                "time": time_display
            })
        return jsonify(result)

# --- 7. PROFILE API (Persistent SQLite) ---
@app.route('/api/profile', methods=['GET', 'POST'])
def handle_profile():
    if request.method == 'POST':
        data = request.json or {}
        with db_session() as db:
            profile = db.query(Profile).first()
            if not profile:
                profile = Profile()
                db.add(profile)
            profile.name = data.get("name", profile.name)
            profile.location = data.get("location", profile.location)
            profile.crops = data.get("crops", profile.crops)
            profile.land_area = float(data.get("land_area", profile.land_area))
            profile.language = data.get("language", profile.language)
        return jsonify({"status": "success"})
        
    with db_session() as db:
        profile = db.query(Profile).first()
        if not profile:
            profile = Profile(name="Ramesh Kumar", location="Atmakur, Andhra Pradesh", crops="Rice, Cotton", land_area=5.0, language="English")
            db.add(profile)
            db.commit()
            profile = db.query(Profile).first()
            
        return jsonify({
            "name": profile.name,
            "location": profile.location,
            "crops": profile.crops,
            "land_area": profile.land_area,
            "language": profile.language
        })

# --- 8. EXPENSES API (Persistent SQLite) ---
@app.route('/api/expenses', methods=['GET', 'POST'])
def handle_expenses():
    if request.method == 'POST':
        data = request.json or {}
        category = data.get("category")
        amount = float(data.get("amount", 0))
        
        if not category or amount <= 0:
            return jsonify({"error": "Invalid details"}), 400
            
        with db_session() as db:
            expense = Expense(category=category, amount=amount)
            db.add(expense)
        return jsonify({"status": "success"})
        
    # GET method
    with db_session() as db:
        expenses = db.query(Expense).all()
        
        result = []
        summary = {"Seeds": 0.0, "Fertilizer": 0.0, "Labor": 0.0}
        
        for e in expenses:
            result.append({
                "id": e.id,
                "category": e.category,
                "amount": e.amount,
                "date": e.date.strftime("%d-%m-%Y")
            })
            if e.category in summary:
                summary[e.category] += e.amount
            else:
                summary[e.category] = e.amount
                
        return jsonify({"expenses": result, "summary": summary})

@app.route('/api/expenses/<int:expense_id>', methods=['DELETE'])
def delete_expense(expense_id):
    with db_session() as db:
        expense = db.query(Expense).filter(Expense.id == expense_id).first()
        if not expense:
            return jsonify({"error": "Expense record not found"}), 404
        db.delete(expense)
    return jsonify({"status": "success"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)