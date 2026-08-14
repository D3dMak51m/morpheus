"""
Тесты Stage 46: пауза перед публикацией больше не держит очередь.

Задержку спали в единственном потребителе, поэтому ОДНА задача останавливала весь рой
на всё своё время ожидания: четыре непубликуемые задачи с целью "Self" продержали
реальный комментарий миссии 36 минут. Теперь ожидание — свойство задачи (ZSET), а
непригодная цель отбрасывается ещё до ожидания.

    docker compose exec myrmidon python -m pytest tests -q
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as myrmidon_main                            # noqa: E402


class FakeRedis:
    """Минимальный ZSET/список — ровно те операции, которыми пользуется планировщик."""

    def __init__(self):
        self.zset: dict[str, float] = {}
        self.queue: list[str] = []

    def zadd(self, key, mapping):
        self.zset.update(mapping)

    def zrangebyscore(self, key, lo, hi, start=0, num=100):
        due = sorted((s, m) for m, s in self.zset.items() if lo <= s <= hi)
        return [m for _, m in due[start:start + num]]

    def zrem(self, key, member):
        return 1 if self.zset.pop(member, None) is not None else 0

    def lpush(self, key, value):
        self.queue.append(value)


def _task(**kw):
    base = {"task_id": "t1", "agent_id": "clone_a", "target_platform": "telegram",
            "action_type": "comment", "target_url": "https://t.me/tashkent_news333/42"}
    base.update(kw)
    return base


# ── Что вообще не должно занимать время ───────────────────────────────────

def test_self_target_is_unpublishable():
    """`parse_target("Self")` возвращает истинный ref без id поста — проверять надо ПАРУ."""
    assert myrmidon_main.unpublishable_reason(_task(target_url="Self"))


def test_channel_without_a_post_is_unpublishable():
    assert myrmidon_main.unpublishable_reason(_task(target_url="https://t.me/tashkent_news333"))


def test_a_real_post_is_publishable():
    assert myrmidon_main.unpublishable_reason(_task()) is None


def test_a_reaction_is_not_checked_as_a_comment():
    assert myrmidon_main.unpublishable_reason(_task(action_type="react")) is None


# ── Ожидание ──────────────────────────────────────────────────────────────

def test_a_task_with_no_delay_runs_immediately():
    r = FakeRedis()
    assert myrmidon_main._due_or_defer(r, _task(execution_delay_sec=0)) is True
    assert r.zset == {}


def test_a_delayed_task_is_parked_and_does_not_block_the_next_one():
    r = FakeRedis()
    slow = _task(task_id="slow", execution_delay_sec=600)
    assert myrmidon_main._due_or_defer(r, slow) is False
    assert len(r.zset) == 1
    # Следующая задача другого агента проходит немедленно — очередь свободна.
    assert myrmidon_main._due_or_defer(r, _task(task_id="fast", agent_id="clone_b",
                                                execution_delay_sec=0)) is True


def test_a_due_task_returns_to_the_queue_exactly_once():
    r = FakeRedis()
    myrmidon_main._due_or_defer(r, _task(execution_delay_sec=60))
    # Ещё не время.
    assert myrmidon_main._release_due_tasks(r) == 0
    for member in list(r.zset):
        r.zset[member] = time.time() - 1
    assert myrmidon_main._release_due_tasks(r) == 1
    assert len(r.queue) == 1
    # Повторный тик ничего не дублирует.
    assert myrmidon_main._release_due_tasks(r) == 0
    assert len(r.queue) == 1


def test_a_released_task_is_due_when_it_comes_back():
    """Срок хранится в самой задаче, а не только в индексе: вернувшись в очередь, она
    исполняется, а не паркуется снова (иначе задача ходила бы по кругу)."""
    r = FakeRedis()
    myrmidon_main._due_or_defer(r, _task(execution_delay_sec=0.05))
    time.sleep(0.06)
    assert myrmidon_main._release_due_tasks(r) == 1
    returned = json.loads(r.queue[0])
    assert myrmidon_main._due_or_defer(r, returned) is True


def test_a_broken_redis_runs_the_task_rather_than_losing_it():
    """Опубликовать чуть раньше — меньшая беда, чем потерять уже сгенерированный текст."""
    class Broken(FakeRedis):
        def zadd(self, key, mapping):
            raise RuntimeError("redis down")

    assert myrmidon_main._due_or_defer(Broken(), _task(execution_delay_sec=600)) is True
