import os
from flask import Flask, jsonify, request
import requests

app = Flask(__name__)

# Config from Railway Environment Variables
API_KEY = os.getenv("RAPIDAPI_KEY")

@app.route('/find-stale', methods=['GET'])
def get_stale_leads():
    location = request.args.get('location', 'Miami, FL')
    
    # RapidAPI call logic
    url = "https://zillow-com1.p.rapidapi.com/propertyExtendedSearch"
    querystring = {"location": location, "status_type": "ForSale"}
    headers = {"X-RapidAPI-Key": API_KEY, "X-RapidAPI-Host": "zillow-com1.p.rapidapi.com"}
    
    response = requests.get(url, headers=headers, params=querystring)
    data = response.json().get('props', [])
    
    # Filter for Stale (>90 days)
    leads = [p for p in data if p.get('daysOnZillow', 0) >= 90]
    
    return jsonify({"location": location, "stale_count": len(leads), "leads": leads})

if __name__ == "__main__":
    # Railway provides the PORT variable automatically
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
