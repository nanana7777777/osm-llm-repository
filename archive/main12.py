import json
import math
from datetime import datetime
import requests
from openai import OpenAI

# OpenAI クライアント（環境変数 OPENAI_API_KEY を使用）
client = OpenAI()

# ===== 設定値 =====
RADIUS_M = 1000           # 検索半径（メートル）
MAX_ITEMS = 500           # LLM に渡す最大候補件数
OVERPASS_TIMEOUT = 60     # Overpass API のサーバ側タイムアウト（秒）
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
# 1km 以内の「名前付き node」を最大 MAX_ITEMS 取得
# ---------------------------
def build_overpass_query_all(lat, lon) -> str:
    lat = float(lat)
    lon = float(lon)

    query = f"""
    [out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:{OVERPASS_MAXSIZE}];
    (
      node(around:{RADIUS_M},{lat},{lon})["name"];
    );
    out tags center {MAX_ITEMS};
    """
    return query


def search_all_pois(lat, lon):
    url = "https://overpass-api.de/api/interpreter"
    query = build_overpass_query_all(lat, lon)
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
# LLM に渡すレコードに整形（タグ＋距離）
# ---------------------------
def build_poi_records(center_lat, center_lon, elements, max_items=MAX_ITEMS):
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

        # 主な分類キー（あれば）
        main_type_key = None
        main_type_value = None
        for key in ["amenity", "shop", "tourism", "leisure", "office", "public_transport", "highway", "railway"]:
            if key in tags:
                main_type_key = key
                main_type_value = tags.get(key)
                break

        cuisine = tags.get("cuisine", "")
        opening_hours = tags.get("opening_hours", "")
        wheelchair = tags.get("wheelchair", "")
        internet_access = tags.get("internet_access", "")
        wifi = "yes" if internet_access.lower() in ("yes", "wlan", "wifi") else (
            "no" if internet_access.lower() == "no" else "unknown"
        )

        record = {
            "name": name,
            "distance": distance,
            "main_type_key": main_type_key,
            "main_type_value": main_type_value,
            "cuisine": cuisine,
            "opening_hours": opening_hours,
            "wheelchair": wheelchair,
            "wifi": wifi,
            "tags": tags,  # 生のタグも全部渡す
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

    # ② ほしい施設のイメージを自由入力
    need = input(
        "\nどんな施設を探しますか？\n"
        "（例：近くのカフェ / 静かなラーメン屋 / 子連れOKのレストラン / 観光スポット など）: "
    ).strip()

    # ③ 出力への要望（件数・形式・優先条件など）
    user_request = input(
        "\n出力に関する要望があれば書いてください\n"
        "（例：近い順に5つ、箇条書きで / スタバがあれば優先して / 落ち着いた雰囲気を優先して など）: "
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

    # 2) 周辺 1km の名前付き node を全部取得（最大 MAX_ITEMS）
    pois_raw = search_all_pois(center_lat, center_lon)
    elements = pois_raw.get("elements", [])
    if not elements:
        print("周辺に名前付きのスポットが見つかりませんでした。")
        raise SystemExit(0)

    # 3) LLMに渡す用のレコードに整形
    poi_records = build_poi_records(
        center_lat,
        center_lon,
        elements,
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
- main_type_key / main_type_value: OSMの主な分類（amenity=cafe, shop=convenience など）
- cuisine: 料理ジャンル（あれば）
- opening_hours: 営業時間（OSMの書式、なければ空文字）
- wheelchair: バリアフリー情報（yes/no/limited など）
- wifi: Wi-Fi 可否（yes/no/unknown）
- tags: その他のOSMタグをすべて含む辞書（brand や operator など）

ユーザーが探しているものの説明:
「{need}」

ユーザーからの追加要望:
「{user_request}」

基本方針は次の通りです：
1. まず「ユーザーが探しているもの（{need}）」に合いそうな施設だけを候補としてください。
   - amenity / shop / tourism / leisure などのタグ
   - name や brand
   - cuisine などを使って、目的に合うかどうかを判断してください。
2. その中で、距離が近い施設を優先してください。
3. opening_hours が分かる場合は「現在営業中」と思われる施設を優先し、
   明らかに営業時間外の施設は、可能な限りおすすめから外してください。
4. ユーザー要望（{user_request}）は可能な範囲で反映しますが、
   上記2と3の基本方針を崩さないよう、ゆるく反映してください。
   例えば「スタバがあれば優先して」と書かれていたら、
   brand や name からスターバックスを探し、候補にあれば優先してください。
5. データに存在しない情報（口コミの星の数など）は、事実のように書かないでください。
   雰囲気の説明はしてもよいですが、「評価4.5」など具体的な数値は出さないでください。

候補の中からおすすめの施設をいくつか選んでください。
件数はユーザー要望が明確ならそれに従い、
特に指定がなければ3～5件程度を目安にしてください。

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
