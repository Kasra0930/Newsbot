
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


# ---------------- CONFIG ----------------

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["CHANNEL_ID"]

TZ = timezone(timedelta(hours=3, minutes=30))
STATE_FILE = "seen.json"

MAX_POSTS = 10
MAX_MESSAGE_LENGTH = 3500
MAX_HISTORY = 10000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

FEEDS = [
    "https://www.ithome.com/rss/",
    "https://news.google.com/rss/search?q=%D9%87%D9%88%D8%B4+%D9%85%D8%B5%D8%B7%D9%86%D8%B9%DB%8C&hl=fa&gl=IR&ceid=IR:fa",
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://hnrss.org/frontpage?points=150",
    "https://www.bleepingcomputer.com/feed/",
]

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


# ---------------- TEXT PROCESSING ----------------

def normalize(text):
    text = html.unescape(text or "")
    text = text.replace("ي", "ی").replace("ك", "ک")
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
    text = re.sub(r"\s+", " ", text).strip()

    return text[:limit]


def make_hash(text):
    return hashlib.sha256(
        normalize(text).encode("utf-8")
    ).hexdigest()


def normalize_url(url):
    try:
        parts = urlsplit(url.strip())

        tracking = {
            "utm_source", "utm_medium", "utm_campaign",
            "utm_term", "utm_content", "fbclid", "gclid",
            "ref", "source"
        }

        query = [
            (k, v)
            for k, v in parse_qsl(parts.query)
            if k.lower() not in tracking
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


# ---------------- CATEGORIZATION ----------------

def categorize(text):
    t = normalize(text)

    scores = {}

    for category, keywords in CATEGORIES.items():
        scores[category] = sum(
            1 for keyword in keywords
            if normalize(keyword) in t
        )

    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return None

    return best


# ---------------- PERSISTENT STATE ----------------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except (FileNotFoundError, json.JSONDecodeError):
        logging.warning("State file missing or invalid.")

    return {"urls": [], "titles": []}


def save_state(state):
    state["urls"] = state["urls"][-MAX_HISTORY:]
    state["titles"] = state["titles"][-MAX_HISTORY:]

    temp = STATE_FILE + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    os.replace(temp, STATE_FILE)


def is_seen(state, url, title):
    return (
        make_hash(normalize_url(url)) in state["urls"]
        or make_hash(title) in state["titles"]
    )


def mark_seen(state, articles):
    for article in articles:
        state["urls"].append(
            make_hash(normalize_url(article["url"]))
        )

        state["titles"].append(
            make_hash(article["title"])
        )

    state["urls"] = list(dict.fromkeys(state["urls"]))
    state["titles"] = list(dict.fromkeys(state["titles"]))


# ---------------- RSS READER ----------------

def fetch_news(state):
    posts = []

    for feed_url in FEEDS:
        try:
            response = requests.get(
                feed_url,
                timeout=20,
                headers={"User-Agent": "DailyNewsBot/1.0"}
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if feed.bozo:
                logging.warning("RSS warning: %s", feed_url)

            for entry in feed.entries[:20]:
                title = clean(entry.get("title", ""), 200)
                link = entry.get("link", "")

                summary = clean(
                    entry.get("summary", ""),
                    600
                )

                if not title or not link:
                    continue

                category = categorize(title + " " + summary)

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
            logging.exception("Feed failed: %s", feed_url)

    # Remove duplicates inside the current batch
    unique = {}
    for post in posts:
        key = make_hash(post["title"])
        unique.setdefault(key, post)

    return list(unique.values())


# ---------------- TELEGRAM ----------------

def build_messages(posts):
    header = (
        "🧠 <b>آلبرت و انیشتاین | گزارش روز</b>\n"
        f"📅 {datetime.now(TZ).strftime('%Y-%m-%d')}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    messages = []
    current = header
    current_posts = []

    for post in posts:
        item = (
            f"📰 <b>{html.escape(post['title'])}</b>\n"
            f"🏷 {html.escape(post['category'])}\n\n"
            f"{html.escape(post['summary'])}\n\n"
            f"🔗 {html.escape(post['url'], quote=True)}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        if len(current) + len(item) > MAX_MESSAGE_LENGTH:
            if current_posts:
                messages.append((current, current_posts))

            current = header
            current_posts = []

        current += item
        current_posts.append(post)

    if current_posts:
        messages.append((current, current_posts))

    return messages


def send_message(text):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result}"
        )

    return True


# ---------------- MAIN ----------------

def main():
    state = load_state()

    logging.info("Starting Daily News...")
    logging.info("Previously stored URLs: %s", len(state["urls"]))

    posts = fetch_news(state)

    logging.info("New articles found: %s", len(posts))

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


# ---------------- CONFIG ----------------

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["CHANNEL_ID"]

TZ = timezone(timedelta(hours=3, minutes=30))
STATE_FILE = "seen.json"

MAX_POSTS = 10
MAX_MESSAGE_LENGTH = 3500
MAX_HISTORY = 10000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

FEEDS = [
    "https://www.ithome.com/rss/",
    "https://news.google.com/rss/search?q=%D9%87%D9%88%D8%B4+%D9%85%D8%B5%D8%B7%D9%86%D8%B9%DB%8C&hl=fa&gl=IR&ceid=IR:fa",
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://hnrss.org/frontpage?points=150",
    "https://www.bleepingcomputer.com/feed/",
]

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


# ---------------- TEXT PROCESSING ----------------

def normalize(text):
    text = html.unescape(text or "")
    text = text.replace("ي", "ی").replace("ك", "ک")
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
    text = re.sub(r"\s+", " ", text).strip()

    return text[:limit]


def make_hash(text):
    return hashlib.sha256(
        normalize(text).encode("utf-8")
    ).hexdigest()


def normalize_url(url):
    try:
        parts = urlsplit(url.strip())

        tracking = {
            "utm_source", "utm_medium", "utm_campaign",
            "utm_term", "utm_content", "fbclid", "gclid",
            "ref", "source"
        }

        query = [
            (k, v)
            for k, v in parse_qsl(parts.query)
            if k.lower() not in tracking
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


# ---------------- CATEGORIZATION ----------------

def categorize(text):
    t = normalize(text)

    scores = {}

    for category, keywords in CATEGORIES.items():
        scores[category] = sum(
            1 for keyword in keywords
            if normalize(keyword) in t
        )

    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return None

    return best


# ---------------- PERSISTENT STATE ----------------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except (FileNotFoundError, json.JSONDecodeError):
        logging.warning("State file missing or invalid.")

    return {"urls": [], "titles": []}


def save_state(state):
    state["urls"] = state["urls"][-MAX_HISTORY:]
    state["titles"] = state["titles"][-MAX_HISTORY:]

    temp = STATE_FILE + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    os.replace(temp, STATE_FILE)


def is_seen(state, url, title):
    return (
        make_hash(normalize_url(url)) in state["urls"]
        or make_hash(title) in state["titles"]
    )


def mark_seen(state, articles):
    for article in articles:
        state["urls"].append(
            make_hash(normalize_url(article["url"]))
        )

        state["titles"].append(
            make_hash(article["title"])
        )

    state["urls"] = list(dict.fromkeys(state["urls"]))
    state["titles"] = list(dict.fromkeys(state["titles"]))


# ---------------- RSS READER ----------------

def fetch_news(state):
    posts = []

    for feed_url in FEEDS:
        try:
            response = requests.get(
                feed_url,
                timeout=20,
                headers={"User-Agent": "DailyNewsBot/1.0"}
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if feed.bozo:
                logging.warning("RSS warning: %s", feed_url)

            for entry in feed.entries[:20]:
                title = clean(entry.get("title", ""), 200)
                link = entry.get("link", "")

                summary = clean(
                    entry.get("summary", ""),
                    600
                )

                if not title or not link:
                    continue

                category = categorize(title + " " + summary)

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
            logging.exception("Feed failed: %s", feed_url)

    # Remove duplicates inside the current batch
    unique = {}
    for post in posts:
        key = make_hash(post["title"])
        unique.setdefault(key, post)

    return list(unique.values())


# ---------------- TELEGRAM ----------------

def build_messages(posts):
    header = (
        "🧠 <b>آلبرت و انیشتاین | گزارش روز</b>\n"
        f"📅 {datetime.now(TZ).strftime('%Y-%m-%d')}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    messages = []
    current = header
    current_posts = []

    for post in posts:
        item = (
            f"📰 <b>{html.escape(post['title'])}</b>\n"
            f"🏷 {html.escape(post['category'])}\n\n"
            f"{html.escape(post['summary'])}\n\n"
            f"🔗 {html.escape(post['url'], quote=True)}\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        if len(current) + len(item) > MAX_MESSAGE_LENGTH:
            if current_posts:
                messages.append((current, current_posts))

            current = header
            current_posts = []

        current += item
        current_posts.append(post)

    if current_posts:
        messages.append((current, current_posts))

    return messages


def send_message(text):
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result}"
        )

    return True


# ---------------- MAIN ----------------

def main():
    state = load_state()

    logging.info("Starting Daily News...")
    logging.info("Previously stored URLs: %s", len(state["urls"]))

    posts = fetch_news(state)

    logging.info("New articles found: %s", len(posts))

    posts = posts[:MAX_POSTS]

    if not posts:
        logging.info("No new technology news.")
        return

    messages = build_messages(posts)

    for message, articles in messages:
        try:
            send_message(message)

            # Only mark articles after successful delivery.
            mark_seen(state, articles)
            save_state(state)

            logging.info(
                "Successfully sent %s articles.",
                len(articles)
            )

        except Exception:
            save_state(state)
            logging.exception("Telegram delivery failed.")
            raise

    logging.info("Daily News completed successfully.")


if __name__ == "__main__":
    main()

    posts = posts[:MAX_POSTS]

    if not posts:
        logging.info("No new technology news.")
        return

    messages = build_messages(posts)

    for message, articles in messages:
        try:
            send_message(message)

            # Only mark articles after successful delivery.
            mark_seen(state, articles)
            save_state(state)

            logging.info(
                "Successfully sent %s articles.",
                len(articles)
            )

        except Exception:
            save_state(state)
            logging.exception("Telegram delivery failed.")
            raise

    logging.info("Daily News completed successfully.")


if __name__ == "__main__":
    main()
