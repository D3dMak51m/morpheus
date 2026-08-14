"""
Стенд сравнения гейта релевантности: СТАРЫЙ (Stage 36) против НОВОГО (Stage 38).

Гоняет оба на одних и тех же РЕАЛЬНЫХ входах (цель/позиция миссии, текст поста,
профиль канала — выгруженные из боевой БД) и печатает сравнительную таблицу.
Это не unit-тест: он ходит в живую Ollama, поэтому запускается вручную.

    docker compose exec orpheus python tests/bench_relevance.py [cases.json profiles.json]
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import textutil                                    # noqa: E402
from app.main import (                                      # noqa: E402
    _build_relevance_prompt, _channel_alignment, _channel_context, _parse_verdict,
    generate_text,
)

RUNS = int(sys.argv[3]) if len(sys.argv) > 3 else 3

# ── Старый гейт (Stage 36), воспроизведён дословно ────────────────────────

_OLD_STOPWORDS = {
    "поддерживать", "поддержка", "поддержки", "против", "продвигать", "продвижение",
    "развитие", "развития", "развитой", "удобный", "удобного", "системно", "системный",
    "решаются", "решать", "проблема", "проблемы", "нужно", "важно", "нельзя", "также",
    "всегда", "будет", "более", "менее", "очень", "может", "чтобы", "потому", "вместе",
    "целью", "цель", "миссия", "миссии", "наша", "наши", "сторонник", "позиция",
}


def old_entities(goal, stance, limit=12):
    seen = []
    for tok in re.findall(r"[а-яёa-z]{5,}", f"{goal} {stance}".lower()):
        if tok in _OLD_STOPWORDS or tok in seen:
            continue
        seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


def old_hit(post, ents):
    t = (post or "").lower()
    return any(len(e[:5]) >= 5 and e[:5] in t for e in ents)


def old_gate(goal, stance, post, cp):
    ents = old_entities(goal, stance)
    hint = (" или упоминает: " + ", ".join(ents)) if ents else ""
    ctx = _channel_context(cp)
    prompt = ((ctx + "\n\n" if ctx else "")
              + f"Тема миссии: {goal[:300]}\n\n"
              f"Сообщение в этом канале: \"{post[:400]}\"\n\n"
              "Связано ли это сообщение с темой миссии — с её предметом, сторонниками "
              "или противниками, причинами или последствиями" + hint + " — хотя бы "
              "косвенно, как новость, мнение, жалоба или эмоция? Ответь одним словом: ДА или НЕТ.")
    ans = generate_text(prompt, max_tokens=5, temperature=0.2, penalties=False).strip().lower()
    llm_yes = ("да" in ans) or ("yes" in ans) or ans.startswith("1")
    return (llm_yes or old_hit(post, ents)), ans[:12]


# ── Новый гейт (Stage 38) ─────────────────────────────────────────────────

def new_gate(goal, stance, post_raw, cp):
    post = textutil.judging_text(post_raw, "")
    if not post:
        return False, "clean:empty", "no"
    ents = textutil.keywords(goal, stance)
    aligned = _channel_alignment(cp, ents)
    prompt = _build_relevance_prompt(goal, stance, post, _channel_context(cp), "", ents, aligned)
    ans = generate_text(prompt, max_tokens=6, temperature=0.1, penalties=False)
    verdict = _parse_verdict(ans)
    kw = bool(ents) and textutil.keyword_hit(post, ents)
    if verdict == "no" and kw:
        verdict = "weak"
    relevant = verdict == "yes" or (verdict == "weak" and (aligned or kw))
    return relevant, (ans or "").strip()[:12], verdict


def main():
    cases_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cases.json"
    profiles_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/profiles.json"
    cases = json.loads(Path(cases_path).read_text())
    profiles = {p["channel_ref"].lower(): p for p in json.loads(Path(profiles_path).read_text())}

    print(f"{'мис':>3} {'канал':17} {'СТАРЫЙ':>22} {'НОВЫЙ':>28}  пост")
    print("-" * 118)
    old_yes = new_yes = 0
    for c in cases:
        cp = profiles.get((c.get("channel_ref") or "").lower())
        goal, stance = c.get("goal") or "", c.get("stance") or ""
        post = (c.get("post") or "").replace("\n", " ")

        o_votes = [old_gate(goal, stance, post, cp) for _ in range(RUNS)]
        o_rel = sum(1 for r, _ in o_votes if r)
        n_votes = [new_gate(goal, stance, post, cp) for _ in range(RUNS)]
        n_rel = sum(1 for r, _, _ in n_votes if r)
        verdicts = "/".join(v for _, _, v in n_votes)

        old_yes += o_rel > RUNS // 2
        new_yes += n_rel > RUNS // 2
        print(f"{c['mission_id']:>3} {(c.get('channel_ref') or '')[:17]:17} "
              f"{f'{o_rel}/{RUNS} llm={o_votes[0][1]}':>22} "
              f"{f'{n_rel}/{RUNS} {verdicts}':>28}  {post[:44]}")

    print("-" * 118)
    print(f"Постов признано пригодными: старый {old_yes}/{len(cases)} · новый {new_yes}/{len(cases)}")


if __name__ == "__main__":
    main()
