import os
import sys
import json
import hashlib
import smtplib
import argparse
import feedparser
import requests
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

# type: "rss" = RSSフィード, "watch" = ページ差分検知
# section: メール内のセクション名（""なら区分けなし）
FEEDS = {
    "ai": [
        {"name": "OpenAI",                "url": "https://openai.com/news/rss.xml",                                           "type": "rss", "section": ""},
        {"name": "Anthropic",             "url": "https://www.anthropic.com/rss.xml",                                         "type": "rss", "section": ""},
        {"name": "Google DeepMind",       "url": "https://deepmind.google/blog/rss.xml",                                      "type": "rss", "section": ""},
        {"name": "VentureBeat AI",        "url": "https://venturebeat.com/category/ai/feed/",                                 "type": "rss", "section": ""},
        {"name": "TechCrunch AI",         "url": "https://techcrunch.com/category/artificial-intelligence/feed/",             "type": "rss", "section": ""},
        {"name": "Google AI Blog",        "url": "https://blog.google/technology/ai/rss/",                                    "type": "rss", "section": ""},
        {"name": "Hugging Face",          "url": "https://huggingface.co/blog/feed.xml",                                     "type": "rss", "section": ""},
        {"name": "The Decoder",           "url": "https://the-decoder.com/feed/",                                             "type": "rss", "section": ""},
        {"name": "MIT Tech Review",       "url": "https://www.technologyreview.com/feed/",                                    "type": "rss", "section": ""},
    ],
    "realestate": [
        # 一次情報ライン（差分検知）
        {"name": "国交省 不動産価格指数", "url": "https://www.mlit.go.jp/totikensangyo/totikensangyo_tk5_000085.html", "type": "watch", "section": "一次情報"},
        {"name": "REINSライブラリ",       "url": "https://www.reins.or.jp/library/",                                    "type": "watch", "section": "一次情報"},
        {"name": "不動産経済研究所",      "url": "https://www.fudousankeizai.co.jp/",                                   "type": "watch", "section": "一次情報"},
        # 業界メディアライン（RSS）
        {"name": "楽待新聞",              "url": "https://www.rakumachi.jp/news/feed/",                                  "type": "rss",   "section": "業界メディア"},
        {"name": "SUUMOジャーナル",       "url": "https://suumo.jp/journal/feed/",                                       "type": "rss",   "section": "業界メディア"},
        {"name": "住宅産業新聞",          "url": "https://www.housenews.jp/feed",                                        "type": "rss",   "section": "業界メディア"},
    ],
}

MODE_CONFIG = {
    "ai": {
        "seen_file":    "seen_ai.json",
        "watch_file":   None,
        "use_sections": False,
        "email_subject": f"【AI新着】{{date}} — {{count}}件の新着記事",
        "email_header":  "AI最新情報まとめ",
        "summary_prompt":       "以下はAI分野のメディア「{source}」の記事です（英語の場合も含む）。日本語で3〜5行、平易な言葉でざっくり要約してください。\n\nタイトル: {title}\n\n本文:\n{body}",
        "summary_prompt_notitle":"以下はAI分野のメディア「{source}」の記事タイトルです（英語の場合も含む）。タイトルから推測できる内容を日本語で3〜5行、平易な言葉で説明してください。\n\nタイトル: {title}",
    },
    "realestate": {
        "seen_file":    "seen_realestate.json",
        "watch_file":   "seen_watch_realestate.json",
        "use_sections": True,
        "email_subject": "【不動産新着】{date} — {count}件の新着",
        "email_header":  "不動産最新情報まとめ",
        "summary_prompt":       "以下は不動産業界メディア「{source}」の記事です。日本語で3〜5行、平易な言葉でざっくり要約してください。\n\nタイトル: {title}\n\n本文:\n{body}",
        "summary_prompt_notitle":"以下は不動産業界メディア「{source}」の記事タイトルです。タイトルから推測できる内容を日本語で3〜5行、平易な言葉で説明してください。\n\nタイトル: {title}",
    },
}

SECTION_ORDER = ["一次情報", "業界メディア"]

client = Anthropic(api_key=ANTHROPIC_API_KEY, timeout=60.0, max_retries=2)


def load_seen(seen_path: Path) -> set:
    if seen_path.exists():
        return set(json.loads(seen_path.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set, seen_path: Path):
    seen_path.write_text(json.dumps(list(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def load_watch_hashes(watch_path: Path) -> dict:
    if watch_path.exists():
        return json.loads(watch_path.read_text(encoding="utf-8"))
    return {}


def save_watch_hashes(hashes: dict, watch_path: Path):
    watch_path.write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_feed(url: str):
    """RSSフィードをタイムアウト付きで取得してパースする。取得失敗時は空のフィードを返す。"""
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except Exception:
        return feedparser.parse(b"")


def fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        paragraphs = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
        return text[:3000] if text else ""
    except Exception:
        return ""


def compute_page_hash(url: str) -> str:
    """ページのテキストコンテンツをMD5ハッシュ化して返す。取得失敗時は空文字。"""
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return hashlib.md5(text.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def summarize(source: str, title: str, body: str, config: dict) -> str:
    if body:
        prompt = config["summary_prompt"].format(source=source, title=title, body=body)
    else:
        prompt = config["summary_prompt_notitle"].format(source=source, title=title)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def build_email_body(articles: list[dict], page_updates: list[dict], config: dict) -> str:
    date_str = datetime.now().strftime('%Y年%m月%d日')
    fetch_time = datetime.now().strftime('%Y/%m/%d %H:%M')
    lines = [f"{config['email_header']} — {date_str}\n"]

    if config["use_sections"]:
        # セクション別に集約
        sections: dict[str, list] = {}
        for item in page_updates:
            s = item.get("section", "一次情報")
            sections.setdefault(s, []).append({**item, "is_watch": True})
        for item in articles:
            s = item.get("section", "その他")
            sections.setdefault(s, []).append({**item, "is_watch": False})

        for section_name in SECTION_ORDER:
            items = sections.get(section_name, [])
            if not items:
                continue
            lines.append("=" * 36)
            lines.append(f"【{section_name}ライン】")
            lines.append("=" * 36)
            for item in items:
                if item["is_watch"]:
                    lines.append(f"🔔 {item['source']} — ページ更新を検知")
                    lines.append(f"取得日時：{fetch_time}  ソース：{item['source']}")
                    lines.append(f"🔗 {item['url']}")
                else:
                    lines.append(f"📌 {item['source']} より新着")
                    lines.append(f"タイトル：{item['title']}")
                    lines.append(f"取得日時：{fetch_time}  ソース：{item['source']}")
                    lines.append("🗒 ざっくり言うと：")
                    lines.append(item["summary"])
                    lines.append(f"🔗 元記事 → {item['url']}")
                lines.append("")
    else:
        for a in articles:
            lines.append(f"📌 {a['source']} より新着")
            lines.append(f"タイトル：{a['title']}")
            lines.append(f"取得日時：{fetch_time}  ソース：{a['source']}")
            lines.append("🗒 ざっくり言うと：")
            lines.append(a["summary"])
            lines.append(f"🔗 元記事 → {a['url']}")
            lines.append("")

    return "\n".join(lines)


def send_email(body: str, total_count: int, config: dict):
    date_str = datetime.now().strftime('%m/%d')
    subject = config["email_subject"].format(date=date_str, count=total_count)

    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["ai", "realestate"], default="ai",
                        help="配信モード: ai（朝9時・15時）または realestate（昼12時）")
    args = parser.parse_args()

    config = MODE_CONFIG[args.mode]
    base_dir = Path(__file__).parent

    # ── RSS フィード処理 ──────────────────────────────────
    seen_path = base_dir / config["seen_file"]
    seen = load_seen(seen_path)
    new_articles = []

    rss_feeds = [f for f in FEEDS[args.mode] if f.get("type", "rss") == "rss"]
    for feed_info in rss_feeds:
        feed = fetch_feed(feed_info["url"])
        for entry in feed.entries[:10]:
            url = entry.get("link", "")
            if not url or url in seen:
                continue
            title = entry.get("title", "(タイトルなし)")
            print(f"  処理中: [{feed_info['name']}] {title}")
            body = fetch_article_text(url)
            summary = summarize(feed_info["name"], title, body, config)
            new_articles.append({
                "source":  feed_info["name"],
                "title":   title,
                "url":     url,
                "summary": summary,
                "section": feed_info.get("section", ""),
            })
            seen.add(url)

    save_seen(seen, seen_path)

    # ── 差分検知処理 ─────────────────────────────────────
    page_updates = []
    watch_feeds = [f for f in FEEDS[args.mode] if f.get("type") == "watch"]

    if watch_feeds and config["watch_file"]:
        watch_path = base_dir / config["watch_file"]
        watch_hashes = load_watch_hashes(watch_path)

        for feed_info in watch_feeds:
            url = feed_info["url"]
            print(f"  差分確認: [{feed_info['name']}]")
            new_hash = compute_page_hash(url)
            if not new_hash:
                print(f"    → 取得失敗、スキップ")
                continue
            old_hash = watch_hashes.get(url, "")
            if not old_hash:
                print(f"    → 初回登録")
            elif new_hash != old_hash:
                print(f"    → 更新検知!")
                page_updates.append({
                    "source":  feed_info["name"],
                    "url":     url,
                    "section": feed_info.get("section", "一次情報"),
                })
            watch_hashes[url] = new_hash

        save_watch_hashes(watch_hashes, watch_path)

    # ── メール送信 ───────────────────────────────────────
    total_count = len(new_articles) + len(page_updates)
    if total_count == 0:
        print("新着・更新なし。メール送信をスキップします。")
        return

    email_body = build_email_body(new_articles, page_updates, config)
    send_email(email_body, total_count, config)
    print(f"メール送信完了（RSS:{len(new_articles)}件 / 更新検知:{len(page_updates)}件）")


if __name__ == "__main__":
    main()
