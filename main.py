import os
import json
import logging
import re
import random
from flask import Flask, jsonify, request
import requests
from urllib.parse import quote, urlencode

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 8080))
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")
DEFAULT_STALE_DAYS = 90

DISTRESS_KEYWORDS = [
    "probate", "as-is", "as is", "tlc", "motivated", "fixer", 
    "handyman", "bank owned", "reo", "foreclosure", "estate sale",
    "investor", "cash only", "needs work", "potential", "must sell",
    "distressed", "below market", "bring offers", "price reduced"
]

if not SCRAPER_API_KEY:
    logger.warning("SCRAPER_API_KEY not set - scraping will fail")
else:
    logger.info(f"StaleEngine ready with ScraperAPI (key length: {len(SCRAPER_API_KEY)})")


def scrape_with_scraperapi(url: str) -> str:
    """Fetch URL through ScraperAPI proxy."""
    
    api_url = "https://api.scraperapi.com"
    
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "render": "false",  # Set to true if JS rendering needed
        "country_code": "us",
    }
    
    logger.info(f"Fetching via ScraperAPI: {url}")
    
    response = requests.get(api_url, params=params, timeout=60)
    response.raise_for_status()
    
    return response.text


def build_zillow_url(location: str, page: int = 1) -> str:
    """Build Zillow search URL."""
    location = location.strip()
    location_slug = location.replace(" ", "-").replace(",", "").replace("--", "-")
    
    base_url = f"https://www.zillow.com/{location_slug}/for_sale/"
    
    if page > 1:
        base_url += f"{page}_p/"
    
    # Add sort by days on market
    base_url += "?searchQueryState=" + quote(json.dumps({
        "pagination": {"currentPage": page},
        "isMapVisible": False,
        "filterState": {
            "sortSelection": {"value": "days"},
            "isAllHomes": {"value": True}
        },
        "isListVisible": True
    }))
    
    return base_url


def extract_listings_from_html(html: str) -> list:
    """Extract property listings from Zillow HTML."""
    
    properties = []
    
    # Method 1: Look for __NEXT_DATA__ JSON
    next_data_match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.DOTALL
    )
    
    if next_data_match:
        try:
            data = json.loads(next_data_match.group(1))
            
            # Navigate to find listResults
            props = data.get("props", {})
            page_props = props.get("pageProps", {})
            search_state = page_props.get("searchPageState", {})
            cat1 = search_state.get("cat1", {})
            search_results = cat1.get("searchResults", {})
            list_results = search_results.get("listResults", [])
            
            if list_results:
                logger.info(f"Found {len(list_results)} listings via __NEXT_DATA__")
                # Debug: log first listing's keys to see available fields
                if list_results and len(list_results) > 0:
                    first = list_results[0]
                    logger.info(f"Sample listing keys: {list(first.keys())}")
                    # Log hdpData specifically
                    hdp = first.get("hdpData", {})
                    if hdp:
                        logger.info(f"hdpData keys: {list(hdp.keys()) if isinstance(hdp, dict) else 'not a dict'}")
                        home_info = hdp.get("homeInfo", {}) if isinstance(hdp, dict) else {}
                        if home_info:
                            logger.info(f"homeInfo keys: {list(home_info.keys())}")
                            logger.info(f"daysOnZillow: {home_info.get('daysOnZillow', 'NOT FOUND')}")
                return list_results
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse __NEXT_DATA__: {e}")
    
    # Method 2: Look for inline listResults
    list_results_match = re.search(
        r'"listResults"\s*:\s*(\[.*?\])\s*,\s*"mapResults"',
        html, re.DOTALL
    )
    
    if list_results_match:
        try:
            list_results = json.loads(list_results_match.group(1))
            logger.info(f"Found {len(list_results)} listings via listResults pattern")
            return list_results
        except:
            pass
    
    # Method 3: Look for any JSON array with zpid
    zpid_arrays = re.findall(
        r'\[(?:[^]]*"zpid"[^]]*)\]',
        html
    )
    
    for arr_str in zpid_arrays:
        try:
            arr = json.loads(arr_str)
            if isinstance(arr, list) and len(arr) > 0 and isinstance(arr[0], dict):
                if "zpid" in arr[0] or "address" in arr[0]:
                    logger.info(f"Found {len(arr)} listings via zpid pattern")
                    return arr
        except:
            continue
    
    logger.warning("Could not extract listings from HTML")
    return properties


def search_zillow(location: str, page: int = 1) -> list:
    """Search Zillow for listings."""
    
    if not SCRAPER_API_KEY:
        raise ValueError("SCRAPER_API_KEY not configured")
    
    url = build_zillow_url(location, page)
    
    try:
        html = scrape_with_scraperapi(url)
        
        # Check for blocks/errors
        if "captcha" in html.lower():
            logger.error("Hit CAPTCHA - ScraperAPI should handle this, retrying...")
            # Retry with render=true
            html = scrape_with_scraperapi_render(url)
        
        listings = extract_listings_from_html(html)
        return listings
        
    except requests.exceptions.RequestException as e:
        logger.error(f"ScraperAPI request failed: {e}")
        raise


def scrape_with_scraperapi_render(url: str) -> str:
    """Fetch URL with JavaScript rendering enabled."""
    
    api_url = "https://api.scraperapi.com"
    
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "render": "true",
        "country_code": "us",
        "wait_for_selector": "article",
    }
    
    logger.info(f"Fetching with JS render: {url}")
    
    response = requests.get(api_url, params=params, timeout=90)
    response.raise_for_status()
    
    return response.text


def normalize_property(prop: dict) -> dict:
    """Normalize property data to standard format."""
    
    # Address
    address = prop.get("address", "") or prop.get("addressStreet", "")
    if isinstance(address, dict):
        address = address.get("streetAddress", "") or f"{address.get('city', '')}, {address.get('state', '')}"
    
    # Price
    price = prop.get("unformattedPrice", 0) or prop.get("price", 0)
    if isinstance(price, str):
        price = int(''.join(filter(str.isdigit, price)) or 0)
    
    # Days on market - check multiple locations
    days_on = 0
    
    # Check hdpData.homeInfo
    hdp_data = prop.get("hdpData", {})
    if isinstance(hdp_data, dict):
        home_info = hdp_data.get("homeInfo", {})
        if isinstance(home_info, dict):
            days_on = home_info.get("daysOnZillow", 0) or home_info.get("timeOnZillow", 0)
    
    # Direct field
    if not days_on:
        days_on = prop.get("daysOnZillow", 0) or prop.get("timeOnZillow", 0)
    
    # If value is very large, it's likely milliseconds - convert to days
    if days_on and days_on > 10000:
        # Convert milliseconds to days
        days_on = days_on // (1000 * 60 * 60 * 24)
    
    # Check variableData text
    if not days_on:
        var_data = prop.get("variableData", {})
        if isinstance(var_data, dict):
            text = var_data.get("text", "")
            match = re.search(r'(\d+)\s*day', text.lower())
            if match:
                days_on = int(match.group(1))
    
    # Check flexFieldText (sometimes has "X days on Zillow")
    if not days_on:
        flex_text = prop.get("flexFieldText", "")
        if flex_text:
            match = re.search(r'(\d+)\s*day', flex_text.lower())
            if match:
                days_on = int(match.group(1))
    
    # URL
    detail_url = prop.get("detailUrl", "") or prop.get("hdpUrl", "")
    if detail_url and not detail_url.startswith("http"):
        detail_url = f"https://www.zillow.com{detail_url}"
    
    # Status text for keyword matching
    status = prop.get("statusText", "")
    marketing_status = prop.get("marketingStatusSimplifiedCd", "")
    flex_text = prop.get("flexFieldText", "")
    
    return {
        "zpid": prop.get("zpid") or prop.get("id"),
        "address": address,
        "city": prop.get("addressCity", ""),
        "state": prop.get("addressState", ""),
        "zip": prop.get("addressZipcode", ""),
        "price": price,
        "bedrooms": prop.get("beds", 0),
        "bathrooms": prop.get("baths", 0),
        "sqft": prop.get("area", 0),
        "property_type": prop.get("homeType", "") or marketing_status,
        "days_on_market": days_on,
        "status_text": f"{status} {marketing_status} {flex_text}".strip(),
        "zestimate": prop.get("zestimate", 0),
        "url": detail_url,
        "image": prop.get("imgSrc", ""),
        "_search_text": f"{address} {status} {marketing_status} {flex_text}".lower()
    }


def find_stale(properties: list, min_days: int) -> list:
    """Find stale listings (on market too long)."""
    leads = []
    for prop in properties:
        norm = normalize_property(prop)
        if norm["days_on_market"] >= min_days:
            norm["signal"] = "STALE"
            norm.pop("_search_text", None)
            leads.append(norm)
    return sorted(leads, key=lambda x: x["days_on_market"], reverse=True)


def find_distress(properties: list) -> list:
    """Find listings with distress keywords."""
    leads = []
    for prop in properties:
        norm = normalize_property(prop)
        
        # Use pre-built search text
        search_text = norm.get("_search_text", "")
        
        matched = [kw for kw in DISTRESS_KEYWORDS if kw in search_text]
        
        if matched:
            norm["signal"] = "DISTRESS"
            norm["matched_keywords"] = matched
            norm.pop("_search_text", None)
            leads.append(norm)
    
    return leads


# --- ROUTES ---

@app.route('/', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "service": "StaleEngine",
        "version": "7.0.0",
        "scraper_api_configured": SCRAPER_API_KEY is not None,
        "endpoints": {
            "GET /find-stale?location=Houston,TX": "Find listings 90+ days on market",
            "GET /find-stale?location=77001&days=60": "Custom days threshold",
            "GET /find-distress?location=Phoenix,AZ": "Find distress keywords",
            "GET /search?location=Miami,FL": "Combined stale + distress search"
        }
    })


@app.route('/find-stale', methods=['GET'])
def route_find_stale():
    location = request.args.get('location')
    days = request.args.get('days', DEFAULT_STALE_DAYS, type=int)
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({
            "success": False, 
            "error": "Missing 'location' parameter",
            "example": "/find-stale?location=Chicago,IL&days=90"
        }), 400
    
    if not SCRAPER_API_KEY:
        return jsonify({
            "success": False,
            "error": "SCRAPER_API_KEY not configured in environment"
        }), 500
    
    try:
        properties = search_zillow(location, page)
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found. Try different location format (e.g., 'Chicago IL' or '60601')",
                "results_count": 0,
                "leads": []
            })
        
        leads = find_stale(properties, days)
        
        return jsonify({
            "success": True,
            "location": location,
            "filter": f"{days}+ days on market",
            "total_scraped": len(properties),
            "results_count": len(leads),
            "page": page,
            "leads": leads
        })
        
    except Exception as e:
        logger.exception("Error in find-stale")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/find-distress', methods=['GET'])
def route_find_distress():
    location = request.args.get('location')
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' parameter"}), 400
    
    if not SCRAPER_API_KEY:
        return jsonify({"success": False, "error": "SCRAPER_API_KEY not configured"}), 500
    
    try:
        properties = search_zillow(location, page)
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found",
                "results_count": 0,
                "leads": []
            })
        
        leads = find_distress(properties)
        
        return jsonify({
            "success": True,
            "location": location,
            "filter": "distress keywords",
            "keywords": DISTRESS_KEYWORDS,
            "total_scraped": len(properties),
            "results_count": len(leads),
            "page": page,
            "leads": leads
        })
        
    except Exception as e:
        logger.exception("Error in find-distress")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/search', methods=['GET'])
def route_search():
    location = request.args.get('location')
    days = request.args.get('days', DEFAULT_STALE_DAYS, type=int)
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' parameter"}), 400
    
    if not SCRAPER_API_KEY:
        return jsonify({"success": False, "error": "SCRAPER_API_KEY not configured"}), 500
    
    try:
        properties = search_zillow(location, page)
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found",
                "summary": {"stale": 0, "distress": 0, "hot_leads": 0},
                "stale_leads": [],
                "distress_leads": []
            })
        
        stale = find_stale(properties, days)
        distress = find_distress(properties)
        
        # Hot leads = both stale AND distressed
        stale_ids = {p["zpid"] for p in stale if p.get("zpid")}
        distress_ids = {p["zpid"] for p in distress if p.get("zpid")}
        hot_ids = stale_ids & distress_ids
        
        return jsonify({
            "success": True,
            "location": location,
            "page": page,
            "total_scraped": len(properties),
            "summary": {
                "stale": len(stale),
                "distress": len(distress),
                "hot_leads": len(hot_ids)
            },
            "hot_lead_ids": list(hot_ids),
            "stale_leads": stale,
            "distress_leads": distress
        })
        
    except Exception as e:
        logger.exception("Error in search")
        return jsonify({"success": False, "error": str(e)}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({
        "error": "Endpoint not found",
        "endpoints": ["/", "/find-stale", "/find-distress", "/search"]
    }), 404


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=PORT, debug=True)
