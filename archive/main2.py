import os
import json
import requests
import math

def calc_distance(lat1, lon1, lat2, lon2):
    # Haversine formula
    R = 6371000  # Earth radius in meters
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    return R * c


# ---------------------------
# 1️⃣ 地名検索（Nominatim）
# ---------------------------
def search_place(query: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "format": "json",
        "q": query,
        "limit": 3,
    }
    headers = {
        "User-Agent": "osm-llm-demo/1.0"
    }
    res = requests.get(url, params=params, headers=headers)
    res.raise_for_status()
    return res.json()


# ---------------------------
# 2️⃣ 周辺施設検索（Overpass API）
# ---------------------------
def search_nearby(lat, lon, radius=300):
    url = "https://overpass-api.de/api/interpreter"
    
    query = f"""
    [out:json];
    (
      node(around:{radius},{lat},{lon})["amenity"];
      way(around:{radius},{lat},{lon})["amenity"];
      relation(around:{radius},{lat},{lon})["amenity"];
    );
    out center;
    """

    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()


# ---------------------------
# 実行
# ---------------------------
if __name__ == "__main__":
    places = search_place("Kyoto Station")
    lat = float(places[0]["lat"])
    lon = float(places[0]["lon"])

    pois = search_nearby(lat, lon)

    print("\n=== オススメ周辺施設（距離付き） ===")
    for el in pois["elements"]:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        
        poi_lat = el.get("lat") or el.get("center", {}).get("lat")
        poi_lon = el.get("lon") or el.get("center", {}).get("lon")

        if not poi_lat or not poi_lon:
            continue

        dist = calc_distance(lat, lon, poi_lat, poi_lon)

        print(f"{name}: {dist:.0f} m")
