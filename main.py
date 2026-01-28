import os
import logging
from flask import Flask, jsonify, request
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- CONFIG ---
API_KEY = os.getenv("RAPIDAPI_KEY")
API_HOST = "realtor-data1.p.rapidapi.com"
PORT = int(os.environ.get("PORT", 8080))

DEFAULT_STALE_DAYS = 90

DISTRESS_KEYWORDS = [
    "probate", "as-is", "as is", "tlc", "motivated", "fixer", 
    "handyman", "bank owned", "reo", "foreclosure", "estate sale",
    "investor", "cash only", "needs work", "potential", "must sell",
    "distressed", "below market", "bring offers", "price reduced"
]

if not API_KEY:
    logger.warning("RAPIDAPI_KEY not set")
else:
    logger.info(f"StaleEngine ready. API: {API_HOST}")


def get_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def parse_location(location: str) -> dict:
    """Parse location string into API query format."""
    location = location.strip()
    
    # Check if it's a ZIP code
    if location.isdigit() and len(location) == 5:
        return {"postal_code": location}
    
    # Check for city, state format
    if "," in location:
        parts = [p.strip() for p in location.split(",")]
        query = {"city": parts[0]}
        if len(parts) > 1 and len(parts[1]) == 2:
            query["state_code"] = parts[1].upper()
        return query
    
    # Default to city search
    return {"city": location}


def search_properties(location: str, page: int = 1, limit: int = 50) -> list:
    """
    Search properties using Realtor Data API.
    Docs: https://rapidapi.com/thepropertyapi-thepropertyapi-default/api/realtor-data1
    """
    session = get_session()
    url = f"https://{API_HOST}/property_list/"
    
    location_query = parse_location(location)
    
    payload = {
        "query": {
            "status": ["for_sale"],
            **location_query
        },
        "limit": limit,
        "offset": (page - 1) * limit,
        "sort": {
            "direction": "asc",
            "field": "list_date"  # Oldest first = most stale
        }
    }
    
    headers = {
        "X-RapidAPI-Key": API_KEY,
        "X-RapidAPI-Host": API_HOST,
        "Content-Type": "application/json"
    }
    
    logger.info(f"Searching: {location} (page {page})")
    
    response = session.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    
    data = response.json()
    
    # Handle different response formats
    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        return data.get("data", data.get("results", data.get("properties", [])))
    return []


def calculate_days_on_market(list_date) -> int:
    """Calculate days since listing."""
    if not list_date:
        return 0
    try:
        if isinstance(list_date, str):
            # Try common formats
            for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"]:
                try:
                    dt = datetime.strptime(list_date[:19], fmt[:len(list_date)])
                    return (datetime.now() - dt).days
                except:
                    continue
        return 0
    except:
        return 0


def normalize_property(prop: dict) -> dict:
    """Normalize property to standard format."""
    
    # Handle nested location/address
    location = prop.get("location", {})
    address_obj = location.get("address", {}) if isinstance(location, dict) else {}
    
    # Build address string
    address = prop.get("address")
    if not address and address_obj:
        line = address_obj.get("line", "")
        city = address_obj.get("city", "")
        state = address_obj.get("state_code", "")
        address = f"{line}, {city}, {state}".strip(", ")
    if not address:
        address = f"{prop.get('street', '')} {prop.get('city', '')}".strip()
    
    # Get price
    price = prop.get("list_price") or prop.get("price") or 0
    if isinstance(price, str):
        price = int(''.join(filter(str.isdigit, price)) or 0)
    
    # Days on market
    list_date = prop.get("list_date")
    days_on = calculate_days_on_market(list_date)
    
    # Description
    desc = prop.get("description", {})
    if isinstance(desc, dict):
        description_text = desc.get("text", "")
        beds = desc.get("beds")
        baths = desc.get("baths")
        sqft = desc.get("sqft")
        prop_type = desc.get("type")
    else:
        description_text = str(desc) if desc else ""
        beds = prop.get("beds") or prop.get("bedrooms")
        baths = prop.get("baths") or prop.get("bathrooms")
        sqft = prop.get("sqft") or prop.get("living_area")
        prop_type = prop.get("type") or prop.get("property_type")
    
    # URL
    prop_url = prop.get("href") or prop.get("url") or prop.get("rdc_web_url", "")
    if prop_url and not prop_url.startswith("http"):
        prop_url = f"https://www.realtor.com{prop_url}"
    
    # Photo
    photo = prop.get("primary_photo", {})
    if isinstance(photo, dict):
        photo = photo.get("href", "")
    
    return {
        "property_id": prop.get("property_id"),
        "address": address,
        "city": address_obj.get("city") or prop.get("city"),
        "state": address_obj.get("state_code") or prop.get("state"),
        "zip": address_obj.get("postal_code") or prop.get("postal_code"),
        "price": price,
        "bedrooms": beds,
        "bathrooms": baths,
        "sqft": sqft,
        "property_type": prop_type,
        "days_on_market": days_on,
        "list_date": list_date,
        "description": description_text[:500] if description_text else None,
        "url": prop_url,
        "photo": photo
    }


def find_stale(properties: list, min_days: int) -> list:
    """Find properties on market longer than min_days."""
    leads = []
    for prop in properties:
        norm = normalize_property(prop)
        if norm["days_on_market"] >= min_days:
            norm["signal"] = "STALE"
            leads.append(norm)
    return sorted(leads, key=lambda x: x["days_on_market"], reverse=True)


def find_distress(properties: list) -> list:
    """Find properties with distress keywords."""
    leads = []
    for prop in properties:
        norm = normalize_property(prop)
        text = f"{norm.get('address', '')} {norm.get('description', '')}".lower()
        matched = [kw for kw in DISTRESS_KEYWORDS if kw in text]
        if matched:
            norm["signal"] = "DISTRESS"
            norm["matched_keywords"] = matched
            leads.append(norm)
    return leads


# --- ROUTES ---

@app.route('/', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "service": "StaleEngine",
        "version": "4.0.0",
        "api_configured": API_KEY is not None,
        "api_host": API_HOST,
        "endpoints": {
            "GET /find-stale?location=Houston,TX": "Listings 90+ days on market",
            "GET /find-stale?location=77001&days=60": "By ZIP, custom days",
            "GET /find-distress?location=Phoenix,AZ": "Distress keyword matches",
            "GET /search?location=Miami,FL": "Combined search"
        }
    })


@app.route('/find-stale', methods=['GET'])
def route_find_stale():
    location = request.args.get('location')
    days = request.args.get('days', DEFAULT_STALE_DAYS, type=int)
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' param", "example": "/find-stale?location=Austin,TX"}), 400
    
    if not API_KEY:
        return jsonify({"success": False, "error": "API key not configured"}), 500
    
    try:
        properties = search_properties(location, page)
        leads = find_stale(properties, days)
        
        return jsonify({
            "success": True,
            "location": location,
            "filter": f"{days}+ days",
            "total_searched": len(properties),
            "results_count": len(leads),
            "page": page,
            "leads": leads
        })
        
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response else 502
        logger.error(f"API error: {e}")
        return jsonify({
            "success": False,
            "error": f"Upstream API error ({status})",
            "details": str(e)
        }), 502
    except Exception as e:
        logger.exception("Error")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/find-distress', methods=['GET'])
def route_find_distress():
    location = request.args.get('location')
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' param"}), 400
    
    if not API_KEY:
        return jsonify({"success": False, "error": "API key not configured"}), 500
    
    try:
        properties = search_properties(location, page)
        leads = find_distress(properties)
        
        return jsonify({
            "success": True,
            "location": location,
            "filter": "distress keywords",
            "keywords": DISTRESS_KEYWORDS,
            "total_searched": len(properties),
            "results_count": len(leads),
            "page": page,
            "leads": leads
        })
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"API error: {e}")
        return jsonify({"success": False, "error": "Upstream API error"}), 502
    except Exception as e:
        logger.exception("Error")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/search', methods=['GET'])
def route_search():
    location = request.args.get('location')
    days = request.args.get('days', DEFAULT_STALE_DAYS, type=int)
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' param"}), 400
    
    if not API_KEY:
        return jsonify({"success": False, "error": "API key not configured"}), 500
    
    try:
        properties = search_properties(location, page)
        stale = find_stale(properties, days)
        distress = find_distress(properties)
        
        # Hot leads = both stale AND distressed
        stale_ids = {p["property_id"] for p in stale if p.get("property_id")}
        distress_ids = {p["property_id"] for p in distress if p.get("property_id")}
        hot_ids = stale_ids & distress_ids
        
        return jsonify({
            "success": True,
            "location": location,
            "total_searched": len(properties),
            "page": page,
            "summary": {
                "stale": len(stale),
                "distress": len(distress),
                "hot_leads": len(hot_ids)
            },
            "hot_lead_ids": list(hot_ids),
            "stale_leads": stale,
            "distress_leads": distress
        })
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"API error: {e}")
        return jsonify({"success": False, "error": "Upstream API error"}), 502
    except Exception as e:
        logger.exception("Error")
        return jsonify({"success": False, "error": str(e)}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found", "endpoints": ["/", "/find-stale", "/find-distress", "/search"]}), 404


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=PORT, debug=True)
