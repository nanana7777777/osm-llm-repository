import os
import json
import requests
import math
from openai import OpenAI

# ==========================================
# 設定
# ==========================================
client = OpenAI()
MODEL_NAME = "gpt-5-nano-2025-08-07"
SEARCH_RADIUS = 1000  # 北大路駅から半径1km
FIXED_LOCATION = "北大路駅 京都府" # 場所を固定

# ==========================================
# ユーティリティ
# ==========================================
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = map(math.radians, [lat1, lat2])
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return int(R * c)

# ==========================================
# 1. 座標特定 (固定された場所を使用)
# ==========================================
def get_fixed_coordinates():
    print(f"📍 検索場所を「{FIXED_LOCATION}」に固定して座標を取得します...")
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": FIXED_LOCATION, "format": "json", "limit": 1, "countrycodes": "jp"}
    headers = {"User-Agent": "osm-kitaoji-fixed/1.0"}
    
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        data = res.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0]["display_name"]
    except Exception as e:
        print(f"座標取得エラー: {e}")
    return None, None, None

# ==========================================
# 2. LLMによる意図の翻訳 (タグ・キーワード生成)
# ==========================================
def translate_user_intent(user_input):
    """
    ユーザーの文章（例: 駐車場があってコーヒー...）を
    Overpass APIが理解できる「検索タグ」と「キーワード」に翻訳する。
    """
    system_prompt = """
    あなたはGIS検索の翻訳エンジニアです。
    ユーザーの要望文を分析し、OSMサーバー検索用のキーワードリストを作成してください。

    ルール:
    1. ユーザーの意図（What）を汲み取り、類義語や関連タグ（日・英）に変換する。
       - "コーヒー" -> ["cafe", "coffee", "kissaten", "喫茶店"]
       - "駐車場" -> ["parking", "car_park", "coin_parking", "駐車場"]
    2. 検索漏れを防ぐため、主要なタグ（amenity, shop）と、条件（parking等）をすべてフラットなリストにする。
    3. 場所に関する単語（北大路など）は含めない。

    出力形式(JSON):
    {
      "search_keywords": ["...", "..."]
    }
    """

    try:
        res = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"要望: {user_input}"}
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content).get("search_keywords", [])
    except Exception as e:
        print(f"AI翻訳エラー: {e}")
        return []

# ==========================================
# 3. サーバーサイド検索 & データ取得
# ==========================================
def fetch_osm_data(lat, lon, radius, keywords):
    """
    翻訳されたキーワードを使って、Overpass APIサーバー上でデータを絞り込んで取得する。
    """
    url = "https://overpass-api.de/api/interpreter"
    
    if not keywords: return []

    # 正規表現の作成
    regex_str = "|".join([k for k in keywords if k])
    
    # ノードとウェイ（中心点）を取得
    query = f"""
    [out:json][timeout:30];
    (
      node["name"~"{regex_str}",i](around:{radius},{lat},{lon});
      node["amenity"~"{regex_str}",i](around:{radius},{lat},{lon});
      node["shop"~"{regex_str}",i](around:{radius},{lat},{lon});
      node["cuisine"~"{regex_str}",i](around:{radius},{lat},{lon});
      
      way["name"~"{regex_str}",i](around:{radius},{lat},{lon});
      way["amenity"~"{regex_str}",i](around:{radius},{lat},{lon});
      way["shop"~"{regex_str}",i](around:{radius},{lat},{lon});
      way["cuisine"~"{regex_str}",i](around:{radius},{lat},{lon});
    );
    out center;
    """
    
    print(f"📡 OSM検索実行中 (Keywords: {keywords[:5]}... Total: {len(keywords)})")
    
    try:
        res = requests.post(url, data={"data": query}, timeout=35)
        res.raise_for_status()
        return res.json().get("elements", [])
    except Exception as e:
        print(f"Overpassエラー: {e}")
        return []

# ==========================================
# 4. データ整形・保存
# ==========================================
def process_and_save_data(elements, center_lat, center_lon, user_input):
    processed = []
    
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name", "名称なし")
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        
        maps_url = ""
        distance_str = "不明"
        dist_val = 99999

        if lat and lon:
            dist_val = calculate_distance(center_lat, center_lon, lat, lon)
            distance_str = f"約{dist_val}m"
            maps_url = f"http://googleusercontent.com/maps.google.com/maps?q={lat},{lon}"

        processed.append({
            "name": name,
            "type": tags.get("amenity") or tags.get("shop") or "unknown",
            "distance": distance_str,
            "maps_url": maps_url,
            "details": tags # 全タグ情報を保持
        })

    # 距離順にソート
    processed.sort(key=lambda x: int(x["distance"].replace("約","").replace("m","")) if "約" in x["distance"] else 99999)

    # JSONファイルに保存
    save_data = {
        "user_intent": user_input,
        "search_location": FIXED_LOCATION,
        "hit_count": len(processed),
        "results": processed
    }
    
    filename = "osm_kitaoji_log.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    
    print(f"💾 検索結果を '{filename}' に保存しました ({len(processed)}件)。")
    
    return processed

# ==========================================
# 5. 最終回答生成 (LLM)
# ==========================================
def generate_response(user_input, data_list):
    if not data_list:
        return "申し訳ありません。条件に合う施設が見つかりませんでした。"

    top_list = data_list[:10] # 上位10件

    system_prompt = """
    あなたは「北大路周辺の案内人」です。
    ユーザーの要望と、検索された地図データをもとに、おすすめスポットを3つ程度紹介してください。
    
    ルール:
    1. ユーザーの条件（駐車場やWifiなど）がタグに含まれているか確認し、あれば「駐車場あり」などと強調する。
    2. なければ「データ上は不明ですが」と正直に伝える。
    3. [Googleマップ](URL) のリンクを必ず付ける。
    """

    user_prompt = f"""
    ユーザー要望: {user_input}
    検索データ: {json.dumps(top_list, ensure_ascii=False)}
    """

    res = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    )
    return res.choices[0].message.content

# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    # 場所は固定なので、ユーザーには「要望」だけ聞く
    print(f"\n📍 現在地設定: {FIXED_LOCATION}")
    user_input = input("北大路周辺で、何をお探しですか？\n(例: 駐車場があってコーヒーが飲める場所)\n> ")

    # 1. 座標取得 (固定)
    lat, lon, addr = get_fixed_coordinates()
    if not lat:
        print("場所の特定に失敗しました。")
        exit()

    # 2. 意図翻訳 (LLM)
    print("\n🧠 AIが要望を検索タグに翻訳中...")
    keywords = translate_user_intent(user_input)
    print(f"   Keywords: {keywords}")

    # 3. サーバー検索 (Overpass)
    raw_data = fetch_osm_data(lat, lon, SEARCH_RADIUS, keywords)

    # 4. データ保存 & 整形
    processed_data = process_and_save_data(raw_data, lat, lon, user_input)

    # 5. 回答生成 (LLM)
    print("\n📝 おすすめスポット:\n")
    print(generate_response(user_input, processed_data))
