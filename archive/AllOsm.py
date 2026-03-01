import os
import json
from openai import OpenAI
from dotenv import load_dotenv

# .env 読み込み
load_dotenv()

# ==========================================
# 設定
# ==========================================
client = OpenAI()
MODEL_NAME = "gpt-4o-mini"  # コストパフォーマンスの良いモデル推奨
JSON_FILE_PATH = "北大路駅_osm_data.json"

# ==========================================
# 1. データの読み込み関数
# ==========================================
def load_osm_data(filename):
    if not os.path.exists(filename):
        print(f"❌ ファイルが見つかりません: {filename}")
        return []
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

# ==========================================
# メイン処理: 全データ丸投げモード
# ==========================================
if __name__ == "__main__":
    # データをロード
    all_data = load_osm_data(JSON_FILE_PATH)
    if not all_data:
        exit()
    
    # 全データを文字列化 (約1.6万トークン)
    # ※そのままAIに渡します
    all_data_text = json.dumps(all_data, ensure_ascii=False)

    history = []
    print("\n🧪 全データ丸投げ実験モード (AllOsm) 起動しました。")
    print("データ内のあらゆる情報をAIが直接探します。")

    while True:
        user_input = input("\nYou: ")
        if user_input.lower() in ["q", "exit", "quit"]:
            break

        # システムプロンプト: AIに「あなたは地図データそのものを見ている」と教える
        system_prompt = """
        あなたはGISデータの専門家です。
        以下のJSONデータには、ある地域のすべての地図情報が含まれています。
        このデータ全体を分析し、ユーザーの質問に答えてください。
        
        # ルール
        1. データにある情報（tags）を根拠に回答してください。
        2. 距離計算が必要な場合は、データ内の座標(lat, lon)を使って「おおよその位置関係」を判断してください。
           （現在地: 北大路駅 35.04457, 135.75870 と仮定）
        3. 推論能力をフル活用し、「ポテト」ならファストフード、「誕生日」ならケーキ屋やレストランなどを連想して探してください。
        """

        # AIへのメッセージ構成
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"【全地図データ】\n{all_data_text}\n\n【ユーザーの質問】: {user_input}"}
        ]

        print("🤖 AIが全155件のデータを読んでいます... (少し時間がかかります)")
        
        try:
            res = client.chat.completions.create(
                model=MODEL_NAME, 
                messages=messages
            )
            response = res.choices[0].message.content
            print(f"\nAI: {response}")
            
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": response})

        except Exception as e:
            print(f"エラーが発生しました: {e}")
