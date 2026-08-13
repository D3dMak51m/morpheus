"""
Тесты Stage 47: инструменты (поиск + чтение) и разведка по предмету миссии.

Почему разведка переписана — измерено на живом корпусе (1594 факта, 30 дней):

  * текст миссии на 70% состоит из её СОБСТВЕННЫХ доводов, которых ни одна новость не
    повторяет, поэтому совпадение по всему тексту требовало статьи, которая спорит за
    нас;
  * IDF по такому корпусу делает «редкими» обычные слова («людей» 21, «нужен» 5), и
    сумма слабых совпадений всегда била пару сильных: наверх лезли Самарканд, день
    рождения газеты и утонувшее судно в Зимбабве;
  * одного слова темы мало — «трафик» привёл посещаемость магазинов электроники,
    «развяз» привёл «развязать войну».

    docker compose exec daedalus python -m pytest tests -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import tools                                            # noqa: E402


# ── Запрос ────────────────────────────────────────────────────────────────

def test_query_is_stripped_of_quotes_and_newlines():
    q = tools.clean_query('«Поддержка   общественного\nтранспорта»')
    assert "«" not in q and "\n" not in q
    assert q.startswith("Поддержка общественного транспорта")


def test_query_is_bounded():
    assert len(tools.clean_query("слово " * 200)) <= 120


# ── Отсев мусорных источников ─────────────────────────────────────────────

def test_app_stores_and_maps_are_never_opened():
    """Измерено: первый же поиск разведки записал в базу знаний страницу Яндекс.Метро
    из Google Play как «факт об общественном транспорте»."""
    for url in ("https://play.google.com/store/apps/details?id=ru.yandex.metro",
                "https://apps.apple.com/app/id123",
                "https://yandex.ru/maps/10335/tashkent/transport/"):
        assert any(h in url for h in tools.SKIP_HOSTS), url


def test_ordinary_news_sites_are_not_skipped():
    for url in ("https://www.gazeta.uz/ru/2026/08/11/transport/",
                "https://tashtrans.uz/avtobusnye-marshruty-tashkenta/"):
        assert not any(h in url for h in tools.SKIP_HOSTS), url


def test_search_survives_an_unreachable_backend(monkeypatch):
    """Рой без поиска отвечает тем, что знает, а не падает."""
    def boom(*a, **k):
        raise RuntimeError("searxng down")
    monkeypatch.setattr(tools.httpx, "get", boom)
    assert tools.search("ташкент автобусы") == []
    assert tools.available() is False


def test_empty_query_is_not_searched(monkeypatch):
    called = []
    monkeypatch.setattr(tools.httpx, "get", lambda *a, **k: called.append(1))
    assert tools.search("   ") == []
    assert called == []
