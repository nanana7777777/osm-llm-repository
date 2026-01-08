import json
import math
from datetime import datetime
import requests
from openai import OpenAI

# OpenAI クライアント（OPENAI_API_KEY は環境変数から取る）
client = OpenAI()

RADIUS_M = 1000  # 検索半径 1000m


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
# カテゴリ別に Overpass クエリを組み立て
# ---------------------------
def build_overpass_query(lat, lon, category_choice: str) -> str:
    lat = float(lat)
    lon = float(lon)

    blocks = []

    # 1. 飲食店（amenity = restaurant / cafe / fast_food）
    if category_choice == "1":
        for val in ["restaurant", "cafe", "fast_food"]:
            blocks.append(
                f'node(around:{RADIUS_M},{lat},{lon})["amenity"="{val}"];'
            )
            blocks.append(
                f'way(around:{RADIUS_M},{lat},{lon})["amenity"="{val}"];'
            )
            blocks.append(
                f'relation(around:{RADIUS_M},{lat},{lon})["amenity"="{val}"];'
            )

    # 2. 公共施設（交番 / 郵便局 / 区役所 / 図書館 / 病院 など）
    elif category_choice == "2":
        amenity_vals = [
            "police",
            "post_office",
            "townhall",
            "library",
            "hospital",
            "clinic",
            "bank",
        ]
        for val in amenity_vals:
            blocks.append(
                f'node(around:{RADIUS_M},{lat},{lon})["amenity"="{val}"];'
            )
            blocks.append(
                f'way(around:{RADIUS_M},{lat},{lon})["amenity"="{val}"];'
            )
            blocks.append(
                f'relation(around:{RADIUS_M},{lat},{lon})["amenity"="{val}"];'
            )

    # 3. 観光スポット（観光名所、博物館、ホテルなど）
    elif category_choice == "3":
        tourism_vals = [
            "attraction",
            "museum",
            "gallery",
            "viewpoint",
            "hotel",
            "guest_house",
            "hostel",
            "theme_park",
            "zoo",
            "aquarium",
        ]
        for val in tourism_vals:
            blocks.append(
                f'node(around:{RADIUS_M},{lat},{lon})["tourism"="{val}"];'
            )
            blocks.append(
                f'way(around:{RADIUS_M},{lat},{lon})["tourism"="{val}"];'
            )
            blocks.append(
                f'relation(around:{RADIUS_M},{lat},{lon})["tourism"="{val}"];'
            )

    # 4. ショッピング施設（shop=* 全般）
    elif category_choice == "4":
        blocks.append(
            f'node(around:{RADIUS_M},{lat},{lon})["shop"];'
        )
        blocks.append(
            f'way(around:{RADIUS_M},{lat},{lon})["shop"];'
        )
        blocks.append(
            f'relation(around:{RADIUS_M},{lat},{lon})["shop"];'
        )

    # その他（念のため何も指定されていなければ何も取らない）
    else:
        return """
        [out:json][timeout:10];
        (
        );
        out tags center;
        """

    blocks_str = "\n      ".join(blocks)
    query = f"""
    [out:json][timeout:25];
    (
      {blocks_str}
    );
    out tags center;
    """
    return query


# ---------------------------
# Overpass で周辺検索
# ---------------------------
def search_category_pois(lat, lon, category_choice: str):
    url = "https://overpass-api.de/api/interpreter"
    query = build_overpass_query(lat, lon, category_choice)
    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()


# ---------------------------
# 飲食店ジャンルのフィルタ（cuisine ベース）
# ---------------------------
def cuisine_match(cuisine_raw: str, food_genre_choice: str) -> bool:
    """
    cuisine_raw: OSMの cuisine タグ（"ramen;japanese" など）
    food_genre_choice: "1"〜"6"
    """
    if not cuisine_raw:
        # ジャンル指定が「その他（6）」なら無いものも許容
        return food_genre_choice == "6"

    cuisine_lower = cuisine_raw.lower()

    if food_genre_choice == "1":  # ラーメン
        return "ramen" in cuisine_lower
    if food_genre_choice == "2":  # カフェ
        return ("coffee" in cuisine_lower) or ("cafe" in cuisine_lower)
    if food_genre_choice == "3":  # 和食
        return "japanese" in cuisine_lower
    if food_genre_choice == "4":  # 寿司
        return "sushi" in cuisine_lower
    if food_genre_choice == "5":  # 焼肉
        return ("yakiniku" in cuisine_lower) or ("bbq" in cuisine_lower)
    if food_genre_choice == "6":  # 指定なし
        return True

    return True


# ---------------------------
# LLM に渡すレコードに整形（タグ5種＋距離）
# ---------------------------
def build_poi_records(center_lat, center_lon, elements, category_choice, food_genre_choice=None, max_items=50):
    records = []

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        # 座標（node or way/relation center）
        poi_lat = el.get("lat") or (el.get("center") or {}).get("lat")
        poi_lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not poi_lat or not poi_lon:
            continue

        distance = int(
            calc_distance(center_lat, center_lon, poi_lat, poi_lon)
        )

        # カテゴリキー＆値（amenity / shop / tourism）
        if "amenity" in tags:
            category_key = "amenity"
            category_value = tags.get("amenity")
        elif "shop" in tags:
            category_key = "shop"
            category_value = tags.get("shop")
        elif "tourism" in tags:
            category_key = "tourism"
            category_value = tags.get("tourism")
        else:
            # 想定外はスキップ
            continue

        cuisine = tags.get("cuisine", "")
        opening_hours = tags.get("opening_hours", "")
        wheelchair = tags.get("wheelchair", "")

        # 飲食店カテゴリの場合、ジャンル指定で絞る
        if category_choice == "1" and food_genre_choice is not None:
            if not cuisine_match(cuisine, food_genre_choice):
                continue

        record = {
            "name": name,
            "distance": distance,
            "category_key": category_key,
            "category_value": category_value,
            "cuisine": cuisine,
            "opening_hours": opening_hours,
            "wheelchair": wheelchair,
        }
        records.append(record)

    # 距離が近い順にソートして max_items 件に絞る
    records.sort(key=lambda x: x["distance"])
    return records[:max_items]


# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    # ① 場所を入力
    place = input("どこで探しますか？（例：京都駅）: ").strip()

    # ② カテゴリ選択
    print("\nカテゴリを選んでください:")
    print("1. 飲食店")
    print("2. 公共施設")
    print("3. 観光スポット")
    print("4. ショッピング施設")
    category_choice = input("番号を入力: ").strip()

    if category_choice not in {"1", "2", "3", "4"}:
        print("対応していないカテゴリです。終了します。")
        raise SystemExit(1)

    # ③ 飲食店ならジャンル選択
    food_genre_choice = None
    if category_choice == "1":
        print("\n飲食店のジャンルを選んでください:")
        print("1. ラーメン")
        print("2. カフェ")
        print("3. 和食")
        print("4. 寿司")
        print("5. 焼肉")
        print("6. 指定なし（すべての飲食店）")
        food_genre_choice = input("番号を入力: ").strip()

        if food_genre_choice not in {"1", "2", "3", "4", "5", "6"}:
            print("対応していないジャンルです。終了します。")
            raise SystemExit(1)

    # ④ ユーザー要望（自由記述）
    user_request = input("\n出力に関する要望があれば書いてください（例：近い順に5つ、箇条書きで / 何もなければ空 Enter）: ").strip()

    # 1) 場所 → 座標
    places = search_place(place)
    if not places:
        print("場所が見つかりませんでした。別の名前を試してください。")
        raise SystemExit(1)

    center_lat = float(places[0]["lat"])
    center_lon = float(places[0]["lon"])
    print(f"\n検索場所: {places[0].get('display_name')}")
    print(f"中心座標: lat={center_lat}, lon={center_lon}")
    print(f"検索半径: {RADIUS_M} m")

    # 2) カテゴリ別に周辺検索
    pois_raw = search_category_pois(center_lat, center_lon, category_choice)
    elements = pois_raw.get("elements", [])
    if not elements:
        print("周辺に該当する施設が見つかりませんでした。")
        raise SystemExit(0)

    # 3) LLMに渡す用のレコードに整形（タグ5種＋距離）
    poi_records = build_poi_records(
        center_lat,
        center_lon,
        elements,
        category_choice,
        food_genre_choice=food_genre_choice,
        max_items=50,
    )

    if not poi_records:
        print("条件に合致する施設が見つかりませんでした。")
        raise SystemExit(0)

    print(f"\n候補件数（距離順にソート済み）: {len(poi_records)} 件")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---------------------------
    # LLM へのプロンプト
    # ---------------------------
    base_prompt = f"""
あなたは利用者に周辺の施設をおすすめするアシスタントです。
現在時刻は {now_str} とします。

入力として、現在地周辺の候補施設のリスト（JSON配列）を渡します。
各要素には以下の情報が入っています。

- name: 店名・施設名
- distance: 検索地点からの距離（メートル）
- category_key / category_value: OSMの分類（例: amenity / cafe）
- cuisine: 料理ジャンル（あれば）
- opening_hours: 営業時間（OSMの書式、なければ空文字）
- wheelchair: バリアフリー情報（yes/no/limited など）

ユーザーからの追加要望:
「{user_request}」

ただし、次の基本方針は必ず守ってください：
1. なるべく距離が近い施設を優先してください。
2. opening_hours が分かる場合は「現在営業中」と思われる施設を優先し、
   明らかに営業時間外の施設は、可能な限りおすすめから外してください。
3. ユーザー要望は可能な範囲で反映しますが、
   上記1と2の基本方針を崩してはいけません。
4. データに存在しない情報（口コミの星の数など）は、事実のように書かないでください。
   雰囲気の説明はしてよいですが、「評価4.5」などの具体的な数値は出さないでください。

候補の中からおすすめの施設をいくつか選んでください。
件数はユーザー要望が明確ならそれに従い、
特に指定がなければ3件程度を目安にしてください。

出力フォーマット（日本語）:
- 名前: ○○
  距離: 123 m
  営業時間: （不明なら「不明」）
  説明: どんな場所か、どんな人におすすめかを1～2文で。
"""

    prompt_with_data = base_prompt + "\n候補施設リスト(JSON):\n" + json.dumps(
        poi_records, ensure_ascii=False
    )

    res = client.chat.completions.create(
        model="gpt-5-nano-2025-08-07",
        messages=[{"role": "user", "content": prompt_with_data}],
    )

    print("\n=== AI が選ぶおすすめ施設 ===")
    print(res.choices[0].message.content)
