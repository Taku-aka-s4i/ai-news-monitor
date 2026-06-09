import os
import json
import smtplib
import argparse
import feedparser
import requests
from pathlib import Path
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

FEEDS = {
    "ai": [
        {"name": "OpenAI",          "url": "https://openai.com/news/rss.xml"},
        {"name": "Anthropic",       "url": "https://www.anthropic.com/rss.xml"},
        {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml"},
    ],
    "realestate": [
        {"name": "楽待新聞",       "url": "https://www.rakumachi.jp/news/feed/"},
        {"name": "SUUMOジャーナル","url": "https://suumo.jp/journal/feed/"},
        {"name": "住宅産業新聞",   "url": "https://www.housenews.jp/feed"},
    ],
}

MODE_CONFIG = {
    "ai": {
        "seen_file": "seen_ai.json",
        "email_subject": f"【AI新着】{{date}} — {{count}}件の新着記事",
        "email_header": "AI最新情報まとめ",
        "summary_prompt": "以下はAI企業「{source}」の記事です。日本語で3〜5行、平易な言葉でざっくり要約してください。\n\nタイトル: {title}\n\n本文:\n{body}",
        "summary_prompt_notitle": "以下はAI企業「{source}」の記事タイトルです。タイトルから推測できる内容を日本語で3〜5行、平易な言葉で説明してください。\n\nタイトル: {title}",
    },
    "realestate": {
        "seen_file": "seen_realestate.json",
        "email_subject": "【不動産新着】{date} — {count}件の新着記事",
        "email_header": "不動産最新情報まとめ",
        "summary_prompt": "以下は不動産業界メディア「{source}」の記事です。日本語で3〜5行、平易な言葉でざっくり要約してください。\n\nタイトル: {title}\n\n本文:\n{body}",
        "summary_prompt_notitle": "以下は不動産業界メディア「{source}」の記事タイトルです。タイトルから推測できる内容を日本語で3〜5行、平易な言葉で説明してください。\n\nタイトル: {title}",
    },
}

client = Anthropic(api_key=ANTHROPIC_API_KEY)


def load_seen(seen_path: Path) -> set:
    if seen_path.exists():
        return set(json.loads(seen_path.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set, seen_path: Path):
    seen_path.write_text(json.dumps(list(seen), ensure_ascii=False, indent=2), encoding="utf-8")


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


def build_email_body(articles: list[dict], config: dict) -> str:
    date_str = datetime.now().strftime('%Y年%m月%d日')
    lines = [f"{config['email_header']} — {date_str}\n"]
    for a in articles:
        lines.append(f"📌 {a['source']} より新着")
        lines.append(f"タイトル：{a['title']}")
        lines.append(f"🗒 ざっくり言うと：")
        lines.append(a["summary"])
        lines.append(f"🔗 元記事 → {a['url']}")
        lines.append("")
    return "\n".join(lines)


def send_email(body: str, article_count: int, config: dict):
    date_str = datetime.now().strftime('%m/%d')
    subject = config["email_subject"].format(date=date_str, count=article_count)

    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["ai", "realestate"], default="ai",
                        help="配信モード: ai（朝8時）または realestate（昼12時）")
    args = parser.parse_args()

    config = MODE_CONFIG[args.mode]
    seen_path = Path(__file__).parent / config["seen_file"]
    seen = load_seen(seen_path)
    new_articles = []

    for feed_info in FEEDS[args.mode]:
        feed = feedparser.parse(feed_info["url"])
        for entry in feed.entries[:10]:
            url = entry.get("link", "")
            if not url or url in seen:
                continue

            title = entry.get("title", "(タイトルなし)")
            print(f"  処理中: [{feed_info['name']}] {title}")

            body = fetch_article_text(url)
            summary = summarize(feed_info["name"], title, body, config)

            new_articles.append({
                "source": feed_info["name"],
                "title": title,
                "url": url,
                "summary": summary,
            })
            seen.add(url)

    save_seen(seen, seen_path)

    if not new_articles:
        print("新着記事なし。メール送信をスキップします。")
        return

    email_body = build_email_body(new_articles, config)
    send_email(email_body, len(new_articles), config)
    print(f"メール送信完了（{len(new_articles)}件）")


if __name__ == "__main__":
    main()
