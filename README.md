# 地図データとLLMを利用した対話型地理情報推薦システム

DEIM2026での発表システムの実装コードです。
LLMの文脈理解とOpenStreetMap (OSM) の実データを統合し、ハルシネーションを防ぎながら複合条件検索を実現します。

## 概要
* **論文**: [DEIM 2026 (https://pub.confit.atlas.jp/ja/event/deim2026/presentation/4K-01)

## システムの特徴
* LLMには検索タグの生成のみを行わせ、実際のデータ検索・距離計算はOSMのJSONデータを用いてローカルで処理（ハルシネーション対策）。
* 緯度経度からの空間距離計算アルゴリズムの実装。

## 技術スタック
* Python 3.9.16
* OpenAI API (gpt-5-mini)
* Overpass API

## 実行方法
1. `.env.example` をコピーして `.env` を作成し、OpenAIのAPIキーを設定。
2. 以下のコマンドで実行
   ```bash
   python main.py
