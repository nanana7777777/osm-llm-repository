#原点回帰
#検索地点、場所を把握、近くのスポットを間違いないように、取りこぼさないようにまとめる。これを最優先で完成させます。
#アルゴリズムとしては
#ユーザー入力→LLMでユーザーが行きたい場所、探したいものをしっかり把握、次にOSMにわたす準備　例京都駅 ラーメン屋 →OSMからLLMで探したデータを取りこぼしなく参照する、取ってくる、→取ってきた参照した生データを元にLLmで最後の文章作成
import os
import json
import requests
from openai import OpenAI

# ==========================================
# 設定
# ==========================================
client = OpenAI()
MODEL_NAME = "gpt-5-nano-2025-08-07"
SEARCH_RADIUS = 500 

# ==========================================
# 1. 検索クエリ最適化 (場所をシンプルに)
# ==========================================
def optimize_search_conditions(user_input):
    """
    Nominatimが迷わないよう、場所名は「名称 + 県名」程度に留める。
    """
    system_prompt = """
    あなたはGIS検索の司令塔です。
    ユーザー入力から「場所」と「検索キーワード」を抽出し、JSONで出力してください。

    1. target_place: 
       - Nominatimで検索するための「最も一般的でシンプルな地名」。
       - **住所を長く詳しく書きすぎないこと。** ヒット率が下がります。
       - 駅名や施設名なら、それ単体＋都道府県名くらいがベストです。
       - 悪い例: "京都府 京都市 下京区 京都駅" (細かすぎて失敗する)
       - 良い例: "京都駅 京都府"

    2. search_keywords: 
       - OSMサーバー検索用のキーワードリスト。
       - 取りこぼしがないよう、日本語（店名用）と英語（タグ用）を必ず含める。
       - "ラーメン" -> ["ラーメン", "らーめん", "中華そば", "ramen", "noodle", "chinese"]

    出力形式:
    {
      "target_place": "...",
      "search_keywords": ["...", "..."]
    }
    """

    try:
        res = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"入力: {user_input}"}
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print(f"AI Error: {e}")
        return {"target_place": user_input, "search_keywords": []}

# ==========================================
# 2. 座標特定 (変更なし)
# ==========================================
def get_coordinates(place_name):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": place_name,
        "format": "json",
        "limit": 1,
        "countrycodes": "jp"
    }
    headers = {"User-Agent": "osm-stable-search/1.0"}
    
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        data = res.json()
        if data:
            # 念のため、見つかった場所の名前を表示して確認できるようにする
            found_name = data[0]["display_name"]
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return lat, lon, found_name
    except:
        pass
    return None, None, None

# ==========================================
# 3. OSMサーバー検索 (正規表現)
# ==========================================
def fetch_targeted_data(lat, lon, radius, keywords):
    url = "https://overpass-api.de/api/interpreter"
    
    if not keywords: return []

    # 正規表現結合
    regex_str = "|".join([k for k in keywords if k])
    
    # 検索クエリ
    query = f"""
    [out:json][timeout:25];
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
    
    print(f"📡 OSMサーバー検索中... (Regex: {regex_str[:20]}...)")
    
    try:
        res = requests.post(url, data={"data": query}, timeout=30)
        res.raise_for_status()
        return res.json().get("elements", [])
    except Exception as e:
        print(f"Overpass Error: {e}")
        return []

# ==========================================
# 4. LLM回答生成
# ==========================================
def generate_final_answer(user_input, place_name, data_list):
    if not data_list:
        return "周辺に条件に合う施設が見つかりませんでした。（場所の特定に失敗したか、OSMデータがない可能性があります）"

    limited_list = data_list[:30]

    system_prompt = """
    あなたは地図検索アシスタントです。
    提供された施設リスト（OSMデータ）に基づき、おすすめを紹介してください。
    データにある事実（店名やタグ）のみを使用してください。
    """

    user_prompt = f"""
    ユーザー入力: {user_input}
    検索中心地: {place_name}
    結果リスト: {json.dumps(limited_list, ensure_ascii=False)}
    """

    res = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )
    return res.choices[0].message.content

# ==========================================
# MAIN
# ==========================================
if __name__ == "__main__":
    user_input = input("\n何をお探しですか？\n> ")

    # 1. 準備
    print("\n🧠 解析中...")
    cond = optimize_search_conditions(user_input)
    target_place = cond.get("target_place")
    keywords = cond.get("search_keywords")
    
    print(f"   場所名: {target_place}")
    print(f"   KW    : {keywords}")

    # 2. 場所特定
    lat, lon, found_name = get_coordinates(target_place)
    if not lat:
        print("❌ 場所が見つかりませんでした。入力された地名を確認してください。")
        exit()
    
    # 検索された住所の先頭部分を表示して、ユーザーが正しいか判断できるようにする
    print(f"📍 特定完了: {found_name[:30]}...") 
    print(f"   (Lat: {lat}, Lon: {lon})")

    # 3. 検索
    data = fetch_targeted_data(lat, lon, SEARCH_RADIUS, keywords)
    print(f"   → ヒット数: {len(data)}件")

    # 4. 回答
    print("\n📝 回答生成中...\n")
    print(generate_final_answer(user_input, found_name, data))
