import sys
import os

# Connect to Daedalus DB and insert real targets
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append("/home/homer/PycharmProjects/morpheus/daedalus")
from app.models import ScrapingLandscape

DATABASE_URL = "postgresql://morpheus_user:morpheus_pass@localhost:5432/morpheus_db"
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
db = Session()

feeds = [
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://russian.rt.com/rss",
    "https://centrasia.org/rss/redtram.xml",
    "https://khovar.tj/rus/feed/"
]

for feed in feeds:
    existing = db.query(ScrapingLandscape).filter_by(target_identifier=feed).first()
    if not existing:
        new_target = ScrapingLandscape(
            platform="rss",
            type="feed",
            target_identifier=feed,
            is_active=True,
            associated_tags=["news", "global"]
        )
        db.add(new_target)

db.commit()
print("Real RSS targets added to Daedalus successfully.")
