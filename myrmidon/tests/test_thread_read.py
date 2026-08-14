"""
Тесты Stage 48: чтение ветки не должно блокировать само себя.

Найдено в бою: при холодном кеше личностей роя задача-комментарий вставала намертво
на «session busy» — чтение ветки пыталось открыть сессию того же аккаунта, чей лок оно
само и держало. А так как отложенные задачи отдаются одному потребителю, вместе с ней
вставал весь исполнительный цикл.

    docker compose exec myrmidon python -m pytest tests -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.drivers.tg_client import TelegramDriver                 # noqa: E402


def test_identities_are_read_from_cache_only(monkeypatch):
    """Пустой кеш возвращает пустое множество и НЕ идёт открывать сессии."""
    opened = []

    class FakeRedis:
        def smembers(self, key):
            return set()

    from app import outcome_engine
    monkeypatch.setattr(outcome_engine, "_get_redis", lambda: FakeRedis())
    monkeypatch.setattr(outcome_engine, "swarm_identities",
                        lambda sf: opened.append("НЕ ДОЛЖНО ВЫЗЫВАТЬСЯ") or set())
    assert TelegramDriver._swarm_ids() == set()
    assert opened == [], "чтение ветки полезло пересобирать кеш и заблокирует само себя"


def test_warm_cache_is_used(monkeypatch):
    class FakeRedis:
        def smembers(self, key):
            return {"kxx_007", "123456"}

    from app import outcome_engine
    monkeypatch.setattr(outcome_engine, "_get_redis", lambda: FakeRedis())
    assert TelegramDriver._swarm_ids() == {"kxx_007", "123456"}


def test_broken_redis_does_not_raise(monkeypatch):
    from app import outcome_engine

    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(outcome_engine, "_get_redis", boom)
    assert TelegramDriver._swarm_ids() == set()
