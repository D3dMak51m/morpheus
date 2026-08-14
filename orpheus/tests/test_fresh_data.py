"""
Тесты Stage 47: когда рой идёт в интернет за тем, чего знать не может.

Счёт матча, цена, курс, сегодняшняя новость — этого нет ни в обученных данных модели,
ни в корпусе, пока какая-нибудь лента случайно об этом не напишет. Ответ «по памяти»
в таком случае — выдумка. Но и ходить в интернет на каждый пост нельзя: поиск и чтение
страниц стоят времени и очереди на единственной GPU.

    docker compose exec orpheus python -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as orpheus_main                             # noqa: E402


@pytest.fixture
def fake_llm(monkeypatch):
    calls = []

    def make(answers):
        queue = list(answers)

        def _gen(prompt, max_tokens=None, temperature=None, penalties=True):
            calls.append(prompt)
            return queue.pop(0) if queue else ""

        monkeypatch.setattr(orpheus_main, "generate_text", _gen)
        return calls

    return make


# ── Когда спрашивать ──────────────────────────────────────────────────────

def test_ordinary_post_does_not_reach_the_model(fake_llm):
    """Предфильтр по словам бесплатный; вопрос модели — нет. Обычный пост не должен
    стоить ни одного вызова, иначе поиск встанет на пути каждого комментария."""
    calls = fake_llm(["ДА"])
    assert orpheus_main._needs_fresh_data(
        {"post_text": "Просто красивая фотография заката над городом"}) is False
    assert calls == []


def test_a_question_about_a_price_asks_the_model(fake_llm):
    calls = fake_llm(["ДА"])
    assert orpheus_main._needs_fresh_data(
        {"incoming_text": "а сколько сейчас стоит проезд в автобусе?"}) is True
    assert len(calls) == 1


def test_the_model_can_still_say_no(fake_llm):
    """Маркер есть, но данные не нужны — «когда-нибудь потом» не повод лезть в сеть."""
    fake_llm(["НЕТ"])
    assert orpheus_main._needs_fresh_data(
        {"incoming_text": "когда уже наконец потеплеет, устал от жары"}) is False


def test_a_lite_companion_never_searches(fake_llm):
    calls = fake_llm(["ДА"])
    assert orpheus_main._needs_fresh_data(
        {"lite": True, "incoming_text": "какой был счёт вчера?"}) is False
    assert calls == []


# ── Из чего собирается запрос ─────────────────────────────────────────────

def test_the_query_carries_the_place():
    """«Сколько стоит проезд» без города находит цены другой страны — гео канала это
    единственная привязка, которая у нас есть."""
    q = orpheus_main._lookup_query({
        "incoming_text": "сколько стоит проезд в автобусе, говорят подорожало",
        "channel_profile": {"geo_label": "ташкент, узбекистан"},
    })
    assert q.startswith("ташкент")
    assert "проезд" in q


def test_a_query_without_substance_is_not_searched():
    assert orpheus_main._lookup_query({"incoming_text": "ага"}) == ""


# ── Поведение при недоступном поиске ──────────────────────────────────────

def test_a_failed_lookup_returns_nothing_rather_than_stalling(monkeypatch):
    """Если поиск недоступен — отвечаем тем, что знаем, и молчим о том, чего не знаем.
    Пустой список означает, что в промпт не попадёт блок свежих данных вообще."""
    class Boom:
        def get(self, *a, **k): raise RuntimeError("no redis")
        def setex(self, *a, **k): pass

    def fail(*a, **k):
        raise RuntimeError("searxng down")

    monkeypatch.setattr(orpheus_main.httpx, "Client", fail)
    assert orpheus_main._fetch_fresh(
        {"incoming_text": "сколько стоит проезд в ташкенте"}, Boom()) == []
