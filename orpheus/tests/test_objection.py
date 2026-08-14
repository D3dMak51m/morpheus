"""
Тесты Stage 46: возражение, на которое отвечает команда, и выбор техники против него.

Главная проверка здесь — заземление. Слабая модель охотно сочиняет правдоподобный
довод, которого в ветке никто не высказывал; отвечать на выдуманное возражение хуже,
чем не отвечать вовсе, потому что мы публично вкладываем слова в чужой рот.

Всё офлайн: LLM замокан, живые замеры делают bench-скрипты.

    docker compose exec orpheus python -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as orpheus_main                             # noqa: E402
from app.persona import PersonaEngine, PROFILES_CACHE            # noqa: E402

THREAD = (
    "Азиз: автобусы ходят раз в полчаса, никакая полоса это не исправит\n"
    "Дилшод: да пусть лучше дороги расширят, пробки же из-за узких улиц\n"
    "Марат: у меня работа в другом конце города, без машины никак"
)


@pytest.fixture
def fake_llm(monkeypatch):
    """Подменяет генерацию: возвращает заранее заданные ответы по очереди."""
    calls = []

    def make(answers):
        queue = list(answers)

        def _gen(prompt, max_tokens=None, temperature=None, penalties=True):
            calls.append({"prompt": prompt, "penalties": penalties})
            return queue.pop(0) if queue else ""

        monkeypatch.setattr(orpheus_main, "generate_text", _gen)
        return calls

    return make


# ── Заземление возражения ─────────────────────────────────────────────────

def test_objection_present_in_the_thread_is_kept():
    kept = orpheus_main._grounded_objection(
        "автобусы ходят раз в полчаса, полоса это не исправит", THREAD)
    assert "полчаса" in kept


def test_invented_objection_is_discarded():
    """Довода про тарифы в ветке нет — это галлюцинация, а не позиция оппонента."""
    assert orpheus_main._grounded_objection(
        "проезд подорожает вдвое, пенсионерам станет нечем платить", THREAD) == ""


def test_no_objection_answer_is_not_an_objection():
    assert orpheus_main._grounded_objection("НЕТ", THREAD) == ""
    assert orpheus_main._grounded_objection("", THREAD) == ""


def test_the_authors_name_is_stripped_from_the_quote():
    """Модель цитирует вместе с автором («Дилшод: пусть лучше…») — в промпт должна
    попасть реплика, а не строчка лога."""
    got = orpheus_main._grounded_objection(
        "Дилшод: да пусть лучше дороги расширят, пробки же из-за узких улиц", THREAD)
    assert got.startswith("да пусть лучше дороги расширят")


def test_extraction_asks_without_penalties(fake_llm):
    """Короткая извлекающая задача — как и классификация, без anti-parroting штрафов:
    они сбивают модель с чистых ответов (измерено на 'дятьнет')."""
    calls = fake_llm(["автобусы ходят раз в полчаса"])
    got = orpheus_main._extract_objection(
        {"thread_context": THREAD, "position": {"our_side": "город должен вкладываться в транспорт"}})
    assert "полчаса" in got
    assert calls[0]["penalties"] is False


# ── Выбор техники ─────────────────────────────────────────────────────────

def test_technique_maps_the_models_word(fake_llm):
    fake_llm(["рамка"])
    assert orpheus_main._technique_for("пусть лучше дороги расширят", "за транспорт",
                                       has_facts=False) == "reframe"


def test_fact_correction_is_offered_only_with_facts_in_the_dossier(fake_llm):
    """Просить слабую модель «опровергнуть фактом», которого нет, — способ получить
    выдуманный факт. Без досье вариант просто не предлагается."""
    calls = fake_llm(["факт"])
    got = orpheus_main._technique_for("автобусов мало", "за транспорт", has_facts=False)
    assert "факт —" not in calls[0]["prompt"]
    assert got != "factual_correction"      # слова нет в списке → падаем в безопасный дефолт


def test_avoid_excludes_the_technique_a_teammate_already_used(fake_llm):
    calls = fake_llm(["уступка"])
    got = orpheus_main._technique_for("автобусов мало", "за транспорт", has_facts=True,
                                      avoid="concede_and_redirect")
    assert "уступка" not in calls[0]["prompt"]
    assert got != "concede_and_redirect"


def test_unreadable_answer_falls_back_to_the_safest_move(fake_llm):
    """Непрочитанный ответ не должен превращаться в выдуманный факт или в эскалацию."""
    fake_llm(["дятьнет"])
    assert orpheus_main._technique_for("автобусов мало", "за транспорт",
                                       has_facts=True) == "concede_and_redirect"


# ── Разрешение тактики целиком ────────────────────────────────────────────

def test_objection_drives_the_tactic(fake_llm):
    fake_llm(["OPPOSE", "пусть лучше дороги расширят, пробки из-за узких улиц", "рамка"])
    req = {"tactic": "dynamic", "post_text": "В городе выделят полосу для автобусов",
           "thread_context": THREAD, "position": {"our_side": "выделенная полоса нужна"}}
    assert orpheus_main._resolve_dynamic_tactic(req) == "reframe"
    assert req["_mood"] == "OPPOSE"
    assert "дороги расширят" in req["objection"]


def test_a_friendly_thread_is_not_asked_who_argues_with_us(fake_llm):
    """Промпт извлечения ЦИТИРУЕТ спорящего, а не решает, есть ли спор (слабая модель
    такое решение проваливает — измерено 2/2 «НЕТ» на ветке с явной оппозицией). Значит
    на дружелюбной ветке спрашивать нельзя: он процитирует союзника как врага."""
    calls = fake_llm(["AGREE"])
    req = {"tactic": "dynamic", "post_text": "В городе выделят полосу для автобусов",
           "thread_context": "Азиз: наконец-то нормальный транспорт",
           "position": {"our_side": "выделенная полоса нужна"}}
    assert orpheus_main._resolve_dynamic_tactic(req) == "amplify"
    assert len(calls) == 1                       # только настроение, без извлечения
    assert not req.get("objection")


def test_a_propagated_objection_is_not_extracted_again(fake_llm):
    """Ответчик получает довод от открывающего: платить за извлечение второй раз незачем,
    а вот технику он обязан выбрать другую."""
    calls = fake_llm(["OPPOSE", "основание"])
    req = {"tactic": "dynamic", "post_text": "пост", "thread_context": THREAD,
           "objection": "у меня работа в другом конце города, без машины никак",
           "avoid_tactic": "reframe", "position": {"our_side": "полоса нужна"}}
    assert orpheus_main._resolve_dynamic_tactic(req) == "ask_evidence"
    assert len(calls) == 2      # настроение + техника, без извлечения


def test_pinned_tactic_is_respected(fake_llm):
    fake_llm([])
    assert orpheus_main._resolve_dynamic_tactic(
        {"tactic": "soft_support", "post_text": "пост", "thread_context": THREAD}) is None


def test_lite_companion_inherits_instead_of_deciding(fake_llm):
    fake_llm([])
    assert orpheus_main._resolve_dynamic_tactic(
        {"tactic": "dynamic", "lite": True, "post_text": "пост",
         "thread_context": THREAD}) is None


# ── Промпт ────────────────────────────────────────────────────────────────

def test_the_objection_reaches_the_prompt():
    """Техника без предъявленного возражения беспредметна — модель выдумает себе спор."""
    PROFILES_CACHE["agent-x"] = {
        "identity": {"full_name": "Азиз", "city": "Ташкент", "occupation": "инженер"},
        "personality": {}, "core_mission": "город", "behavioral_rules": {},
    }
    try:
        prompt = PersonaEngine().assemble_mission_prompt("agent-x", {
            "mode": "comment", "post_text": "В городе выделят полосу",
            "objection": "пусть лучше дороги расширят",
            "tactic": "reframe", "role": "support",
            "position": {"our_side": "полоса нужна"},
        })
        assert "пусть лучше дороги расширят" in prompt
        assert "Возражение" in prompt
    finally:
        PROFILES_CACHE.pop("agent-x", None)


def test_the_allys_line_counts_as_an_echo():
    """Найдено в бою: «ответчик» опубликовал реплику открывающего слово в слово.
    Текст союзника приходит в `alpha_context`, и сравнивать надо с ним тоже."""
    from app.guardrails import OutputGuardrails
    g = OutputGuardrails()
    ally = "一秒的拥堵，却要忍受四十分钟。地铁解决不了所有问题啊！"
    assert g.is_echo(ally, ["", "Мэрия обсуждает расширение проспекта", ally])
