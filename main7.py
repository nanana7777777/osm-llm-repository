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
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


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


# ★ LLM に amenity分類させる
def classify_amenity(user_text):
    prompt = f"""
ユーザーの入力から、OSMの amenityタグに変換してください。
返答は JSON形式で、"amenity" のキーのみ含めてください。

例：
- カフェ → {{"amenity": "cafe"}}
- 近くのラーメン屋 → {{"amenity": "restaurant"}}
- 郵便局 → {{"amenity": "post_office"}}

入力: {user_text}
"""

    res = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return json.loads(res.choices[0].message.content)


def search_nearby(lat, lon, amenity, radius=300):
    tag = amenity["amenity"]
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json];
    node(around:{radius},{lat},{lon})["amenity"="{tag}"];
    out;
    """
    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()


# ========= Main =========
if __name__ == "__main__":
    place = input("どこで探しますか？（例：京都駅）: ")
    need = input("どんな施設？（例：カフェ、ラーメン屋）: ")

    places = search_place(place)
    lat = float(places[0]["lat"])
    lon = float(places[0]["lon"])

    amenity = classify_amenity(need)
    print(f"\n➡ AI分類: amenity = {amenity}")

    pois = search_nearby(lat, lon, amenity)

    results = []
    for el in pois.get("elements", []):
        name = el.get("tags", {}).get("name")
        if not name:
            continue
        dist = calc_distance(lat, lon, el["lat"], el["lon"])
        results.append({"name": name, "distance": int(dist)})

    if not results:
        print("該当する施設が周辺にありませんでした。")
        exit()

    prompt = f"""
次のリストからおすすめを３つ選んで、
名前＋距離＋短い説明を日本語で箇条書きで出してください。

{json.dumps(results, ensure_ascii=False)}
"""

    res = client.chat.completions.create(
        model="gpt-5-nano-2025-08-07",
        messages=[{"role": "user", "content": prompt}]
    )

    print("\n=== AI が選ぶおすすめ施設 ===")
    print(res.choices[0].message.content)
