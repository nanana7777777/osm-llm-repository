import os
import json
import requests
import math
from openai import OpenAI

client = OpenAI()  # OPENAI_API_KEY は環境変数から読まれます

# -----------------------------
# 距離計算（Haversine）
# -----------------------------
def calc_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # 地球半径[m]
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# -----------------------------
# 地名 → 緯度経度（Nominatim）
# -----------------------------
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


# -----------------------------
# LLM に「OSMタグ(key, value)」を決めてもらう
# 例：
#  カフェ      → {"key":"amenity","value":"cafe"}
#  コンビニ    → {"key":"shop","value":"convenience"}
#  ホテル      → {"key":"tourism","value":"hotel"}
# -----------------------------
def classify_osm_tag(user_text: str) -> dict:
    prompt = f"""
あなたは OpenStreetMap のタグ付けに詳しいアシスタントです。
ユーザーの要望から、最も適切な OSM のタグ (key, value) を1つ選んでください。

必ず次の形式の JSON **だけ** を返してください：

{{"key": "<tag key>", "value": "<tag value>"}}

key は次のいずれかから選んでください:
- "amenity"
- "shop"
- "tourism"
- "leisure"
- "public_transport"
- "railway"
- "highway"

例:
- 「近くのカフェ」      → {{"key":"amenity","value":"cafe"}}
- 「近くのラーメン屋」  → {{"key":"amenity","value":"restaurant"}}
- 「ホテル」            → {{"key":"tourism","value":"hotel"}}
- 「コンビニ」          → {{"key":"shop","value":"convenience"}}
- 「駅」                → {{"key":"railway","value":"station"}}

ユーザー入力: {user_text}
"""
    res = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    # モデルから返ってきた content を JSON として解釈
    return json.loads(res.choices[0].message.content)


# -----------------------------
# 周辺検索（Overpass）
# node / way / relation + out center;
# -----------------------------
def search_nearby(lat, lon, key: str, value: str, radius: int = 300):
    url = "https://overpass-api.de/api/interpreter"

    # key="value" にマッチする node / way / relation を検索
    query = f"""
    [out:json];
    (
      node(around:{radius},{lat},{lon})["{key}"="{value}"];
      way(around:{radius},{lat},{lon})["{key}"="{value}"];
      relation(around:{radius},{lat},{lon})["{key}"="{value}"];
    );
    out center;
    """

    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    place = input("どこで探しますか？（例：京都駅）: ")
    need = input("どんな施設？（例：カフェ、ラーメン屋、ホテル、コンビニなど）: ")

    # 1) 場所から座標取得
    places = search_place(place)
    if not places:
        print("場所が見つかりませんでした。別の名前を試してください。")
        exit(1)

    lat = float(places[0]["lat"])
    lon = float(places[0]["lon"])
    print(f"\n検索場所: {places[0].get('display_name')}")

    # 2) ユーザーの要望を OSMタグに変換
    tag_info = classify_osm_tag(need)
    key = tag_info.get("key")
    value = tag_info.get("value")
    print(f"➡ AI判定: key = {key}, value = {value}")

    if not key or not value:
        print("タグの判定に失敗しました。入力を変えて試してみてください。")
        exit(1)

    # 3) 周辺検索
    pois = search_nearby(lat, lon, key, value)

    # 4) 結果整理（ここで「取れるだけ全部」詰める）
    results = []
    for el in pois.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        # node or way/relation(center)
        poi_lat = el.get("lat") or (el.get("center") or {}).get("lat")
        poi_lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not poi_lat or not poi_lon:
            continue

        dist = calc_distance(lat, lon, poi_lat, poi_lon)

        # ★ ここで OSM が持っている情報をできるだけ保持
        results.append({
            "id": el.get("id"),
            "type": el.get("type"),          # node / way / relation
            "lat": float(poi_lat),
            "lon": float(poi_lon),
            "distance_m": int(dist),
            "tags": tags                     # ← 付いているタグは全部ここに入る
        })

    if not results:
        print("周辺に該当する施設が見つかりませんでした。")
        exit(0)

    # 距離でソートして、近い順にある程度までに絞る（トークン節約）
    results.sort(key=lambda x: x["distance_m"])
    top_results = results[:30]

    # 5) LLM に「おすすめ3つ＋説明」を生成させる
    prompt = f"""
以下は、ユーザーの現在地周辺にある候補施設のリストです。
JSONの各要素には、id・type・座標・距離・OSMのtagsが含まれます。

ユーザーの要望: 「{need}」

このリストからおすすめを3つ選び、
- 名前（tags.name）
- 距離（distance_m メートル）
- どんな場所か（tags の情報から推測）
- ユーザーへの一言おすすめコメント

を日本語で箇条書きで出してください。

施設リスト(JSON):
{json.dumps(top_results, ensure_ascii=False)}
"""

    res = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    print("\n=== AI が選ぶおすすめ施設 ===")
    print(res.choices[0].message.content)
