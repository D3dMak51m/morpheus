"""
Tests for the ORPHEUS SIMULATION handler (isolated polygon generation).

The critical property proven here: a simulation request touches NOTHING outside
its own reply key — no MUNINN memory, no ``morpheus:recent_outputs`` history, no
``metrics:*`` counters, no execution queue. The fake Redis records every call, so
any future stray write breaks the test.

Run inside the orpheus container:
    docker compose exec orpheus python -m pytest tests -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.simulation import build_sim_prompt, handle_simulation_generation  # noqa: E402


class FakeRedis:
    """Records every operation so the test can assert what was (not) touched."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.pushed: dict[str, list] = {}

    def lpush(self, key, value):
        self.calls.append(("lpush", key))
        self.pushed.setdefault(key, []).append(value)

    def expire(self, key, ttl):
        self.calls.append(("expire", key))

    def incr(self, key):                       # pragma: no cover - must never happen
        self.calls.append(("incr", key))

    def lrange(self, *a, **kw):                # pragma: no cover - must never happen
        self.calls.append(("lrange", a))
        return []


class FakeGuardrails:
    def __init__(self, valid=True, echo=False):
        self.valid, self.echo = valid, echo

    def validate_output(self, text):
        return (True, "") if self.valid else (False, "слишком длинно")

    def is_echo(self, text, refs):
        return self.echo

    def clean_output(self, text):
        return text.strip()


BASE_REQ = {
    "reply_key": "reply:simgen:test",
    "request_id": "test-1",
    "mode": "comment",
    "persona": {
        "agent_key": "sim_alpha", "codename": "Алый", "full_name": "Рустам",
        "caste": "alpha", "bio": "инженер-транспортник", "core_mission": "обсуждать город",
        "interests": ["транспорт", "город"],
        "style": {"tone_level": 8, "aggression": 6, "emoji_frequency": 2, "quirks": ["без точек"]},
        "system_prompt": "",
    },
    "mission": {"title": "М", "goal": "продвигать электротранспорт",
                "stance": "электробусы лучше дизельных", "tactic": "aggressive_displacement"},
    "channel": {"username": "@city", "title": "Город", "geo_label": "Ташкент",
                "tags": ["транспорт"]},
    "post": {"text": "Электробусы опять сломались", "media_context": "фото: автобус на обочине"},
    "thread": [{"author": "Алишер", "text": "и так каждый день"}],
    "knowledge": [{"title": "Парк", "content": "Закуплено 40 электробусов.", "tags": ["транспорт"]}],
    "rules": ["Не упоминай политику."],
}


# ── Prompt assembly ────────────────────────────────────────────────────────

def test_prompt_contains_every_context_block():
    prompt = build_sim_prompt(BASE_REQ)
    assert "Рустам" in prompt and "инженер-транспортник" in prompt
    assert "Не упоминай политику." in prompt
    assert "Закуплено 40 электробусов." in prompt
    assert "Город" in prompt and "Ташкент" in prompt
    assert "Электробусы опять сломались" in prompt
    assert "фото: автобус на обочине" in prompt
    assert "и так каждый день" in prompt
    assert "продвигать электротранспорт" in prompt
    assert "электробусы лучше дизельных" in prompt
    # the mission's tactic drives the directive
    assert "НЕ СОГЛАСИСЬ" in prompt


def test_persona_system_prompt_is_injected():
    req = {**BASE_REQ, "persona": {**BASE_REQ["persona"], "system_prompt": "Ты сварливый сосед."}}
    assert "Ты сварливый сосед." in build_sim_prompt(req)


def test_reply_mode_uses_the_incoming_message():
    req = {**BASE_REQ, "mode": "reply",
           "incoming": {"author": "Нигора", "text": "а ты откуда знаешь?"}}
    prompt = build_sim_prompt(req)
    assert "Нигора" in prompt and "а ты откуда знаешь?" in prompt
    assert "НЕ повторяй и не пересказывай его слова" in prompt


def test_prompt_override_replaces_everything_and_fills_placeholders():
    req = {**BASE_REQ,
           "prompt_override": "СИСТЕМА: {persona_name}. ПОСТ: {post}. ФАКТЫ: {knowledge}. ЦЕЛЬ: {goal}"}
    prompt = build_sim_prompt(req)
    assert prompt.startswith("СИСТЕМА: Рустам.")
    assert "Электробусы опять сломались" in prompt
    assert "Закуплено 40 электробусов." in prompt
    assert "продвигать электротранспорт" in prompt
    assert "Как звучать по-человечески" not in prompt      # default scaffolding is gone


def test_avoid_list_becomes_an_anti_repeat_block():
    prompt = build_sim_prompt({**BASE_REQ, "avoid": ["я уже это писал"]})
    assert "НЕ повторяйся" in prompt and "я уже это писал" in prompt


def test_post_mode_asks_for_channel_material_not_a_comment():
    prompt = build_sim_prompt({**BASE_REQ, "mode": "post", "kind": "post",
                               "post": {"text": "запуск электробусов"}})
    assert "Что пишем" in prompt and "запуск электробусов" in prompt
    assert "Напиши ОДИН пост для этого канала" in prompt
    assert "Как звучать по-человечески" not in prompt      # comment scaffolding is skipped


def test_article_mode_asks_for_a_longer_piece():
    prompt = build_sim_prompt({**BASE_REQ, "mode": "post", "kind": "article",
                               "post": {"text": "тарифы"}})
    assert "заголовок и 3–5 абзацев" in prompt


def test_post_mode_skips_the_echo_guard():
    """The topic legitimately reappears in the material — echo must not veto it."""
    redis = FakeRedis()
    handle_simulation_generation(
        {**BASE_REQ, "mode": "post", "post": {"text": "электробусы"}},
        redis, FakeGuardrails(echo=True), lambda p, **kw: "Электробусы вышли на маршрут.")
    payload = json.loads(redis.pushed["reply:simgen:test"][0])
    assert payload["status"] == "ok" and payload["guardrail"] == "passed"


def test_empty_request_still_builds_a_usable_prompt():
    prompt = build_sim_prompt({"persona": {}, "post": {}})
    assert "Задача" in prompt and len(prompt) > 100


# ── Handler ────────────────────────────────────────────────────────────────

def test_handler_returns_text_and_prompt():
    redis = FakeRedis()
    handle_simulation_generation(
        BASE_REQ, redis, FakeGuardrails(), lambda p, **kw: "Опять сломались, как всегда")
    payload = json.loads(redis.pushed["reply:simgen:test"][0])
    assert payload["status"] == "ok"
    assert payload["text"] == "Опять сломались, как всегда"
    assert payload["guardrail"] == "passed"
    assert "Электробусы опять сломались" in payload["prompt"]
    assert payload["tactic"] == "aggressive_displacement"


def test_handler_never_writes_memory_metrics_or_queues():
    redis = FakeRedis()
    handle_simulation_generation(BASE_REQ, redis, FakeGuardrails(), lambda p, **kw: "текст")
    touched = {key for op, key in redis.calls}
    assert touched == {"reply:simgen:test"}, f"simulation touched extra keys: {touched}"
    assert not any(op in ("incr", "lrange") for op, _ in redis.calls)


def test_handler_returns_the_rejected_draft_flagged_for_inspection():
    """A polygon must SHOW what production would have thrown away."""
    redis = FakeRedis()
    handle_simulation_generation(
        BASE_REQ, redis, FakeGuardrails(valid=False), lambda p, **kw: "плохой черновик")
    payload = json.loads(redis.pushed["reply:simgen:test"][0])
    assert payload["status"] == "ok"
    assert payload["text"] == "плохой черновик"
    assert payload["guardrail"] == "failed" and "слишком длинно" in payload["reason"]


def test_handler_reports_a_dead_llm():
    redis = FakeRedis()
    handle_simulation_generation(BASE_REQ, redis, FakeGuardrails(), lambda p, **kw: "")
    payload = json.loads(redis.pushed["reply:simgen:test"][0])
    assert payload["status"] == "error" and payload["reason"] == "llm_empty_response"
    assert payload["text"] == ""


def test_handler_passes_generation_knobs_through():
    seen = {}

    def fake_generate(prompt, max_tokens=None, temperature=None):
        seen.update({"max_tokens": max_tokens, "temperature": temperature})
        return "ок"

    handle_simulation_generation(
        {**BASE_REQ, "max_tokens": 60, "temperature": 0.3}, FakeRedis(),
        FakeGuardrails(), fake_generate)
    assert seen == {"max_tokens": 60, "temperature": 0.3}


def test_handler_survives_an_exploding_llm():
    redis = FakeRedis()

    def boom(prompt, **kw):
        raise RuntimeError("ollama недоступен")

    handle_simulation_generation(BASE_REQ, redis, FakeGuardrails(), boom)
    payload = json.loads(redis.pushed["reply:simgen:test"][0])
    assert payload["status"] == "error" and "ollama недоступен" in payload["reason"]
