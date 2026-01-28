import os
import json
import logging
import random
import time
import re
from flask import Flask, jsonify, request
import requests
from urllib.parse import quote

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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


def get_search_query_state(location: str, page: int = 1) -> dict:
    """Build Zillow's search query state object."""
    
    # Determine if location is a ZIP code
    is_zip = location.strip().isdigit() and len(location.strip()) == 5
    
    if is_zip:
        region_type = "zipcode"
        search_term = location.strip()
    else:
        region_type = "city"
        search_term = location.strip()
    
    return {
        "pagination": {"currentPage": page},
        "isMapVisible": False,
        "filterState": {
            "sortSelection": {"value": "days"},  # Sort by days on Zillow
            "isAllHomes": {"value": True},
            "isForSaleByAgent": {"value": True},
            "isForSaleByOwner": {"value": True},
            "isNewConstruction": {"value": False},
            "isForSaleForeclosure": {"value": True},
            "isComingSoon": {"value": False},
            "isAuction": {"value": True},
        },
        "isListVisible": True,
        "mapZoom": 11,
        "usersSearchTerm": search_term
    }


def search_zillow_api(location: str, page: int = 1) -> list:
    """
    Use Zillow's internal search API.
    This is the same API their frontend uses.
    """
    
    # Zillow's internal API endpoint
    api_url = "https://www.zillow.com/async-create-search-page-state"
    
    # Build the search request
    search_query = get_search_query_state(location, page)
    
    payload = {
        "searchQueryState": search_query,
        "wants": {
            "cat1": ["listResults"],
            "cat2": ["total"]
        },
        "requestId": random.randint(1, 10),
        "isDebugRequest": False
    }
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://www.zillow.com",
        "Referer": f"https://www.zillow.com/{quote(location)}/",
        "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    # Add delay
    time.sleep(random.uniform(0.3, 0.8))
    
    try:
        logger.info(f"Searching Zillow API for: {location} (page {page})")
        
        response = requests.put(
            api_url,
            json=payload,
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 403:
            logger.warning("API returned 403 - trying alternative method")
            return search_zillow_graphql(location, page)
        
        response.raise_for_status()
        data = response.json()
        
        # Extract results
        cat1 = data.get("cat1", {})
        search_results = cat1.get("searchResults", {})
        list_results = search_results.get("listResults", [])
        
        logger.info(f"Found {len(list_results)} listings")
        return list_results
        
    except Exception as e:
        logger.error(f"API search failed: {e}")
        # Try fallback
        return search_zillow_graphql(location, page)


def search_zillow_graphql(location: str, page: int = 1) -> list:
    """
    Fallback: Use Zillow's GraphQL endpoint.
    """
    
    url = "https://www.zillow.com/graphql/"
    
    # GraphQL query for property search
    query = """
    query SearchResultsQuery($searchQueryState: SearchQueryState!) {
        searchResults(searchQueryState: $searchQueryState) {
            listResults {
                zpid
                address
                price
                beds
                baths
                area
                daysOnZillow
                statusText
                detailUrl
                imgSrc
                homeType
                zestimate
            }
            totalResultCount
        }
    }
    """
    
    variables = {
        "searchQueryState": get_search_query_state(location, page)
    }
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.zillow.com",
        "Referer": "https://www.zillow.com/",
    }
    
    try:
        response = requests.post(
            url,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=15
        )
        
        if response.status_code != 200:
            logger.warning(f"GraphQL returned {response.status_code}")
            return search_redfin_fallback(location)
        
        data = response.json()
        results = data.get("data", {}).get("searchResults", {}).get("listResults", [])
        return results
        
    except Exception as e:
        logger.error(f"GraphQL search failed: {e}")
        return search_redfin_fallback(location)


def search_redfin_fallback(location: str) -> list:
    """
    Ultimate fallback: Try Redfin instead.
    """
    logger.info(f"Trying Redfin fallback for: {location}")
    
    # Redfin's search endpoint
    search_url = "https://www.redfin.com/stingray/do/location-autocomplete"
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
    }
    
    try:
        # First get region ID
        response = requests.get(
            search_url,
            params={"location": location, "v": "2"},
            headers=headers,
            timeout=10
        )
        
        # Redfin returns JSONP-like response
        text = response.text
        if text.startswith("{}&&"):
            text = text[4:]
        
        data = json.loads(text)
        
        if not data.get("payload", {}).get("sections"):
            return []
        
        # Get first matching region
        sections = data["payload"]["sections"]
        if not sections:
            return []
        
        first_section = sections[0]
        if not first_section.get("rows"):
            return []
        
        region = first_section["rows"][0]
        region_url = region.get("url", "")
        
        if not region_url:
            return []
        
        # Now fetch listings for this region
        gis_url = f"https://www.redfin.com/stingray/api/gis"
        
        # Get region params
        response2 = requests.get(
            f"https://www.redfin.com{region_url}",
            headers=headers,
            timeout=10
        )
        
        # Extract listings from page
        html = response2.text
        
        # Find the JSON data in the page
        pattern = r'window\.__reactServerState\s*=\s*({.*?});'
        match = re.search(pattern, html, re.DOTALL)
        
        if match:
            try:
                state = json.loads(match.group(1))
                homes = state.get("ReactServerAgent.cache", {})
                # Navigate to find homes
                for key, value in homes.items():
                    if "homes" in str(value).lower():
                        if isinstance(value, dict) and "res" in value:
                            res = value["res"]
                            if isinstance(res, dict):
                                homes_list = res.get("homes", [])
                                if homes_list:
                                    return convert_redfin_to_zillow_format(homes_list)
            except:
                pass
        
        return []
        
    except Exception as e:
        logger.error(f"Redfin fallback failed: {e}")
        return []


def convert_redfin_to_zillow_format(redfin_homes: list) -> list:
    """Convert Redfin data to Zillow-like format."""
    converted = []
    for home in redfin_homes:
        converted.append({
            "zpid": home.get("propertyId") or home.get("listingId"),
            "address": home.get("streetLine", {}).get("value", "") if isinstance(home.get("streetLine"), dict) else str(home.get("streetLine", "")),
            "price": home.get("price", {}).get("value", 0) if isinstance(home.get("price"), dict) else home.get("price", 0),
            "beds": home.get("beds"),
            "baths": home.get("baths"),
            "area": home.get("sqFt", {}).get("value", 0) if isinstance(home.get("sqFt"), dict) else home.get("sqFt", 0),
            "daysOnZillow": home.get("dom", 0),  # Days on market
            "statusText": home.get("listingRemarks", ""),
            "detailUrl": home.get("url", ""),
            "imgSrc": home.get("primaryPhotoDisplayLevel", {}).get("photoUrl", "") if isinstance(home.get("primaryPhotoDisplayLevel"), dict) else "",
            "homeType": home.get("propertyType", ""),
        })
    return converted


def normalize_property(prop: dict) -> dict:
    """Normalize property data."""
    
    address = prop.get("address", "")
    if isinstance(address, dict):
        address = address.get("streetAddress", "")
    
    price = prop.get("price", 0)
    if isinstance(price, str):
        price = int(''.join(filter(str.isdigit, price)) or 0)
    
    days_on = prop.get("daysOnZillow", 0) or prop.get("dom", 0)
    
    detail_url = prop.get("detailUrl", "")
    if detail_url and not detail_url.startswith("http"):
        detail_url = f"https://www.zillow.com{detail_url}"
    
    return {
        "zpid": prop.get("zpid"),
        "address": address,
        "price": price,
        "bedrooms": prop.get("beds", 0),
        "bathrooms": prop.get("baths", 0),
        "sqft": prop.get("area", 0),
        "property_type": prop.get("homeType", ""),
        "days_on_market": days_on,
        "status_text": prop.get("statusText", ""),
        "zestimate": prop.get("zestimate", 0),
        "url": detail_url,
        "image": prop.get("imgSrc", "")
    }


def find_stale(properties: list, min_days: int) -> list:
    """Find stale listings."""
    leads = []
    for prop in properties:
        norm = normalize_property(prop)
        if norm["days_on_market"] >= min_days:
            norm["signal"] = "STALE"
            leads.append(norm)
    return sorted(leads, key=lambda x: x["days_on_market"], reverse=True)


def find_distress(properties: list) -> list:
    """Find distress keyword matches."""
    leads = []
    for prop in properties:
        norm = normalize_property(prop)
        text = f"{norm.get('address', '')} {norm.get('status_text', '')}".lower()
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
        "version": "6.0.0",
        "description": "Real estate stale listing finder",
        "endpoints": {
            "GET /find-stale?location=Houston,TX": "Listings 90+ days on market",
            "GET /find-stale?location=77001&days=60": "Custom days threshold",
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
        return jsonify({"success": False, "error": "Missing 'location' parameter"}), 400
    
    try:
        properties = search_zillow_api(location, page)
        
        if not properties:
            return jsonify({
                "success": True,
                "location": location,
                "message": "No properties found or service unavailable",
                "results_count": 0,
                "leads": []
            })
        
        leads = find_stale(properties, days)
        
        return jsonify({
            "success": True,
            "location": location,
            "filter": f"{days}+ days on market",
            "total_found": len(properties),
            "results_count": len(leads),
            "page": page,
            "leads": leads
        })
        
    except Exception as e:
        logger.exception("Error in find-stale")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/find-distress', methods=['GET'])
def route_find_distress():
    location = request.args.get('location')
    page = request.args.get('page', 1, type=int)
    
    if not location:
        return jsonify({"success": False, "error": "Missing 'location' parameter"}), 400
    
    try:
        properties = search_zillow_api(location, page)
        
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
            "total_found": len(properties),
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
    
    try:
        properties = search_zillow_api(location, page)
        
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
        
        stale_ids = {p["zpid"] for p in stale if p.get("zpid")}
        distress_ids = {p["zpid"] for p in distress if p.get("zpid")}
        hot_ids = stale_ids & distress_ids
        
        return jsonify({
            "success": True,
            "location": location,
            "page": page,
            "total_found": len(properties),
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
    return jsonify({"error": "Not found", "endpoints": ["/", "/find-stale", "/find-distress", "/search"]}), 404


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=PORT, debug=True)
