import os
import sys
import json
import hashlib
import smtplib
import re
import argparse
import feedparser
import requests
from pathlib import Path
from urllib.parse import urljoin

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

# type: "rss" = RSSフィード, "scrape" = 一覧ページから記事リンクを抽出, "watch" = ページ差分検知
# link_pattern: type="scrape" のとき、記事リンクとみなすhrefの正規表現
# section: メール内のセクション名（""なら区分けなし）
FEEDS = {
    "ai": [
        {"name": "OpenAI",                "url": "https://openai.com/news/rss.xml",                                           "type": "rss", "section": ""},
        # AnthropicはRSSを提供していない（2026-07-25時点で /rss.xml 等は全て404）ため一覧ページを直接スクレイプする
        {"name": "Anthropic",             "url": "https://www.anthropic.com/news",                                            "type": "scrape", "section": "", "link_pattern": r"^/news/[^/?#]+$"},
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
        "health_file":  "health_ai.json",
        "use_sections": False,
        "email_subject": f"【AI新着】{{date}} — {{count}}件の新着記事",
        "email_header":  "AI最新情報まとめ",
        "summary_prompt":       "以下はAI分野のメディア「{source}」の記事です（英語の場合も含む）。日本語で3〜5行、平易な言葉でざっくり要約してください。\n\nタイトル: {title}\n\n本文:\n{body}",
        "summary_prompt_notitle":"以下はAI分野のメディア「{source}」の記事タイトルです（英語の場合も含む）。タイトルから推測できる内容を日本語で3〜5行、平易な言葉で説明してください。\n\nタイトル: {title}",
    },
    "realestate": {
        "seen_file":    "seen_realestate.json",
        "watch_file":   "seen_watch_realestate.json",
        "health_file":  "health_realestate.json",
        "use_sections": True,
        "email_subject": "【不動産新着】{date} — {count}件の新着",
        "email_header":  "不動産最新情報まとめ",
        "summary_prompt":       "以下は不動産業界メディア「{source}」の記事です。日本語で3〜5行、平易な言葉でざっくり要約してください。\n\nタイトル: {title}\n\n本文:\n{body}",
        "summary_prompt_notitle":"以下は不動産業界メディア「{source}」の記事タイトルです。タイトルから推測できる内容を日本語で3〜5行、平易な言葉で説明してください。\n\nタイトル: {title}",
    },
}

SECTION_ORDER = ["一次情報", "業界メディア"]

# 記事が1件も取れない実行がこの回数続いたら、取得元が壊れたとみなして警告する
EMPTY_RUN_ALERT_THRESHOLD = 3

UA_HEADERS = {"User-Agent": "Mozilla/5.0"}

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


def load_health(health_path: Path) -> dict:
    if health_path.exists():
        try:
            return json.loads(health_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sources": {}, "last_alert_date": ""}


def save_health(health: dict, health_path: Path):
    health_path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")


def record_health(health: dict, name: str, ok: bool, error: str) -> dict:
    """取得元1件分の成否を記録し、更新後のレコードを返す。

    ok=False が EMPTY_RUN_ALERT_THRESHOLD 回続いた取得元は警告対象になる。
    """
    record = health.setdefault("sources", {}).setdefault(name, {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if ok:
        record["consecutive_failures"] = 0
        record["last_ok"] = now
        record["last_error"] = ""
    else:
        record["consecutive_failures"] = record.get("consecutive_failures", 0) + 1
        record["last_error"] = error
        record.setdefault("last_ok", "")
    return record


def collect_health_warnings(health: dict) -> list[str]:
    """連続失敗が閾値に達した取得元の警告文を組み立てる。"""
    warnings = []
    for name, record in sorted(health.get("sources", {}).items()):
        streak = record.get("consecutive_failures", 0)
        if streak < EMPTY_RUN_ALERT_THRESHOLD:
            continue
        last_ok = record.get("last_ok") or "記録なし"
        warnings.append(
            f"⚠️ {name}: {streak}回連続で取得できていません（{record.get('last_error', '原因不明')}）"
            f"\n   最終取得成功: {last_ok}"
        )
    return warnings


def fetch_feed(url: str) -> tuple[list, str]:
    """RSSフィードをタイムアウト付きで取得してパースする。

    戻り値は (エントリのリスト, エラー文字列)。成功時のエラー文字列は空。
    """
    try:
        resp = requests.get(url, timeout=15, headers=UA_HEADERS)
        resp.raise_for_status()
    except Exception as e:
        return [], f"取得失敗: {e}"

    parsed = feedparser.parse(resp.content)
    if not parsed.entries:
        # bozo_exception はパース失敗の原因（XMLでないHTMLが返ってきた等）を持つ
        if parsed.bozo:
            return [], f"パース失敗: {parsed.bozo_exception}"
        return [], "フィードは取得できたがエントリが0件"
    return list(parsed.entries), ""


def fetch_scraped_links(url: str, link_pattern: str) -> tuple[list, str]:
    """一覧ページのHTMLから記事リンクとタイトルを抽出する。

    RSSが提供されていないサイト向け。戻り値は fetch_feed と同じ (エントリ, エラー文字列) で、
    エントリは feedparser と同じく "link" / "title" を持つ辞書。
    """
    try:
        resp = requests.get(url, timeout=15, headers=UA_HEADERS)
        resp.raise_for_status()
    except Exception as e:
        return [], f"取得失敗: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    pattern = re.compile(link_pattern)
    entries = []
    found = set()

    for a in soup.find_all("a", href=True):
        if not pattern.match(a["href"]):
            continue
        link = urljoin(url, a["href"])
        if link in found:
            continue

        # CSSクラス名はビルドごとに変わるため構造だけで判断する。
        # 見出しタグがあればそれを、無ければ日付・カテゴリを除いたリンクテキストをタイトルとする。
        heading = a.find(["h1", "h2", "h3", "h4"])
        if heading:
            title = heading.get_text(" ", strip=True)
        else:
            for time_tag in a.find_all("time"):
                (time_tag.parent if time_tag.parent is not a else time_tag).decompose()
            title = a.get_text(" ", strip=True)

        title = " ".join(title.split())
        if not title:
            continue
        found.add(link)
        entries.append({"link": link, "title": title})

    if not entries:
        return [], f"HTMLから記事リンクを抽出できなかった（パターン: {link_pattern}）"
    return entries, ""


def fetch_article_text(url: str) -> str:
    try:
        resp = requests.get(url, timeout=10, headers=UA_HEADERS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        paragraphs = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
        return text[:3000] if text else ""
    except Exception:
        return ""


def compute_page_hash(url: str) -> tuple[str, str]:
    """ページのテキストコンテンツをMD5ハッシュ化して返す。戻り値は (ハッシュ, エラー文字列)。"""
    try:
        resp = requests.get(url, timeout=15, headers=UA_HEADERS)
        resp.raise_for_status()
    except Exception as e:
        return "", f"取得失敗: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    if not text:
        return "", "ページ本文が空"
    return hashlib.md5(text.encode("utf-8")).hexdigest(), ""


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


def build_email_body(articles: list[dict], page_updates: list[dict], config: dict,
                     warnings: list[str] | None = None) -> str:
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

    if warnings:
        lines.append("=" * 36)
        lines.append("【取得元の異常】")
        lines.append("=" * 36)
        lines.extend(warnings)
        lines.append("")
        lines.append("※ 配信元のURL変更・フィード廃止が疑われます。monitor.py の FEEDS を確認してください。")

    return "\n".join(lines)


def build_alert_body(warnings: list[str], config: dict) -> str:
    """新着ゼロで通常メールが出ないときに送る、警告のみのメール本文。"""
    date_str = datetime.now().strftime('%Y年%m月%d日 %H:%M')
    lines = [
        f"{config['email_header']} — 取得元の異常検知（{date_str}）\n",
        f"{EMPTY_RUN_ALERT_THRESHOLD}回以上連続で取得に失敗している配信元があります。\n",
    ]
    lines.extend(warnings)
    lines.append("")
    lines.append("※ 配信元のURL変更・フィード廃止が疑われます。monitor.py の FEEDS を確認してください。")
    return "\n".join(lines)


def send_mail(subject: str, body: str):
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def send_email(body: str, total_count: int, config: dict):
    date_str = datetime.now().strftime('%m/%d')
    send_mail(config["email_subject"].format(date=date_str, count=total_count), body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["ai", "realestate"], default="ai",
                        help="配信モード: ai（朝9時・15時）または realestate（昼12時）")
    args = parser.parse_args()

    config = MODE_CONFIG[args.mode]
    base_dir = Path(__file__).parent

    health_path = base_dir / config["health_file"]
    health = load_health(health_path)

    # ── 記事フィード処理（RSS / スクレイプ） ─────────────────
    seen_path = base_dir / config["seen_file"]
    seen = load_seen(seen_path)
    new_articles = []

    article_feeds = [f for f in FEEDS[args.mode] if f.get("type", "rss") in ("rss", "scrape")]
    for feed_info in article_feeds:
        name = feed_info["name"]
        if feed_info.get("type", "rss") == "scrape":
            entries, error = fetch_scraped_links(feed_info["url"], feed_info["link_pattern"])
        else:
            entries, error = fetch_feed(feed_info["url"])

        record = record_health(health, name, ok=bool(entries), error=error)
        if entries:
            print(f"  [OK] {name}: {len(entries)}件取得")
        else:
            streak = record["consecutive_failures"]
            print(f"  [失敗] {name}: {error}（{streak}回連続）")

        for entry in entries[:10]:
            url = entry.get("link", "")
            if not url or url in seen:
                continue
            title = entry.get("title", "(タイトルなし)")
            print(f"  処理中: [{name}] {title}")
            body = fetch_article_text(url)
            summary = summarize(name, title, body, config)
            new_articles.append({
                "source":  name,
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
            name = feed_info["name"]
            print(f"  差分確認: [{name}]")
            new_hash, error = compute_page_hash(url)
            record = record_health(health, name, ok=bool(new_hash), error=error)
            if not new_hash:
                print(f"    → [失敗] {error}（{record['consecutive_failures']}回連続）")
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

    # ── 取得元の健全性チェック ────────────────────────────
    warnings = collect_health_warnings(health)
    for warning in warnings:
        print(f"警告: {warning}")

    # メール送信で落ちても取得結果の記録が消えないよう、先に保存しておく
    save_health(health, health_path)

    # ── メール送信 ───────────────────────────────────────
    total_count = len(new_articles) + len(page_updates)

    if total_count > 0:
        # 通常配信。警告があれば末尾に付けて気づけるようにする
        email_body = build_email_body(new_articles, page_updates, config, warnings)
        send_email(email_body, total_count, config)
        print(f"メール送信完了（記事:{len(new_articles)}件 / 更新検知:{len(page_updates)}件）")
    else:
        print("新着・更新なし。メール送信をスキップします。")
        # 新着が無いと通常メールが飛ばないため、警告だけ別途通知する（1日1回まで）
        today = datetime.now().strftime("%Y-%m-%d")
        if warnings and health.get("last_alert_date") != today:
            alert_body = build_alert_body(warnings, config)
            try:
                send_mail(f"【監視警告】{len(warnings)}件の取得元が停止しています", alert_body)
                health["last_alert_date"] = today
                save_health(health, health_path)
                print(f"取得エラー警告メールを送信しました（{len(warnings)}件）")
            except Exception as e:
                print(f"警告メールの送信に失敗: {e}")


if __name__ == "__main__":
    main()
