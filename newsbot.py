
import os
import re
import json
import html
import time
import hashlib
import logging
import requests
import feedparser

from datetime import datetime, timezone, timedelta
from urllib.parse import quote

# ---------------- CONFIG ----------------

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

STATE_FILE = "seen.json"

MAX_POSTS = 15
MAX_MESSAGE_LENGTH = 3500
MAX_HISTORY = 10000
REQUEST_TIMEOUT = 25

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; DailyNewsBot/1.0)"
})

# ---------------- RSS SOURCES ----------------

FEEDS = {
    "زومیت": "https://feeds.zoomit.ir/zoomit-it",
    "دیجیاتو": "https://digiato.com/feed/",
    "گیمفا": "https://gamefa.com/feed/",
    "زومجی": "https://www.zoomg.ir/feed/",
    "ویجیاتو": "https://vigiato.net/feed",
    "BBC فارسی": "https://feeds.bbci.co.uk/persian/rss.xml",

    "The Verge": "https://www.theverge.com/rss/index.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "Hacker News": "https://hnrss.org/frontpage",
    "BleepingComputer": "https://www.bleepingcomputer.com/feed/",

    "IGN": "https://feeds.feedburner.com/ign/all",
    "PC Gamer": "https://www.pcgamer.com/rss/",
    "GameSpot": "https://www.gamespot.com/feeds/news/",
}

# ---------------- CATEGORIES ----------------

CATEGORIES = {
    "هوش مصنوعی": [
        "هوش مصنوعی", "artificial intelligence", "openai",
        "chatgpt", "gemini", "claude", "llm", "machine learning",
        "deep learning", "ai model", "مدل زبانی"
    ],

    "گیمینگ": [
        "بازی", "گیم", "گیمینگ", "گیمفا", "کنسول",
        "playstation", "xbox", "nintendo", "steam",
        "gaming", "game", "video game", "pc gamer",
        "esports", "esport", "پابجی", "کالاف",
        "fortnite", "minecraft", "elden ring"
    ],

    "برنامه‌نویسی": [
        "python", "javascript", "programming", "developer",
        "github", "software development", "coding",
        "برنامه نویسی", "برنامه‌نویسی", "کدنویسی",
        "زبان برنامه نویسی"
    ],

    "امنیت سایبری": [
        "cybersecurity", "security", "hacker",
        "vulnerability", "malware", "ransomware",
        "هک", "امنیت سایبری", "بدافزار",
        "آسیب پذیری", "آسیب‌پذیری"
    ],

    "سخت‌افزار و گجت": [
        "hardware", "gpu", "cpu", "nvidia", "amd",
        "intel", "processor", "smartphone", "laptop",
        "گوشی", "پردازنده", "کارت گرافیک",
        "سخت افزار", "سخت‌افزار", "لپ تاپ"
    ],

    "ارز دیجیتال": [
        "bitcoin", "ethereum", "crypto", "blockchain",
        "cryptocurrency", "بیت کوین", "بیت‌کوین",
        "اتریوم", "ارز دیجیتال", "بلاکچین"
    ],

    "استارتاپ و فناوری": [
        "startup", "technology", "tech",
        "google", "microsoft", "apple",
        "استارتاپ", "فناوری", "تکنولوژی"
    ]
}

# ---------------- TEXT TOOLS ----------------

def clean_text(value):
    if not value:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"<script.*?</script>", "", value,
                   flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", "", value,
                   flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def is_persian(text):
    return bool(re.search(r"[\u0600-\u06FF]", text))


def translate_to_persian(text):
    text = clean_text(text)

    if not text or is_persian(text):
        return text

    try:
        url = "https://translate.googleapis.com/translate_a/single"

        response = SESSION.get(
            url,
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "fa",
                "dt": "t",
                "q": text[:4000]
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()
        data = response.json()

        translated = "".join(
            part[0]
            for part in data[0]
            if part and part[0]
        )

        return translated.strip() or text

    except Exception as error:
        logging.warning("Translation failed: %s", error)
        return text


def make_hash(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ---------------- STATE MANAGEMENT ----------------

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"urls": [], "titles": []}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            return {
                "urls": data.get("urls", []),
                "titles": data.get("titles", [])
            }

    except Exception as error:
        logging.error("State loading failed: %s", error)

    return {"urls": [], "titles": []}


def save_state(state):
    state["urls"] = state["urls"][-MAX_HISTORY:]
    state["titles"] = state["titles"][-MAX_HISTORY:]

    temporary = STATE_FILE + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)

    os.replace(temporary, STATE_FILE)


def is_seen(state, url, title):
    url_hash = make_hash(url) if url else ""
    title_hash = make_hash(title.lower())

    return (
        url_hash in state["urls"]
        or title_hash in state["titles"]
    )


def mark_seen(state, url, title):
    if url:
        state["urls"].append(make_hash(url))

    state["titles"].append(make_hash(title.lower()))


# ---------------- CATEGORY DETECTION ----------------

def categorize(title, summary):
    content = (title + " " + summary).lower()

    for category, keywords in CATEGORIES.items():
        for keyword in keywords:
            if keyword.lower() in content:
                return category

    return "فناوری و اخبار عمومی"


# ---------------- FETCH NEWS ----------------

def fetch_news(state):
    articles = []

    for source, feed_url in FEEDS.items():
        try:
            response = SESSION.get(
                feed_url,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            logging.info(
                "Feed loaded: %s | Entries: %s",
                source,
                len(feed.entries)
            )

            for entry in feed.entries:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(
                    entry.get("summary", "")
                    or entry.get("description", "")
                )

                url = entry.get("link", "").strip()

                if not title or not url:
                    continue

                if is_seen(state, url, title):
                    continue

                articles.append({
                    "source": source,
                    "title": title,
                    "summary": summary,
                    "url": url
                })

        except Exception as error:
            logging.error(
                "Feed error %s: %s",
                source,
                error
            )

    logging.info("New articles found: %s", len(articles))

    return articles


# ---------------- MESSAGE BUILDER ----------------

def build_message(article):
    title = translate_to_persian(article["title"])

    summary = article["summary"]

    if summary:
        summary = translate_to_persian(summary)
    else:
        summary = "خلاصه‌ای از خبر در منبع اصلی قابل مشاهده است."

    category = categorize(title, summary)

    title = html.escape(title)
    summary = html.escape(summary[:700])
    source = html.escape(article["source"])
    url = html.escape(article["url"], quote=True)

    return (
        f"📰 <b>{title}</b>\n\n"
        f"{summary}\n\n"
        f"🏷 <b>دسته:</b> {category}\n"
        f"🌐 <b>منبع:</b> {source}\n"
        f"🔗 <a href=\"{url}\">مشاهده خبر اصلی</a>\n"
        f"\n"
        f"<i>آلبرت و انیشتاین | گزارش روز</i>"
    )


# ---------------- TELEGRAM ----------------

def send_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = SESSION.post(
        url,
        data={
            "chat_id": CHANNEL_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=REQUEST_TIMEOUT
    )

    logging.info(
        "Telegram HTTP status: %s",
        response.status_code
    )

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError("Invalid Telegram response")

    if not response.ok or not result.get("ok"):
        description = result.get("description", str(result))

        logging.error("Telegram error: %s", description)

        raise RuntimeError(description)

    return True


# ---------------- MAIN ----------------

def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN or CHANNEL_ID is missing"
        )

    logging.info("Starting Daily News...")

    state = load_state()

    logging.info(
        "Previously stored URLs: %s",
        len(state["urls"])
    )

    articles = fetch_news(state)

    articles = articles[:MAX_POSTS]

    if not articles:
        logging.info("No new articles.")
        return

    logging.info("Articles selected: %s", len(articles))

    sent_count = 0

    for article in articles:
        try:
            message = build_message(article)

            if len(message) > MAX_MESSAGE_LENGTH:
                message = message[:MAX_MESSAGE_LENGTH - 30]

            send_message(message)

            mark_seen(
                state,
                article["url"],
                article["title"]
            )

            save_state(state)

            sent_count += 1

            logging.info(
                "Published %s/%s",
                sent_count,
                len(articles)
            )

            time.sleep(1)

        except Exception as error:
            logging.error(
                "Article delivery failed: %s",
                error
            )

            # Continue with the next article.
            continue

    logging.info(
        "Daily News finished. Published: %s",
        sent_count
    )


if __name__ == "__main__":
    main()
