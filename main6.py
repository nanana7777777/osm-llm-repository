import os
import json
import requests
import math
from openai import OpenAI

client = OpenAI()

def calc_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def search_place(query: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "format": "json",
        "q": query,
        "limit": 3,
        "countrycodes": "jp"
    }
    headers = {"User-Agent": "osm-llm-demo/1.0"}
    res = requests.get(url, params=params, headers=headers)
    res.raise_for_status()
    return res.json()

CATEGORY_MAP = {
    "1": ["restaurant", "cafe", "fast_food"],
    "2": ["hospital", "clinic", "doctor", "pharmacy"],
    "3": ["police", "townhall", "post_office", "library"]
}

def build_amenity_regex(cats):
    return "|".join(cats)

def search_nearby(lat, lon, categories, radius=300):
    amenity_regex = build_amenity_regex(categories)
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json];
    node(around:{radius},{lat},{lon})["amenity"~"{amenity_regex}"];
    out;
    """
    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()

if __name__ == "__main__":
    user_input = input("調べたい場所を入力してください: ")
    places = search_place(user_input)
    lat = float(places[0]["lat"])
    lon = float(places[0]["lon"])

    print("\n調べたいカテゴリを選んでください:")
    print("1. 飲食店")
    print("2. 病院")
    print("3. 公共施設")
    category_input = input("番号を入力: ")
    categories = CATEGORY_MAP[category_input]

    pois = search_nearby(lat, lon, categories)

    results = []
    for el in pois["elements"]:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        poi_lat = el.get("lat")
        poi_lon = el.get("lon")
        dist = calc_distance(lat, lon, poi_lat, poi_lon)
        results.append({"name": name, "distance": int(dist)})

    # LLM におすすめ選出させる
    prompt = f"""
次の施設データから、距離が近い順にオススメ3つだけ選んで、
「名前」「距離」「短い説明」を日本語で箇条書きで出してください。

施設リスト(JSON):
{json.dumps(results, ensure_ascii=False)}
"""

    response = client.chat.completions.create(
        model="gpt-5-nano-2025-08-07",
        messages=[{"role": "user", "content": prompt}]
    )

    print("\n=== AI が選ぶおすすめ施設 ===")
    print(response.choices[0].message.content)
