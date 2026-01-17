import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# CONFIGURATION: Set these in Railway "Variables" tab
# 1. RAPIDAPI_KEY (Your key from rapidapi.com)
# 2. PORT (Optional, Railway defaults to this)
API_KEY = os.getenv("RAPIDAPI_KEY")
API_HOST = "zillow-com1.p.rapidapi.com"

# Distress Keywords to look for in descriptions
DISTRESS_KEYWORDS = ["probate", "as-is", "tlc", "motivated", "fixer", "handyman", "bank owned"]

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "active", "engine": "StaleEngine", "version": "1.0.0"})

@app.route('/find-stale', methods=['GET'])
def get_stale_leads():
    # Get location from query param: /find-stale?location=Miami, FL
    location = request.args.get('location', 'Miami, FL')
    
    if not API_KEY:
        return jsonify({"error": "RapidAPI Key missing in environment variables"}), 500

    url = f"https://{API_HOST}/propertyExtendedSearch"
    querystring = {"location": location, "status_type": "ForSale"}
    headers = {
        "X-RapidAPI-Key": API_KEY,
        "X-RapidAPI-Host": API_HOST
    }

    try:
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        data = response.json()
        properties = data.get('props', [])

        stale_leads = []
        for prop in properties:
            # 1. Check for 'Stale' signal (Days on Zillow > 90)
            days_on = prop.get('daysOnZillow', 0)
            
            # 2. Check for 'Distress' signal in description (if available)
            # Note: Some RapidAPI endpoints provide a snippet; others need a second call.
            desc = prop.get('listing_description', "").lower()
            is_distressed = any(word in desc for word in DISTRESS_KEYWORDS)

            if days_on >= 90 or is_distressed:
                stale_leads.append({
                    "address": prop.get('address'),
                    "price": prop.get('price'),
                    "days_on": days_on,
                    "signal": "STALE" if days_on >= 90 else "DISTRESS KEYWORD",
                    "url": f"https://www.zillow.com{prop.get('detailUrl')}"
                })

        return jsonify({
            "success": True,
            "location": location,
            "total_found": len(properties),
            "stale_count": len(stale_leads),
            "leads": stale_leads
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    # Railway passes the PORT variable; we listen on 0.0.0.0 to be public
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
