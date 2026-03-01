#カテゴリ分け

import os
import json
import requests
import math

def calc_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


# 地名検索 API
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


# カテゴリ設定（ここだけに残す）
CATEGORY_MAP = {
    "1": ["restaurant", "cafe", "fast_food"],   # 飲食店
    "2": ["hospital", "clinic", "doctor", "pharmacy"],  # 病院・医療（修正）
    "3": ["police", "townhall", "post_office", "library"]  # 公共施設
}

def build_amenity_regex(cats):
    joined = "|".join(cats)
    return f"(?:{joined})"  # ← $ を削除



# 周辺施設取得 API
def search_nearby(lat, lon, categories, radius=300):
    url = "https://overpass-api.de/api/interpreter"

    # OR でつなぐ
    filters = "\n".join([f'node(around:{radius},{lat},{lon})["amenity"="{cat}"];' for cat in categories])
    filters += "\n" + "\n".join([f'way(around:{radius},{lat},{lon})["amenity"="{cat}"];' for cat in categories])
    filters += "\n" + "\n".join([f'relation(around:{radius},{lat},{lon})["amenity"="{cat}"];' for cat in categories])

    query = f"""
    [out:json];
    (
      {filters}
    );
    out center;
    """

    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()



if __name__ == "__main__":
    user_input = input("調べたい場所を入力してください: ")

    places = search_place(user_input)
    if not places:
        print("場所が見つかりません。")
        exit()

    lat = float(places[0]["lat"])
    lon = float(places[0]["lon"])
    print(f"検索場所: {places[0]['display_name']}")

    print("\n調べたいカテゴリを選んでください:")
    print("1. 飲食店")
    print("2. 病院")
    print("3. 公共施設")
    category_input = input("番号を入力: ")

    if category_input not in CATEGORY_MAP:
        print("対応していないカテゴリです。")
        exit()

    categories = CATEGORY_MAP[category_input]

    pois = search_nearby(lat, lon, categories)

    print("\n=== 周辺施設（距離付き） ===")
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
