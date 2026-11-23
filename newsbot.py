import os
import re
import json
import html
import hashlib
import logging
import feedparser
import requests

from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


# ==================================================
# CONFIGURATION
# ==================================================

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["CHANNEL_ID"]

TZ = timezone(timedelta(hours=3, minutes=30))

STATE_FILE = "seen.json"

MAX_POSTS = 10
MAX_MESSAGE_LENGTH = 3500
MAX_HISTORY = 10000

REQUEST_TIMEOUT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


# ==================================================
# RSS SOURCES
# ==================================================

FEEDS = [
    "https://www.ithome.com/rss/",
    "https://news.google.com/rss/search?q=%D9%87%D9%88%D8%B4+%D9%85%D8%B5%D8%B7%D9%86%D8%B9%DB%8C&hl=fa&gl=IR&ceid=IR:fa",
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://hnrss.org/frontpage?points=150",
    "https://www.bleepingcomputer.com/feed/",
]


# ==================================================
# CATEGORIES
# ==================================================

CATEGORIES = {
    "هوش مصنوعی": [
        "ai", "artificial intelligence", "llm", "gpt",
        "gemini", "claude", "openai", "anthropic",
        "machine learning", "deep learning",
        "هوش مصنوعی", "یادگیری ماشین", "مدل زبانی"
    ],

    "برنامه‌نویسی": [
        "python", "javascript", "github", "developer",
        "software", "programming", "coding", "framework",
        "برنامه‌نویسی", "کدنویسی", "توسعه‌دهنده", "نرم‌افزار"
    ],

    "سخت‌افزار": [
        "nvidia", "gpu", "cpu", "chip", "processor",
        "amd", "intel", "hardware", "semiconductor",
        "چیپ", "پردازنده", "سخت‌افزار", "تراشه"
    ],

    "امنیت سایبری": [
        "security", "hacking", "hack", "breach",
        "malware", "ransomware", "vulnerability",
        "cybersecurity", "exploit",
        "امنیت", "هک", "بدافزار", "آسیب‌پذیری"
    ],

    "استارتاپ و فناوری": [
        "startup", "technology", "tech", "innovation",
        "funding", "acquisition", "big tech",
        "استارتاپ", "فناوری", "سرمایه‌گذاری"
    ],

    "ارز دیجیتال": [
        "crypto", "bitcoin", "ethereum", "blockchain",
        "web3", "cryptocurrency",
        "کریپتو", "بیت کوین", "اتریوم", "بلاکچین"
    ]
}


# ==================================================
# TEXT PROCESSING
# ==================================================

def normalize(text):
    text = html.unescape(text or "")

    text = text.replace("ي", "ی")
    text = text.replace("ك", "ک")
    text = text.replace("\u200c", " ")

    text = re.sub(r"\s+", " ", text)

    return text.strip().lower()


def clean(text, limit=600):
    text = html.unescape(text or "")

    text = re.sub(
        r"<(script|style).*?>.*?</\1>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()[:limit]


def make_hash(text):
    return hashlib.sha256(
        normalize(text).encode("utf-8")
    ).hexdigest()


def normalize_url(url):
    try:
        parts = urlsplit(url.strip())

        tracking = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
            "ref",
            "source"
        }

        query = [
            (key, value)
            for key, value in parse_qsl(parts.query)
            if key.lower() not in tracking
        ]

        return urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(query),
            ""
        ))

    except Exception:
        return url.strip()


# ==================================================
# CATEGORIZATION
# ==================================================

def categorize(text):
    text = normalize(text)

    scores = {}

    for category, keywords in CATEGORIES.items():
        score = 0

        for keyword in keywords:
            keyword = normalize(keyword)

            if re.fullmatch(r"[a-z0-9 ]+", keyword):
                pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"

                if re.search(pattern, text):
                    score += 1
            elif keyword in text:
                score += 1

        scores[category] = score

    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return None

    return best


# ==================================================
# PERSISTENT STATE
# ==================================================

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Invalid state format")

        urls = data.get("urls", [])
        titles = data.get("titles", [])

        if not isinstance(urls, list):
            urls = []

        if not isinstance(titles, list):
            titles = []

        return {
            "urls": urls,
            "titles": titles
        }

    except FileNotFoundError:
        logging.warning("State file not found.")
    except (json.JSONDecodeError, ValueError):
        logging.warning("State file is invalid.")

    return {
        "urls": [],
        "titles": []
    }


def save_state(state):
    state["urls"] = list(dict.fromkeys(state["urls"]))[-MAX_HISTORY:]
    state["titles"] = list(dict.fromkeys(state["titles"]))[-MAX_HISTORY:]

    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, STATE_FILE)


def is_seen(state, url, title):
    url_hash = make_hash(normalize_url(url))
    title_hash = make_hash(title)

    return (
        url_hash in state["urls"]
        or title_hash in state["titles"]
    )


def mark_seen(state, articles):
    for article in articles:
        url_hash = make_hash(
            normalize_url(article["url"])
        )

        title_hash = make_hash(
            article["title"]
        )

        state["urls"].append(url_hash)
        state["titles"].append(title_hash)

    state["urls"] = list(dict.fromkeys(state["urls"]))
    state["titles"] = list(dict.fromkeys(state["titles"]))


# ==================================================
# RSS READER
# ==================================================

def fetch_news(state):
    posts = []

    for feed_url in FEEDS:

        try:
            response = requests.get(
                feed_url,
                timeout=20,
                headers={
                    "User-Agent": "DailyNewsBot/1.0"
                }
            )

            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if feed.bozo:
                logging.warning(
                    "RSS parsing warning: %s",
                    feed_url
                )

            logging.info(
                "Feed loaded: %s | Entries: %s",
                feed_url,
                len(feed.entries)
            )

            for entry in feed.entries[:20]:

                title = clean(
                    entry.get("title", ""),
                    200
                )

                link = entry.get("link", "")

                summary = clean(
                    entry.get("summary", ""),
                    600
                )

                if not title or not link:
                    continue

                category = categorize(
                    title + " " + summary
                )

                if category is None:
                    continue

                if is_seen(state, link, title):
                    continue

                posts.append({
                    "title": title,
                    "url": link,
                    "summary": summary or "خلاصه‌ای در منبع موجود نیست.",
                    "category": category
                })

        except Exception:
            logging.exception(
                "Feed failed: %s",
                feed_url
            )

    # Remove duplicate articles in current batch

    unique = {}

    for post in posts:
        key = make_hash(post["title"])

        if key not in unique:
            unique[key] = post

    return list(unique.values())


# ==================================================
# TELEGRAM MESSAGE BUILDER
# ==================================================

def build_messages(posts):

    header = (
        "🧠 <b>آلبرت و انیشتاین | گزارش روز</b>\n"
        f"📅 {datetime.now(TZ).strftime('%Y-%m-%d')}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    messages = []

    current_message = header
    current_articles = []

    for post in posts:

        title = html.escape(post["title"])
        category = html.escape(post["category"])
        summary = html.escape(post["summary"])
        link = html.escape(post["url"], quote=True)

        item = (
            f"📰 <b>{title}</b>\n"
            f"🏷 {category}\n\n"
            f"{summary}\n\n"
            f"🔗 {link}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        if len(current_message) + len(item) > MAX_MESSAGE_LENGTH:

            if current_articles:
                messages.append((
                    current_message,
                    current_articles
                ))

            current_message = header
            current_articles = []

        current_message += item
        current_articles.append(post)

    if current_articles:
        messages.append((
            current_message,
            current_articles
        ))

    return messages


# ==================================================
# TELEGRAM SENDER
# ==================================================

def send_message(text):

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    response = requests.post(
        url,
        json=payload,
        timeout=REQUEST_TIMEOUT
    )

    logging.info(
        "Telegram HTTP status: %s",
        response.status_code
    )

    try:
        result = response.json()

    except ValueError:
        raise RuntimeError(
            "Telegram returned invalid JSON: "
            + response.text[:1000]
        )

    # Print the actual Telegram error.

    if not response.ok or not result.get("ok"):

        description = result.get(
            "description",
            str(result)
        )

        error_code = result.get(
            "error_code",
            response.status_code
        )

        logging.error(
            "Telegram API error %s: %s",
            error_code,
            description
        )

        raise RuntimeError(
            f"Telegram API error {error_code}: {description}"
        )

    logging.info("Telegram message sent successfully.")

    return True


# ==================================================
# MAIN
# ==================================================

def main():

    state = load_state()

    logging.info("Starting Daily News...")

    logging.info(
        "Previously stored URLs: %s",
        len(state["urls"])
    )

    posts = fetch_news(state)

    logging.info(
        "New articles found: %s",
        len(posts)
    )

    posts = posts[:MAX_POSTS]

    if not posts:
        logging.info("No new technology news.")
        return

    messages = build_messages(posts)

    logging.info(
        "Messages to send: %s",
        len(messages)
    )

    for message, articles in messages:

        try:
            send_message(message)

            # Save only after successful delivery.

            mark_seen(state, articles)
            save_state(state)

            logging.info(
                "Successfully sent %s articles.",
                len(articles)
            )

        except Exception:

            # Preserve state of previously delivered messages.

            save_state(state)

            logging.exception(
                "Telegram delivery failed."
            )

            raise

    logging.info(
        "Daily News completed successfully."
    )


# ==================================================
# ENTRY POINT
# ==================================================

if __name__ == "__main__":
    main()
