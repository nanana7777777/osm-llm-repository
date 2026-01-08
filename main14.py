import os
import json
import requests
import math
from openai import OpenAI

client = OpenAI()

# ================== Utility: Distance ==================
def calc_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


# ================== Nominatim: Place Search ==================
def search_place(query: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"format": "json", "q": query, "limit": 3, "countrycodes": "jp"}
    headers = {"User-Agent": "osm-llm-demo/1.0"}
    res = requests.get(url, params=params, headers=headers, timeout=30)
    res.raise_for_status()
    places = res.json()
    if not places:
        return places

    # 駅を優先
    for p in places:
        if "station" in p.get("type", "") or "駅" in p.get("display_name", ""):
            return [p]

    return [places[0]]


# ================== Station Fix: Overpass補正 ==================
def fix_station_center(place_name):
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:30];
    (
      node["railway"="station"]["name"~"{place_name}"];
      way["railway"="station"]["name"~"{place_name}"];
      relation["railway"="station"]["name"~"{place_name}"];
    );
    out center 1;
    """
    try:
        res = requests.post(url, data={"data": query})
        res.raise_for_status()
    except:
        return None

    data = res.json()
    if not data.get("elements"):
        return None

    el = data["elements"][0]
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")

    if lat and lon:
        return float(lat), float(lon)
    return None


# ================== Overpass: Nearby POI ==================
def fetch_all_pois(lat, lon, radius=1000, maxsize="200000000"):
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:60][maxsize:{maxsize}];
    (
      node(around:{radius},{lat},{lon})[name];
      way(around:{radius},{lat},{lon})[name];
      relation(around:{radius},{lat},{lon})[name];
    );
    out center;
    """
    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()


# ================== MAIN ==================
if __name__ == "__main__":
    place = input("どこで探しますか？（例：京都駅）: ")
    user_needs = input("どんな施設を探しますか？: ")
    extra_req = input("出力に関する要望があれば書いてください: ")

    places = search_place(place)
    if not places:
        print("場所が見つかりません")
        exit()

    center_lat = float(places[0]["lat"])
    center_lon = float(places[0]["lon"])
    display_name = places[0]["display_name"]

    # 駅の場合補正
    fixed = fix_station_center(place)
    if fixed:
        center_lat, center_lon = fixed
        print("\n🛠 駅補正：中心座標を駅に修正しました！")

    print(f"\n検索場所: {display_name}")
    print(f"中心座標: lat={center_lat}, lon={center_lon}")
    print(f"検索半径: 1000 m")

    pois_raw = fetch_all_pois(center_lat, center_lon)
    elements = pois_raw.get("elements", [])[:500]

    # 距離付きフィルタ
    pois = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if not lat or not lon:
            continue

        dist = calc_distance(center_lat, center_lon, lat, lon)

        pois.append({
            "name": name,
            "distance": int(dist),
            "tags": tags
        })

    # 距離順
    pois.sort(key=lambda x: x["distance"])

    if not pois:
        print("周辺に名前付きのスポットが見つかりませんでした。")
        exit()

    print(f"\n候補件数（距離順にソート済み）: {len(pois)} 件")

    # ================== LLMへ ==================
    prompt = f"""
ユーザーの求める施設:
「{user_needs}」

ユーザーの希望:
「{extra_req}」

次の施設候補リストから、
ユーザーの意図に最も合うものを選んでください。

出力形式：
- 名前
- 距離
- 短い説明（想像でOK）

施設リスト(JSON):
{json.dumps(pois[:200], ensure_ascii=False)}
""".strip()

    res = client.chat.completions.create(
        model="gpt-5-nano-2025-08-07",
        messages=[{"role": "user", "content": prompt}],
    )

    print("\n=== AI が選ぶおすすめ施設 ===")
    print(res.choices[0].message.content)
