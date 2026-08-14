"""
Тесты Stage 46: роль — это РАБОТА в обсуждении, а не объём генерации.

alpha/beta/gamma описывали стоимость: один бот говорил, двое повторяли его дешевле.
Старые ростеры должны продолжать работать, поэтому легаси-значения принимаются и
читаются как работы, но новые назначения делаются по работам.

    docker compose exec daedalus python -m pytest tests -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import mission_control                                  # noqa: E402
from app.router_missions import VALID_ROLES, AutoAssignRequest    # noqa: E402


# ── Словарь ролей ─────────────────────────────────────────────────────────

def test_functional_roles_are_accepted():
    for role in ("scout", "opener", "support", "closer"):
        assert role in VALID_ROLES


def test_legacy_castes_still_pass_validation():
    """Иначе существующие ростеры перестали бы сохраняться."""
    for role in ("alpha", "beta", "gamma"):
        assert role in VALID_ROLES


def test_legacy_role_reads_as_the_job_it_actually_did():
    assert mission_control.functional_role("alpha") == "opener"
    assert mission_control.functional_role("beta") == "support"
    assert mission_control.functional_role("gamma") == "scout"
    assert mission_control.functional_role("support") == "support"


# ── Авто-набор ────────────────────────────────────────────────────────────

def test_auto_assign_defaults_to_someone_who_opens():
    assert AutoAssignRequest().counts() == {"scout": 0, "opener": 1, "support": 0, "closer": 0}


def test_legacy_auto_assign_body_is_mapped_not_stored():
    """Старый клиент просит 1α/2β/1γ — это «один открывает, пара поддерживает»."""
    counts = AutoAssignRequest(alpha=1, beta=2, gamma=1).counts()
    assert counts["opener"] == 1 and counts["support"] == 2 and counts["scout"] == 1
    assert "alpha" not in counts


def test_a_roster_gets_support_before_a_closer():
    """Отвечать на возражение — это работа; гасить ветку нужно только когда она горит."""
    assert mission_control._dynamic_role_counts(1) == {"scout": 0, "opener": 1, "support": 0, "closer": 0}
    two = mission_control._dynamic_role_counts(2)
    assert two["opener"] == 1 and two["support"] == 1 and two["closer"] == 0
    four = mission_control._dynamic_role_counts(4)
    assert four["opener"] == 1 and four["support"] >= 1 and four["closer"] == 1
    assert sum(mission_control._dynamic_role_counts(5).values()) == 5


def test_caste_is_a_budget_hint_for_a_job_not_the_job_itself():
    """Полный когнитивный alpha уместен там, где надо думать; дешёвая gamma — в разведке."""
    assert "alpha" in mission_control.ROLE_CASTE_AFFINITY["opener"]
    assert "alpha" in mission_control.ROLE_CASTE_AFFINITY["support"]
    assert "gamma" in mission_control.ROLE_CASTE_AFFINITY["scout"]
    assert "gamma" not in mission_control.ROLE_CASTE_AFFINITY["opener"]
