"""
Тесты Stage 38: классификация здоровья цели.

Ключевое различие, ради которого это писалось: «у ПОСТА выключены комментарии» —
свойство поста, а не канала; блокировать из-за него весь канал нельзя.

    docker compose exec myrmidon python -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import target_health                                   # noqa: E402


# ── Классификация ─────────────────────────────────────────────────────────

def test_post_with_comments_off_never_blocks_the_channel():
    health, reason, scope = target_health.classify(
        "post 123847 on Матч ТВ has no discussion thread (comments disabled / no linked group)")
    assert scope == "post"
    assert health == "ok"          # канал остаётся в работе
    assert "поста" in reason


def test_guest_forbidden_is_transient_because_the_driver_joins():
    """403 CHAT_GUEST_SEND_FORBIDDEN = «нужно вступить», а не «канал мёртв»."""
    health, reason, scope = target_health.classify(
        "Telegram says: [403 Forbidden] - [403 CHAT_GUEST_SEND_FORBIDDEN]")
    assert health == "degraded" and scope == "target"


def test_write_forbidden_blocks_the_target():
    health, _, scope = target_health.classify("CHAT_WRITE_FORBIDDEN")
    assert health == "blocked" and scope == "target"


def test_unresolvable_channel_blocks_the_target():
    health, reason, _ = target_health.classify("Peer id invalid: -1004410991018")
    assert health == "blocked" and "резолв" in reason


def test_unknown_error_is_degraded_not_blocked():
    """Цель не списывается из-за ошибки, которую мы ещё не понимаем."""
    health, reason, scope = target_health.classify("какая-то новая ошибка Telegram")
    assert health == "degraded" and scope == "target"
    assert "какая-то новая ошибка" in reason


def test_floodwait_is_transient():
    health, _, _ = target_health.classify("FLOOD_WAIT 300")
    assert health == "degraded"


# ── Пропуск заблокированных целей ─────────────────────────────────────────

def test_blocked_target_is_skipped_while_the_verdict_is_fresh():
    import time
    assert target_health.should_skip("blocked", time.time())


def test_blocked_target_is_retried_after_the_window():
    import time
    stale = time.time() - target_health.BLOCKED_RECHECK_SEC - 60
    assert not target_health.should_skip("blocked", stale)


@pytest.mark.parametrize("health", ["ok", "degraded", "unknown"])
def test_only_blocked_targets_are_skipped(health):
    assert not target_health.should_skip(health, None)


# ── Извлечение канала из ссылки ───────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://t.me/Match_TV/123847", "@Match_TV"),
    ("https://t.me/tashkent_news333/31", "@tashkent_news333"),
    ("t.me/somechannel/1", "@somechannel"),
    ("https://t.me/c/1234567/89", "-1001234567"),
])
def test_channel_ref_from_post_url(url, expected):
    assert target_health.url_channel_ref(url) == expected


# ── Отчёт наверх ──────────────────────────────────────────────────────────

def test_post_level_failure_is_not_reported_as_target_health(monkeypatch):
    sent = []
    monkeypatch.setattr(target_health, "report",
                        lambda *a, **kw: sent.append(a) or True)
    health, reason, scope = target_health.report_failure(
        "@Match_TV", "no discussion thread (comments disabled)", 14)
    assert scope == "post" and sent == []          # канал не трогаем


def test_target_level_failure_is_reported(monkeypatch):
    sent = []
    monkeypatch.setattr(target_health, "report",
                        lambda *a, **kw: sent.append(a) or True)
    target_health.report_failure("@Dead", "USERNAME_NOT_OCCUPIED", 14)
    assert sent and sent[0][0] == "@Dead" and sent[0][1] == "blocked"
