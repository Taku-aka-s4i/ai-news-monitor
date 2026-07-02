# ai-news-monitor

AI企業（OpenAI・Anthropic・Google DeepMind）の公式ブログRSSを毎日監視し、新着記事をClaude APIで日本語要約してGmailで届けるツール。

## 機能

### 監視対象（AIモード）
| メディア | RSS URL |
|---------|---------|
| OpenAI | https://openai.com/news/rss.xml |
| Anthropic | https://www.anthropic.com/rss.xml |
| Google DeepMind | https://deepmind.google/blog/rss.xml |
| VentureBeat AI | https://venturebeat.com/category/ai/feed/ |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ |
| Google AI Blog | https://blog.google/technology/ai/rss/ |
| Hugging Face | https://huggingface.co/blog/feed.xml |
| The Decoder | https://the-decoder.com/feed/ |
| MIT Tech Review | https://www.technologyreview.com/feed/ |

### 処理フロー
1. **RSS取得** — 各フィードから最新10件を取得
2. **記事スクレイピング** — 本文をHTMLから抽出（失敗時はタイトル+URLにフォールバック）
3. **日本語要約** — Claude API（Haiku）で3〜5行の平易な要約を生成
4. **重複チェック** — 送信済み記事をJSONで管理し、同じ記事を2回送らない
5. **Gmail送信** — SMTP SSLで指定アドレスにメール送信

### メール形式
```
📌 OpenAI より新着
タイトル：（原題）
🗒 ざっくり言うと：
（日本語3〜5行の要約）
🔗 元記事 → （URL）
```

### 自動実行
macOSのlaunchdで1日3回自動実行される。ログは `logs/monitor.log` に記録。

| 時刻 | モード | 内容 |
|-----|--------|-----|
| 09:00 | ai | AI関連フィードを取得・要約・送信 |
| 12:00 | realestate | 不動産関連フィードを取得・要約・送信 |
| 15:00 | ai | AI関連フィードを再取得・要約・送信 |

launchdの`.plist`は `~/Library/LaunchAgents/` に配置:
- `com.takuma.ai-news-monitor.ai-morning.plist`（9時／ai）
- `com.takuma.ai-news-monitor.realestate-noon.plist`（12時／realestate）
- `com.takuma.ai-news-monitor.ai-afternoon.plist`（15時／ai）

指定時刻にMacがスリープ中ならスリープ復帰時に、電源オフならログイン時に自動的に実行される（launchdの仕様）。

Windows環境の場合はタスクスケジューラで `run.bat` を実行する構成でも動作する（旧構成）。

## セットアップ

### 1. 依存パッケージのインストール

macOS / Linux:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows:
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 環境変数の設定
`.env.example` をコピーして `.env` を作成し、各値を埋める。

```
ANTHROPIC_API_KEY=sk-ant-...        # console.anthropic.com で取得
GMAIL_ADDRESS=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx  # Googleアカウント → セキュリティ → アプリパスワード
RECIPIENT_EMAIL=your@gmail.com
```

### 3. 動作確認
```bash
python monitor.py --mode ai          # AI関連フィード
python monitor.py --mode realestate  # 不動産関連フィード
```

macOSの場合、ラッパースクリプト経由でも実行可能:
```bash
./run.sh ai
./run.sh realestate
```

## 技術スタック
- Python 3.10+
- `feedparser` — RSSパース
- `requests` + `beautifulsoup4` — スクレイピング
- `anthropic` — Claude API（要約生成）
- `python-dotenv` — 環境変数管理
- Gmail SMTP — メール送信

## ファイル構成
```
ai-news-monitor/
├── monitor.py               # メインスクリプト
├── requirements.txt
├── run.sh                   # macOS/Linux用ラッパー
├── run.bat                  # Windowsタスクスケジューラ用
├── run-ai.bat               # Windows用（AIモード）
├── run-realestate.bat       # Windows用（不動産モード）
├── .env.example             # 認証情報テンプレート
├── .env                     # 認証情報（gitignore済み）
├── seen_ai.json             # AI関連の送信済み記事管理（自動生成）
├── seen_realestate.json     # 不動産関連の送信済み記事管理（自動生成）
├── seen_watch_realestate.json  # 不動産の差分検知ハッシュ（自動生成）
└── logs/
    └── monitor.log          # 実行ログ
```

## 今後の拡張予定
- 不動産×AI特化のキーワードフィルタリング
- 複数送信先対応（チーム共有）
- 重要度スコアリングによる厳選配信
