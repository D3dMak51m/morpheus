"""
HUGINN — Web Scraper
======================
Uses curl_cffi and BeautifulSoup to scrape web sources with
hardened OpSec browser-level TLS/JA4 fingerprint emulation.
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, List, Any

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

from app.router import classify_layers

logger = logging.getLogger("huginn.scrapers.web_scraper")

async def run_web_scraper(redis_client, raw_events_queue, is_content_expired_func, publish_func, active_targets: Dict[str, List[Any]]):
    """
    Main async loop for Web scraping using dynamic targets and curl_cffi.
    """
    logger.info("Web scraper started.")
    
    # We use impersonate="chrome" for TLS fingerprint masking
    async with AsyncSession(impersonate="chrome") as session:
        while True:
            try:
                current_targets = active_targets.get("web", [])
                for url_item in current_targets:
                    # Depending on how the target is passed from daedalus, handle dict or string
                    if isinstance(url_item, dict):
                        url = url_item.get("target_identifier")
                    else:
                        url = url_item

                    if not url:
                        continue
                        
                    logger.debug("Web scraper fetching: %s", url)
                    try:
                        response = await session.get(url, timeout=15)
                    except Exception as e:
                        logger.warning("Failed to fetch %s: %s", url, e)
                        continue

                    if response.status_code == 200:
                        soup = BeautifulSoup(response.content, 'html.parser')
                        
                        # Implement robust fallback extractors for MetaData, OpenGraph tags, and structural article tags
                        articles = []
                        
                        # 1. Look for standard article tags
                        for article_tag in soup.find_all('article'):
                            link_tag = article_tag.find('a')
                            if link_tag and link_tag.get('href'):
                                title = article_tag.get_text(strip=True)[:200]
                                articles.append({'link': link_tag.get('href'), 'title': title})
                        
                        # 2. Fallback to common news/blog links if no <article> found
                        if not articles:
                            for a_tag in soup.find_all('a'):
                                href = a_tag.get('href', '')
                                class_attr = ' '.join(a_tag.get('class', []))
                                if 'news' in class_attr or 'article' in class_attr or 'title' in class_attr:
                                    title = a_tag.get_text(strip=True)[:200]
                                    if title and len(title) > 20: # ignore very short links
                                        articles.append({'link': href, 'title': title})

                        # Deduplicate by link
                        unique_articles = {}
                        for a in articles:
                            # Normalize link
                            lnk = a['link']
                            if lnk.startswith('/'):
                                # crude absolute url resolution
                                base_url = "/".join(url.split("/")[:3])
                                lnk = base_url + lnk
                            if lnk not in unique_articles:
                                unique_articles[lnk] = a['title']

                        # Process up to 5 articles to prevent flood
                        count = 0
                        for link, title in list(unique_articles.items())[:5]:
                            post_id = str(uuid.uuid5(uuid.NAMESPACE_URL, link))
                            
                            if redis_client.get(f"cache:web:{post_id}"):
                                continue
                                
                            redis_client.setex(f"cache:web:{post_id}", 86400, "1")
                            
                            timestamp = int(time.time())
                            
                            if is_content_expired_func("web", timestamp):
                                continue

                            layers = classify_layers(title)
                            
                            publish_func(
                                redis_client=redis_client,
                                event_id=str(uuid.uuid4()),
                                source_platform="web",
                                source_target=url,
                                post_id=post_id,
                                text_content=title + "\n" + link,
                                media_type=None,
                                media_path=None,
                                layers=layers,
                                timestamp=timestamp
                            )
                            count += 1
                            
                        if count > 0:
                            logger.info("Published %d web events from %s", count, url)

                    else:
                        logger.warning(f"Failed to fetch {url}: HTTP {response.status_code}")
                        
            except Exception as e:
                logger.error("Error in Web Scraper loop: %s", e)
                
            await asyncio.sleep(300) # Poll every 5 minutes
