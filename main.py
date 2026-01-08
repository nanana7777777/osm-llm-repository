import os
import json
import requests
from openai import OpenAI

# client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
# 一旦こう書き換えて保存してください
client = OpenAI(api_key="YOUR_API_KEY_HERE")

def search_place(query: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "format": "json",
        "q": query,
        "limit": 3,
    }
    headers = {
        "User-Agent": "osm-llm-demo/1.0"
    }
    res = requests.get(url, params=params, headers=headers)
    res.raise_for_status()
    return res.json()

def ask_llm_with_map(json_data):
    prompt = f"""
以下はOpenStreetMapから取得した地図データ(JSON)です。
この情報から分かることを、日本語でわかりやすく説明してください。

制約:
- JSONに書かれていない情報は推測しないでください。
- 分からないことは「不明」と書いてください。

JSON:
{json.dumps(json_data, indent=2, ensure_ascii=False)}
    """.strip()

    response = client.chat.completions.create(
        model="gpt-5-nano-2025-08-07",
        messages=[{"role": "user", "content": prompt}],
    )

    return response.choices[0].message.content

if __name__ == "__main__":
    places = search_place("Kyoto Station")
    print("=== OSM 生データ ===")
    print(json.dumps(places, indent=2, ensure_ascii=False))

    print("\n=== LLMの説明 ===")
    answer = ask_llm_with_map(places)
    print(answer)
