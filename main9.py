import json
import math
from datetime import datetime
import requests
from openai import OpenAI

# OpenAI クライアント（OPENAI_API_KEY は環境変数から）
client = OpenAI()


# ---------------------------
# 距離計算（Haversine）
# ---------------------------
def calc_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # 地球半径[m]
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# ---------------------------
# 地名 → 座標（Nominatim）
# ---------------------------
def search_place(query: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "format": "json",
        "q": query,
        "limit": 1,
        "countrycodes": "jp",
    }
    headers = {"User-Agent": "osm-llm-demo/1.0"}
    res = requests.get(url, params=params, headers=headers)
    res.raise_for_status()
    return res.json()


# ---------------------------
# ユーザーの希望 → OSMタグ(key,value) に分類
# （モデル: gpt-5-nano-2025-08-07）
# ---------------------------
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
        model="gpt-5-nano-2025-08-07",
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(res.choices[0].message.content)


# ---------------------------
# 周辺検索（Overpass）
# node / way / relation を対象
# ---------------------------
def search_nearby(lat, lon, key: str, value: str, radius: int = 300):
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:25];
    (
      node(around:{radius},{lat},{lon})["{key}"="{value}"];
      way(around:{radius},{lat},{lon})["{key}"="{value}"];
      relation(around:{radius},{lat},{lon})["{key}"="{value}"];
    );
    out tags center;
    """

    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()


# ---------------------------
# addr:* をまとめて住所文字列に
# ---------------------------
def build_address_from_tags(tags: dict) -> str:
    parts = []
    for k in [
        "addr:country",
        "addr:postcode",
        "addr:state",     # 都道府県
        "addr:city",      # 市区町村
        "addr:district",  # 区など
        "addr:suburb",
        "addr:neighbourhood",
        "addr:street",
        "addr:block",
        "addr:housenumber",
        "addr:unit",
    ]:
        v = tags.get(k)
        if v:
            parts.append(v)
    return " ".join(parts) if parts else ""


# ---------------------------
# wifi フラグを OSMタグから判定
# ---------------------------
def get_wifi_flag(tags: dict) -> str:
    val = tags.get("internet_access")
    if not val:
        return "unknown"
    val = val.lower()
    if val in ("yes", "wlan", "wifi"):
        return "yes"
    if val in ("no",):
        return "no"
    return "unknown"


# ---------------------------
# LLM に渡すための施設リストを整形
# ---------------------------
def build_poi_records(center_lat, center_lon, elements, key, value, max_items=50):
    records = []

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        # node or way/relation(center)
        poi_lat = el.get("lat") or (el.get("center") or {}).get("lat")
        poi_lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not poi_lat or not poi_lon:
            continue

        distance = int(
            calc_distance(center_lat, center_lon, poi_lat, poi_lon)
        )

        address = build_address_from_tags(tags)
        wifi = get_wifi_flag(tags)
        opening_hours = tags.get("opening_hours", "")
        cuisine = tags.get("cuisine", "")

        # main category
        category_key = key
        category_value = tags.get(key, value)

        record = {
            "name": name,
            "distance": distance,
            "category_key": category_key,
            "category_value": category_value,
            "cuisine": cuisine,
            "opening_hours": opening_hours,
            "wheelchair": tags.get("wheelchair", ""),
            "wifi": wifi,
            "address": address,
            "tags": tags,  # OSM の tags をそのまま全部
        }
        records.append(record)

    # 距離が近い順にソートして max_items 件に絞る
    records.sort(key=lambda x: x["distance"])
    return records[:max_items]


# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    place = input("どこで探しますか？（例：京都駅）: ").strip()
    need = input("どんな施設？（例：カフェ、ラーメン屋、ホテルなど）: ").strip()

    # 1) 場所 → 座標
    places = search_place(place)
    if not places:
        print("場所が見つかりませんでした。別の名前を試してください。")
        raise SystemExit(1)

    center_lat = float(places[0]["lat"])
    center_lon = float(places[0]["lon"])
    print(f"\n検索場所: {places[0].get('display_name')}")
    print(f"中心座標: lat={center_lat}, lon={center_lon}")

    # 2) ユーザー要望を OSM タグに変換
    tag_info = classify_osm_tag(need)
    key = tag_info.get("key")
    value = tag_info.get("value")
    print(f"\n➡ AI 判定タグ: {tag_info}")

    if not key or not value:
        print("タグ判定に失敗しました。入力を変えて試してください。")
        raise SystemExit(1)

    # 3) 周辺検索
    pois_raw = search_nearby(center_lat, center_lon, key, value)

    elements = pois_raw.get("elements", [])
    if not elements:
        print("周辺に該当する施設が見つかりませんでした。")
        raise SystemExit(0)

    # 4) LLMに渡す用のレコードに整形
    poi_records = build_poi_records(center_lat, center_lon, elements, key, value)

    print(f"\n候補件数（距離順にソート済み）: {len(poi_records)} 件")

    # --- 必要ならここで中身を確認（デバッグ用） ---
    # print(json.dumps(poi_records, ensure_ascii=False, indent=2))

    # 5) LLM に「おすすめ3件」を選ばせる
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prompt = f"""
あなたは利用者に周辺の施設をおすすめするアシスタントです。
現在時刻は {now_str} とします。

入力として、現在地周辺の候補施設のリスト（JSON配列）を渡します。
各要素には以下のような情報が入っています。

- name: 店名・施設名
- distance: 検索地点からの距離（メートル）
- category_key / category_value: OSMの分類（例: amenity / cafe）
- cuisine: 料理ジャンル（あれば）
- opening_hours: 営業時間（OSMの書式、なければ空）
- wheelchair: バリアフリー情報（yes/no/limited など）
- wifi: Wi-Fi可否（yes/no/unknown）
- address: 住所情報（あれば）
- tags: その他のOSMタグをすべて含む辞書

方針:
1. 距離が近い施設を優先してください。
2. opening_hours がわかる場合は「現在営業中」の施設を優先し、
   明らかに営業時間外の施設はできるだけ除外してください。
3. 同じような距離の場合は、料理ジャンルや tags の情報を見て、
   一般的な利用者にとって魅力的そうな施設を選んでください。
4. 候補の中からおすすめを3件選んでください。

出力フォーマット（日本語）:
- 名前: ○○
  距離: 123 m
  営業時間: （わからなければ「不明」）
  説明: どんな場所か、どんな人におすすめかを1～2文で。
"""

    prompt_with_data = prompt + "\n候補施設リスト(JSON):\n" + json.dumps(
        poi_records, ensure_ascii=False
    )

    res = client.chat.completions.create(
        model="gpt-5-nano-2025-08-07",
        messages=[{"role": "user", "content": prompt_with_data}],
    )

    print("\n=== AI が選ぶおすすめ施設 ===")
    print(res.choices[0].message.content)
