import requests
import os

# Configuration
API_KEY = os.getenv("RAPIDAPI_KEY", "YOUR_RAPIDAPI_KEY")
TARGET_CITY = "Miami, FL" # Change this to your target area
MIN_DAYS_STALE = 90
DISTRESS_KEYWORDS = ["probate", "as-is", "tlc", "motivated", "fixer", "handyman"]

def fetch_listings(location):
    url = "https://zillow-com1.p.rapidapi.com/propertyExtendedSearch"
    querystring = {"location": location, "status_type": "ForSale"}
    headers = {
        "X-RapidAPI-Key": API_KEY,
        "X-RapidAPI-Host": "zillow-com1.p.rapidapi.com"
    }
    
    response = requests.get(url, headers=headers, params=querystring)
    return response.json().get('props', [])

def analyze_properties(properties):
    leads = []
    for prop in properties:
        # Check if property is stale
        days_on_zillow = prop.get('daysOnZillow', 0)
        
        # Check description for distress (if available in summary)
        # Note: Some APIs require a second call for full description
        description = prop.get('listing_description', "").lower()
        is_distressed = any(word in description for word in DISTRESS_KEYWORDS)
        
        if days_on_zillow >= MIN_DAYS_STALE or is_distressed:
            leads.append({
                "address": prop.get('address'),
                "price": prop.get('price'),
                "days_on": days_on_zillow,
                "url": f"https://www.zillow.com{prop.get('detailUrl')}",
                "signal": "STALE" if days_on_zillow >= MIN_DAYS_STALE else "DISTRESS KEYWORD"
            })
    return leads

if __name__ == "__main__":
    print(f"--- Scanning {TARGET_CITY} for Stale Assets ---")
    raw_data = fetch_listings(TARGET_CITY)
    hot_leads = analyze_properties(raw_data)
    
    for lead in hot_leads:
        print(f"[{lead['signal']}] {lead['address']} - {lead['price']} ({lead['days_on']} days)")
        print(f"Link: {lead['url']}\n")
