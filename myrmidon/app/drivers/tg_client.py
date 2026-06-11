"""
MYRMIDON — Telegram MTProto Driver
=====================================
Executes Telegram actions via Pyrogram MTProto protocol.
"""

import logging
import asyncio
import os
from typing import Optional
import time
import random

# Pyrogram's sync wrapper calls asyncio.get_event_loop() at import time.
# When uvloop is installed (e.g. from FastAPI), this fails in threads without a loop.
# Fix: ensure a loop exists before importing pyrogram.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client
from pyrogram.errors import PeerIdInvalid, UsernameInvalid, ChannelInvalid, FloodWait

logger = logging.getLogger("myrmidon.drivers.tg_client")

class TelegramDriver:
    def __init__(self, agent_id: str, credentials: dict):
        self.agent_id = agent_id
        self.credentials = credentials
        # Real Telegram API credentials: per-account override → shared env vars.
        # No fake fallbacks — Pyrogram is rejected by Telegram with bogus api_id/hash.
        self.api_id = int(credentials.get("api_id") or os.getenv("TG_API_ID", "0") or 0)
        self.api_hash = credentials.get("api_hash") or os.getenv("TG_API_HASH", "")

        # Pyrogram session string lives in auth_cookies. The Auth Factory stores
        # it as a JSONB dict {"session_string": ...}; tolerate raw strings too.
        raw_cookies = credentials.get("auth_cookies")
        if isinstance(raw_cookies, dict):
            self.session_string = raw_cookies.get("session_string")
        else:
            self.session_string = raw_cookies
        
        self.proxy_dict = None
        from app.proxy_manager import get_active_proxy
        active_proxy = get_active_proxy()
        if active_proxy:
            self.proxy_dict = active_proxy.to_pyrogram_proxy()

    async def _execute_comment_async(self, target_url: str, text: str):
        # target_url can be a channel username like @tashkent_news or a full link
        
        if not self.session_string:
             logger.error("TelegramDriver: No session string provided in credentials for agent %s", self.agent_id)
             return False

        if not self.api_id or not self.api_hash:
             logger.error(
                 "TelegramDriver: Missing Telegram api_id/api_hash for agent %s "
                 "(set TG_API_ID/TG_API_HASH or per-account credentials).", self.agent_id
             )
             return False

        app = Client(
            f"agent_{self.agent_id}",
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_string=self.session_string,
            proxy=self.proxy_dict,
            in_memory=True
        )

        try:
            async with app:
                 try:
                     # Resolve target
                     target = target_url.replace("https://t.me/", "").replace("@", "")
                     
                     logger.info("TelegramDriver [%s]: Resolved target %s. Fetching entity...", self.agent_id, target)
                     chat = await app.get_chat(target)
                     
                     logger.info("TelegramDriver [%s]: Sending comment to %s", self.agent_id, chat.title or chat.username)
                     
                     # Simulating typing delay before actual send
                     from app.main import calculate_typing_delay
                     typing_delay = calculate_typing_delay(len(text))
                     logger.debug("TelegramDriver [%s]: Simulated typing for %.1fs", self.agent_id, typing_delay)
                     await asyncio.sleep(typing_delay)

                     # Actually send message (If it's a channel, it might require a discussion group logic to comment, 
                     # for simplicity, assuming direct message to the resolved entity if it's a group, or finding discussion if channel)
                     
                     if chat.type.name == "CHANNEL":
                         # In a real scenario, we'd need the post ID and the linked discussion group to reply.
                         # For the test payload `post_id` is provided, we can use `reply_to_message_id` on the discussion group.
                         # Simplified MVP: just send to the chat if we can.
                         logger.warning("TelegramDriver [%s]: Direct commenting on channels requires discussion group lookup. Attempting send...", self.agent_id)

                     await app.send_message(chat.id, text)
                     logger.info("TelegramDriver [%s]: Successfully sent message to %s", self.agent_id, target)
                     return True

                 except FloodWait as e:
                     logger.error("TelegramDriver [%s]: Flood wait of %s seconds required.", self.agent_id, e.value)
                     return False
                 except Exception as e:
                     logger.error("TelegramDriver [%s]: Failed to execute comment: %s", self.agent_id, e)
                     return False
        except Exception as e:
            logger.error("TelegramDriver [%s]: Failed to initialize Pyrogram (Session Error?): %s", self.agent_id, e)
            return False

    def execute_comment(self, target_url: str, text: str):
         # Pyrogram needs an event loop
         try:
             loop = asyncio.get_event_loop()
         except RuntimeError:
             loop = asyncio.new_event_loop()
             asyncio.set_event_loop(loop)
             
         return loop.run_until_complete(self._execute_comment_async(target_url, text))
