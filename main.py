import os
import re
import json
import logging
import random
import time
from flask import Flask, jsonify, request
import requests
from datetime import datetime

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

PORT = int(os.environ.get("PORT", 8080))
DEFAULT_STALE_DAYS = 90

DISTRESS_KEYWORDS = [
    "probate", "as-is", "as is", "tlc", "motivated", "fixer", 
    "handyman", "bank owned", "reo", "foreclosure", "estate sale",
    "investor", "cash only", "needs work", "potential", "must sell",
    "distressed", "below market", "bring offers", "price reduced"
]

# Rotate user agents to avoid detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


def get_headers():
    """Get request headers with random user agent."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }


def build_zillow_url(location: str, page: int = 1) -> str:
    """Build Zillow search URL from location."""
    # Clean and format location
    location = location.strip()
    
    # Format for URL (replace spaces and commas)
    location_slug = location.replace(" ", "-").replace(",", "").replace("--", "-")
    
    # Build search URL
    base_url = f"https://www.zillow.com/{location_slug}/for_sale/"
    
    if page > 1:
        base_url += f"{page}_p/"
    
    return base_url


def extract_json_data(html: str) -> dict:
    """Extract the embedded JSON data from Zillow HTML."""
    
    # Zillow embeds data in a script tag with id="__NEXT_DATA__"
    pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
    match = re.search(pattern, html, re.DOTALL)
    
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.error("Failed to parse __NEXT_DATA__ JSON")
    
    # Fallback: Try to find preloaded state
    pattern2 = r'"searchPageState":\s*({.*?"searchResults".*?})\s*,'
    match2 = re.search(pattern2, html, re.DOTALL)
    
    if match2:
        try:
            # This is trickier - need to balance braces
            return {"searchPageState": json.loads(match2.group(1))}
        except:
            pass
    
    # Another fallback: look for listResults in the HTML
    pattern3 = r'"listResults":\s*(\[.*?\])\s*,'
    match3 = re.search(pattern3, html)
    
    if match3:
        try:
            return {"listResults": json.loads(match3.group(1))}
        except:
            pass
    
    return {}


def extract_properties_from_data(data: dict) -> list:
    """Navigate the nested JSON to find property listings."""
    properties = []
    
    # Try different paths where Zillow stores listings
    try:
        # Path 1: __NEXT_DATA__ structure
        if "props" in data:
            page_props = data.get("props", {}).get("pageProps", {})
            search_results = page_props.get("searchPageState", {}).get("cat1", {}).get("searchResults", {})
            list_results = search_results.get("listResults", [])
            if list_results:
                return list_results
        
        # Path 2: Direct searchPageState
        if "searchPageState" in data:
            search_results = data["searchPageState"].get("cat1", {}).get("searchResults", {})
            list_results = search_results.get("listResults", [])
            if list_results:
                return list_results
        
        # Path 3: Direct listResults
        if "listResults" in data:
            return data["listResults"]
        
        # Path 4: Recursive search for listResults
        def find_list_results(obj):
            if isinstance(obj, dict):
                if "listResults" in obj and isinstance(obj["listResults"], list):
                    return obj["listResults"]
                for v in obj.values():
                    result = find_list_results(v)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = find_list_results(item)
                    if result:
                        return result
            return None
        
        found = find_list_results(data)
        if found:
            return found
            
    except Exception as e:
        logger.error(f"Error extracting properties: {e}")
    
    return properties


def scrape_zillow(location: str, page: int = 1) -> list:
    """Scrape Zillow listings for a location."""
    url = build_zillow_url(location, page)
    logger.info(f"Scraping: {url}")
    
    # Add small random delay to be respectful
    time.sleep(random.uniform(0.5, 1.5))
    
    try:
        response = requests.get(url, headers=get_headers(), timeout=15)
        response.raise_for_status()
        
        html = response.text
        
        # Check if we hit a CAPTCHA or block
        if "captcha" in html.lower() or "blocked" in html.lower():
            logger.warning("Possible CAPTCHA/block detected")
            return []
        
        # Extract JSON data
        data = extract_json_data(html)
        
        if not data:
            logger.warning("Could not extract JSON data from page")
            return []
        
        # Get properties
        properties = extract_properties_from_data(data)
        logger.info(f"Found {len(properties)} properties")
        
        return properties
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        raise


def normalize_property(prop: dict) -> dict:
    """Normalize Zillow property data."""
    
    # Address
    address = prop.get("address", "")
    if not address:
        street = prop.get("streetAddress", "")
        city = prop.get("city", "")
        state = prop.get("state", "")
        zipcode = prop.get("zipcode", "")
        address = f"{street}, {city}, {state} {zipcode}".strip(", ")
    
    # Price
    price = prop.get("price", 0)
    if isinstance(price, str):
        price = int(''.join(filter(str.isdigit, price)) or 0)
    if not price:
        price = prop.get("unformattedPrice", 0)
    
    # Days on Zillow
    days_on = prop.get("daysOnZillow", 0)
    if not days_on:
        # Try to get from variableData
        var_data = prop.get("variableData", {})
        if isinstance(var_data, dict):
            text = var_data.get("text", "")
            # Parse "X days on Zillow" pattern
            match = re.search(r'(\d+)\s*day', text.lower())
            if match:
                days_on = int(match.group(1))
    
    # URL
    detail_url = prop.get("detailUrl", "")
    if detail_url and not detail_url.startswith("http"):
        detail_url = f"https://www.zillow.com{detail_url}"
    
    # Image
    img = prop.get("imgSrc", "") or prop.get("image", "")
    
    # Beds/Baths/Sqft
    beds = prop.get("beds", 0) or prop.get("bedrooms", 0)
    baths = prop.get("baths", 0) or prop.get("bathrooms", 0)
    sqft = prop.get("area", 0) or prop.get("livingArea", 0)
    
    # Status text (may contain keywords)
    status_text = prop.get("statusText", "")
    var_text = prop.get("variableData", {}).get("text", "") if isinstance(prop.get("variableData"), dict) else ""
    
    return {
        "zpid": prop.get("zpid") or prop.get("id"),
        "address": address,
        "city": prop.get("city", ""),
        "state": prop.get("state", ""),
        "zip": prop.get("zipcode", ""),
        "price": price,
        "bedrooms": beds,
        "bathrooms": baths,
        "sqft": sqft,
        "property_type": prop.get("homeType", ""),
        "days_on_market": days_on,
        "status_text": status_text,
        "variable_text": var_text,
        "zestimate": prop.get("zestimate", 0),
        "url": detail_url,
        "image": img
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
        
        # Search in all text fields
        text = " ".join([
            str(norm.get("address", "")),
            str(norm.get("status_text", "")),
            str(norm.get("variable_text", "")),
        ]).lower()
        
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
        "version": "5.0.0",
        "description": "Self-contained Zillow scraper - no external APIs",
        "endpoints": {
            "GET /find-stale?location=Houston,TX": "Listings 90+ days on market",
            "GET /find-stale?location=77001&days=60": "By ZIP, custom days threshold",
            "GET /find-distress?location=Phoenix,AZ": "Distress keyword matches",
            "GET /search?location=Miami,FL": "Combined stale + distress search"
        },
        "note": "Location can be: City,ST | ZIP code | Neighborhood"
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
            "examples": [
                "/find-stale?location=Austin,TX",
                "/find-stale?location=90210&days=60",
                "/find-stale?location=Brooklyn,NY&page=2"
            ]
        }), 400
    
    try:
        properties = scrape_zillow(location, page)
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found. Try a different location format.",
                "suggestions": [
                    "Use 'City, ST' format (e.g., 'Houston, TX')",
                    "Use ZIP code (e.g., '77001')",
                    "Check spelling"
                ],
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
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Scrape failed: {e}")
        return jsonify({
            "success": False,
            "error": "Failed to scrape Zillow",
            "details": str(e)
        }), 502
    except Exception as e:
        logger.exception("Unexpected error")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/find-distress', methods=['GET'])
def route_find_distress():
    location = request.args.get('location')
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' parameter"}), 400
    
    try:
        properties = scrape_zillow(location, page)
        
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
        
    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": "Scrape failed", "details": str(e)}), 502
    except Exception as e:
        logger.exception("Error")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/search', methods=['GET'])
def route_search():
    location = request.args.get('location')
    days = request.args.get('days', DEFAULT_STALE_DAYS, type=int)
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' parameter"}), 400
    
    try:
        properties = scrape_zillow(location, page)
        
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
        
    except requests.exceptions.RequestException as e:
        return jsonify({"success": False, "error": "Scrape failed", "details": str(e)}), 502
    except Exception as e:
        logger.exception("Error")
        return jsonify({"success": False, "error": str(e)}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({
        "error": "Endpoint not found",
        "endpoints": ["/", "/find-stale", "/find-distress", "/search"]
    }), 404


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=PORT, debug=True)
