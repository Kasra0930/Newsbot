import os, re, json, hashlib, feedparser, requests
from datetime import datetime, timezone, timedelta

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["CHANNEL_ID"]

TZ = timezone(timedelta(hours=3, 30))
STATE_FILE = "seen.json"

FEEDS = [
    "https://www.ithome.com/rss/",
    "https://news.google.com/rss/search?q=%D9%87%D9%88%D8%B4+%D9%85%D8%B5%D8%B7%D9%86%D8%B9%DB%8C&hl=fa&gl=IR&ceid=IR:fa",
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://hnrss.org/frontpage?points=150",
    "https://www.bleepingcomputer.com/feed/",
]

KEYWORDS = {
    "ai", "artificial intelligence", "llm", "gpt", "gemini", "claude", "openai",
    "anthropic", "model", "chip", "nvidia", "gpu", "security", "hack", "breach",
    "python", "developer", "github", "software", "startup", "crypto", "bitcoin",
    "هوش مصنوعی", "امنیت", "چیپ", "برنامه‌نویسی", "استارتاپ", "کریپتو",
}


def is_tech(text):
    t = text.lower()
    return any(k in t for k in KEYWORDS)


def clean(text, limit=300):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def main():
    try:
        seen = set(json.load(open(STATE_FILE)))
    except Exception:
        seen = set()

    posts = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print("feed error:", url, e)
            continue

        for e in feed.entries[:15]:
            title = clean(e.get("title", ""), 200)
            link  = e.get("link", "")
            summ  = clean(e.get("summary", ""), 260)
            if not title or not link:
                continue

            h = hashlib.md5(link.encode()).hexdigest()
            if h in seen:
                continue
            if not is_tech(title + " " + summ):
                continue

            seen.add(h)
            posts.append(f"📰 <b>{title}</b>\n\n{summ}\n\n🔗 {link}")

    if posts:
        header = ("🧠 <b>آلبرت و انیشتاین — گزارش روز</b>\n"
                  + datetime.now(TZ).strftime("%Y-%m-%d") + "\n")
        body = "\n\n➖➖➖\n\n".join(posts[:10])
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": header + "\n" + body,
                  "parse_mode": "HTML"},
            timeout=30,
        )
        print("telegram:", r.status_code, r.text[:300])
    else:
        print("no new posts")

    json.dump(list(seen), open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()