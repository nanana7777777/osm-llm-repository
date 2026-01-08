import json
import requests

# ---------------------------
# 1️⃣ 地名検索（Nominatim）
# ---------------------------
def search_place(query: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "format": "json",
        "q": query,
        "limit": 1,
        "countrycodes": "jp"
    }
    headers = {"User-Agent": "osm-llm-demo/1.0"}
    res = requests.get(url, params=params, headers=headers)
    res.raise_for_status()
    return res.json()

# ---------------------------
# 2️⃣ OSM 生データ取得（全て）
# ---------------------------
def fetch_all_osm_data(lat, lon, radius=300):
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:25];
    (
      node(around:{radius},{lat},{lon});
      way(around:{radius},{lat},{lon});
      relation(around:{radius},{lat},{lon});
    );
    out tags center;
    """
    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()

# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    place = input("調べたい場所（例: 京都駅）: ")
    places = search_place(place)
    
    lat = float(places[0]["lat"])
    lon = float(places[0]["lon"])

    print(f"\n検索対象地点: {places[0]['display_name']}")
    print(f"lat={lat}, lon={lon}")

    data = fetch_all_osm_data(lat, lon)

    print("\n=== OSM 生データ ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))
