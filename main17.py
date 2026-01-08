#ユーザーが入力した情報をタグ検索してからOSMから取ってくる
#スコアリング機能も実装
import os
import json
import requests
from openai import OpenAI

client = OpenAI()
MODEL_NAME = "gpt-5-nano-2025-08-07"
SEARCH_RADIUS = 500

# ==========================================
# 1. 検索クエリの構造化 (厳格モード)
# ==========================================
def optimize_search_conditions(user_input):
    """
    ユーザーの入力を分析する。
    ★重要: ユーザーが言及していない条件（Wifiや電源など）を勝手に補完しないこと。
    """
    system_prompt = """
    あなたは検索クエリ抽出AIです。
    ユーザーの入力文から検索条件を抽出し、JSONで出力してください。

    1. target_place: Nominatim検索用の地名（県名等を補完）。
    2. must_keywords: 
       - 「絶対に外せない」施設の種類（日本語・英語）。
       - 例: "カフェ" -> ["cafe", "coffee"]
    3. want_keywords: 
       - **ユーザーが入力文の中で明示的に求めた**追加条件（日本語・英語）。
       - **重要:** ユーザーが言及していない条件（"wifi", "電源", "静か"など）は絶対にリストに入れないでください。空リストでも構いません。
       - 類義語は含めてOKです（例: "ネット" -> ["wifi", "internet"]）。

    出力例（入力: "桂駅のカフェ"）:
    {
      "target_place": "桂駅 京都府",
      "must_keywords": ["cafe", "coffee"],
      "want_keywords": []  <-- 言及がないので空にする
    }

    出力例（入力: "桂駅のWifiがあるカフェ"）:
    {
      "target_place": "桂駅 京都府",
      "must_keywords": ["cafe", "coffee"],
      "want_keywords": ["wifi", "internet", "wlan"] <-- 言及があるので入れる
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
        )
        return json.loads(res.choices[0].message.content)
    except:
        return {"target_place": user_input, "must_keywords": [], "want_keywords": []}

# ==========================================
# 2. 座標特定 (変更なし)
# ==========================================
def get_coordinates(place_name):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": place_name, "format": "json", "limit": 1, "countrycodes": "jp"}
    headers = {"User-Agent": "osm-llm-strict/1.0"}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        data = res.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0]["display_name"]
    except:
        pass
    return None, None, None

# ==========================================
# 3. データ取得 (変更なし)
# ==========================================
def fetch_nearby_facilities(lat, lon, radius):
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:15][maxsize:1073741824];
    (
      node["amenity"](around:{radius},{lat},{lon});
      node["shop"](around:{radius},{lat},{lon});
      way["amenity"](around:{radius},{lat},{lon});
      way["shop"](around:{radius},{lat},{lon});
    );
    out center;
    """
    try:
        res = requests.post(url, data={"data": query}, timeout=15)
        res.raise_for_status()
        return res.json().get("elements", [])
    except:
        return []

# ==========================================
# 4. スコアリング (厳格マッチング)
# ==========================================
def score_candidates(elements, must_kws, want_kws):
    scored_list = []
    
    must_kws = [k.lower() for k in must_kws if k]
    want_kws = [k.lower() for k in want_kws if k]

    print(f"🔍 スコアリング設定: 必須={must_kws}, 加点={want_kws}")

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name", "")
        if not name: continue

        # --- 検索用テキストの生成 ---
        # タグの値を「検索可能なキーワード」に変換してテキストに埋め込む
        # これにより、ユーザーが「Wifi」と言った時だけヒットするようになる
        
        search_text_parts = [
            name.lower(),
            tags.get("amenity", ""),
            tags.get("shop", ""),
            tags.get("cuisine", "")
        ]

        # ★重要: タグを「キーワード」に翻訳して埋め込む
        # Wifiタグがある -> "wifi" という文字を持たせる
        if tags.get("internet_access") in ["wlan", "yes", "wifi"]:
            search_text_parts.append("wifi internet wlan")
        
        # 電源タグがある -> "power" という文字を持たせる
        if tags.get("socket") in ["yes", "plugs"]:
            search_text_parts.append("power socket outlet 電源 コンセント")
            
        full_text = " ".join(search_text_parts).lower()
        
        score = 0
        
        # 1. 必須キーワード判定
        if must_kws:
            if not any(k in full_text for k in must_kws):
                continue
            score += 50

        # 2. 加点キーワード判定
        # ユーザーが指定したキーワード(want_kws)が full_text にある場合のみ加点される
        # 指定がなければ、いくらWifiがあっても加点されない（0点）
        matched_points = []
        for k in want_kws:
            if k in full_text:
                score += 10
                matched_points.append(k)

        scored_list.append({
            "name": name,
            "score": score,
            "matched": matched_points,
            "details": tags # LLMに渡す用
        })

    # スコア順 > 名前順 でソート
    scored_list.sort(key=lambda x: (-x["score"], x["name"]))
    return scored_list

# ==========================================
# 5. 回答生成 (変更なし)
# ==========================================
def generate_recommendation(user_input, place_name, candidates):
    if not candidates:
        return "条件に合う施設が見つかりませんでした。"
    
    # 上位3件
    top_candidates = candidates[:3]
    
    system_prompt = """
    あなたは誠実なガイドです。
    ユーザーの要望と、データに基づいて案内してください。
    データにないことを「ある」と言わないでください。
    """
    
    user_prompt = f"""
    要望: {user_input}
    場所: {place_name}
    候補: {json.dumps(top_candidates, ensure_ascii=False)}
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

    # 1. 解析
    cond = optimize_search_conditions(user_input)
    print(f"🧠 解析結果 -> 必須: {cond['must_keywords']}, 加点: {cond['want_keywords']}")
    
    # 2. 座標
    lat, lon, addr = get_coordinates(cond['target_place'])
    if not lat: exit()
    print(f"📍 {addr[:15]}...")

    # 3. 取得
    data = fetch_nearby_facilities(lat, lon, SEARCH_RADIUS)
    
    # 4. スコアリング
    candidates = score_candidates(data, cond['must_keywords'], cond['want_keywords'])
    print(f"   → 候補: {len(candidates)}件")

    # 5. 生成
    print("\n📝 回答:\n")
    print(generate_recommendation(user_input, addr, candidates))
