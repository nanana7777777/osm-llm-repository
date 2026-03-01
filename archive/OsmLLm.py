import os
import json
import math
from openai import OpenAI
from dotenv import load_dotenv

# .env 読み込み
load_dotenv()

# ==========================================
# 設定
# ==========================================
client = OpenAI()
MODEL_NAME = "gpt-4o-mini"  # コストパフォーマンスの良いモデル推奨
CURRENT_LAT = 35.0445726    # 北大路駅周辺と仮定 (デフォルト)
CURRENT_LON = 135.7587094
JSON_FILE_PATH = "北大路駅_osm_data.json"

# ==========================================
# 1. データの読み込み & 距離計算
# ==========================================
def load_osm_data(filename):
    if not os.path.exists(filename):
        print(f"❌ ファイルが見つかりません: {filename}")
        return []
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = map(math.radians, [lat1, lat2])
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return int(R * c)

# ★追加: 名前から座標を探す関数
def find_location_center(data, place_name):
    for item in data:
        tags = item.get("tags", {})
        name = tags.get("name", "")
        # 部分一致で探す (例: "立命館" で "立命館小学校" をヒットさせる)
        if place_name in name:
            lat = item.get("lat") or item.get("center", {}).get("lat")
            lon = item.get("lon") or item.get("center", {}).get("lon")
            if lat and lon:
                return lat, lon
    return None, None

# ==========================================
# 2. ユーザーの意図を解析 (修正版)
# ==========================================
def analyze_user_intent(user_input, history):
    """
    ユーザーの入力と会話履歴から、検索すべきタグやキーワードを抽出する
    """
    system_prompt = """
    あなたはGISデータの検索クエリ生成エンジニアです。
    ユーザーの質問と会話履歴から、OSMデータ検索用の条件をJSONで出力してください。

    # 重要ルール
    1. 最優先事項: 【現在の質問】の内容を最優先でキーワード化してください。
    2. 履歴の扱い: 履歴は「場所」の文脈理解にのみ使い、テーマは【現在の質問】のみを採用してください。

    # ★出力言語のルール（ここが重要）
    - keywords (検索タグ): 原則として【英語単語】に変換してください。
      - "誕生日" -> ["restaurant", "cake"]
      - "コンビニ" -> ["convenience"]
    
    - locations (場所名): ユーザーが言及した固有名詞は【日本語のまま】出力してください。
      - NG: "Starbucks"
      - OK: "スターバックス"
    
    # 出力フォーマット (JSON)
    {
      "keywords": ["keyword1", "keyword2"], 
      "locations": ["場所A", "場所B"],
      "category_hint": "カテゴリ名"
    }
    """

    # 直近の会話履歴をテキスト化
    history_text = "\n".join([f"{h['role']}: {h['content']}" for h in history[-4:]])

    try:
        res = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"---履歴開始---\n{history_text}\n---履歴終了---\n\n【現在の質問】: {user_input}"}
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        print(f"解析エラー: {e}")
        return {"keywords": [], "locations": [], "category_hint": "不明"}
# ==========================================
# 3. データ検索ロジック
# ==========================================
def search_osm_data(all_data, criteria):
    keywords = criteria.get("keywords", [])
    results = []
    
    if not keywords:
        return []

    print(f"🔍 検索条件: {keywords}")

    for item in all_data:
        tags = item.get("tags", {})
        # タグのキーと値をすべて検索対象の文字列にする
        tags_str = json.dumps(tags, ensure_ascii=False).lower()
        
        # キーワードのいずれかが含まれていればヒット (OR検索)
        for k in keywords:
            if k.lower() in tags_str:
                results.append(item)
                break
    
    return results

# ==========================================
# 4. データ整形 (修正完了版)
# ==========================================
def process_data(elements, current_lat, current_lon):
    processed = []
    for el in elements:
        # ★ここが抜けていたので修正しました
        tags = el.get("tags", {})
        name = tags.get("name", "名称なし")
        
        # 緯度経度の取得
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")

        dist_val = 99999
        dist_str = "距離不明"
        
        if lat and lon:
            # ★修正: 引数の座標を使って計算
            dist_val = calculate_distance(current_lat, current_lon, lat, lon)
            dist_str = f"約{dist_val}m"

        processed.append({
            "name": name,
            "distance": dist_str,
            "dist_val": dist_val,
            "tags": tags # タグ詳細
        })
    
    # 距離順ソート
    processed.sort(key=lambda x: x["dist_val"])
    return processed[:15] # 上位15件に絞る

# ==========================================
# 5. 回答生成 (History対応)
# ==========================================
def generate_response(user_input, search_results, history, intent):
    
    system_prompt = """
    あなたはドライブ中の家族や友人をサポートする、気の利いたナビゲーターです。
    ユーザーの質問に対して、検索されたJSONデータを元に回答してください。

    # 回答のガイドライン
    1. **提案**: ユーザーの状況（雨、子供連れ、時間帯）を考慮して、リストから最適なものを2-3個提案してください。
    2. **正直さ**: データに「評判」や「混雑状況」は含まれていません。もし聞かれたら「データに口コミはありませんが、チェーン店なので安定しています」や「駅前なので混んでいる可能性があります」のように推測で補足するか、正直にデータがないことを伝えてください。
    3. **文脈**: 「さっきの場所より～」などの指示があれば、会話履歴を踏まえて回答してください。
    4. **タグ活用**: `tags` 情報を読み取り、「テイクアウト可(takeaway=yes)」「屋内(indoor=yes)」などの根拠を示してください。

    回答は親しみやすく、簡潔にお願いします。
    """

    # 検索結果をテキスト化
    data_text = json.dumps(search_results, ensure_ascii=False, indent=2)
    if not search_results:
        data_text = "（該当する施設は見つかりませんでした）"

    # 今回のメッセージを構築
    messages = [
        {"role": "system", "content": system_prompt},
    ]
    # 過去の履歴を追加（直近4ターン分程度）
    messages.extend(history[-4:])
    
    # 最新のコンテキストを追加
    user_content = f"""
    質問: {user_input}
    検索意図: {intent.get('category_hint')}
    検索結果データ:
    {data_text}
    """
    messages.append({"role": "user", "content": user_content})

    res = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages
    )
    return res.choices[0].message.content

# ==========================================
# 6. 実験ログの保存
# ==========================================
def save_interaction_log(user_input, intent, search_results, response, filename="experiment_log.json"):
    log_entry = {
        "user_input": user_input,
        "intent_analysis": intent,
        "hit_count": len(search_results),
        "ai_response": response,
    }
    
    # 追記モードで保存
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            try:
                logs = json.load(f)
            except:
                logs = []
    else:
        logs = []
    
    logs.append(log_entry)
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

# ==========================================
# メイン処理
# ==========================================
if __name__ == "__main__":
    all_data = load_osm_data(JSON_FILE_PATH)
    if not all_data:
        exit()
    
    history = []
    print("\n🚗 ドライブ・ナビゲーター (経路検索対応版) 起動しました。")

    while True:
        user_input = input("\nYou: ")
        if user_input.lower() in ["q", "exit", "quit"]:
            break

        # 1. 意図解析
        intent = analyze_user_intent(user_input, history)
        
        # ★追加: 動的な中心点の決定ロジック
        # デフォルトは設定ファイルの初期値
        search_lat = CURRENT_LAT
        search_lon = CURRENT_LON
        
        target_locs = intent.get("locations", [])
        found_coords = []

        # 抽出された地名をデータから探す
        for loc_name in target_locs:
            lat, lon = find_location_center(all_data, loc_name)
            if lat:
                found_coords.append((lat, lon))
                print(f"📍 地点特定: {loc_name} -> ({lat}, {lon})")

        # 地点が見つかった場合、その中間点を新しい検索中心にする
        if found_coords:
            avg_lat = sum(c[0] for c in found_coords) / len(found_coords)
            avg_lon = sum(c[1] for c in found_coords) / len(found_coords)
            search_lat = avg_lat
            search_lon = avg_lon
            print(f"🎯 検索中心を移動しました: {target_locs} の中間地点")
        else:
            print(f"📍 検索中心: 北大路駅周辺 (デフォルト)")

        # 2. データ検索
        raw_results = search_osm_data(all_data, intent)
        
        # 3. 整形 (★修正: 動的に決まった search_lat, search_lon を渡す)
        processed_results = process_data(raw_results, search_lat, search_lon)
        
        print(f"   (検索キーワード: {intent.get('keywords')} -> {len(processed_results)}件ヒット)")

        # 4. 回答生成
        response = generate_response(user_input, processed_results, history, intent)
        print(f"\nAI: {response}")

        # ログ保存と履歴更新 (★重複を削除しました)
        save_interaction_log(user_input, intent, processed_results, response)

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": response})
