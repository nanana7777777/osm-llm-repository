#ユーザーが入力した情報をタグ検索してからOSMから取ってくる
import os
import json
import requests
from openai import OpenAI

# ==========================================
# 設定
# ==========================================
client = OpenAI()
# コスパ重視モデル指定
MODEL_NAME = "gpt-5-nano-2025-08-07"
# 検索半径 (メートル)
SEARCH_RADIUS = 800 

# ==========================================
# 1. 検索クエリの最適化 (AI)
# ==========================================
def optimize_search_conditions(user_input):
    """
    ユーザー入力を解析し、検索用の地名とキーワードを抽出する
    """
    system_prompt = """
    あなたは検索クエリ生成AIです。
    ユーザー入力から以下の2つを抽出し、JSON形式のみで出力してください。

    1. target_place: 
       - Nominatim検索用の地名（県名や市名を補って特定しやすくする）。
       - 例: "桂" -> "桂駅 京都府"
    
    2. keywords: 
       - 検索キーワードのリスト（日本語と英語）。
       - OpenStreetMapは英語タグが多いため、必ず英語訳を含めること。
       - 例: ラーメン -> ["ramen", "noodle", "chinese", "中華"]

    出力形式:
    {
      "target_place": "...",
      "keywords": ["...", "..."]
    }
    """

    try:
        res = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"ユーザー入力: {user_input}"}
            ],
            response_format={"type": "json_object"}
            # temperature指定は削除（モデル仕様によるエラー回避）
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print(f"AI Error: {e}")
        # エラー時のフォールバック（場所は入力そのまま、KWは空）
        return {"target_place": user_input, "keywords": []}

# ==========================================
# 2. 座標特定 (Nominatim API)
# ==========================================
def get_coordinates(place_name):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": place_name,
        "format": "json",
        "limit": 1,
        "countrycodes": "jp"
    }
    headers = {"User-Agent": "osm-llm-nano-agent/1.0"}
    
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        data = res.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0]["display_name"]
    except Exception as e:
        print(f"Nominatim Error: {e}")
    return None, None, None

# ==========================================
# 3. 周辺データ全取得 (Overpass API - 軽量版)
# ==========================================
def fetch_all_nearby_shops(lat, lon, radius):
    """
    指定座標周辺の店・施設を中心点(node)として全取得
    """
    url = "https://overpass-api.de/api/interpreter"
    
    # タイムアウト15秒、データサイズ制限、out centerによる軽量化
    query = f"""
    [out:json][timeout:15][maxsize:1073741824];
    (
      node["shop"](around:{radius},{lat},{lon});
      node["amenity"](around:{radius},{lat},{lon});
      node["cuisine"](around:{radius},{lat},{lon});
      
      way["shop"](around:{radius},{lat},{lon});
      way["amenity"](around:{radius},{lat},{lon});
      way["cuisine"](around:{radius},{lat},{lon});
    );
    out center;
    """
    
    print(f"📡 Overpass API: 半径{radius}mのデータを取得中...")
    try:
        res = requests.post(url, data={"data": query}, timeout=20)
        res.raise_for_status()
        return res.json().get("elements", [])
    except Exception as e:
        print(f"Overpass Error: {e}")
        return []

# ==========================================
# 4. Pythonフィルタリング
# ==========================================
def filter_candidates(elements, keywords):
    """
    取得データからキーワード一致検索（ここが検索精度の肝）
    """
    candidates = []
    # キーワードがない場合は全件返すのを防ぐため空リスト
    if not keywords: 
        return []

    keywords_lower = [k.lower() for k in keywords]
    print(f"🔍 フィルタリング中... (Keywords: {keywords_lower})")

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name", "")
        if not name: continue

        # 検索対象文字列（名前 + 英語名 + 種類 + 料理ジャンル）
        search_target = f"{name} {tags.get('name:en','')} {tags.get('amenity','')} {tags.get('shop','')} {tags.get('cuisine','')}"
        search_target = search_target.lower()
        
        # キーワードのどれか一つでも含まれればヒット
        if any(k in search_target for k in keywords_lower):
            candidates.append({
                "name": name,
                "type": tags.get("shop") or tags.get("amenity"),
                "cuisine": tags.get("cuisine", "不明"),
                # 距離計算用座標（必要なら計算に使用）
                "lat": el.get("lat") or el.get("center", {}).get("lat"),
                "lon": el.get("lon") or el.get("center", {}).get("lon")
            })
            
    return candidates

# ==========================================
# 5. 最終回答生成 (AI)
# ==========================================
def generate_final_answer(user_input, place_name, candidates):
    if not candidates:
        return "申し訳ありません。近くに条件に合う施設が見つかりませんでした。"

    system_prompt = """
    あなたは街歩き案内人です。
    ユーザーの要望と施設リストを元に、おすすめを紹介してください。
    
    ルール:
    1. リストからユーザーの意図に最も近い店を3件程度選ぶ。
    2. 店名と特徴（何のお店か）を簡潔に伝える。
    3. 候補が微妙な場合（例：ドーナツ希望だがカフェしかない）は正直に「専門店はありませんが」と前置きして提案する。
    """

    user_prompt = f"""
    要望: {user_input}
    場所: {place_name}
    候補リスト: {json.dumps(candidates[:20], ensure_ascii=False)} 
    """

    res = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        # temperature指定なし
    )
    return res.choices[0].message.content

# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    user_input = input("\n質問をどうぞ（例：桂駅周辺のドーナツ屋）\n> ")

    # 1. 解析
    print("\n🧠 AI解析中...")
    conditions = optimize_search_conditions(user_input)
    target_place = conditions.get("target_place", user_input)
    target_keywords = conditions.get("keywords", [])
    
    print(f"   → 場所: {target_place}")
    print(f"   → KW : {target_keywords}")

    # 2. 座標
    lat, lon, address = get_coordinates(target_place)
    if not lat:
        print("場所が見つかりませんでした。")
        exit()
    print(f"📍 座標: {address[:20]}...")

    # 3. データ取得
    all_shops = fetch_all_nearby_shops(lat, lon, SEARCH_RADIUS)

    # 4. 絞り込み
    candidates = filter_candidates(all_shops, target_keywords)
    print(f"   → 候補: {len(candidates)} 件")

    # 5. 回答
    print("\n📝 AI回答生成中...\n")
    final_answer = generate_final_answer(user_input, address, candidates)
    
    print("========================================")
    print(final_answer)
    print("========================================")
