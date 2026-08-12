"""
DAEDALUS — Mission sanity checks (Stage 41)
=============================================
A mission is the operator's message. The engine can carry it faithfully, but it
cannot notice that the message argues with itself — and a mission that does is not
a harmless typo. Live example: mission «За аргентину» had

    goal   = «Сборная Аргентины должна была выиграть финал»
    stance = «Аргентина проиграл из-за тренера»

and the single comment that published in six hours agreed the coach caused the
defeat — i.e. it argued the mission's own goal down, at real people, before anyone
noticed. These checks surface that on save instead.

Two layers, deliberately:

* **structural** — deterministic, instant, no GPU. Catches the documented shapes:
  a `stance` written as a tag salad («Суверенитет, Социализм, Технократия…»),
  which makes qwen2.5:3b default to «нет» on relevance and write muddled rebuttals;
  an active mission with no declared side; a side identical to its opponent.
* **directional** — one short LLM call asking whether the stance argues FOR or
  AGAINST the goal. Measured on the framing that works: asking «consistent?» as
  JSON made the model answer `false` for every input (3/6 only because half the
  cases were genuinely contradictory), while asking for a direction — «за» or
  «против» — scored 5/6, stable 3/3 per case.

Everything is a WARNING, never a block. The one measured miss is a false alarm on
a nuanced stance, and the operator overruling a warning costs a glance; a missed
contradiction costs a comment arguing the wrong way at a real audience.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger("daedalus.mission_validate")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
TEXT_MODEL_NAME = os.getenv("TEXT_MODEL_NAME", "qwen2.5:3b")
DIRECTION_TIMEOUT_SEC = float(os.getenv("MISSION_VALIDATE_TIMEOUT_SEC", "45"))

# Severity is advisory: "error" = the mission will very likely misbehave,
# "warning" = it will work but weakly.
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

_WORD_RE = re.compile(r"[^\s,;/|]+")


def _looks_like_tag_salad(text: str) -> bool:
    """
    True when a stance reads as a list of themes rather than an argued claim.

    CLAUDE.md's operator rule exists because of this exact shape: a stance like
    «Суверенитет, Социализм, Технократия…» gives the model nothing to argue FROM, so
    it defaults to «нет» on the relevance gate and produces muddled rebuttals. A claim
    has clauses; a salad has short comma-separated fragments.
    """
    s = (text or "").strip()
    if not s:
        return False
    segments = [seg.strip() for seg in re.split(r"[,;/|]", s) if seg.strip()]
    if len(segments) < 3:
        return False
    # Every fragment being 1–3 words is what makes it a list rather than a sentence.
    return all(len(_WORD_RE.findall(seg)) <= 3 for seg in segments)


def structural_issues(mission: dict[str, Any]) -> list[dict[str, str]]:
    """Deterministic checks. ``mission`` uses production field names."""
    issues: list[dict[str, str]] = []
    goal = (mission.get("goal") or "").strip()
    stance = (mission.get("stance") or "").strip()
    our_side = (mission.get("our_side") or "").strip()
    opponent = (mission.get("opponent") or "").strip()
    key_points = [p for p in (mission.get("key_points") or []) if str(p).strip()]
    active = (mission.get("status") or "").lower() == "active"

    if not goal:
        issues.append({"field": "goal", "severity": SEVERITY_ERROR,
                       "message": "Цель не заполнена — агентам нечего продвигать."})
    if _looks_like_tag_salad(stance):
        issues.append({"field": "stance", "severity": SEVERITY_ERROR,
                       "message": "Позиция выглядит списком тем, а не утверждением. "
                                  "Модель может спорить только с того, что читается как "
                                  "позиция — напишите одной фразой, что именно вы "
                                  "утверждаете."})
    if active and not our_side:
        issues.append({"field": "our_side", "severity": SEVERITY_WARNING,
                       "message": "Сторона не объявлена. Без неё модель угадывает, за кого "
                                  "она — и иногда угадывает против вашей же цели."})
    if active and not key_points:
        issues.append({"field": "key_points", "severity": SEVERITY_WARNING,
                       "message": "Нет ключевых доводов — агент будет повторять цель общими "
                                  "словами вместо конкретного аргумента."})
    if our_side and opponent and our_side.lower() == opponent.lower():
        issues.append({"field": "opponent", "severity": SEVERITY_ERROR,
                       "message": "Своя и противоположная сторона совпадают."})
    if stance and len(stance.split()) < 3 and not _looks_like_tag_salad(stance):
        issues.append({"field": "stance", "severity": SEVERITY_WARNING,
                       "message": "Позиция слишком коротка, чтобы быть доводом."})
    return issues


def _direction(claim: str, reply: str) -> Optional[str]:
    """
    Ask the model whether ``reply`` argues FOR or AGAINST ``claim``.

    Returns "за" | "против" | None (unavailable). Penalties are off and temperature
    is zero — CLAUDE.md: the anti-parroting penalties push the model off the clean
    answer token on short classification calls.
    """
    # The comparison clause is not decoration. Without it the model read every
    # "A лучше, чем B" as opposition — a natural way to write a stance — and flagged
    # «Развитие транспорта решает пробки лучше, чем новые дороги» as arguing against
    # «Городу нужен развитый транспорт», 3/3. With it, 5/5 on the same set.
    system = ("Есть ЦЕЛЬ и РЕПЛИКА автора. Определи, помогает ли реплика достичь цели.\n"
              "Сравнение вида «A лучше, чем B» — это поддержка A, а не спор с ним.\n"
              "Ответь ОДНИМ словом: «за» или «против». Больше ничего.")
    try:
        resp = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": TEXT_MODEL_NAME, "system": system,
                  "prompt": f"ЦЕЛЬ: {claim}\nРЕПЛИКА: {reply}\nОтвет:",
                  "stream": False, "keep_alive": 0,
                  "options": {"temperature": 0.0, "repeat_penalty": 1.0, "num_predict": 6}},
            timeout=DIRECTION_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        text = (resp.json().get("response") or "").strip().lower()
    except Exception as exc:
        logger.info("Mission validation: direction check unavailable: %s", exc)
        return None
    if "против" in text:
        return "против"
    if "за" in text:
        return "за"
    return None


def directional_issues(mission: dict[str, Any]) -> list[dict[str, str]]:
    """The LLM half: does the stance (and the declared side) argue FOR the goal?"""
    issues: list[dict[str, str]] = []
    goal = (mission.get("goal") or "").strip()
    if not goal:
        return issues

    for field, label in (("stance", "Позиция"), ("our_side", "Своя сторона")):
        value = (mission.get(field) or "").strip()
        if not value or _looks_like_tag_salad(value):
            continue
        verdict = _direction(goal, value)
        if verdict == "против":
            issues.append({
                "field": field, "severity": SEVERITY_ERROR,
                "message": f"{label} читается как спор ПРОТИВ цели миссии. Так уже случалось "
                           f"вживую: агент согласился с тем, что миссия должна была "
                           f"опровергать. Сверьте формулировки.",
            })
    return issues


def validate_mission(mission: dict[str, Any], deep: bool = True) -> dict[str, Any]:
    """
    Check one mission. ``deep=False`` skips the LLM call (instant, structural only).

    Never raises and never blocks: returns ``{issues: [...], checked_direction: bool}``
    so the caller can show them and let the operator decide.
    """
    issues = structural_issues(mission)
    checked_direction = False
    if deep:
        found = directional_issues(mission)
        checked_direction = True
        issues.extend(found)
    return {
        "issues": issues,
        "checked_direction": checked_direction,
        "ok": not any(i["severity"] == SEVERITY_ERROR for i in issues),
    }
