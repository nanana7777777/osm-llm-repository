import json
import math
from datetime import datetime
import requests
from openai import OpenAI

# OpenAI クライアント（OPENAI_API_KEY は環境変数から）
client = OpenAI()

# ===== 設定値 =====
RADIUS_M = 1000          # 検索半径（メートル）
MAX_ITEMS = 100          # LLM に渡す最大候補件数
OVERPASS_TIMEOUT = 60    # Overpass API のサーバ側タイムアウト（秒）
OVERPASS_MAXSIZE = 2000000  # Overpass のレスポンス制限（バイト目安）


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
    res = requests.get(url, params=params, headers=headers, timeout=30)
    res.raise_for_status()
    return res.json()


# ---------------------------
# カテゴリ別に Overpass クエリを組み立て
# （負荷軽減のため node のみ / 必要なタグだけ）
# ---------------------------
def build_overpass_query(lat, lon, category_choice: str) -> str:
    lat = float(lat)
    lon = float(lon)

    blocks = []

    # 1. 飲食店
    #   - amenity=restaurant/cafe/fast_food
    #   - shop=bakery/coffee/beverages なども候補
    if category_choice == "1":
        amenity_vals = ["restaurant", "cafe", "fast_food"]
        for val in amenity_vals:
            blocks.append(
                f'node(around:{RADIUS_M},{lat},{lon})["amenity"="{val}"];'
            )
        shop_vals = ["coffee", "coffee_shop", "bakery", "confectionery", "tea"]
        for val in shop_vals:
            blocks.append(
                f'node(around:{RADIUS_M},{lat},{lon})["shop"="{val}"];'
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

    # 4. ショッピング施設（shop=* 全般）
    elif category_choice == "4":
        blocks.append(
            f'node(around:{RADIUS_M},{lat},{lon})["shop"];'
        )

    else:
        # ここには来ない想定だが、念のため空クエリ返却
        return f"""
        [out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:{OVERPASS_MAXSIZE}];
        (
        );
        out center 0;
        """

    blocks_str = "\n      ".join(blocks)
    query = f"""
    [out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:{OVERPASS_MAXSIZE}];
    (
      {blocks_str}
    );
    out tags center {MAX_ITEMS};
    """
    return query


# ---------------------------
# Overpass で周辺検索（タイムアウト & エラーハンドリング込み）
# ---------------------------
def search_category_pois(lat, lon, category_choice: str):
    url = "https://overpass-api.de/api/interpreter"
    query = build_overpass_query(lat, lon, category_choice)
    try:
        res = requests.post(url, data={"data": query}, timeout=OVERPASS_TIMEOUT + 5)
        res.raise_for_status()
    except requests.exceptions.Timeout:
        print("Overpass API のタイムアウトが発生しました。半径を狭めるか、あとでもう一度お試しください。")
        raise
    except requests.exceptions.HTTPError as e:
        print(f"Overpass API エラー: {e}")
        raise
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
        # 「指定なし」の場合は cuisine がなくても許容
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
# LLM に渡すレコードに整形（タグを厳選＋距離）
# ---------------------------
def build_poi_records(center_lat, center_lon, elements, category_choice,
                      food_genre_choice=None, max_items=MAX_ITEMS):
    records = []

    for el in elements:
        tags = el.get("tags", {})
        if not tags:
            continue

        name = tags.get("name")
        if not name:
            continue

        # 座標（node の lat/lon または center）
        poi_lat = el.get("lat") or (el.get("center") or {}).get("lat")
        poi_lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not poi_lat or not poi_lon:
            continue

        distance = int(
            calc_distance(center_lat, center_lon, poi_lat, poi_lon)
        )

        # カテゴリキー＆値（amenity / shop / tourism）
        category_key = None
        category_value = None
        if "amenity" in tags:
            category_key = "amenity"
            category_value = tags.get("amenity")
        elif "shop" in tags:
            category_key = "shop"
            category_value = tags.get("shop")
        elif "tourism" in tags:
            category_key = "tourism"
            category_value = tags.get("tourism")

        if not category_key:
            # 想定外はスキップ
            continue

        cuisine = tags.get("cuisine", "")
        opening_hours = tags.get("opening_hours", "")
        wheelchair = tags.get("wheelchair", "")
        internet_access = tags.get("internet_access", "")
        wifi = "yes" if internet_access.lower() in ("yes", "wlan", "wifi") else (
            "no" if internet_access.lower() == "no" else "unknown"
        )

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
            "wifi": wifi,
            # LLM が賢く判断できるよう、tags も丸ごと渡す（軽量とはいえここが一番リッチ）
            "tags": tags,
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
    user_request = input(
        "\n出力に関する要望があれば書いてください\n"
        "（例：近い順に5つ、箇条書きで / スタバがあれば優先して / 何もなければ空 Enter）: "
    ).strip()

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

    # 3) LLMに渡す用のレコードに整形
    poi_records = build_poi_records(
        center_lat,
        center_lon,
        elements,
        category_choice,
        food_genre_choice=food_genre_choice,
        max_items=MAX_ITEMS,
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
- wifi: Wi-Fi 可否（yes/no/unknown）
- tags: その他のOSMタグをすべて含む辞書（brand や operator など）

ユーザーからの追加要望:
「{user_request}」

基本方針は次の通りです：
1. なるべく距離が近い施設を優先してください。
2. opening_hours が分かる場合は「現在営業中」と思われる施設を優先し、
   明らかに営業時間外の施設は、可能な限りおすすめから外してください。
3. ユーザー要望は可能な範囲で反映しますが、
   上記1と2の基本方針を崩してはいけません（ゆるく反映してください）。
   例えば「スタバがあれば嬉しい」と書かれていたら、
   brand や name からスターバックスを探し、候補にあれば優先してください。
4. データに存在しない情報（口コミの星の数など）は、事実のように書かないでください。
   雰囲気の説明はしてもよいですが、「評価4.5」など具体的な数値は出さないでください。

候補の中からおすすめの施設をいくつか選んでください。
件数はユーザー要望が明確ならそれに従い、
特に指定がなければ3件程度を目安にしてください。

出力フォーマット（日本語）の一例:
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
