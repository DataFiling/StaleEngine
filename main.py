import requests
import os

# Configuration
API_KEY = os.getenv("RAPIDAPI_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK") # Your "Alert" channel
TARGET_CITY = "Miami, FL" 
MIN_DAYS_STALE = 90
DISTRESS_KEYWORDS = ["probate", "as-is", "tlc", "motivated", "fixer", "handyman", "bank owned"]

def send_alert(lead):
    if not DISCORD_WEBHOOK:
        return
    
    payload = {
        "content": f"🚨 **NEW STALE SIGNAL DETECTED** 🚨\n"
                   f"**Address:** {lead['address']}\n"
                   f"**Price:** {lead['price']}\n"
                   f"**Days on Market:** {lead['days_on']}\n"
                   f"**Why:** {lead['signal']}\n"
                   f"**Link:** {lead['url']}"
    }
    requests.post(DISCORD_WEBHOOK, json=payload)

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
    for prop in properties:
        days_on_zillow = prop.get('daysOnZillow', 0)
        description = prop.get('listing_description', "").lower()
        is_distressed = any(word in description for word in DISTRESS_KEYWORDS)
        
        if days_on_zillow >= MIN_DAYS_STALE or is_distressed:
            lead = {
                "address": prop.get('address'),
                "price": prop.get('price'),
                "days_on": days_on_zillow,
                "url": f"https://www.zillow.com{prop.get('detailUrl')}",
                "signal": "STALE" if days_on_zillow >= MIN_DAYS_STALE else "KEYWORD"
            }
            send_alert(lead)

if __name__ == "__main__":
    print(f"StaleEngine: Scanning {TARGET_CITY}...")
    raw_data = fetch_listings(TARGET_CITY)
    analyze_properties(raw_data)
    print("Scan Complete.")
