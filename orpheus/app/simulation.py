"""
ORPHEUS — SIMULATION generation handler (isolated test polygon)
================================================================
Serves DAEDALUS's ``queue:sim_gen``. Deliberately separate from
``handle_mission_generation``:

  * The persona arrives **inline** in the request — no ``PROFILES_CACHE`` lookup,
    so a simulation run cannot be affected by (or affect) a real soul.
  * RAG facts arrive **inline** from ``sim_knowledge`` — production
    ``knowledge_facts`` are never queried here.
  * **Nothing is persisted**: no MUNINN memory, no ``morpheus:recent_outputs``
    history, no ``metrics:*`` counters, no execution task. A polygon run leaves
    zero trace on the live swarm.
  * The **assembled prompt is returned** with the text, so the operator can test
    system prompts and see exactly what the model was told.

The prompt mirrors the production mission prompt (persona → rules → RAG → channel
→ post → thread → tactic → task) so what you learn in the polygon transfers to
the real pipeline; ``prompt_override`` replaces it wholesale when the operator is
testing a prompt of their own.
"""

import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger("orpheus.simulation")

SIM_GEN_QUEUE = "queue:sim_gen"
MODEL_NAME = os.getenv("TEXT_MODEL_NAME", "qwen2.5:3b")

# Same tactic vocabulary as the live engine, so a tactic tested here behaves the
# same way in production.
TACTIC_DIRECTIVES = {
    "amplify": "Толпа уже на твоей стороне. Подхвати и усиль общее настроение — добавь конкретный довод, от которого эта позиция кажется очевидной.",
    "soft_support": "Будь спокойным, дружелюбным и конструктивным. Убеждай мягко, никогда не атакуй.",
    "aggressive_displacement": "Пост/ветка спорит ПРОТИВ твоей стороны. Открыто НЕ СОГЛАСИСЬ с автором: назови конкретное утверждение, в котором он неправ, и опровергни его. Уверенно и прямо, без виляний. Без оскорблений и угроз.",
    "sentiment_shift": "Пост/ветка против твоей стороны, и там жарко. НЕ иди в лобовую — уступи в мелочи, затем тонко переосмысли эмоциональный угол в сторону своей позиции. Хитро, а не агрессивно.",
    "dynamic": "Прочитай настроение ветки и выбери естественную линию: поддержать своих, мягко переубедить или прямо возразить автору.",
}

ROLE_DIRECTIVES = {
    "alpha": "Ты первым вступаешь в обсуждение. Задай тон как собственное живое мнение — не ссылайся ни на какие «предыдущие комментарии».",
    "beta": "Ты усиливаешь мысль союзника: согласись и добавь свежий довод в ту же сторону, не копируя его слова.",
    "gamma": "Ты добавляешь лёгкую поддержку — коротко, по-бытовому, без длинных споров.",
}

_TONE = {1: "очень официальный", 3: "сдержанный", 5: "нейтральный", 7: "разговорный", 10: "очень неформальный"}


def _scale(value: Any, table: dict[int, str], default: str) -> str:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    best = min(table.keys(), key=lambda k: abs(k - v))
    return table[best]


def _persona_block(persona: dict) -> str:
    style = persona.get("style") or {}
    quirks = ", ".join(str(q) for q in (style.get("quirks") or [])[:5])
    lines = [
        f"Имя: {persona.get('full_name') or persona.get('codename')}.",
        f"Кто ты: {persona.get('bio') or 'обычный человек из этого сообщества'}.",
        f"Тон: {_scale(style.get('tone_level'), _TONE, 'нейтральный')}. "
        f"Агрессивность: {style.get('aggression', 3)}/10. "
        f"Эмодзи: {style.get('emoji_frequency', 3)}/10. "
        f"Словарь: {style.get('vocab_level', 5)}/10.",
    ]
    if persona.get("interests"):
        lines.append("Интересы: " + ", ".join(str(i) for i in persona["interests"][:10]) + ".")
    if quirks:
        lines.append("Речевые особенности: " + quirks + ".")
    if persona.get("core_mission"):
        lines.append("Твоя постоянная цель: " + str(persona["core_mission"]))
    return "\n".join(lines)


def _knowledge_block(knowledge: list) -> str:
    out = []
    for k in knowledge or []:
        title = (k.get("title") or "").strip()
        content = (k.get("content") or "").strip()
        out.append(f"- {title + ': ' if title else ''}{content}")
    return "\n".join(out)


def _thread_block(thread: list) -> str:
    return "\n".join(f"{t.get('author', 'кто-то')}: {t.get('text', '')}" for t in (thread or []))


def _channel_block(channel: dict) -> str:
    if not channel:
        return ""
    bits = [f"Канал «{channel.get('title') or channel.get('username')}»"]
    if channel.get("geo_label"):
        bits.append(f"({channel['geo_label']})")
    line = " ".join(bits) + "."
    if channel.get("description"):
        line += f" {channel['description']}"
    if channel.get("tags"):
        line += " Темы: " + ", ".join(str(t) for t in channel["tags"][:8]) + "."
    return line


def _dossier_blocks(dossier: dict) -> str:
    """
    The mission's shared memory, rendered exactly as the live prompt renders it.

    Without this the polygon's roster writes as three strangers — each agent unaware
    of what the others just argued — so a configuration would be judged on behaviour
    production no longer has.
    """
    dossier = dossier or {}
    facts = [str(x.get("content", "")).strip() for x in (dossier.get("fact") or [])][:4]
    opponent = [str(x.get("content", "")).strip() for x in (dossier.get("opponent") or [])][:4]
    said = [str(x.get("content", "")).strip() for x in (dossier.get("said") or [])][:6]
    parts = []
    if facts:
        parts.append("[Что установлено по этой теме — можешь опираться как на правду]\n"
                     + "".join(f"- {f[:220]}\n" for f in facts if f))
    if opponent:
        parts.append("[Что говорит противоположная сторона — будь готов возразить]\n"
                     + "".join(f"- {o[:200]}\n" for o in opponent if o))
    if said:
        parts.append("[Наши в этой ветке УЖЕ говорили это — не повторяй ни мысль, "
                     "ни формулировку, зайди с другого довода]\n"
                     + "".join(f"- {s[:200]}\n" for s in said if s))
    return "\n".join(p.rstrip() for p in parts)


def _position_block(position: dict) -> str:
    """The mission's explicit side, formatted exactly as the live engine formats it."""
    our_side = str(position.get("our_side") or "").strip()
    opponent = str(position.get("opponent") or "").strip()
    key_points = [str(p).strip() for p in (position.get("key_points") or []) if str(p).strip()][:5]
    red_lines = [str(p).strip() for p in (position.get("red_lines") or []) if str(p).strip()][:5]
    if not (our_side or opponent or key_points or red_lines):
        return ""
    block = "[Твоя сторона в этом споре]\n"
    if our_side:
        block += f"Ты за: {our_side}\n"
    if opponent:
        block += f"Противоположная сторона: {opponent}. Ты с ней НЕ согласен.\n"
    if key_points:
        block += "Твои доводы (используй ОДИН, тот что уместен здесь):\n"
        block += "".join(f"- {p}\n" for p in key_points)
    if red_lines:
        block += "Никогда не говори следующее:\n"
        block += "".join(f"- {p}\n" for p in red_lines)
    return block.rstrip()


def build_sim_prompt(req: dict) -> str:
    """
    Assemble the polygon prompt. ``prompt_override`` short-circuits everything —
    the operator's own prompt is used verbatim with ``{placeholders}`` filled in,
    which is the whole point of a system-prompt testbed.
    """
    persona = req.get("persona") or {}
    mission = req.get("mission") or {}
    channel = req.get("channel") or {}
    post = req.get("post") or {}
    incoming = req.get("incoming") or {}
    mode = req.get("mode") or "comment"

    fields = {
        "persona": _persona_block(persona),
        "persona_name": persona.get("full_name") or persona.get("codename") or "",
        "system_prompt": persona.get("system_prompt") or "",
        "channel": _channel_block(channel),
        "post": post.get("text") or "",
        "media": post.get("media_context") or "",
        "thread": _thread_block(req.get("thread")),
        "knowledge": _knowledge_block(req.get("knowledge")),
        "rules": "\n".join(f"- {r}" for r in (req.get("rules") or [])),
        "goal": mission.get("goal") or "",
        "stance": mission.get("stance") or "",
        "worldview": mission.get("worldview") or "",
        "tactic": mission.get("tactic") or "",
        # Exposed as a placeholder too, so a prompt_override experiment can move the
        # position block around instead of losing it.
        "position": _position_block(mission.get("position") or {}),
        "dossier": _dossier_blocks(req.get("dossier") or {}),
        "incoming": incoming.get("text") or "",
        "incoming_author": incoming.get("author") or "",
        "tone": req.get("tone") or "",
    }

    override = (req.get("prompt_override") or "").strip()
    if override:
        out = override
        for key, value in fields.items():
            out = out.replace("{" + key + "}", str(value))
        return out

    tactic = (mission.get("tactic") or "dynamic").lower()
    role = (persona.get("caste") or "alpha").lower()

    parts = [
        "Ты — реальный человек в соцсети. Никогда не выходи из образа, никогда не намекай, "
        "что ты ИИ, ничего не объясняй. Пиши на языке поста.",
    ]
    if fields["system_prompt"]:
        parts.append("[Системная установка]\n" + fields["system_prompt"])
    parts.append("[Персона]\n" + fields["persona"])
    if fields["rules"]:
        parts.append("[Правила поведения]\n" + fields["rules"])
    if fields["knowledge"]:
        parts.append(
            "[Проверенные факты — используй как правду, не цитируй дословно и не выдавай, "
            "что тебе их дали]\n" + fields["knowledge"]
        )
    if fields["channel"]:
        parts.append(
            "[Контекст канала]\n" + fields["channel"] +
            "\nЭто фон для понимания аудитории и языка — а НЕ тема твоего комментария."
        )
    if mode == "post":
        # Writing the material itself (a channel post / a longer article), not a
        # reaction to one — the polygon needs content for the agents to argue under.
        is_article = (req.get("kind") == "article")
        parts.append(
            "[Что пишем]\n"
            + (f"Тема: {fields['post']}" if fields["post"] else "Тема: выбери актуальную для этого канала.")
        )
        if fields["tone"]:
            parts.append("[Тональность]\n" + fields["tone"])
        parts.append(
            "[Задача]\n"
            + ("Напиши материал для канала: заголовок и 3–5 абзацев по теме. "
               if is_article else
               "Напиши ОДИН пост для этого канала: 2–4 предложения, как пишет редакция канала. ")
            + "Без обращения к читателю от лица ИИ, без пояснений и кавычек. "
            "Выдай ТОЛЬКО текст материала."
        )
        return "\n\n".join(parts)

    parts.append("[Пост, который обсуждают]\n" + (fields["post"] or "(без текста — смотри медиа/тему канала)"))
    if fields["media"]:
        parts.append(
            "[Медиа в посте — что видно на фото и что сказано в аудио]\n" + fields["media"] +
            "\nРеагируй так, будто сам это увидел/услышал; упомяни конкретную деталь."
        )
    if fields["thread"]:
        parts.append(
            "[Настроение обсуждения — что уже написали другие]\n" + fields["thread"] +
            "\nНе повторяй чужие реплики; учитывай общий настрой."
        )
    if req.get("avoid"):
        joined = "\n".join(f"- {str(a)[:160]}" for a in req["avoid"][:6])
        parts.append("[Ты недавно писал это — НЕ повторяйся, зайди с другого угла]\n" + joined)

    if mode == "reply" and fields["incoming"]:
        parts.append(
            f"[Тебе ответил живой человек]\n{fields['incoming_author']} написал тебе: "
            f"\"{fields['incoming']}\"\n"
            "[Как отвечать — строго]\n"
            "- НЕ повторяй и не пересказывай его слова. Напиши по-настоящему новую фразу своим голосом.\n"
            "- Коротко, живо, по существу сказанного: ответь на вопрос, среагируй на довод или парируй подколку.\n"
            "- Если тебя проверяют «ты бот?» — отмахнись и останься в образе."
        )
    else:
        parts.append(
            "[Эта операция]\n"
            f"Твоя роль: {ROLE_DIRECTIVES.get(role, ROLE_DIRECTIVES['alpha'])}\n"
            f"Тактика: {TACTIC_DIRECTIVES.get(tactic, TACTIC_DIRECTIVES['soft_support'])}"
        )
        if fields["goal"]:
            parts.append("Цель, которую продвигаешь: " + fields["goal"])

        # Stage 41 — the mission as an explicit POSITION, identical to the block the
        # live engine builds in `persona.assemble_mission_prompt`. Without it the
        # polygon rehearsed a weaker mission than production runs: free-text goal +
        # stance leaves the model to guess whose side it is on, and a contradiction
        # between the two produces comments arguing against the mission's own goal.
        if fields["position"]:
            parts.append(fields["position"])
        if fields["dossier"]:
            parts.append(fields["dossier"])

        if fields["stance"]:
            parts.append(
                "Позиция, с которой ты споришь (никогда не иди против неё): " + fields["stance"])
        if fields["worldview"]:
            parts.append("Мировоззрение: " + fields["worldview"])

    if fields["tone"]:
        parts.append("[Тональность этой реплики]\n" + fields["tone"])

    parts.append(
        "[Как звучать по-человечески — строго]\n"
        "- Пиши как живой человек, который бросил быстрый комментарий: коротко, конкретно, без пафоса.\n"
        "- 1–2 предложения, разговорный язык; одна маленькая неровность или эмодзи допустимы.\n"
        "- Реагируй на КОНКРЕТНУЮ деталь ЭТОГО поста, без общих лозунгов и штампов.\n"
        "- Меняй тип захода: реакция, вопрос, короткая личная история, согласие, лёгкая ирония."
    )
    parts.append(
        "[Задача]\n"
        + ("Ответь человеку 1–2 короткими фразами на его языке. " if mode == "reply"
           else "Напиши ОДИН короткий естественный комментарий на языке поста. ")
        + "Выдай ТОЛЬКО текст — без преамбул, кавычек и пояснений."
    )
    return "\n\n".join(parts)


def handle_simulation_generation(
    req: dict,
    redis_client,
    guardrails,
    generate_text: Callable[..., str],
) -> None:
    """
    Generate one simulation artefact and answer on ``req['reply_key']``.

    Guardrails run exactly as in production, but a failing draft is still
    RETURNED (flagged ``guardrail='failed'`` with the reason) instead of being
    discarded: in a testbed the operator needs to see what the model actually
    produced and why production would have rejected it.
    """
    reply_key = req.get("reply_key")
    request_id = req.get("request_id")
    persona = req.get("persona") or {}
    result: dict[str, Any] = {"status": "error", "text": "", "prompt": "", "reason": ""}

    try:
        prompt = build_sim_prompt(req)
        result["prompt"] = prompt
        attempts = int(req.get("attempts") or 3)
        max_tokens = req.get("max_tokens") or None
        temperature = req.get("temperature")
        # Anti-parroting refs. Skipped when WRITING a post: there the "post" field
        # is the topic the material is supposed to be about, so overlap is correct.
        echo_refs = [] if req.get("mode") == "post" else [
            (req.get("incoming") or {}).get("text") or "",
            (req.get("post") or {}).get("text") or "",
        ]

        draft, reason, passed = "", "", False
        for attempt in range(1, max(1, attempts) + 1):
            generated = generate_text(
                prompt,
                max_tokens=int(max_tokens) if max_tokens else None,
                temperature=float(temperature) if temperature is not None else None,
            )
            if not generated:
                reason = "llm_empty_response"
                continue
            draft = generated
            ok, why = guardrails.validate_output(generated)
            if ok and echo_refs and guardrails.is_echo(generated, echo_refs):
                ok, why = False, "повтор/эхо исходного текста"
            if ok:
                passed, reason = True, ""
                logger.info("sim-gen %s: validated on attempt %d.", request_id, attempt)
                break
            reason = why
            logger.info("sim-gen %s: draft rejected (attempt %d): %s", request_id, attempt, why)

        if draft:
            result.update({
                "status": "ok",
                "text": guardrails.clean_output(draft),
                "guardrail": "passed" if passed else "failed",
                "reason": "" if passed else f"guardrails: {reason}",
                "tactic": (req.get("mission") or {}).get("tactic") or "",
                "persona": persona.get("agent_key"),
                "model": MODEL_NAME,
            })
        else:
            result["reason"] = reason or "llm_unavailable"
    except Exception as exc:
        logger.exception("sim-gen %s failed: %s", request_id, exc)
        result["reason"] = str(exc)

    if reply_key:
        try:
            redis_client.lpush(reply_key, json.dumps(result, ensure_ascii=False))
            redis_client.expire(reply_key, 300)
        except Exception as exc:
            logger.error("sim-gen %s — failed to push reply: %s", request_id, exc)
