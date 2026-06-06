"""
HUGINN — Web Scraper
======================
Uses BeautifulSoup and requests to scrape web sources.
"""

import asyncio
import logging
import time
import uuid
import requests
from bs4 import BeautifulSoup

from app.router import classify_layers

logger = logging.getLogger("huginn.scrapers.web_scraper")

TARGET_URLS = [
    "https://kun.uz/en" # Example news source
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

async def run_web_scraper(redis_client, raw_events_queue, is_content_expired_func, publish_func):
    """
    Main async loop for Web scraping.
    """
    logger.info("Web scraper started.")
    while True:
        try:
            for url in TARGET_URLS:
                response = requests.get(url, headers=HEADERS, timeout=10)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, 'html.parser')
                    
                    # Very basic extraction - real implementations need site-specific selectors
                    articles = soup.find_all('a', class_='news-title')
                    
                    for article in articles[:5]:
                        link = article.get('href')
                        if link:
                            post_id = link.split('/')[-1]
                            if redis_client.get(f"cache:web:{post_id}"):
                                continue
                            
                            redis_client.setex(f"cache:web:{post_id}", 86400, "1")
                            
                            title = article.get_text(strip=True)
                            timestamp = int(time.time()) # Approximating timestamp
                            
                            layers = classify_layers(title)
                            
                            publish_func(
                                redis_client=redis_client,
                                event_id=str(uuid.uuid4()),
                                source_platform="web",
                                source_target=url,
                                post_id=post_id,
                                text_content=title,
                                media_type=None,
                                media_path=None,
                                layers=layers,
                                timestamp=timestamp
                            )
                else:
                    logger.warning(f"Failed to fetch {url}: {response.status_code}")
                    
        except Exception as e:
            logger.error("Error in Web Scraper loop: %s", e)
            
        await asyncio.sleep(300) # Poll every 5 minutes
