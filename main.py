import os
import logging
from flask import Flask, jsonify, request
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- CONFIGURATION ---
API_KEY = os.getenv("RAPIDAPI_KEY")
PORT = int(os.environ.get("PORT", 8080))

# Supported API providers - switch via REAL_ESTATE_API env var
API_PROVIDER = os.getenv("REAL_ESTATE_API", "realtor-search")

API_CONFIGS = {
    "realtor-search": {
        "host": "realtor-search.p.rapidapi.com",
        "search_endpoint": "/forsale",
        "method": "GET",
        "location_param": "location",
        "results_key": "data",
        "days_field": "list_date",  # We'll calculate days from this
    },
    "us-real-estate": {
        "host": "us-real-estate.p.rapidapi.com",
        "search_endpoint": "/v3/for-sale",
        "method": "GET",
        "location_param": "state_code",
        "results_key": "data.results",
        "days_field": "list_date",
    },
    "realty-in-us": {
        "host": "realty-in-us.p.rapidapi.com",
        "search_endpoint": "/properties/v3/list",
        "method": "POST",
        "location_param": "postal_code",
        "results_key": "data.home_search.results",
        "days_field": "list_date",
    }
}

# Get active config
ACTIVE_CONFIG = API_CONFIGS.get(API_PROVIDER, API_CONFIGS["realtor-search"])
API_HOST = ACTIVE_CONFIG["host"]

# Thresholds
DEFAULT_STALE_DAYS = 90

# Distress keywords
DISTRESS_KEYWORDS = [
    "probate", "as-is", "as is", "tlc", "motivated", "fixer", 
    "handyman", "bank owned", "reo", "foreclosure", "estate sale",
    "investor", "cash only", "needs work", "potential", "must sell",
    "distressed", "below market", "must see", "bring offers"
]

# Startup logging
if not API_KEY:
    logger.warning("RAPIDAPI_KEY is not set - API calls will fail")
else:
    logger.info(f"StaleEngine initialized with provider: {API_PROVIDER}")
    logger.info(f"API Host: {API_HOST}")


def get_http_session():
    """Create a requests session with retry logic."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_api_headers():
    """Return headers for RapidAPI requests."""
    return {
        "X-RapidAPI-Key": API_KEY,
        "X-RapidAPI-Host": API_HOST
    }


def get_nested_value(data: dict, key_path: str, default=None):
    """Get nested dictionary value using dot notation."""
    keys = key_path.split('.')
    value = data
    try:
        for key in keys:
            value = value[key]
        return value
    except (KeyError, TypeError):
        return default


def calculate_days_on_market(list_date_str: str) -> int:
    """Calculate days on market from list date string."""
    if not list_date_str:
        return 0
    try:
        from datetime import datetime, timezone
        # Handle various date formats
        for fmt in ["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]:
            try:
                list_date = datetime.strptime(list_date_str[:19], fmt[:len(list_date_str)])
                now = datetime.now()
                return (now - list_date).days
            except ValueError:
                continue
        return 0
    except Exception:
        return 0


def search_properties_realtor_search(location: str, page: int = 1) -> dict:
    """Search using realtor-search API."""
    session = get_http_session()
    url = f"https://{API_HOST}/forsale"
    
    params = {
        "location": location,
        "page": page
    }
    
    logger.info(f"[realtor-search] Searching: {location}, page {page}")
    response = session.get(url, headers=get_api_headers(), params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def search_properties_realty_in_us(location: str, page: int = 1) -> dict:
    """Search using realty-in-us API (POST method)."""
    session = get_http_session()
    url = f"https://{API_HOST}/properties/v3/list"
    
    payload = {
        "limit": 50,
        "offset": (page - 1) * 50,
        "status": ["for_sale"],
        "sort": {
            "direction": "desc",
            "field": "list_date"
        }
    }
    
    # Determine if location is a ZIP code or city/state
    if location.isdigit() and len(location) == 5:
        payload["postal_code"] = location
    else:
        payload["city"] = location.split(",")[0].strip()
        if "," in location:
            payload["state_code"] = location.split(",")[1].strip()
    
    headers = get_api_headers()
    headers["Content-Type"] = "application/json"
    
    logger.info(f"[realty-in-us] Searching: {location}, page {page}")
    response = session.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def search_properties(location: str, page: int = 1) -> list:
    """
    Search for properties using the configured API provider.
    Returns a normalized list of properties.
    """
    try:
        if API_PROVIDER == "realtor-search":
            data = search_properties_realtor_search(location, page)
            # Normalize the response
            properties = data.get("data", []) if isinstance(data.get("data"), list) else []
            
        elif API_PROVIDER == "realty-in-us":
            data = search_properties_realty_in_us(location, page)
            properties = get_nested_value(data, "data.home_search.results", [])
            
        else:
            # Default fallback
            data = search_properties_realtor_search(location, page)
            properties = data.get("data", []) if isinstance(data.get("data"), list) else []
        
        return properties if properties else []
        
    except Exception as e:
        logger.error(f"Search failed: {str(e)}")
        raise


def normalize_property(prop: dict) -> dict:
    """Normalize property data from different API formats."""
    # Try different field names used by various APIs
    address = (
        prop.get("address") or 
        prop.get("location", {}).get("address", {}).get("line") or
        prop.get("streetAddress") or
        f"{prop.get('street', '')} {prop.get('city', '')}"
    )
    
    if isinstance(address, dict):
        address = address.get("line") or f"{address.get('street', '')} {address.get('city', '')}"
    
    price = (
        prop.get("price") or 
        prop.get("list_price") or
        prop.get("listPrice") or
        get_nested_value(prop, "price_raw") or
        0
    )
    
    # Handle price as string with $ and commas
    if isinstance(price, str):
        price = int(''.join(filter(str.isdigit, price)) or 0)
    
    days_on = (
        prop.get("days_on_market") or
        prop.get("daysOnMarket") or
        prop.get("dom") or
        0
    )
    
    # If no days_on_market, try to calculate from list_date
    if not days_on:
        list_date = prop.get("list_date") or prop.get("listDate") or prop.get("listed_date")
        if list_date:
            days_on = calculate_days_on_market(str(list_date))
    
    # Get description for keyword matching
    description = (
        prop.get("description") or
        prop.get("text") or
        get_nested_value(prop, "description.text") or
        ""
    )
    
    # Build property URL
    prop_url = (
        prop.get("url") or
        prop.get("property_url") or
        prop.get("permalink") or
        prop.get("rdc_web_url") or
        ""
    )
    if prop_url and not prop_url.startswith("http"):
        prop_url = f"https://www.realtor.com{prop_url}"
    
    return {
        "id": prop.get("property_id") or prop.get("zpid") or prop.get("id"),
        "address": address,
        "city": prop.get("city") or get_nested_value(prop, "location.address.city"),
        "state": prop.get("state") or get_nested_value(prop, "location.address.state_code"),
        "zip": prop.get("postal_code") or prop.get("zip") or get_nested_value(prop, "location.address.postal_code"),
        "price": price,
        "bedrooms": prop.get("beds") or prop.get("bedrooms") or get_nested_value(prop, "description.beds"),
        "bathrooms": prop.get("baths") or prop.get("bathrooms") or get_nested_value(prop, "description.baths"),
        "sqft": prop.get("sqft") or prop.get("livingArea") or get_nested_value(prop, "description.sqft"),
        "property_type": prop.get("type") or prop.get("propertyType") or get_nested_value(prop, "description.type"),
        "days_on_market": days_on,
        "list_date": prop.get("list_date") or prop.get("listDate"),
        "description": description[:500] if description else None,
        "url": prop_url,
        "image": prop.get("photo") or prop.get("primary_photo", {}).get("href") if isinstance(prop.get("primary_photo"), dict) else prop.get("primary_photo")
    }


def extract_stale_leads(properties: list, stale_days: int = DEFAULT_STALE_DAYS) -> list:
    """Filter properties for stale listings."""
    leads = []
    
    for prop in properties:
        normalized = normalize_property(prop)
        days_on = normalized.get("days_on_market", 0)
        
        if days_on >= stale_days:
            normalized["signal"] = "STALE"
            leads.append(normalized)
    
    return leads


def extract_distress_signals(properties: list) -> list:
    """Filter properties with distress keywords."""
    leads = []
    
    for prop in properties:
        normalized = normalize_property(prop)
        
        # Build searchable text from all text fields
        searchable = " ".join([
            str(normalized.get("address", "")),
            str(normalized.get("description", "")),
        ]).lower()
        
        matched = [kw for kw in DISTRESS_KEYWORDS if kw in searchable]
        
        if matched:
            normalized["signal"] = "DISTRESS"
            normalized["matched_keywords"] = matched
            leads.append(normalized)
    
    return leads


# --- ROUTES ---

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "online",
        "service": "StaleEngine",
        "version": "3.0.0",
        "api_configured": API_KEY is not None,
        "api_provider": API_PROVIDER,
        "api_host": API_HOST,
        "endpoints": {
            "GET /find-stale?location=<city,state|zip>": "Find listings on market 90+ days",
            "GET /find-distress?location=<city,state|zip>": "Find listings with distress keywords",
            "GET /search?location=<city,state|zip>": "Combined search"
        },
        "supported_providers": list(API_CONFIGS.keys())
    })


@app.route('/find-stale', methods=['GET'])
def find_stale():
    """Find stale listings."""
    location = request.args.get('location')
    stale_days = request.args.get('days', DEFAULT_STALE_DAYS, type=int)
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({
            "success": False, 
            "error": "Missing required 'location' parameter",
            "example": "/find-stale?location=Houston,TX"
        }), 400
    
    if not API_KEY:
        return jsonify({"success": False, "error": "API key not configured"}), 500
    
    try:
        properties = search_properties(location, page)
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found",
                "results_count": 0,
                "leads": []
            })
        
        leads = extract_stale_leads(properties, stale_days)
        leads.sort(key=lambda x: x.get('days_on_market', 0), reverse=True)
        
        return jsonify({
            "success": True,
            "location": location,
            "filter": f"{stale_days}+ days on market",
            "total_searched": len(properties),
            "results_count": len(leads),
            "page": page,
            "leads": leads
        })
        
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "error": "Request timed out"}), 504
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error: {e}")
        return jsonify({
            "success": False,
            "error": f"API error: {e.response.status_code}",
            "message": "The upstream API may be down. Try a different provider.",
            "hint": f"Current provider: {API_PROVIDER}. Set REAL_ESTATE_API env var to switch."
        }), 502
    except Exception as e:
        logger.exception(f"Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/find-distress', methods=['GET'])
def find_distress():
    """Find listings with distress signals."""
    location = request.args.get('location')
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({
            "success": False, 
            "error": "Missing required 'location' parameter",
            "example": "/find-distress?location=Phoenix,AZ"
        }), 400
    
    if not API_KEY:
        return jsonify({"success": False, "error": "API key not configured"}), 500
    
    try:
        properties = search_properties(location, page)
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found",
                "results_count": 0,
                "leads": []
            })
        
        leads = extract_distress_signals(properties)
        
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
        
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "error": "Request timed out"}), 504
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error: {e}")
        return jsonify({
            "success": False,
            "error": f"API error: {e.response.status_code}",
            "message": "The upstream API may be down. Try a different provider."
        }), 502
    except Exception as e:
        logger.exception(f"Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/search', methods=['GET'])
def combined_search():
    """Combined stale + distress search."""
    location = request.args.get('location')
    stale_days = request.args.get('days', DEFAULT_STALE_DAYS, type=int)
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({
            "success": False, 
            "error": "Missing required 'location' parameter",
            "example": "/search?location=Miami,FL&days=60"
        }), 400
    
    if not API_KEY:
        return jsonify({"success": False, "error": "API key not configured"}), 500
    
    try:
        properties = search_properties(location, page)
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found",
                "summary": {"stale": 0, "distress": 0, "hot": 0},
                "stale_leads": [],
                "distress_leads": []
            })
        
        stale_leads = extract_stale_leads(properties, stale_days)
        distress_leads = extract_distress_signals(properties)
        
        stale_leads.sort(key=lambda x: x.get('days_on_market', 0), reverse=True)
        
        # Find "hot" leads (both stale AND distressed)
        stale_ids = {l.get('id') for l in stale_leads if l.get('id')}
        distress_ids = {l.get('id') for l in distress_leads if l.get('id')}
        hot_ids = stale_ids & distress_ids
        
        return jsonify({
            "success": True,
            "location": location,
            "provider": API_PROVIDER,
            "filters": {
                "stale_days": stale_days,
                "distress_keywords": DISTRESS_KEYWORDS
            },
            "total_searched": len(properties),
            "page": page,
            "summary": {
                "stale": len(stale_leads),
                "distress": len(distress_leads),
                "hot": len(hot_ids)
            },
            "hot_lead_ids": list(hot_ids),
            "stale_leads": stale_leads,
            "distress_leads": distress_leads
        })
        
    except requests.exceptions.Timeout:
        return jsonify({"success": False, "error": "Request timed out"}), 504
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error: {e}")
        return jsonify({
            "success": False,
            "error": f"API error: {e.response.status_code}",
            "message": "The upstream API may be down. Try a different provider."
        }), 502
    except Exception as e:
        logger.exception(f"Error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({
        "success": False,
        "error": "Endpoint not found",
        "endpoints": ["/", "/find-stale", "/find-distress", "/search"]
    }), 404


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=PORT, debug=True)
