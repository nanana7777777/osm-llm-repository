import json
import math

# ==========================================
# 設定: ここを書き換えて検証できます
# ==========================================
# パターンA: コードのデフォルト (北大路駅中心)
CURRENT_LAT = 35.0445726
CURRENT_LON = 135.7587094

# パターンB: PDFの実験で使われたと推測される地点 (ハンデルスベーゲン付近)
# CURRENT_LAT = 35.043792
# CURRENT_LON = 135.759865

FILE_PATH = "北大路駅_osm_data.json"

# ==========================================
# 計算ロジック (OsmLLm.pyより抜粋)
# ==========================================
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # 地球の半径 (m)
    phi1, phi2 = map(math.radians, [lat1, lat2])
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

# ターゲット店舗リスト (PDFより)
targets = [
    # Q1: ファストフード
    "マクドナルド",
    "ケンタッキーフライドチキン",
    "ミスタードーナツ",
    # Q2: カフェ
    "スターバックス",
    "BANANA LIFE",
    "コメダ珈琲店",
    "ハンデルスベーゲン",
    # Q3: 金融機関
    "京都中央信用金庫",
    "中央信用金庫",
    "滋賀銀行",
    # Q4: スーパー
    "KOHYO",
    "Jupiter",
    "オーガニックプラザ",
    # Q5: 病院
    "京都警察病院",
    "京都博愛会冨田病院",
    "ひきだ歯科医院",
    "柏井歯科医院",
    # Q6: 交通
    "北大路バスターミナル",
    # Q7: 自転車
    "Bike Laboratory",
    "チャリパ",
    # Q8: 肉
    "いきなり！ステーキ",
    "市場小路",
    # Q9: 家具
    "ニトリ",
    # Q10: 和食 (重複除く)
    "千鳥寿司",
    "小木曽製粉所",
    "大戸屋ごはん処"
]

# データ読み込みと計算
try:
    with open(FILE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    print(f"基準点: ({CURRENT_LAT}, {CURRENT_LON})")
    print("-" * 60)
    print(f"{'店舗名':<20} | {'計算距離':<10} | {'座標'}")
    print("-" * 60)

    for target_name in targets:
        for element in data:
            name = element.get("tags", {}).get("name", "")
            if target_name in name:
                lat = element.get("lat") or element.get("center", {}).get("lat")
                lon = element.get("lon") or element.get("center", {}).get("lon")
                
                if lat and lon:
                    dist = calculate_distance(CURRENT_LAT, CURRENT_LON, lat, lon)
                    print(f"{name[:18]:<20} | {dist:.1f}m      | ({lat}, {lon})")

except FileNotFoundError:
    print(f"エラー: {FILE_PATH} が見つかりません。")
