"""
MYRMIDON — Telegram MTProto Driver
=====================================
Executes Telegram actions via Pyrogram MTProto protocol.

Commenting on a *channel post* is not a direct send to the channel (the userbot
is not a channel admin). Telegram routes comments through the channel's linked
*discussion group*: every channel post is auto-forwarded into that group, and a
"comment" is simply a reply to that forwarded message. This driver resolves the
discussion message via ``get_discussion_message`` and replies there, joining the
discussion group on demand. Direct groups/supergroups are posted to directly.
"""

import logging
import asyncio
import os
from typing import Optional, Tuple, Union, Callable, List, Dict

from app import dialogue_store
from app import account_health
from app.telemetry import emit as emit_event

# A FloodWait up to this many seconds is slept-through and retried; longer ones put
# the agent on a cooldown instead of blocking the worker.
FLOOD_MAX_WAIT_SEC = int(os.getenv("TG_FLOOD_MAX_WAIT_SEC", "45"))

# Pyrogram's sync wrapper calls asyncio.get_event_loop() at import time.
# When uvloop is installed (e.g. from FastAPI), this fails in threads without a loop.
# Fix: ensure a loop exists before importing pyrogram.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client
from pyrogram.errors import (
    FloodWait,
    UserNotParticipant,
    ChatWriteForbidden,
    ChannelPrivate,
    MsgIdInvalid,
)

logger = logging.getLogger("myrmidon.drivers.tg_client")


def parse_target(target_url: str) -> Tuple[Optional[Union[str, int]], Optional[int]]:
    """
    Parse a Telegram target into ``(chat_ref, post_id)``.

    Supported forms:
      https://t.me/<username>/<post_id>   → ("<username>", <post_id>)
      https://t.me/<username>             → ("<username>", None)
      https://t.me/c/<internal>/<post_id> → (-100<internal>, <post_id>)   (private)
      @username / username                → ("username", None)
    """
    raw = (target_url or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.lstrip("@").strip("/")
    if not raw:
        return None, None

    parts = raw.split("/")
    # Private supergroup/channel link form: c/<internal_id>/<post_id>
    if parts[0] == "c" and len(parts) >= 2:
        internal = parts[1]
        post_id = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None
        try:
            return int(f"-100{internal}"), post_id
        except ValueError:
            return None, post_id

    username = parts[0]
    post_id = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    return username, post_id


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

        # Coordinates of the last comment this driver posted (disc chat + msg id),
        # so the swarm can have gamma agents react to alpha's exact comment.
        self.last_post_ref: Optional[dict] = None

    async def _simulate_typing(self, text: str) -> None:
        """Human-like pause before sending; capped so missions don't stall."""
        try:
            from app.main import calculate_typing_delay
            delay = min(calculate_typing_delay(len(text)), 30.0)
        except Exception:
            delay = 2.0
        logger.debug("TelegramDriver [%s]: simulated typing for %.1fs", self.agent_id, delay)
        await asyncio.sleep(delay)

    async def _flood_retry(self, e: "FloodWait") -> bool:
        """Short FloodWait → sleep it off and signal retry; long → cooldown, give up."""
        wait = getattr(e, "value", None) or getattr(e, "x", 0) or 0
        if wait and wait <= FLOOD_MAX_WAIT_SEC:
            logger.warning("TelegramDriver [%s]: FloodWait %ss — waiting, will retry.", self.agent_id, wait)
            await asyncio.sleep(wait + 2)
            return True
        logger.error("TelegramDriver [%s]: FloodWait %ss — too long, backing off.", self.agent_id, wait)
        account_health.set_cooldown(self.agent_id, int(wait) or 300, "FloodWait")
        return False

    async def _resolve_text(self, fallback: str, text_provider, context_text: str, thread_context: str, chat) -> str:
        """
        Decide the comment text. If a text_provider is supplied (mission path), it
        is called with the post context, author and the *atmosphere* of the thread
        (recent comments / mood) to produce a real, context-aware comment (ORPHEUS).
        On any miss it degrades gracefully to ``fallback`` so a mission never stalls
        when the cognitive core is slow or down.
        """
        if not text_provider:
            return fallback
        author = chat.title or chat.username or str(chat.id)
        try:
            loop = asyncio.get_event_loop()
            generated = await loop.run_in_executor(None, text_provider, context_text, author, thread_context)
        except Exception as e:
            logger.error("TelegramDriver [%s]: text provider failed: %s", self.agent_id, e)
            generated = ""
        if generated and generated.strip():
            logger.info("TelegramDriver [%s]: using cognitively-generated comment (%d chars).", self.agent_id, len(generated))
            return generated.strip()
        logger.info("TelegramDriver [%s]: generation empty — falling back to deterministic text.", self.agent_id)
        return fallback

    async def _comment_on_channel_post(self, app: Client, chat, post_id: int, text: str):
        """
        Post a comment on a channel post by replying in the linked discussion group.
        Returns ``(sent_message, disc_chat_id)`` on success (so the caller can start
        a dialogue watch on the bot's own comment), or ``(None, None)`` on failure.
        """
        try:
            disc = await app.get_discussion_message(chat.id, post_id)
        except MsgIdInvalid:
            logger.error(
                "TelegramDriver [%s]: post %s on %s has no discussion thread "
                "(comments disabled / no linked group).",
                self.agent_id, post_id, chat.title or chat.id,
            )
            return None, None
        except Exception as e:
            logger.error(
                "TelegramDriver [%s]: failed to resolve discussion message for post %s: %s",
                self.agent_id, post_id, e,
            )
            return None, None

        disc_chat_id = disc.chat.id
        for attempt in (1, 2):
            try:
                sent = await app.send_message(disc_chat_id, text, reply_to_message_id=disc.id)
                logger.info(
                    "TelegramDriver [%s]: comment posted on '%s' post %s (discussion chat %s, msg %s).",
                    self.agent_id, chat.title or chat.id, post_id, disc_chat_id, sent.id,
                )
                return sent, disc_chat_id
            except (UserNotParticipant, ChatWriteForbidden) as e:
                if attempt == 1:
                    logger.info(
                        "TelegramDriver [%s]: not a participant of discussion %s — joining and retrying.",
                        self.agent_id, disc_chat_id,
                    )
                    try:
                        await app.join_chat(disc_chat_id)
                    except Exception as je:
                        logger.error(
                            "TelegramDriver [%s]: failed to join discussion group %s: %s",
                            self.agent_id, disc_chat_id, je,
                        )
                        return None, None
                    continue
                logger.error(
                    "TelegramDriver [%s]: cannot write to discussion %s: %s",
                    self.agent_id, disc_chat_id, e,
                )
                return None, None
            except FloodWait as e:
                if attempt == 1 and await self._flood_retry(e):
                    continue
                return None, None
            except Exception as e:
                logger.error("TelegramDriver [%s]: failed to post comment: %s", self.agent_id, e)
                account_health.handle_fault(self.agent_id, e)
                return None, None
        return None, None

    async def _read_post_context(self, app: Client, chat, post_id: Optional[int]) -> str:
        """Read the text/caption of the post being replied to (best-effort)."""
        if not post_id:
            return ""
        try:
            post = await app.get_messages(chat.id, post_id)
            if post:
                return (post.text or post.caption or "").strip()
        except Exception as e:
            logger.warning("TelegramDriver [%s]: could not read post %s for context: %s", self.agent_id, post_id, e)
        return ""

    async def _read_thread_context(self, app: Client, chat, post_id: Optional[int], limit: int) -> str:
        """
        Read the most recent comments under a channel post — the *atmosphere* of the
        discussion: who is saying what and in what mood. Returned as compact
        "author: text" lines so ORPHEUS can read the sentiment of the crowd and let
        the bot react to it like a human would (agree, push back, defuse, pile on).
        Best-effort: returns "" if the thread can't be read.
        """
        if not post_id:
            return ""
        lines: List[str] = []
        try:
            async for msg in app.get_discussion_replies(chat.id, post_id):
                txt = (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()
                if not txt:
                    continue
                u = getattr(msg, "from_user", None)
                who = "someone"
                if u is not None:
                    who = u.username or u.first_name or str(u.id)
                    if getattr(u, "is_self", False):
                        who = "me(my earlier comment)"
                lines.append(f"{who}: {txt}")
                if len(lines) >= limit:
                    break
        except Exception as e:
            logger.debug("TelegramDriver [%s]: could not read thread for post %s: %s", self.agent_id, post_id, e)
            return ""
        # Keep the most recent ``limit`` exchanges (the iterator yields oldest-first).
        return "\n".join(lines[-limit:])

    def _credentials_ok(self) -> bool:
        if not self.session_string:
            logger.error("TelegramDriver: No session string in credentials for agent %s", self.agent_id)
            return False
        if not self.api_id or not self.api_hash:
            logger.error(
                "TelegramDriver: Missing Telegram api_id/api_hash for agent %s "
                "(set TG_API_ID/TG_API_HASH or per-account credentials).", self.agent_id
            )
            return False
        return True

    def _build_client(self) -> Client:
        # no_updates=True: these clients are short-lived request/response sessions
        # (post a comment, poll a thread) — we never consume live updates, so we
        # skip the dispatcher entirely (less overhead, clean shutdown).
        return Client(
            f"agent_{self.agent_id}",
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_string=self.session_string,
            proxy=self.proxy_dict,
            in_memory=True,
            no_updates=True,
        )

    async def _execute_comment_async(self, target_url: str, text: str, text_provider=None,
                                     watch_meta: Optional[dict] = None) -> bool:
        if not self._credentials_ok():
            return False

        chat_ref, post_id = parse_target(target_url)
        if chat_ref is None:
            logger.error("TelegramDriver [%s]: could not parse target '%s'.", self.agent_id, target_url)
            return False

        app = self._build_client()

        try:
            async with app:
                try:
                    chat = await app.get_chat(chat_ref)
                except Exception as e:
                    logger.error("TelegramDriver [%s]: failed to resolve target %s: %s", self.agent_id, chat_ref, e)
                    return False

                logger.info(
                    "TelegramDriver [%s]: resolved %s (type=%s, post_id=%s).",
                    self.agent_id, chat.title or chat.username or chat.id,
                    getattr(chat.type, "name", chat.type), post_id,
                )

                is_channel = getattr(chat.type, "name", "") == "CHANNEL"

                if is_channel and not post_id:
                    logger.error(
                        "TelegramDriver [%s]: channel target without a post id — nothing to comment on.",
                        self.agent_id,
                    )
                    return False

                # Read the post being replied to AND the mood of the existing
                # discussion, so the comment is generated from real context and
                # reacts to the crowd. Then resolve the final text (ORPHEUS/fallback).
                emit_event(self.agent_id, "reading_post",
                           "читает пост: " + (chat.title or str(chat.id)),
                           status="active", target=target_url)
                context_text = await self._read_post_context(app, chat, post_id)
                if is_channel:
                    emit_event(self.agent_id, "reading_thread",
                               "оценивает настроение обсуждения", status="active",
                               target=target_url)
                thread_context = await self._read_thread_context(
                    app, chat, post_id, dialogue_store.DIALOGUE_THREAD_CONTEXT_LIMIT
                ) if is_channel else ""
                final_text = await self._resolve_text(text, text_provider, context_text, thread_context, chat)
                if not final_text or not final_text.strip():
                    logger.error("TelegramDriver [%s]: no text to post.", self.agent_id)
                    return False

                emit_event(self.agent_id, "posting", "публикует комментарий", status="active",
                           target=target_url)
                await self._simulate_typing(final_text)

                if is_channel:
                    sent, disc_chat_id = await self._comment_on_channel_post(app, chat, post_id, final_text)
                    if sent is None:
                        emit_event(self.agent_id, "error", "не смог опубликовать комментарий",
                                   status="error", target=target_url)
                        return False
                    emit_event(self.agent_id, "commented", "опубликовал комментарий: " + final_text[:60],
                               status="ok", target=target_url)
                    # Remember where we posted so the swarm can react to this comment.
                    self.last_post_ref = {"disc_chat_id": disc_chat_id, "message_id": sent.id}
                    # Start listening for human replies to this comment so the bot
                    # can carry the conversation autonomously (see dialogue_engine).
                    self._register_dialogue_watch(
                        chat, post_id, disc_chat_id, sent.id, context_text, watch_meta, depth=0,
                    )
                    return True

                # Group / supergroup / private chat → post directly (optionally as a
                # reply when a message id was supplied).
                for attempt in (1, 2):
                    try:
                        await app.send_message(chat.id, final_text, reply_to_message_id=post_id)
                        logger.info("TelegramDriver [%s]: sent message to %s.", self.agent_id, chat.title or chat.id)
                        emit_event(self.agent_id, "commented", "опубликовал сообщение: " + final_text[:60],
                                   status="ok", target=target_url)
                        return True
                    except FloodWait as e:
                        if attempt == 1 and await self._flood_retry(e):
                            continue
                        return False
                    except Exception as e:
                        logger.error("TelegramDriver [%s]: failed to send message: %s", self.agent_id, e)
                        account_health.handle_fault(self.agent_id, e)
                        return False
                return False
        except Exception as e:
            logger.error("TelegramDriver [%s]: failed to initialize Pyrogram (session error?): %s", self.agent_id, e)
            account_health.handle_fault(self.agent_id, e)
            return False

    def _register_dialogue_watch(self, chat, post_id, disc_chat_id, bot_msg_id,
                                 post_context, watch_meta, depth) -> None:
        """Best-effort: register a watch on a comment the bot just posted."""
        try:
            meta = watch_meta or {}
            # Prefer the @username (Pyrogram resolves large channel ids unreliably cold).
            channel_ref = chat.username or chat.id
            dialogue_store.register_watch(
                agent_id=self.agent_id,
                channel_ref=channel_ref,
                post_id=post_id,
                disc_chat_id=disc_chat_id,
                bot_msg_id=bot_msg_id,
                post_context=post_context,
                narrative_goal=meta.get("narrative_goal", ""),
                tactic=meta.get("tactic", "soft_support"),
                role=meta.get("role", "alpha"),
                depth=depth,
                opponent_id=meta.get("opponent_id"),
            )
        except Exception as e:
            logger.error("TelegramDriver [%s]: failed to register dialogue watch: %s", self.agent_id, e)

    def execute_comment(self, target_url: str, text: str, text_provider=None,
                        watch_meta: Optional[dict] = None) -> bool:
        # Serialize all use of this agent's Telegram session (mission posting,
        # dialogue polling, scouting) behind a per-agent advisory lock so two
        # operations never drive the same AUTH_KEY simultaneously.
        token = self._await_session_lock()
        try:
            return self._run(
                self._execute_comment_async(target_url, text, text_provider, watch_meta)
            )
        finally:
            dialogue_store.release_session_lock(self.agent_id, token)

    # ── Session-loop / lock helpers ────────────────────────────────────────

    @staticmethod
    def _run(coro):
        """
        Run a coroutine on a FRESH event loop and tear it down cleanly.

        A fresh loop per call (instead of reusing one via get_event_loop) avoids
        Pyrogram leaving background tasks (ping_worker / disconnect) bound to a
        reused loop — which caused the transient "got Future attached to a
        different loop" crash in the long-lived daemon threads (target/dialogue
        engines) that drive many short-lived sessions.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

    def _await_session_lock(self, attempts: int = 12, delay: float = 5.0) -> Optional[str]:
        """
        Try to claim the agent's session lock, briefly retrying if a concurrent
        operation holds it. Falls through (returns None) after the budget so a
        mission is never blocked indefinitely by a stuck poll.
        """
        import time as _time
        for _ in range(attempts):
            token = dialogue_store.acquire_session_lock(self.agent_id)
            if token:
                return token
            _time.sleep(delay)
        logger.warning("TelegramDriver [%s]: proceeding without session lock (held elsewhere).", self.agent_id)
        return None

    # ── Reactions (gamma "white noise": cheap promote of an ally's comment) ─

    def execute_reaction(self, target_url: str, react_msg_id: int, emoji: str) -> bool:
        """React (emoji) to an ally's comment under a channel post. Cheap, no LLM —
        this is what gamma agents do. ``react_msg_id`` is the comment's id in the
        linked discussion group."""
        if not self._credentials_ok():
            return False
        token = self._await_session_lock()
        try:
            return self._run(self._execute_reaction_async(target_url, react_msg_id, emoji))
        except Exception as e:
            logger.error("TelegramDriver [%s]: execute_reaction crashed: %s", self.agent_id, e)
            return False
        finally:
            dialogue_store.release_session_lock(self.agent_id, token)

    async def _execute_reaction_async(self, target_url: str, react_msg_id: int, emoji: str) -> bool:
        chat_ref, post_id = parse_target(target_url)
        if chat_ref is None or not post_id:
            return False
        app = self._build_client()
        async with app:
            try:
                chat = await app.get_chat(chat_ref)
                # Resolve/cache the linked discussion peer via the channel+post.
                disc = await app.get_discussion_message(chat.id, post_id)
                disc_chat_id = disc.chat.id
            except Exception as e:
                logger.warning("TelegramDriver [%s]: reaction target resolve failed: %s", self.agent_id, e)
                return False
            for attempt in (1, 2):
                try:
                    await app.send_reaction(disc_chat_id, react_msg_id, emoji)
                    logger.info("TelegramDriver [%s]: reacted %s to msg %s.", self.agent_id, emoji, react_msg_id)
                    emit_event(self.agent_id, "reacted", f"поставил реакцию {emoji} на коммент союзника",
                               status="ok", target=target_url)
                    return True
                except (UserNotParticipant, ChatWriteForbidden):
                    if attempt == 1:
                        try:
                            await app.join_chat(disc_chat_id)
                        except Exception:
                            return False
                        continue
                    return False
                except FloodWait as e:
                    if attempt == 1 and await self._flood_retry(e):
                        continue
                    return False
                except Exception as e:
                    logger.warning("TelegramDriver [%s]: send_reaction failed: %s", self.agent_id, e)
                    account_health.handle_fault(self.agent_id, e)
                    return False
        return False

    # ── Channel enumeration (the agent's subscribed channels = its targets) ─

    def list_channels(self) -> List[dict]:
        """
        List the channels/groups this account is subscribed to — the agent's whole
        universe of potential targets / news sources. Acquires the session lock so
        it never collides with the poller or a mission.
        """
        if not self._credentials_ok():
            return []
        token = self._await_session_lock()
        try:
            return self._run(self._list_channels_async())
        except Exception as e:
            logger.error("TelegramDriver [%s]: list_channels failed: %s", self.agent_id, e)
            return []
        finally:
            dialogue_store.release_session_lock(self.agent_id, token)

    async def _list_channels_async(self) -> List[dict]:
        app = self._build_client()
        out: List[dict] = []
        async with app:
            async for d in app.get_dialogs():
                chat = d.chat
                tname = getattr(chat.type, "name", "") or ""
                if tname not in ("CHANNEL", "SUPERGROUP", "GROUP"):
                    continue
                out.append({
                    "chat_id": str(chat.id),
                    "title": chat.title or getattr(chat, "first_name", "") or "",
                    "username": chat.username,
                    "type": tname.lower(),
                    "members": getattr(chat, "members_count", None),
                    "unread": getattr(d, "unread_messages_count", None),
                })
        return out

    # ── Target-channel post scanning (polled by target_engine) ─────────────

    def fetch_new_posts(self, channels: List[dict], since_map: Dict[str, int],
                        per_channel_limit: int = 5) -> List[dict]:
        """
        For each target channel, read its most recent posts and return the ones
        newer than the last-seen id. Read-only; one session under the lock.
        ``channels``: [{chat_id, username}]; ``since_map``: {chat_id: last_seen_id}.
        Returns [{chat_id, username, newest, first_seen, posts:[{post_id,text}]}].
        """
        if not self._credentials_ok():
            return []
        token = dialogue_store.acquire_session_lock(self.agent_id)
        if not token:
            return []
        try:
            return self._run(
                self._fetch_new_posts_async(channels, since_map, per_channel_limit))
        except Exception as e:
            logger.error("TelegramDriver [%s]: fetch_new_posts crashed: %s", self.agent_id, e)
            return []
        finally:
            dialogue_store.release_session_lock(self.agent_id, token)

    async def _fetch_new_posts_async(self, channels, since_map, limit) -> List[dict]:
        app = self._build_client()
        out: List[dict] = []
        async with app:
            for ch in channels:
                cid = str(ch.get("chat_id"))
                username = ch.get("username")
                ref = username or (int(cid) if cid.lstrip("-").isdigit() else cid)
                since = int(since_map.get(cid, 0))
                newest = since
                posts = []
                try:
                    async for m in app.get_chat_history(ref, limit=limit):
                        if m.id > newest:
                            newest = m.id
                        if since and m.id <= since:
                            continue
                        txt = (getattr(m, "text", None) or getattr(m, "caption", None) or "").strip()
                        if txt:
                            posts.append({"post_id": m.id, "text": txt})
                except Exception as e:
                    logger.debug("TelegramDriver [%s]: cannot read history of %s: %s", self.agent_id, ref, e)
                    continue
                out.append({
                    "chat_id": cid, "username": username, "newest": newest,
                    "first_seen": (since == 0), "posts": posts,
                })
        return out

    # ── Autonomous dialogue cycle (polled by dialogue_engine) ──────────────

    def run_dialogue_cycle(self, watches: List[dict], generate_reply: Callable[[dict], str],
                           max_depth: int) -> dict:
        """
        For one agent: open the session once, scan each watched comment for *new
        human replies*, generate an answer (``generate_reply`` → ORPHEUS) and post
        it, then return the bookkeeping the engine needs (handled ids, follow-up
        watches, last-seen updates). Acquires the per-agent session lock; if the
        session is busy this cycle it simply returns empty and retries next tick.
        """
        empty = {"handled": [], "new_watches": [], "updates": {}}
        if not self._credentials_ok():
            return empty
        token = dialogue_store.acquire_session_lock(self.agent_id)
        if not token:
            logger.info("TelegramDriver [%s]: session busy — skipping dialogue cycle.", self.agent_id)
            return empty
        try:
            return self._run(
                self._run_dialogue_cycle_async(watches, generate_reply, max_depth)
            )
        except Exception as e:
            logger.error("TelegramDriver [%s]: dialogue cycle crashed: %s", self.agent_id, e)
            return empty
        finally:
            dialogue_store.release_session_lock(self.agent_id, token)

    async def _run_dialogue_cycle_async(self, watches: List[dict], generate_reply, max_depth: int) -> dict:
        results: Dict[str, list] = {"handled": [], "new_watches": [], "updates": {}}
        app = self._build_client()
        loop = asyncio.get_event_loop()
        async with app:
            for w in watches:
                watch_id = w.get("watch_id")
                depth = int(w.get("depth", 0))
                if depth >= max_depth:
                    results["handled"].append(("__expire__", watch_id))
                    continue

                channel_ref = w.get("channel_ref")
                post_id = w.get("post_id")
                bot_msg_id = w.get("bot_msg_id")
                disc_chat_id = w.get("disc_chat_id")
                last_seen = int(w.get("last_seen_reply_id", 0))

                # Collect new, human, direct replies to *this* bot comment.
                new_replies = []
                max_id = last_seen
                try:
                    async for m in app.get_discussion_replies(channel_ref, post_id):
                        if m.id > max_id:
                            max_id = m.id
                        if m.id <= last_seen:
                            continue
                        if getattr(m, "reply_to_message_id", None) != bot_msg_id:
                            continue
                        u = getattr(m, "from_user", None)
                        # Skip our own messages and other bots. We DO answer humans
                        # Telegram won't identify: from_user=None happens for privacy-
                        # restricted users AND for people who reply *anonymously* (the
                        # message is then attributed to the group via sender_chat). They
                        # replied directly to us (reply_to == our comment), so a real
                        # person is talking to us — keep the conversation going; we just
                        # can't scope memory per-person.
                        if u is not None and (getattr(u, "is_self", False) or getattr(u, "is_bot", False)):
                            continue
                        txt = (getattr(m, "text", None) or getattr(m, "caption", None) or "").strip()
                        if not txt:
                            continue
                        if dialogue_store.is_handled(disc_chat_id, m.id):
                            continue
                        new_replies.append((m, u, txt))
                except Exception as e:
                    logger.warning("TelegramDriver [%s]: failed reading replies for watch %s: %s",
                                   self.agent_id, watch_id, e)
                    continue

                if max_id != last_seen:
                    results["updates"][watch_id] = max_id

                if not new_replies:
                    continue

                # Read the thread mood once (shared across replies on this post).
                chat = await app.get_chat(channel_ref)
                thread_context = await self._read_thread_context(
                    app, chat, post_id, dialogue_store.DIALOGUE_THREAD_CONTEXT_LIMIT
                )

                for (m, u, txt) in new_replies:
                    # Per-person memory when we can identify the human; otherwise fall
                    # back to anonymous/thread-scoped memory so recall degrades gracefully.
                    sc = getattr(m, "sender_chat", None)
                    if u is not None:
                        opponent_id = str(u.id)
                        author = u.username or u.first_name or opponent_id
                    elif sc is not None:
                        opponent_id = f"anon:{sc.id}"
                        author = "аноним"
                    else:
                        opponent_id = f"thread:{disc_chat_id}:{post_id}"
                        author = "собеседник"
                    payload = {
                        "mode": "reply",
                        "agent_id": self.agent_id,
                        "incoming_text": txt,
                        "author": author,
                        "opponent_id": opponent_id,
                        "post_text": w.get("post_context", ""),
                        "thread_context": thread_context,
                        "narrative_goal": w.get("narrative_goal", ""),
                        "tactic": w.get("tactic", "soft_support"),
                        "role": w.get("role", "alpha"),
                    }
                    logger.info("TelegramDriver [%s]: human %s replied to bot — generating answer.",
                                self.agent_id, author)
                    emit_event(self.agent_id, "reply_detected",
                               f"{author} ответил: " + txt[:50], status="active", target=author)
                    reply_text = await loop.run_in_executor(None, generate_reply, payload)
                    dialogue_store.mark_handled(disc_chat_id, m.id)
                    if not reply_text or not reply_text.strip():
                        logger.info("TelegramDriver [%s]: no answer generated for %s — skipping.",
                                    self.agent_id, author)
                        continue
                    await self._simulate_typing(reply_text)
                    try:
                        # Reply via the message object so Pyrogram uses its already-
                        # resolved chat (avoids the cold large-id resolution failure
                        # of send_message(disc_chat_id, ...)).
                        sent = await m.reply_text(reply_text.strip())
                    except FloodWait as e:
                        if await self._flood_retry(e):
                            try:
                                sent = await m.reply_text(reply_text.strip())
                            except Exception:
                                continue
                        else:
                            continue
                    except Exception as e:
                        logger.error("TelegramDriver [%s]: failed to answer %s: %s", self.agent_id, author, e)
                        account_health.handle_fault(self.agent_id, e)
                        continue
                    results["handled"].append((disc_chat_id, m.id))
                    emit_event(self.agent_id, "replied",
                               f"ответил {author}: " + reply_text.strip()[:55],
                               status="ok", target=author)
                    # Continue the conversation: watch our own answer for a counter-reply.
                    results["new_watches"].append({
                        "channel_ref": channel_ref,
                        "post_id": post_id,
                        "disc_chat_id": disc_chat_id,
                        "bot_msg_id": sent.id,
                        "post_context": w.get("post_context", ""),
                        "narrative_goal": w.get("narrative_goal", ""),
                        "tactic": w.get("tactic", "soft_support"),
                        "role": w.get("role", "alpha"),
                        "depth": int(w.get("depth", 0)) + 1,
                        "opponent_id": opponent_id,
                    })
                    logger.info("TelegramDriver [%s]: answered %s and extended the conversation (depth %d).",
                                self.agent_id, author, int(w.get("depth", 0)) + 1)
        return results
