# まとめ： 「キーワードに該当する施設だけ をピンポイントで指名して、その施設の プロフィール全部 をダウンロードしている」という挙動になります。

#2026-01-08: SVN導入テスト。ここからバージョン管理スタート！
import os
import json
import requests
import math
import time
from openai import OpenAI

# ==========================================
# 設定
# ==========================================
client = OpenAI()
MODEL_NAME = "gpt-5-nano-2025-08-07"
SEARCH_RADIUS = 1000  # 1km
TIMEOUT_SEC = 90      # タイムアウトを60秒に延長

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

def save_results_to_file(place, user_input, data, filename="osm_search_log.json"):
    output_content = {
        "user_input": user_input,
        "search_center": place,
        "hit_count": len(data),
        "results": data
    }
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_content, f, ensure_ascii=False, indent=2)
    except:
        pass

# ==========================================
# 1. 検索クエリ最適化
# ==========================================
def optimize_search_conditions(user_input):
    system_prompt = """
    あなたはGIS検索の司令塔です。
    ユーザー入力から「検索中心点」と「検索キーワード」をJSONで出力してください。

    rules:
    1. target_place (Where): Nominatim用住所。
    2. search_keywords (What): 
       - OSMサーバー検索用キーワード。
       - **数は「最大8個」程度に厳選すること**（多すぎるとエラーになるため）。
       - 英語タグ（restaurant, cafe等）を優先し、日本語は代表的なものだけにする。
       - `target_place` に含まれる単語は除外すること。

    出力: {"target_place": "...", "search_keywords": ["...", "..."]}
    """
    try:
        res = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": f"入力: {user_input}"}],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except:
        return {"target_place": user_input, "search_keywords": []}

# ==========================================
# 2. 座標特定
# ==========================================
def get_coordinates(place_name):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": place_name, "format": "json", "limit": 1, "countrycodes": "jp"}
    headers = {"User-Agent": "osm-robust-agent/1.0"}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=10)
        data = res.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0]["display_name"]
    except:
        pass
    return None, None, None

# ==========================================
# 3. OSMサーバー検索 (リトライ機能付き)
# ==========================================
def fetch_targeted_data(lat, lon, radius, keywords):
    url = "https://overpass-api.de/api/interpreter"
    
    if not keywords: return []

    # 第1段階: 全キーワードで検索
    # キーワードが多すぎる場合は先頭10個にカットして安全性を確保
    safe_keywords = keywords[:10]
    regex_str = "|".join([k for k in safe_keywords if k])
    
    # タイムアウト指定を延長 [timeout:60]
    query = f"""
    [out:json][timeout:{TIMEOUT_SEC}];
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
    
    print(f"📡 OSM検索中 (KW数:{len(safe_keywords)})...")
    
    try:
        res = requests.post(url, data={"data": query}, timeout=TIMEOUT_SEC + 5)
        res.raise_for_status()
        elements = res.json().get("elements", [])
        
        # もし0件だったら、キーワードを「英語のみ」に絞ってリトライ（日本語Regexが重い場合があるため）
        if len(elements) == 0:
            print("⚠️ 0件でした。検索条件を緩和してリトライします...")
            english_keywords = [k for k in safe_keywords if k.isascii()] # ASCII文字(英語)のみ抽出
            
            if not english_keywords:
                return [] # 英語キーワードがなければ終了

            retry_regex = "|".join(english_keywords)
            print(f"📡 リトライ中 (Regex: {retry_regex})...")
            
            query_retry = f"""
            [out:json][timeout:{TIMEOUT_SEC}];
            (
              node["amenity"~"{retry_regex}",i](around:{radius},{lat},{lon});
              node["shop"~"{retry_regex}",i](around:{radius},{lat},{lon});
              node["cuisine"~"{retry_regex}",i](around:{radius},{lat},{lon});
              way["amenity"~"{retry_regex}",i](around:{radius},{lat},{lon});
              way["shop"~"{retry_regex}",i](around:{radius},{lat},{lon});
            );
            out center;
            """
            res_retry = requests.post(url, data={"data": query_retry}, timeout=TIMEOUT_SEC + 5)
            res_retry.raise_for_status()
            return res_retry.json().get("elements", [])

        return elements

    except Exception as e:
        print(f"Overpass Error: {e}")
        return []

# ==========================================
# 4. データ整形
# ==========================================
def process_data_for_llm(elements, center_lat, center_lon):
    processed = []
    
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name", "名称なし")
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        
        distance_val = 99999
        distance_str = "距離不明"
        maps_url = ""
        
        if lat and lon:
            dist_m = calculate_distance(center_lat, center_lon, lat, lon)
            distance_val = dist_m
            distance_str = f"約{dist_m}m"
            maps_url = f"http://googleusercontent.com/maps.google.com/maps?q={lat},{lon}"

        processed.append({
            "name": name,
            "distance": distance_str,
            "dist_val": distance_val,
            "maps_url": maps_url,
            "details": tags
        })
    
    processed.sort(key=lambda x: x["dist_val"])
    for item in processed:
        del item["dist_val"]
        
    return processed

# ==========================================
# 5. 回答生成
# ==========================================
def generate_final_answer(user_input, place_name, data_list):
    if not data_list:
        return "申し訳ありません。サーバー混雑やデータ不足により、条件に合う施設が見つかりませんでした。"

    top_list = data_list[:15] # リストを少し多めに渡す

    system_prompt = """
    あなたは親切なナビゲーターです。
    リストの施設を距離の近い順に紹介してください。
    [Googleマップ](URL) のリンクを含めてください。
    """

    user_prompt = f"""
    入力: {user_input}
    場所: {place_name}
    データ: {json.dumps(top_list, ensure_ascii=False)}
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
    user_input = input("\n何をお探しですか？\n> ")

    print("\n🧠 解析中...")
    cond = optimize_search_conditions(user_input)
    target_place = cond.get("target_place")
    keywords = cond.get("search_keywords")
    print(f"   Where: {target_place}")
    print(f"   What : {keywords}")

    lat, lon, found_name = get_coordinates(target_place)
    if not lat:
        print("❌ 場所が見つかりませんでした。")
        exit()
    print(f"📍 {found_name[:20]}...")

    # 検索実行
    raw_data = fetch_targeted_data(lat, lon, SEARCH_RADIUS, keywords)
    
    # 整形
    processed_data = process_data_for_llm(raw_data, lat, lon)
    print(f"   → ヒット数: {len(processed_data)}件")
    
    save_results_to_file(found_name, user_input, processed_data)

    print("\n📝 ナビゲーション:\n")
    print(generate_final_answer(user_input, found_name, processed_data))
