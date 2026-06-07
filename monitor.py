import os
import json
import smtplib
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

RSS_FEEDS = [
    {"name": "OpenAI", "url": "https://openai.com/news/rss.xml"},
    {"name": "Anthropic", "url": "https://www.anthropic.com/rss.xml"},
    {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml"},
]

SEEN_FILE = Path(__file__).parent / "seen_articles.json"
client = Anthropic(api_key=ANTHROPIC_API_KEY)


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False, indent=2), encoding="utf-8")


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


def summarize(source: str, title: str, url: str, body: str) -> str:
    if body:
        prompt = f"以下はAI企業「{source}」の記事です。日本語で3〜5行、平易な言葉でざっくり要約してください。\n\nタイトル: {title}\n\n本文:\n{body}"
    else:
        prompt = f"以下はAI企業「{source}」の記事タイトルです。タイトルから推測できる内容を日本語で3〜5行、平易な言葉で説明してください。\n\nタイトル: {title}"

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def build_email_body(articles: list[dict]) -> str:
    lines = [f"AI最新情報まとめ — {datetime.now().strftime('%Y年%m月%d日')}\n"]
    for a in articles:
        lines.append(f"📌 {a['source']} より新着")
        lines.append(f"タイトル：{a['title']}")
        lines.append(f"🗒 ざっくり言うと：")
        lines.append(a["summary"])
        lines.append(f"🔗 元記事 → {a['url']}")
        lines.append("")
    return "\n".join(lines)


def send_email(body: str, article_count: int):
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = f"【AI新着】{datetime.now().strftime('%m/%d')} — {article_count}件の新着記事"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def main():
    seen = load_seen()
    new_articles = []

    for feed_info in RSS_FEEDS:
        feed = feedparser.parse(feed_info["url"])
        for entry in feed.entries[:10]:
            url = entry.get("link", "")
            if not url or url in seen:
                continue

            title = entry.get("title", "(タイトルなし)")
            print(f"  処理中: [{feed_info['name']}] {title}")

            body = fetch_article_text(url)
            summary = summarize(feed_info["name"], title, url, body)

            new_articles.append({
                "source": feed_info["name"],
                "title": title,
                "url": url,
                "summary": summary,
            })
            seen.add(url)

    save_seen(seen)

    if not new_articles:
        print("新着記事なし。メール送信をスキップします。")
        return

    email_body = build_email_body(new_articles)
    send_email(email_body, len(new_articles))
    print(f"メール送信完了（{len(new_articles)}件）")


if __name__ == "__main__":
    main()
