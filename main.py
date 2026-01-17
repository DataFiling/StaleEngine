import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# --- PRE-FLIGHT CONFIGURATION ---
# We pull the key at the top level so Gunicorn sees it immediately.
API_KEY = os.getenv("RAPIDAPI_KEY")
API_HOST = "zillow-com1.p.rapidapi.com"
PORT = int(os.environ.get("PORT", 8080))

# Safety Check: Log status without revealing the actual secret key
if not API_KEY:
    print("⚠️ [StaleEngine] WARNING: RAPIDAPI_KEY is not set in environment variables.")
else:
    print(f"✅ [StaleEngine] Engine primed. Key detected (Length: {len(API_KEY)})")

# Distress Keywords for the "Signal" search
DISTRESS_KEYWORDS = ["probate", "as-is", "tlc", "motivated", "fixer", "handyman", "bank owned"]

@app.route('/', methods=['GET'])
def health_check():
    # RapidAPI uses this to see if your server is "Alive"
    return jsonify({
        "status": "online",
        "engine": "StaleEngine",
        "key_loaded": API_KEY is not None
    })

@app.route('/find-stale', methods=['GET'])
def get_stale_leads():
    location = request.args.get('location')
    
    # 1. Validation: Ensure the user provided a location
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' query parameter."}), 400

    # 2. Validation: Ensure the engine has its key
    if not API_KEY:
        return jsonify({"success": False, "error": "Server Configuration Error: API Key missing."}), 500

    url = f"https://{API_HOST}/propertyExtendedSearch"
    querystring = {"location": location, "status_type": "ForSale"}
    headers = {
        "X-RapidAPI-Key": API_KEY,
        "X-RapidAPI-Host": API_HOST
    }

    try:
        response = requests.get(url, headers=headers, params=querystring, timeout=10)
        response.raise_for_status()
        data = response.json()
        properties = data.get('props', [])

        stale_leads = []
        for prop in properties:
            days_on = prop.get('daysOnZillow', 0)
            desc = prop.get('listing_description', "").lower()
            is_distressed = any(word in desc for word in DISTRESS_KEYWORDS)

            if days_on >= 90 or is_distressed:
                stale_leads.append({
                    "address": prop.get('address'),
                    "price": prop.get('price'),
                    "days_on": days_on,
                    "signal": "STALE" if days_on >= 90 else "DISTRESS",
                    "url": f"https://www.zillow.com{prop.get('detailUrl')}"
                })

        return jsonify({
            "success": True,
            "location": location,
            "results_count": len(stale_leads),
            "leads": stale_leads
        })

    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": f"Upstream API Error: {str(e)}"}), 502

if __name__ == "__main__":
    # Local development run
    app.run(host='0.0.0.0', port=PORT)
