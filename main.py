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
API_HOST = "zillow-com1.p.rapidapi.com"
PORT = int(os.environ.get("PORT", 8080))

# Thresholds
DEFAULT_STALE_DAYS = 90
DEFAULT_PAGE_SIZE = 40

# Distress keywords to search for in listings
DISTRESS_KEYWORDS = [
    "probate", "as-is", "as is", "tlc", "motivated", "fixer", 
    "handyman", "bank owned", "reo", "foreclosure", "estate sale",
    "investor", "cash only", "needs work", "potential", "must sell"
]

# Log startup status
if not API_KEY:
    logger.warning("RAPIDAPI_KEY is not set - API calls will fail")
else:
    logger.info(f"StaleEngine initialized. API key loaded (length: {len(API_KEY)})")


def get_http_session():
    """Create a requests session with retry logic."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
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


def search_properties(location: str, status: str = "ForSale", page: int = 1) -> dict:
    """
    Search for properties via Zillow API.
    Returns raw API response or raises an exception.
    """
    session = get_http_session()
    url = f"https://{API_HOST}/propertyExtendedSearch"
    
    params = {
        "location": location,
        "status_type": status,
        "page": page
    }
    
    logger.info(f"Searching properties: location={location}, status={status}, page={page}")
    
    response = session.get(
        url, 
        headers=get_api_headers(), 
        params=params, 
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def extract_stale_leads(properties: list, stale_days: int = DEFAULT_STALE_DAYS) -> list:
    """
    Filter properties for stale listings (on market too long).
    """
    leads = []
    
    for prop in properties:
        days_on = prop.get('daysOnZillow') or prop.get('days_on_zillow') or 0
        
        if days_on >= stale_days:
            leads.append(format_lead(prop, days_on, "STALE"))
    
    return leads


def extract_distress_signals(properties: list) -> list:
    """
    Filter properties that have distress keywords in available text fields.
    Note: Basic search has limited description data - checks address and other fields.
    """
    leads = []
    
    for prop in properties:
        # Combine available text fields for keyword search
        searchable_text = " ".join([
            str(prop.get('address', '')),
            str(prop.get('listingSubType', {}).get('text', '') if isinstance(prop.get('listingSubType'), dict) else ''),
            str(prop.get('variableData', {}).get('text', '') if isinstance(prop.get('variableData'), dict) else ''),
        ]).lower()
        
        matched_keywords = [kw for kw in DISTRESS_KEYWORDS if kw in searchable_text]
        
        if matched_keywords:
            days_on = prop.get('daysOnZillow') or prop.get('days_on_zillow') or 0
            lead = format_lead(prop, days_on, "DISTRESS")
            lead['matched_keywords'] = matched_keywords
            leads.append(lead)
    
    return leads


def format_lead(prop: dict, days_on: int, signal: str) -> dict:
    """Format a property into a standardized lead object."""
    zpid = prop.get('zpid')
    detail_url = prop.get('detailUrl', '')
    
    # Build Zillow URL
    if detail_url and not detail_url.startswith('http'):
        detail_url = f"https://www.zillow.com{detail_url}"
    elif zpid and not detail_url:
        detail_url = f"https://www.zillow.com/homedetails/{zpid}_zpid/"
    
    return {
        "zpid": zpid,
        "address": prop.get('address'),
        "price": prop.get('price'),
        "bedrooms": prop.get('bedrooms'),
        "bathrooms": prop.get('bathrooms'),
        "living_area": prop.get('livingArea'),
        "home_type": prop.get('homeType'),
        "days_on_market": days_on,
        "signal": signal,
        "listing_status": prop.get('listingStatus'),
        "zestimate": prop.get('zestimate'),
        "url": detail_url,
        "image": prop.get('imgSrc')
    }


# --- ROUTES ---

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "online",
        "service": "StaleEngine",
        "version": "2.0.0",
        "api_configured": API_KEY is not None,
        "endpoints": {
            "/find-stale": "Find listings on market 90+ days",
            "/find-distress": "Find listings with distress keywords",
            "/search": "Combined search with filters"
        }
    })


@app.route('/find-stale', methods=['GET'])
def find_stale():
    """
    Find stale listings (on market for extended period).
    
    Query params:
        - location (required): City, ZIP, or address
        - days (optional): Minimum days on market (default: 90)
        - page (optional): Page number (default: 1)
    """
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
        return jsonify({
            "success": False, 
            "error": "Server misconfigured: API key not set"
        }), 500
    
    try:
        data = search_properties(location, page=page)
        properties = data.get('props') or data.get('results') or []
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found for this location",
                "results_count": 0,
                "leads": []
            })
        
        leads = extract_stale_leads(properties, stale_days)
        
        # Sort by days on market descending
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
        logger.error(f"Timeout searching {location}")
        return jsonify({
            "success": False, 
            "error": "Request timed out - try again"
        }), 504
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error for {location}: {e.response.status_code}")
        return jsonify({
            "success": False,
            "error": f"API returned error: {e.response.status_code}",
            "details": str(e)
        }), 502
        
    except Exception as e:
        logger.exception(f"Unexpected error searching {location}")
        return jsonify({
            "success": False, 
            "error": "Internal server error",
            "details": str(e)
        }), 500


@app.route('/find-distress', methods=['GET'])
def find_distress():
    """
    Find listings with distress signals/keywords.
    
    Query params:
        - location (required): City, ZIP, or address
        - page (optional): Page number (default: 1)
    """
    location = request.args.get('location')
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({
            "success": False, 
            "error": "Missing required 'location' parameter",
            "example": "/find-distress?location=Phoenix,AZ"
        }), 400
    
    if not API_KEY:
        return jsonify({
            "success": False, 
            "error": "Server misconfigured: API key not set"
        }), 500
    
    try:
        data = search_properties(location, page=page)
        properties = data.get('props') or data.get('results') or []
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found for this location",
                "results_count": 0,
                "leads": []
            })
        
        leads = extract_distress_signals(properties)
        
        return jsonify({
            "success": True,
            "location": location,
            "filter": "distress keywords",
            "keywords_checked": DISTRESS_KEYWORDS,
            "total_searched": len(properties),
            "results_count": len(leads),
            "page": page,
            "leads": leads
        })
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout searching {location}")
        return jsonify({
            "success": False, 
            "error": "Request timed out - try again"
        }), 504
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error for {location}: {e.response.status_code}")
        return jsonify({
            "success": False,
            "error": f"API returned error: {e.response.status_code}",
            "details": str(e)
        }), 502
        
    except Exception as e:
        logger.exception(f"Unexpected error searching {location}")
        return jsonify({
            "success": False, 
            "error": "Internal server error",
            "details": str(e)
        }), 500


@app.route('/search', methods=['GET'])
def combined_search():
    """
    Combined search - finds both stale and distressed properties.
    
    Query params:
        - location (required): City, ZIP, or address
        - days (optional): Minimum days for stale (default: 90)
        - page (optional): Page number (default: 1)
    """
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
        return jsonify({
            "success": False, 
            "error": "Server misconfigured: API key not set"
        }), 500
    
    try:
        data = search_properties(location, page=page)
        properties = data.get('props') or data.get('results') or []
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found for this location",
                "results_count": 0,
                "stale_leads": [],
                "distress_leads": []
            })
        
        stale_leads = extract_stale_leads(properties, stale_days)
        distress_leads = extract_distress_signals(properties)
        
        # Sort stale by days on market
        stale_leads.sort(key=lambda x: x.get('days_on_market', 0), reverse=True)
        
        # Find properties that are BOTH stale and distressed
        stale_zpids = {l['zpid'] for l in stale_leads}
        distress_zpids = {l['zpid'] for l in distress_leads}
        hot_leads_zpids = stale_zpids & distress_zpids
        
        return jsonify({
            "success": True,
            "location": location,
            "filters": {
                "stale_days": stale_days,
                "distress_keywords": DISTRESS_KEYWORDS
            },
            "total_searched": len(properties),
            "page": page,
            "summary": {
                "stale_count": len(stale_leads),
                "distress_count": len(distress_leads),
                "hot_leads_count": len(hot_leads_zpids)
            },
            "hot_lead_ids": list(hot_leads_zpids),
            "stale_leads": stale_leads,
            "distress_leads": distress_leads
        })
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout searching {location}")
        return jsonify({
            "success": False, 
            "error": "Request timed out - try again"
        }), 504
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error for {location}: {e.response.status_code}")
        return jsonify({
            "success": False,
            "error": f"API returned error: {e.response.status_code}",
            "details": str(e)
        }), 502
        
    except Exception as e:
        logger.exception(f"Unexpected error searching {location}")
        return jsonify({
            "success": False, 
            "error": "Internal server error",
            "details": str(e)
        }), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({
        "success": False,
        "error": "Endpoint not found",
        "available_endpoints": ["/", "/find-stale", "/find-distress", "/search"]
    }), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({
        "success": False,
        "error": "Internal server error"
    }), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=PORT, debug=True)
