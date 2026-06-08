import feedparser
feeds = [
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://russian.rt.com/rss",
    "https://centrasia.org/rss/redtram.xml",
    "https://khovar.tj/rus/feed/"
]
for f in feeds:
    d = feedparser.parse(f)
    print(f"Feed: {f}")
    print(f"Entries: {len(d.entries)}")
    if d.entries:
        print(d.entries[0].title)
    print("-" * 20)
