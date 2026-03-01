import os
import json
import requests
import math
import time
from openai import OpenAI
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

# ==========================================
# 設定
# ==========================================
client = OpenAI()

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

# ---------------------------
# 2️⃣ OSM 生データ取得（全て）
# ---------------------------
def fetch_all_osm_data(lat, lon, radius=300):
    url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:90];
    (
      node(around:{radius},{lat},{lon});
      way(around:{radius},{lat},{lon});
      relation(around:{radius},{lat},{lon});
    );
    out tags center;
    """
    res = requests.post(url, data={"data": query})
    res.raise_for_status()
    return res.json()

# ---------------------------
# Main
# ---------------------------
if __name__ == "__main__":
    place = input("調べたい場所（例: 京都駅）: ")
    
    # 場所検索
    places = search_place(place)
    if not places:
        print("場所が見つかりませんでした。")
        exit()

    lat = float(places[0]["lat"])
    lon = float(places[0]["lon"])

    print(f"\n検索対象地点: {places[0]['display_name']}")
    print(f"lat={lat}, lon={lon}")

# データ取得
    print("データを取得中...")
    raw_data = fetch_all_osm_data(lat, lon)

    # ==========================================
    # ★追加: 名前があるデータだけを選別するフィルター
    # ==========================================
    clean_data = []
    if "elements" in raw_data:
        for item in raw_data["elements"]:
            # 「タグ」があり、かつ「名前」が書かれているものだけ残す
            if "tags" in item and "name" in item["tags"]:
                clean_data.append(item)
    
    print(f"整理結果: {len(raw_data.get('elements', []))}件 → {len(clean_data)}件 に絞り込みました")

    # ファイルに保存（clean_data を使う）
    import os
    filename = f"{place}_osm_data.json"
    full_path = os.path.join(os.getcwd(), filename)

    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(clean_data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保存完了！\n場所: {full_path}")
