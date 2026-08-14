"""
Тесты Stage 38: гигиена входа, разбор вердикта, сборка промпта гейта, отбор RAG.

Все проверки офлайновые — LLM и DAEDALUS замоканы. Живые замеры делают
`bench_relevance.py` и `bench_rag.py` (они ходят в реальные сервисы).

    docker compose exec orpheus python -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import rag, textutil                                    # noqa: E402
from app.main import (                                           # noqa: E402
    _build_relevance_prompt, _channel_alignment, _parse_verdict,
)


# ── Гигиена входа ─────────────────────────────────────────────────────────

def test_promo_tail_and_links_are_stripped():
    raw = ("Электробусы вышли на маршруты города.\n\n"
           "Подписывайтесь на наш канал! https://t.me/somechannel Наш канал в MAX")
    out = textutil.clean_post_text(raw)
    assert "Электробусы вышли на маршруты" in out
    assert "t.me" not in out and "Подписывайтесь" not in out and "MAX" not in out


def test_schedule_ocr_is_recognised_as_nothing_to_discuss():
    """Именно такие OCR-дампы афиш заставляли модель отвечать «нет» на всё."""
    dump = ("[Фото1]: на изображении текст: «Иван Олейников СОВЕТОВ 25 ИЮЛЯ 13:55 "
            "КОММЕНТАТОРЫ: АЛЕКСАНДР НЕЦЕНКО, АЛЕКСАНДР АКСЁНОВ Максим Болдырев Игорь "
            "Дивеев 25 ИЮЛЯ 16:10 КОММЕНТАТОРЫ: РОМАН ТРУШЕЧКИН, ПЁТР КОПТЕВ»")
    assert textutil.is_schedule_dump(dump)
    assert textutil.judging_text(dump, "") == ""


def test_normal_post_survives_cleaning():
    text = "Родри – лучший игрок ЧМ. А как же Месси?"
    assert textutil.judging_text(text, "") == text


def test_media_description_is_added_when_post_is_media_only():
    out = textutil.judging_text("", "[Фото]: черлидерши на футбольном поле")
    assert "черлидерши" in out


def test_long_post_is_truncated_on_a_sentence_boundary():
    text = ("Первое предложение про транспорт. " * 40)
    out = textutil.clean_post_text(text, max_len=200)
    assert len(out) <= 201 and out.endswith((".", "…"))


# ── Словарь миссии ────────────────────────────────────────────────────────

def test_generic_words_never_become_mission_keywords():
    """«должен»/«выиграть» раньше попадали в keyword-override и давали ложные срабатывания."""
    kws = textutil.keywords("Сборная Аргентины должен был выиграть финал",
                            "Аргентина проиграл из-за тренера")
    assert "должен" not in kws and "выиграть" not in kws and "проиграл" not in kws
    assert "аргентины" in kws or "аргентина" in kws
    assert "тренера" in kws


def test_keyword_hit_needs_a_real_stem_not_a_prefix_collision():
    assert textutil.keyword_hit("Месси снова забил", ["месси"])
    # «долже» (5 букв) раньше матчился в неродственных словах — теперь стем 6 символов
    assert not textutil.keyword_hit("Компания должна отчитаться", ["должность"])


# ── Разбор вердикта ───────────────────────────────────────────────────────

@pytest.mark.parametrize("answer,expected", [
    ("ДА", "yes"), ("да.", "yes"), ("Да, безусловно", "yes"),
    ("СЛАБО", "weak"), ("слабо, но связь есть", "weak"),
    ("НЕТ", "no"), ("нет.", "no"), ("", "no"),
    ("yes", "yes"), ("no", "no"),
])
def test_verdict_parsing(answer, expected):
    assert _parse_verdict(answer) == expected


def test_garbled_answer_defaults_to_no():
    assert _parse_verdict("дятьнет") == "no"


# ── Промпт гейта ──────────────────────────────────────────────────────────

def test_prompt_asks_about_joining_not_about_topic_match():
    p = _build_relevance_prompt("Месси лучший игрок", "аргентина сильна",
                                "Родри – лучший игрок ЧМ", "Канал про футбол", "", ["месси"], False)
    assert "естественно и по делу вступить в обсуждение" in p
    assert "ДА" in p and "СЛАБО" in p and "НЕТ" in p


def test_prompt_includes_the_live_discussion():
    p = _build_relevance_prompt("тема", "", "пост", "", "Алишер: да сколько можно", [], False)
    assert "Алишер: да сколько можно" in p
    assert "Что уже пишут в комментариях" in p


def test_aligned_channel_is_context_not_a_licence_to_accept_everything():
    p = _build_relevance_prompt("тема", "", "пост", "", "", ["футбол"], True)
    assert "Аудитория канала близка нашей теме" in p
    assert "подойдёт любой пост" not in p


def test_channel_alignment_needs_two_matching_words():
    """Одного совпадения мало: у общегородского канала «пробки» — лишь одна из семи тем."""
    football = {"topics": ["футболл", "чемпионат мира"], "recent_themes": [{"theme": "месси"}],
                "summary": "канал про спорт"}
    assert _channel_alignment(football, ["месси", "футбола"])
    assert not _channel_alignment(football, ["энергоблок", "тарифы"])

    city = {"topics": ["украина и рф", "энергетика", "пробки", "свет", "политика"],
            "recent_themes": [{"theme": "отсутствие света"}], "summary": "городские новости"}
    # миссия про транспорт: совпадает только «пробки» — канал НЕ считается тематическим
    assert not _channel_alignment(city, ["транспорта", "общественный", "пробки"])


# ── Отбор фактов RAG ──────────────────────────────────────────────────────

def _fake_search(monkeypatch, matches):
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"matches": matches}

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, *a, **kw): return _Resp()

    monkeypatch.setattr(rag.httpx, "Client", _Client)
    monkeypatch.setattr(rag, "_embed", lambda text: [0.1] * 768)


def test_rag_admits_a_fact_that_shares_vocabulary(monkeypatch):
    _fake_search(monkeypatch, [
        {"content": "Президент Аргентины о критике сборной после чемпионата.",
         "similarity": 0.81, "landscape_layers": ["global"], "categories": ["sports"]},
        {"content": "Уровень безработицы в России упал до минимальных значений.",
         "similarity": 0.84, "landscape_layers": ["state"], "categories": ["economy"]},
    ])
    out = rag.fetch_fresh_context("Аргентина проиграла финал", ["global", "state"],
                                  mission_goal="Сборная Аргентины сильнейшая")
    assert "Президент Аргентины" in out
    # более «похожий» по эмбеддингу, но не по словам — не проходит
    assert "безработицы" not in out


def test_rag_returns_nothing_rather_than_noise(monkeypatch):
    """Пустой контекст честнее, чем уверенный мусор."""
    _fake_search(monkeypatch, [
        {"content": "Минобороны сообщило об ударе по позициям в Запорожской области.",
         "similarity": 0.79, "landscape_layers": ["state"], "categories": ["security"]},
        {"content": "Врач рассказал об аллергии на витамины.",
         "similarity": 0.78, "landscape_layers": ["personal"], "categories": ["health"]},
    ])
    out = rag.fetch_fresh_context("Опять пробка на кольце", ["global", "state"],
                                  mission_goal="Развитие городского транспорта")
    assert out == rag._NO_CONTEXT


def test_rag_keeps_a_very_strong_match_without_shared_words(monkeypatch):
    _fake_search(monkeypatch, [
        {"content": "Городской пассажирский электротранспорт получил новое финансирование.",
         "similarity": 0.93, "landscape_layers": ["city"], "categories": ["infrastructure"]},
    ])
    out = rag.fetch_fresh_context("Пробки в городе", ["city"], mission_goal="")
    assert "электротранспорт" in out


def test_forced_context_wins(monkeypatch):
    _fake_search(monkeypatch, [])
    out = rag.fetch_fresh_context("что угодно", ["global"], forced_context="Факт от оператора")
    assert "FORCED/OPERATOR" in out and "Факт от оператора" in out


def test_rag_strips_markup_from_injected_facts(monkeypatch):
    _fake_search(monkeypatch, [
        {"content": 'Электробусы вышли на маршрут. <img align="left" alt="Preview" src="x.jpg">',
         "similarity": 0.86, "landscape_layers": ["city"], "categories": []},
    ])
    out = rag.fetch_fresh_context("электробусы в городе", ["city"], mission_goal="транспорт")
    assert "Электробусы вышли на маршрут" in out
    assert "<img" not in out and "Preview" not in out
