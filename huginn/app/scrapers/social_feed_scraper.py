"""
HUGINN — Social Feed Scraper
==============================
Polls authenticated user timelines/feeds for viral content using true network requests.
Utilizes curl_cffi to bypass basic TLS fingerprinting.
"""

import asyncio
import logging
import time
import uuid
import json
import re
from typing import Dict, List, Any

from curl_cffi import requests
from bs4 import BeautifulSoup
from app.router import classify_layers

logger = logging.getLogger("huginn.scrapers.social_feed_scraper")

async def fetch_feed(session: requests.AsyncSession, platform: str, target_identifier: str) -> List[Dict[str, Any]]:
    """
    Executes real HTTP requests to retrieve feed data.
    In a real-world scenario, this targets actual GraphQL endpoints, Syndication APIs, or RSS hubs.
    """
    results = []
    try:
        if platform == "twitter" or platform == "x":
            # Attempt to use the public syndication API for Twitter profiles as a fallback
            url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{target_identifier}"
            response = await session.get(url, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                script_tag = soup.find("script", id="__NEXT_DATA__")
                if script_tag:
                    data = json.loads(script_tag.text)
                    instructions = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
                    for entry in instructions:
                        if entry.get("type") == "tweet":
                            tweet = entry.get("content", {}).get("tweet", {})
                            text = tweet.get("full_text") or tweet.get("text")
                            if text:
                                results.append({
                                    "id": tweet.get("id_str", str(uuid.uuid4())),
                                    "text": text,
                                    "timestamp": int(time.time()), # Approximation, parse created_at for real
                                    "media": None # Parse media entities if needed
                                })
            else:
                logger.warning("Syndication API returned %d for %s", response.status_code, target_identifier)
                
        elif platform == "instagram":
            # Real Instagram scraping requires heavy auth/cookies. 
            # This demonstrates the structural request, though it will likely 302 to login without session cookies.
            url = f"https://www.instagram.com/{target_identifier}/?__a=1&__d=dis"
            response = await session.get(url, timeout=15)
            if response.status_code == 200:
                try:
                    data = response.json()
                    user = data.get("graphql", {}).get("user", {})
                    edges = user.get("edge_owner_to_timeline_media", {}).get("edges", [])
                    for edge in edges[:5]:
                        node = edge.get("node", {})
                        caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
                        text = caption_edges[0]["node"]["text"] if caption_edges else ""
                        results.append({
                            "id": node.get("id", str(uuid.uuid4())),
                            "text": text,
                            "timestamp": node.get("taken_at_timestamp", int(time.time())),
                            "media": node.get("display_url")
                        })
                except json.JSONDecodeError:
                    logger.warning("Failed to decode JSON from Instagram for %s", target_identifier)
        elif platform == "rss" or platform == "web":
            # Production-grade RSS/Atom feed parser
            import feedparser
            import httpx
            url = target_identifier if target_identifier.startswith("http") else f"https://{target_identifier}"
            
            # Use httpx to bypass curl_cffi libcurl DNS bugs in Alpine/Slim docker images
            async with httpx.AsyncClient(verify=False) as httpx_client:
                response = await httpx_client.get(url, timeout=20.0)
            
            if response.status_code == 200:
                # Parse the raw bytes using feedparser
                feed = feedparser.parse(response.content)
                if feed.entries:
                    for entry in feed.entries[:15]:
                        title = entry.get("title", "")
                        desc = entry.get("summary", entry.get("description", ""))
                        
                        # Clean HTML from description
                        clean_desc = re.sub('<[^<]+?>', '', desc)
                        link = entry.get("link", "")
                        
                        # Resolve true timestamp
                        ts = int(time.time())
                        if entry.get("published_parsed"):
                            ts = int(time.mktime(entry.published_parsed))
                        elif entry.get("updated_parsed"):
                            ts = int(time.mktime(entry.updated_parsed))
                            
                        results.append({
                            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, link if link else (title + str(ts)))),
                            "text": f"{title}\n\n{clean_desc}\n\n{link}".strip(),
                            "timestamp": ts,
                            "media": None
                        })
                else:
                    logger.warning("Feedparser found 0 entries for %s. Might be raw HTML.", url)
            else:
                logger.warning("Feed returned %d for %s", response.status_code, url)
    except Exception as e:
        logger.error("Network error fetching feed for %s on %s: %s", target_identifier, platform, e)
        
    return results


async def run_social_feed_scraper(redis_client, raw_events_queue, is_content_expired_func, publish_func, active_targets: Dict[str, List[Any]]):
    """
    Main async loop for scraping social feeds using real network sessions.
    """
    logger.info("Real Social feed scraper started. Target types: feed")
    
    try:
        # Wait for initial sync_landscape_loop to populate targets
        await asyncio.sleep(5)
        logger.info("Finished initial sleep. ACTIVE_TARGETS keys: %s", list(active_targets.keys()))
        
        # Use curl_cffi to mimic a real Chrome browser.
        async with requests.AsyncSession(impersonate="chrome") as session:
            logger.info("AsyncSession started.")
            while True:
                try:
                    feed_platforms = ["twitter", "instagram", "web", "rss"]
                    
                    for platform in feed_platforms:
                        targets = active_targets.get(platform, [])
                        logger.info("Platform %s has %d targets", platform, len(targets))
                        for target_item in targets:
                            
                            target_identifier = target_item.get("target_identifier") if isinstance(target_item, dict) else target_item
                            target_type = target_item.get("type", "channel") if isinstance(target_item, dict) else "channel"
                            
                            if not target_identifier or target_type != "feed":
                                continue
                                
                            logger.info("Fetching feed for %s", target_identifier)
                            # Fetch real feed
                            posts = await fetch_feed(session, platform, target_identifier)
                            
                            for post in posts:
                                post_id = post["id"]
                                cache_key = f"cache:feed:{platform}:{target_identifier}:{post_id}"
                                
                                if redis_client.get(cache_key):
                                    continue
                                    
                                redis_client.setex(cache_key, 86400, "1")
                                
                                timestamp = post["timestamp"]
                                if not is_content_expired_func(platform, timestamp):
                                    layers = classify_layers(post["text"])
                                    publish_func(
                                        redis_client=redis_client,
                                        event_id=str(uuid.uuid4()),
                                        source_platform=platform,
                                        source_target=target_identifier,
                                        post_id=post_id,
                                        text_content=post["text"],
                                        media_type="image" if post.get("media") else None,
                                        media_path=post.get("media"),
                                        layers=layers,
                                        timestamp=timestamp
                                    )
                                    logger.info("Published real feed event for %s on %s", target_identifier, platform)
                
                except Exception as e:
                    logger.error("Error in Social Feed Scraper loop: %s", e)
                    
                await asyncio.sleep(120) # Poll every 2 minutes

    except Exception as outer_e:
        logger.error("Fatal error in Social Feed Scraper: %s", outer_e)
