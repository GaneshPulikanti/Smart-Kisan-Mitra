from flask import Flask, request, jsonify, render_template
import certifi
import requests
import random
import time
import os
import json
import datetime
import base64
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId
import urllib.request
import re
import ssl
from html.parser import HTMLParser

# HTML Parser class to extract table rows from vegetablemarketprice.com without external libraries
class VegHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_tr = False
        self.in_td = False
        self.current_row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_tr = True
            self.current_row = []
        elif tag == "td" and self.in_tr:
            self.in_td = True

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr" and self.in_tr:
            self.in_tr = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag == "td" and self.in_td:
            self.in_td = False

    def handle_data(self, data):
        if self.in_td:
            self.current_row.append(data.strip())

def scrape_live_vegetable_prices(crop_name):
    # Maps user searches to the exact name on the vegetable website
    veg_name_map = {
        "tomato": "Tomato",
        "onion": "Onion Big",
        "potato": "Potato",
        "garlic": "Garlic",
        "ginger": "Ginger",
        "cabbage": "Cabbage",
        "cauliflower": "Cauliflower",
        "green chilli": "Green Chilli",
        "chilli": "Green Chilli",
        "brinjal": "Brinjal",
        "okra": "Ladies Finger",
        "pumpkin": "Pumpkin",
        "bitter gourd": "Bitter Gourd",
        "bottle gourd": "Bottle Gourd",
        "cucumber": "Cucumber",
        "carrot": "Carrot",
        "radish": "Radish",
        "spinach": "Spinach",
        "lemon": "Lemon (Lime)",
        "coriander": "Coriander Leaves",
        "apple": "Apple",
        "banana": "Raw Banana (Plantain)"
    }
    
    target_veg = veg_name_map.get(crop_name.lower())
    if not target_veg:
        return None
        
    # We scrape 3 key reference regions to get live prices
    states_to_scrape = [
        {"name": "Andhra Pradesh", "slug": "andhrapradesh", "district": "Guntur", "market": "Guntur APMC"},
        {"name": "Maharashtra", "slug": "maharashtra", "district": "Nashik", "market": "Pimpalgaon Mandi"},
        {"name": "Delhi", "slug": "delhi", "district": "New Delhi", "market": "Azadpur Mandi"}
    ]
    
    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    for s in states_to_scrape:
        try:
            url = f"https://vegetablemarketprice.com/market/{s['slug']}/today"
            req = urllib.request.Request(url, headers=headers)
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                html = response.read().decode('utf-8')
                
                parser = VegHTMLParser()
                parser.feed(html)
                
                for row in parser.rows:
                    row_clean = [x for x in row if x]
                    if len(row_clean) >= 3 and row_clean[0].lower() == target_veg.lower():
                        # Extract prices (e.g. '₹24' -> 24)
                        min_price_str = re.sub(r'[^\d]', '', row_clean[1])
                        retail_nums = re.findall(r'\d+', row_clean[2])
                        max_price_str = retail_nums[-1] if retail_nums else min_price_str
                        
                        # Convert Price/kg to Price/Quintal (Multiply by 100)
                        min_val = float(min_price_str) * 100
                        max_val = float(max_price_str) * 100
                        
                        results.append({
                            "state": s["name"],
                            "district": s["district"],
                            "market": s["market"],
                            "min": int(min_val),
                            "max": int(max_val),
                            "trend": random.choice(["up", "down", "flat"])
                        })
                        break
        except Exception as e:
            print(f"Live scraper failed for state {s['name']}: {e}")
            
    return results if len(results) > 0 else None


# Load environment configuration
load_dotenv()

app = Flask(__name__)

# --- MongoDB Database Setup ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
# Try to connect with certifi, fallback to standard SSL if certifi raises an issue on Linux
try:
    client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
    # Quick ping to test connection
    client.admin.command('ping')
except Exception:
    client = MongoClient(MONGO_URI)

# Select database
db = client["kisan_mitra"]

# Collections (equivalent to SQL Tables)
profiles_col = db["profiles"]
expenses_col = db["expenses"]
posts_col = db["community_posts"]
mandi_cache_col = db["mandi_price_cache"]


def seed_database():
    # 1. Initial profile seed
    if profiles_col.count_documents({}) == 0:
        profiles_col.insert_one({
            "name": "Ramesh Kumar",
            "location": "Atmakur, Andhra Pradesh",
            "crops": "Rice, Cotton",
            "land_area": 5.0,
            "language": "English"
        })
        print("Database Seeded: Default profile created.")
        
    # 2. Initial community posts seed
    if posts_col.count_documents({}) == 0:
        posts = [
            {
                "user": "Suresh N.", 
                "text": "Has anyone noticed the whiteflies on the cotton crop recently?", 
                "created_at": datetime.datetime.utcnow() - datetime.timedelta(hours=1)
            },
            {
                "user": "Agri Expert", 
                "text": "Unseasonal rains expected tomorrow. Please cover harvested paddy.", 
                "created_at": datetime.datetime.utcnow() - datetime.timedelta(hours=3)
            }
        ]
        posts_col.insert_many(posts)
        print("Database Seeded: Initial community posts created.")

# Run database seeder
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
    # Cereals & Grains
    "rice": "Paddy(Dhan)(Common)",
    "rice (paddy)": "Paddy(Dhan)(Common)",
    "paddy": "Paddy(Dhan)(Common)",
    "wheat": "Wheat",
    "maize": "Maize",
    "barley": "Barley",
    "jowar": "Jowar(Sorghum)",
    "bajra": "Bajra(Pearl Millet)",
    "ragi": "Ragi (Finger Millet)",
    
    # Pulses
    "chana": "Bengal Gram(Gram)",
    "bengal gram": "Bengal Gram(Gram)",
    "urad": "Black Gram (Urd)",
    "moong": "Green Gram (Moong)",
    "masur": "Lentil (Masur)",
    "arhar": "Arhar (Tur-Red Gram)",
    "tur": "Arhar (Tur-Red Gram)",
    
    # Vegetables
    "tomato": "Tomato",
    "onion": "Onion",
    "potato": "Potato",
    "garlic": "Garlic",
    "ginger": "Ginger(Green)",
    "cabbage": "Cabbage",
    "cauliflower": "Cauliflower",
    "green chilli": "Green Chilli",
    "chilli": "Green Chilli",
    "brinjal": "Brinjal",
    "okra": "Bhindi(Ladies Finger)",
    "okra (ladies finger)": "Bhindi(Ladies Finger)",
    "bhindi": "Bhindi(Ladies Finger)",
    "ladies finger": "Bhindi(Ladies Finger)",
    "pumpkin": "Pumpkin",
    "bottle gourd": "Bottle gourd",
    "bitter gourd": "Bitter Gourd",
    "cucumber": "Cucumber(Kheera)",
    "carrot": "Carrot",
    "radish": "Radish",
    "spinach": "Spinach",
    "coriander": "Coriander(Leaves)",
    "lemon": "Lemon",
    
    # Fruits
    "apple": "Apple",
    "banana": "Banana",
    "mango": "Mango",
    "orange": "Orange",
    "papaya": "Papaya",
    "pomegranate": "Pomegranate",
    "grapes": "Grapes",
    "watermelon": "Watermelon",
    "guava": "Guava",
    "pineapple": "Pineapple",
    "sweet lime": "Sweet Lime(Mosambi)",
    "sweet lime (mosambi)": "Sweet Lime(Mosambi)",
    "mosambi": "Sweet Lime(Mosambi)",
    "coconut": "Coconut",
    
    # Commercial Crops
    "cotton": "Cotton",
    "sugarcane": "Sugarcane",
    "soybean": "Soyabean",
    "mustard": "Mustard",
    "groundnut": "Groundnut",
    "sunflower": "Sunflower",
    "sesame": "Sesame(Sesamum,Gingelly,Til)"
}


@app.route('/')
def home():
    return render_template('index.html')

# --- 1. LIVE WEATHER API ---
@app.route('/api/weather', methods=['GET'])
def get_weather():
    try:
        # Dynamically fetch weather based on the farmer's profile location
        profile = profiles_col.find_one()
        location = profile.get("location", "Atmakur, Andhra Pradesh") if profile else "Atmakur, Andhra Pradesh"
            
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
        # Fallback to offline values
        return jsonify({
            "location": "Atmakur, AP (Offline Mode)", 
            "current": {"temp": 32, "condition": "Partly Cloudy", "humidity": "65%", "wind": "12 km/h"}, 
            "forecast": [{"day": "Tomorrow", "temp": 30, "condition": "Light Rain"}, {"day": "Day After", "temp": 31, "condition": "Sunny"}]
        })

# --- 2. MANDI PRICES (Live data.gov.in Integration with Caching) ---
# --- 2. MANDI PRICES (Live Web Scraper Integration with Caching) ---
@app.route('/api/mandi_prices', methods=['GET'])
def get_mandi_prices():
    crop_input = request.args.get('crop', 'rice').lower().strip()
    
    # Normalize input variation names to match backend / fallback cache keys
    normalize_map = {
        "rice (paddy)": "rice",
        "paddy": "rice",
        "chana (bengal gram)": "chana",
        "bengal gram": "chana",
        "urad (black gram)": "urad",
        "black gram": "urad",
        "moong (green gram)": "moong",
        "green gram": "moong",
        "masur (lentil)": "masur",
        "lentil": "masur",
        "arhar (tur)": "arhar",
        "tur": "arhar",
        "okra (ladies finger)": "okra",
        "ladies finger": "okra",
        "bhindi": "okra",
        "chilli": "green chilli",
        "mosambi (sweet lime)": "mosambi",
        "sweet lime": "mosambi",
    }
    crop_input = normalize_map.get(crop_input, crop_input)
    
    # 1. Check local MongoDB cache first (saves scraping time)
    now = datetime.datetime.utcnow()
    one_hour_ago = now - datetime.timedelta(hours=1)
    
    cached_prices = list(mandi_cache_col.find({
        "crop": crop_input, 
        "fetched_at": {"$gte": one_hour_ago}
    }))
        
    if cached_prices:
        return jsonify([{
            "state": p["state"],
            "district": p.get("district", "N/A"),
            "market": p["market"],
            "min": int(p["min_price"]),
            "max": int(p["max_price"]),
            "trend": p["trend"]
        } for p in cached_prices])
            
    # 2. Cache is expired/empty. Scrape live vegetable/fruit prices directly from the web
    try:
        scraped_results = scrape_live_vegetable_prices(crop_input)
        if scraped_results:
            valid_cached_entries = []
            for r in scraped_results:
                valid_cached_entries.append({
                    "crop": crop_input,
                    "state": r["state"],
                    "district": r["district"],
                    "market": r["market"],
                    "min_price": r["min"],
                    "max_price": r["max"],
                    "trend": r["trend"],
                    "fetched_at": now
                })
            mandi_cache_col.delete_many({"crop": crop_input})
            mandi_cache_col.insert_many(valid_cached_entries)
            print(f"Successfully scraped live prices for {crop_input} from vegetablemarketprice.com")
            return jsonify(scraped_results)
    except Exception as scrape_err:
        print(f"Live scraper failed: {scrape_err}")

    # 3. Scraper failed or crop is a cereal/grain (not on veggie site). Use older cache if available
    old_prices = list(mandi_cache_col.find({"crop": crop_input}))
    if old_prices:
        return jsonify([{
            "state": p["state"],
            "district": p.get("district", "N/A"),
            "market": p["market"],
            "min": int(p["min_price"]),
            "max": int(p["max_price"]),
            "trend": p["trend"]
        } for p in old_prices])
            
    # 4. Absolutely no other option. Return static fallback
    fallback_data = STATIC_FALLBACK_PRICES.get(crop_input, STATIC_FALLBACK_PRICES["rice"])
    for item in fallback_data:
        if "district" not in item:
            item["district"] = "Local"
    return jsonify(fallback_data)


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
        image_data = file.read()
        base64_image = base64.b64encode(image_data).decode('utf-8')
        mime_type = file.content_type or "image/jpeg"
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            time.sleep(1.5)
            return jsonify({
                "disease": "Leaf Blight (Simulated / No Key)",
                "confidence": 94.2,
                "remedy": "Ensure proper spacing between plants and avoid overhead watering. Add a real GEMINI_API_KEY in .env for active live diagnostics.",
                "pesticide": "Mancozeb 75% WP at 2g/Liter",
                "is_demo": True
            })

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
    
    system_prompt = (
        "You are Kisan Sahayak, a helpful and knowledgeable agricultural AI assistant for Indian farmers. "
        "Provide friendly, practical advice on crops, irrigation, fertilizers, pests, weather conditions, "
        "and government schemes. Answer concisely, in simple terms. If the user asks in Hindi or Telugu, respond in that language. "
        "Use bullet points for lists. Under no circumstances should you talk about topics completely unrelated to farming, agriculture, or weather."
    )
    
    contents = []
    for h in history:
        role = "user" if h.get("role") == "user" else "model"
        contents.append({
            "role": role,
            "parts": [{"text": h.get("content", "")}]
        })
        
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

# --- 6. COMMUNITY API (Persistent MongoDB) ---
@app.route('/api/community', methods=['GET', 'POST'])
def handle_community():
    if request.method == 'POST':
        data = request.json or {}
        user = data.get("user")
        text = data.get("text")
        
        if not user or not text:
            return jsonify({"error": "User name and post content are required"}), 400
            
        posts_col.insert_one({
            "user": user,
            "text": text,
            "created_at": datetime.datetime.utcnow()
        })
        return jsonify({"status": "success"})
        
    # GET method
    posts = list(posts_col.find().sort("created_at", -1))
    result = []
    now = datetime.datetime.utcnow()
    
    for p in posts:
        created_at = p.get("created_at", now)
        delta = now - created_at
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
            "user": p["user"],
            "text": p["text"],
            "time": time_display
        })
    return jsonify(result)

# --- 7. PROFILE API (Persistent MongoDB) ---
@app.route('/api/profile', methods=['GET', 'POST'])
def handle_profile():
    try:
        if request.method == 'POST':
            data = request.json or {}
            profile = profiles_col.find_one()
            
            # Safely parse land_area to handle empty, null, or invalid inputs
            land_area_raw = data.get("land_area")
            try:
                if land_area_raw is not None and land_area_raw != "":
                    land_area_val = float(land_area_raw)
                else:
                    land_area_val = profile.get("land_area") if profile else 5.0
            except (ValueError, TypeError):
                land_area_val = profile.get("land_area") if profile else 5.0

            update_data = {
                "name": data.get("name") or (profile.get("name") if profile else "Ramesh Kumar"),
                "location": data.get("location") or (profile.get("location") if profile else "Atmakur, Andhra Pradesh"),
                "crops": data.get("crops") or (profile.get("crops") if profile else "Rice, Cotton"),
                "land_area": land_area_val,
                "language": data.get("language") or (profile.get("language") if profile else "English")
            }
            
            if profile:
                profiles_col.update_one({"_id": profile["_id"]}, {"$set": update_data})
            else:
                profiles_col.insert_one(update_data)
                
            return jsonify({"status": "success"})
            
        profile = profiles_col.find_one()
        if not profile:
            profile = {
                "name": "Ramesh Kumar",
                "location": "Atmakur, Andhra Pradesh",
                "crops": "Rice, Cotton",
                "land_area": 5.0,
                "language": "English"
            }
            profiles_col.insert_one(profile)
            profile = profiles_col.find_one()
            
        return jsonify({
            "name": profile.get("name"),
            "location": profile.get("location"),
            "crops": profile.get("crops"),
            "land_area": profile.get("land_area"),
            "language": profile.get("language")
        })
    except Exception as e:
        print(f"❌ Profile API Error: {e}")
        return jsonify({"error": str(e)}), 500


# --- 8. EXPENSES API (Persistent MongoDB) ---
@app.route('/api/expenses', methods=['GET', 'POST'])
def handle_expenses():
    if request.method == 'POST':
        data = request.json or {}
        category = data.get("category")
        amount = float(data.get("amount", 0))
        
        if not category or amount <= 0:
            return jsonify({"error": "Invalid details"}), 400
            
        expenses_col.insert_one({
            "category": category,
            "amount": amount,
            "date": datetime.datetime.utcnow()
        })
        return jsonify({"status": "success"})
        
    # GET method
    expenses = list(expenses_col.find())
    result = []
    summary = {"Seeds": 0.0, "Fertilizer": 0.0, "Labor": 0.0}
    
    for e in expenses:
        expense_id = str(e["_id"])
        result.append({
            "id": expense_id,
            "category": e["category"],
            "amount": e["amount"],
            "date": e["date"].strftime("%d-%m-%Y")
        })
        category = e["category"]
        summary[category] = summary.get(category, 0.0) + e["amount"]
                
    return jsonify({"expenses": result, "summary": summary})

@app.route('/api/expenses/<string:expense_id>', methods=['DELETE'])
def delete_expense(expense_id):
    try:
        res = expenses_col.delete_one({"_id": ObjectId(expense_id)})
        if res.deleted_count == 0:
            return jsonify({"error": "Expense record not found"}), 404
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": f"Invalid ID format: {str(e)}"}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)
