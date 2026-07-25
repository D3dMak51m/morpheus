"""
Tests for the SIMULATION polygon (isolated test environment).

Isolation is enforced structurally by this suite: the test database contains
**only** the ``sim_*`` tables (``SimBase.metadata``). No production table exists
at all, so if any simulation endpoint ever wrote to ``missions``, ``souls_accounts``
or ``knowledge_facts``, the call would blow up with "no such table" instead of
silently touching real data.

Run inside the daedalus container:
    docker compose exec daedalus python -m pytest tests -q
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sim_generator, sim_landscape                      # noqa: E402
from app.database import get_db                                   # noqa: E402
from app.models import Base as ProductionBase                     # noqa: E402
from app.models_simulation import (                               # noqa: E402
    SimBase, SimChannel, SimComment, SimEvent, SimJob, SimKnowledge, SimPost,
)
from app.router_simulation import router as simulation_router, edit_perm, view_perm  # noqa: E402


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SimBase.metadata.create_all(bind=engine)          # ONLY the simulation namespace
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture()
def client(session_factory):
    app = FastAPI()
    app.include_router(simulation_router)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    class _User:
        id, username, is_superadmin = 1, "morpheus", True

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[view_perm] = lambda: _User()
    app.dependency_overrides[edit_perm] = lambda: _User()
    return TestClient(app)


@pytest.fixture()
def world(client):
    return client.get("/api/v1/simulation/state").json()["world"]


def make_channel(client, username="@test_channel", title="Тестовый канал"):
    r = client.post("/api/v1/simulation/channels", json={"username": username, "title": title})
    assert r.status_code == 201, r.text
    return r.json()


def make_post(client, channel_id, text="Тестовый пост про транспорт."):
    r = client.post("/api/v1/simulation/posts", json={"channel_id": channel_id, "text": text})
    assert r.status_code == 201, r.text
    return r.json()


def make_persona(client, key="p1", codename="Альфа"):
    r = client.post("/api/v1/simulation/personas", json={
        "agent_key": key, "codename": codename, "caste": "alpha",
        "interests": ["транспорт"], "style": {"tone_level": 7}})
    assert r.status_code == 201, r.text
    return r.json()


def make_account(client, handle="@user1", name="Пользователь"):
    r = client.post("/api/v1/simulation/accounts", json={"handle": handle, "display_name": name})
    assert r.status_code == 201, r.text
    return r.json()


# ── Isolation ──────────────────────────────────────────────────────────────

def test_simulation_namespace_is_disjoint_from_production():
    sim_tables = set(SimBase.metadata.tables)
    prod_tables = set(ProductionBase.metadata.tables)
    assert sim_tables & prod_tables == set(), "simulation must not reuse a production table"
    assert all(t.startswith("sim_") for t in sim_tables)


def test_no_simulation_table_references_production():
    for table in SimBase.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.column.table.name.startswith("sim_"), (
                f"{table.name} has a FK into production table {fk.column.table.name}")


def test_full_workflow_touches_no_production_table(client):
    """The DB has zero production tables — a stray write would raise here."""
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    account = make_account(client)
    client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "author_kind": "account", "account_id": account["id"],
        "text": "ручной коммент"})
    client.post(f"/api/v1/simulation/posts/{post['id']}/reactions", json={"emoji": "👍", "delta": 3})
    assert client.get("/api/v1/simulation/state").status_code == 200


# ── Worlds ─────────────────────────────────────────────────────────────────

def test_state_bootstraps_a_default_world(client):
    body = client.get("/api/v1/simulation/state").json()
    assert body["world"]["id"] >= 1
    assert body["channels"] == [] and body["personas"] == []
    assert "available" in body["engine"]


def test_seed_creates_a_playable_environment(client, world):
    r = client.post(f"/api/v1/simulation/worlds/{world['id']}/seed")
    assert r.status_code == 200, r.text
    created = r.json()["created"]
    assert created["channels"] == 2 and created["posts"] == 3
    assert created["personas"] == 2 and created["accounts"] == 3
    state = client.get("/api/v1/simulation/state").json()
    assert len(state["channels"]) == 2
    assert state["world"]["counts"]["posts"] == 3


def test_reset_wipes_content_but_keeps_the_world(client, world):
    client.post(f"/api/v1/simulation/worlds/{world['id']}/seed")
    r = client.post(f"/api/v1/simulation/worlds/{world['id']}/reset")
    assert r.status_code == 200
    assert r.json()["counts"]["channels"] == 0
    assert client.get("/api/v1/simulation/state").json()["world"]["id"] == world["id"]


def test_second_world_is_separate(client, world):
    other = client.post("/api/v1/simulation/worlds", json={"name": "Второй полигон"}).json()
    make_channel(client)  # lands in the default world
    assert client.get(f"/api/v1/simulation/channels?world_id={other['id']}").json()["channels"] == []
    assert len(client.get(f"/api/v1/simulation/channels?world_id={world['id']}").json()["channels"]) == 1


# ── Channels & posts ───────────────────────────────────────────────────────

def test_channel_crud_and_duplicate_guard(client):
    channel = make_channel(client, "no_at_sign", "Канал")
    assert channel["username"] == "@no_at_sign"
    dup = client.post("/api/v1/simulation/channels",
                      json={"username": "@no_at_sign", "title": "Другой"})
    assert dup.status_code == 409

    upd = client.put(f"/api/v1/simulation/channels/{channel['id']}", json={
        "username": "@renamed", "title": "Переименован", "subscribers": 500,
        "tags": ["город"], "avatar_color": "teal"})
    assert upd.status_code == 200 and upd.json()["subscribers"] == 500

    assert client.delete(f"/api/v1/simulation/channels/{channel['id']}").status_code == 200
    assert client.get("/api/v1/simulation/channels").json()["channels"] == []


def test_post_edit_records_revision_and_can_be_restored(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"], "исходный текст")

    edited = client.put(f"/api/v1/simulation/posts/{post['id']}",
                        json={"text": "изменённый текст", "note": "правка оператора"}).json()
    assert edited["text"] == "изменённый текст"

    revisions = client.get(f"/api/v1/simulation/posts/{post['id']}/revisions").json()["revisions"]
    assert len(revisions) == 1 and revisions[0]["snapshot"]["text"] == "исходный текст"

    restored = client.post(
        f"/api/v1/simulation/posts/{post['id']}/revisions/{revisions[0]['id']}/restore").json()
    assert restored["text"] == "исходный текст"
    # the restore itself is also journalled
    assert len(client.get(f"/api/v1/simulation/posts/{post['id']}/revisions").json()["revisions"]) == 2


def test_post_can_move_to_another_channel(client):
    a, b = make_channel(client, "@a", "A"), make_channel(client, "@b", "B")
    post = make_post(client, a["id"])
    moved = client.put(f"/api/v1/simulation/posts/{post['id']}", json={"channel_id": b["id"]}).json()
    assert moved["channel_id"] == b["id"] and moved["channel"]["username"] == "@b"


def test_post_media_reactions_and_time_are_editable(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    when = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc).isoformat()
    body = {
        "media": [{"kind": "image", "url": "http://x/y.jpg", "caption": "фото"},
                  {"kind": "link", "url": "http://news", "name": "ссылка"}],
        "reactions": {"🔥": 7}, "views": 999, "pinned": True,
        "author_label": "Редакция", "published_at": when,
    }
    out = client.put(f"/api/v1/simulation/posts/{post['id']}", json=body).json()
    assert len(out["media"]) == 2 and out["reactions"] == {"🔥": 7}
    assert out["pinned"] is True and out["author_label"] == "Редакция"
    assert out["published_at"].startswith("2024-05-01")


def test_post_reactions_endpoint_increments_and_clears(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    url = f"/api/v1/simulation/posts/{post['id']}/reactions"
    assert client.post(url, json={"emoji": "👍", "delta": 2}).json()["reactions"] == {"👍": 2}
    assert client.post(url, json={"emoji": "👍", "delta": -2}).json()["reactions"] == {}


# ── Comments (Telegram-like tree) ──────────────────────────────────────────

def test_comment_tree_reply_and_branch_delete(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    account = make_account(client)

    root = client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "author_kind": "account", "account_id": account["id"],
        "text": "корневой"}).json()
    child = client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "parent_id": root["id"], "author_kind": "external",
        "author_label": "Прохожий", "text": "ответ"}).json()
    grandchild = client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "parent_id": child["id"], "author_kind": "external",
        "author_label": "Ещё один", "text": "ответ на ответ"}).json()

    thread = client.get(f"/api/v1/simulation/posts/{post['id']}").json()
    assert thread["post"]["comments_count"] == 3
    assert {c["id"]: c["parent_id"] for c in thread["comments"]} == {
        root["id"]: None, child["id"]: root["id"], grandchild["id"]: child["id"]}

    client.delete(f"/api/v1/simulation/comments/{child['id']}")
    left = client.get(f"/api/v1/simulation/posts/{post['id']}").json()["comments"]
    assert [c["id"] for c in left] == [root["id"]]     # branch removed with its replies


def test_comment_author_and_status_are_editable(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    account = make_account(client)
    persona = make_persona(client)

    comment = client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "author_kind": "account", "account_id": account["id"],
        "text": "первый вариант", "status": "draft"}).json()
    assert comment["author_label"] == "Пользователь"

    swapped = client.put(f"/api/v1/simulation/comments/{comment['id']}", json={
        "author_kind": "persona", "persona_id": persona["id"], "account_id": None,
        "text": "переписано вручную"}).json()
    assert swapped["author_label"] == "Альфа" and swapped["text"] == "переписано вручную"

    published = client.post(f"/api/v1/simulation/comments/{comment['id']}/publish").json()
    assert published["status"] == "published" and published["published_at"]


def test_comment_requires_a_real_executor(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    assert client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "author_kind": "account", "text": "нет аккаунта"}).status_code == 400
    assert client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "author_kind": "persona", "text": "нет агента"}).status_code == 400


def test_comment_reactions(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    account = make_account(client)
    c = client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "author_kind": "account", "account_id": account["id"],
        "text": "текст"}).json()
    out = client.post(f"/api/v1/simulation/comments/{c['id']}/reactions",
                      json={"emoji": "❤️", "delta": 5}).json()
    assert out["reactions"] == {"❤️": 5}


# ── Accounts / personas ────────────────────────────────────────────────────

def test_account_is_a_manual_executor_and_persona_is_ai(client):
    account = make_account(client)
    assert account["initials"] == "ПО"
    persona = make_persona(client)
    assert persona["caste"] == "alpha" and persona["agent_key"] == "p1"


def test_persona_export_shapes_a_production_soul_draft(client):
    persona = client.post("/api/v1/simulation/personas", json={
        "agent_key": "tested_1", "codename": "Испытанный", "full_name": "Иван Иванов",
        "caste": "beta", "core_mission": "тестовая цель", "interests": ["город"],
        "style": {"tone_level": 8, "aggression": 6, "quirks": ["пишет без точек"]},
        "settings": {"behavioral_rules": {"max_posts_per_hour": 4}}}).json()
    exported = client.get(f"/api/v1/simulation/personas/{persona['id']}/export").json()
    assert exported["agent_id"] == "tested_1"
    assert exported["communication_style"]["tone_level"] == 8
    assert exported["communication_style"]["quirks"] == ["пишет без точек"]
    assert exported["behavioral_rules"] == {"max_posts_per_hour": 4}
    assert exported["source"] == "simulation"


def test_persona_edits_persist(client):
    persona = make_persona(client)
    updated = client.put(f"/api/v1/simulation/personas/{persona['id']}", json={
        "agent_key": "p1", "codename": "Альфа-2", "caste": "gamma",
        "interests": ["тарифы"], "style": {"aggression": 9},
        "system_prompt": "Ты — сварливый сосед."}).json()
    assert updated["codename"] == "Альфа-2" and updated["caste"] == "gamma"
    assert updated["system_prompt"] == "Ты — сварливый сосед."


# ── Missions (simulation-only) ─────────────────────────────────────────────

def test_mission_groups_agents_and_runs_only_in_simulation(client, monkeypatch):
    started: list[int] = []
    monkeypatch.setattr(sim_generator, "run_job_async", lambda job_id: started.append(job_id))

    channel = make_channel(client)
    post = make_post(client, channel["id"])
    a1 = make_persona(client, "m1", "Первый")
    a2 = make_persona(client, "m2", "Второй")

    mission = client.post("/api/v1/simulation/missions", json={
        "title": "Тестовый сценарий", "goal": "проверить агентов",
        "stance": "электротранспорт — это хорошо", "tactic": "soft_support",
        "agent_ids": [a1["id"], a2["id"]]}).json()
    assert len(mission["agents"]) == 2

    run = client.post(f"/api/v1/simulation/missions/{mission['id']}/run",
                      json={"post_id": post["id"], "per_agent": 2, "mode": "generate"})
    assert run.status_code == 200, run.text
    job = run.json()
    assert job["total"] == 4 and job["mission_id"] == mission["id"]
    assert started == [job["id"]]                     # ran through the sim job runner only


def test_paused_mission_refuses_to_run(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    persona = make_persona(client)
    mission = client.post("/api/v1/simulation/missions", json={
        "title": "На паузе", "status": "paused", "agent_ids": [persona["id"]]}).json()
    r = client.post(f"/api/v1/simulation/missions/{mission['id']}/run", json={"post_id": post["id"]})
    assert r.status_code == 400


def test_mission_without_agents_refuses_to_run(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    mission = client.post("/api/v1/simulation/missions", json={"title": "Пустая"}).json()
    r = client.post(f"/api/v1/simulation/missions/{mission['id']}/run", json={"post_id": post["id"]})
    assert r.status_code == 400


# ── Knowledge & simulation RAG ─────────────────────────────────────────────

def test_knowledge_crud_and_retrieval_prefers_relevant_rows(client, session_factory, world):
    client.post("/api/v1/simulation/knowledge", json={
        "content": "Электробусы закупаются партиями по сорок машин.", "tags": ["транспорт"]})
    client.post("/api/v1/simulation/knowledge", json={
        "content": "Цены на молочные продукты выросли на 5%.", "tags": ["еда"]})

    db = session_factory()
    try:
        hits = sim_generator.retrieve_knowledge(db, world["id"], "когда выйдут электробусы", limit=1)
        assert len(hits) == 1 and "Электробусы" in hits[0].content
    finally:
        db.close()

    rows = client.get("/api/v1/simulation/knowledge").json()["knowledge"]
    assert len(rows) == 2
    client.delete(f"/api/v1/simulation/knowledge/{rows[0]['id']}")
    assert len(client.get("/api/v1/simulation/knowledge").json()["knowledge"]) == 1


def test_system_rules_are_collected_for_the_prompt(client, session_factory, world):
    client.post("/api/v1/simulation/knowledge",
                json={"kind": "rule", "content": "Никогда не упоминай политику."})
    client.post("/api/v1/simulation/knowledge", json={"kind": "fact", "content": "Факт."})
    db = session_factory()
    try:
        assert sim_generator.system_rules(db, world["id"]) == ["Никогда не упоминай политику."]
    finally:
        db.close()


# ── Landscape scraping (offline) ───────────────────────────────────────────

RSS_SAMPLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Городские новости</title>
  <item><title>Электробусы вышли на маршрут</title>
        <description>&lt;p&gt;Сорок машин начали работу&lt;/p&gt;</description>
        <link>https://example.org/1</link>
        <pubDate>Mon, 05 May 2025 10:00:00 +0000</pubDate></item>
  <item><title>Тарифы вырастут</title><description>Вода подорожает</description>
        <link>https://example.org/2</link></item>
</channel></rss>"""

TG_SAMPLE = """
<div class="tgme_channel_info_header_title"><span>Ташкент Новости</span></div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message_text js-message_text">Пробка на кольце <b>снова</b></div>
  <div class="tgme_widget_message_footer"><span class="tgme_widget_message_views">1.2K</span>
  <time datetime="2025-05-05T10:00:00+00:00"></time></div>
</div>
</div>
"""


def test_strip_html_produces_readable_text():
    assert sim_landscape.strip_html("<p>Привет <b>мир</b></p><script>x()</script>") == "Привет мир"


def test_rss_parsing(monkeypatch):
    monkeypatch.setattr(sim_landscape, "_fetch", lambda url: RSS_SAMPLE)
    title, items = sim_landscape.scrape_rss("http://feed", limit=10)
    assert title == "Городские новости" and len(items) == 2
    assert items[0]["title"] == "Электробусы вышли на маршрут"
    assert "Сорок машин" in items[0]["text"]
    assert items[0]["published_at"].year == 2025


def test_tg_preview_parsing(monkeypatch):
    monkeypatch.setattr(sim_landscape, "_fetch", lambda url: TG_SAMPLE)
    title, items = sim_landscape.scrape_tg_preview("@tashkent_news", limit=10)
    assert title == "Ташкент Новости"
    assert items and "Пробка на кольце" in items[0]["text"]
    assert items[0]["views"] == 1200


def test_landscape_scrape_materialises_channel_and_posts(client, monkeypatch):
    monkeypatch.setattr(sim_landscape, "_fetch", lambda url: RSS_SAMPLE)
    r = client.post("/api/v1/simulation/landscape/scrape",
                    json={"kind": "rss", "url": "http://feed", "limit": 10})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["status"] == "ok" and report["posts"] == 2

    posts = client.get(f"/api/v1/simulation/posts?channel_id={report['channel_id']}").json()["posts"]
    assert len(posts) == 2
    assert any("Электробусы" in p["text"] for p in posts)
    assert any(m["kind"] == "link" for m in posts[0]["media"])
    # the ad-hoc scrape is remembered as a reusable source
    assert client.get("/api/v1/simulation/landscape").json()["sources"][0]["url"] == "http://feed"


def test_landscape_scrape_as_knowledge(client, monkeypatch):
    monkeypatch.setattr(sim_landscape, "_fetch", lambda url: RSS_SAMPLE)
    r = client.post("/api/v1/simulation/landscape/scrape", json={
        "kind": "rss", "url": "http://feed", "as_knowledge": True, "tags": ["новости"]}).json()
    assert r["knowledge"] == 2 and r["posts"] == 0
    rows = client.get("/api/v1/simulation/knowledge?kind=news").json()["knowledge"]
    assert len(rows) == 2 and rows[0]["origin"] == "scrape:rss"


def test_landscape_scrape_reports_failure_without_crashing(client, monkeypatch):
    def boom(url):
        raise RuntimeError("сеть недоступна")
    monkeypatch.setattr(sim_landscape, "_fetch", boom)
    r = client.post("/api/v1/simulation/landscape/scrape", json={"kind": "rss", "url": "http://x"})
    assert r.status_code == 200 and r.json()["status"] == "error"
    events = client.get("/api/v1/simulation/events?kind=landscape").json()["events"]
    assert events[0]["status"] == "error"


# ── Generation (LLM stubbed — no GPU in tests) ─────────────────────────────

def test_generate_one_stores_prompt_rag_and_publishes(client, monkeypatch):
    captured = {}

    def fake_generation(payload, timeout=None):
        captured.update(payload)
        return {"status": "ok", "text": "Электробусы — это давно пора.",
                "prompt": "PROMPT", "guardrail": "passed", "model": "test-model"}

    monkeypatch.setattr(sim_generator, "request_generation", fake_generation)

    channel = make_channel(client)
    post = make_post(client, channel["id"], "В городе запускают электробусы")
    persona = make_persona(client)
    client.post("/api/v1/simulation/knowledge",
                json={"content": "Электробусы закупили в количестве 40 машин.", "tags": ["транспорт"]})

    r = client.post("/api/v1/simulation/generate", json={
        "post_id": post["id"], "persona_id": persona["id"], "mode": "generate_publish"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["comment"]["status"] == "published" and body["comment"]["origin"] == "ai"
    assert body["comment"]["author_label"] == "Альфа"
    assert body["prompt"] == "PROMPT"
    assert body["rag"] and "Электробусы закупили" in body["rag"][0]["content"]

    # the persona travelled INLINE — no production profile lookup is possible
    assert captured["persona"]["agent_key"] == "p1"
    assert captured["post"]["text"] == "В городе запускают электробусы"
    assert captured["knowledge"][0]["content"].startswith("Электробусы закупили")


def test_generate_draft_mode_does_not_publish(client, monkeypatch):
    monkeypatch.setattr(sim_generator, "request_generation",
                        lambda payload, timeout=None: {"status": "ok", "text": "черновик", "prompt": ""})
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    persona = make_persona(client)
    out = client.post("/api/v1/simulation/generate", json={
        "post_id": post["id"], "persona_id": persona["id"], "mode": "draft"}).json()
    assert out["comment"]["status"] == "draft" and out["comment"]["published_at"] is None


def test_generate_failure_is_recorded_not_faked(client, monkeypatch):
    monkeypatch.setattr(sim_generator, "request_generation", lambda payload, timeout=None: {
        "status": "error", "text": "", "reason": "orpheus_timeout (180s)"})
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    persona = make_persona(client)
    out = client.post("/api/v1/simulation/generate", json={
        "post_id": post["id"], "persona_id": persona["id"]}).json()
    assert out["status"] == "error" and "orpheus_timeout" in out["reason"]
    assert out["comment"]["status"] == "error"
    events = client.get("/api/v1/simulation/events?kind=comment").json()["events"]
    assert events[0]["status"] == "error"


def test_reply_generation_uses_the_branch_as_context(client, monkeypatch):
    captured = {}

    def fake_generation(payload, timeout=None):
        captured.update(payload)
        return {"status": "ok", "text": "ответ агента", "prompt": ""}

    monkeypatch.setattr(sim_generator, "request_generation", fake_generation)
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    account = make_account(client)
    persona = make_persona(client)
    human = client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "author_kind": "account", "account_id": account["id"],
        "text": "а зачем это вообще нужно?"}).json()

    out = client.post("/api/v1/simulation/generate", json={
        "post_id": post["id"], "persona_id": persona["id"], "parent_id": human["id"]}).json()
    assert out["comment"]["parent_id"] == human["id"]
    assert captured["mode"] == "reply"
    assert captured["incoming"]["text"] == "а зачем это вообще нужно?"


def test_generate_post_writes_channel_material(client, monkeypatch):
    captured = {}

    def fake_generation(payload, timeout=None):
        captured.update(payload)
        return {"status": "ok", "text": "Мэрия отчиталась о запуске сорока электробусов.", "prompt": "P"}

    monkeypatch.setattr(sim_generator, "request_generation", fake_generation)
    channel = make_channel(client)
    persona = make_persona(client)

    r = client.post("/api/v1/simulation/generate/post", json={
        "channel_id": channel["id"], "persona_id": persona["id"],
        "topic": "запуск электробусов", "kind": "post", "create": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["post"]["text"].startswith("Мэрия отчиталась")
    assert body["post"]["source"] == "generated"
    assert captured["mode"] == "post" and captured["post"]["text"] == "запуск электробусов"

    posts = client.get(f"/api/v1/simulation/posts?channel_id={channel['id']}").json()["posts"]
    assert len(posts) == 1


def test_generate_article_can_return_text_without_creating_a_post(client, monkeypatch):
    monkeypatch.setattr(sim_generator, "request_generation",
                        lambda payload, timeout=None: {"status": "ok", "text": "Длинный материал…", "prompt": ""})
    channel = make_channel(client)
    body = client.post("/api/v1/simulation/generate/post", json={
        "channel_id": channel["id"], "kind": "article", "create": False}).json()
    assert body["status"] == "ok" and body["post"] is None
    assert client.get(f"/api/v1/simulation/posts?channel_id={channel['id']}").json()["posts"] == []


def test_import_rejects_an_unknown_source(client):
    r = client.post("/api/v1/simulation/knowledge/import", json={"what": "everything"})
    assert r.status_code == 400 and "history" in r.json()["detail"]


# ── Mass generation runner ─────────────────────────────────────────────────

def test_batch_endpoint_requires_someone_to_act(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    assert client.post("/api/v1/simulation/generate/batch",
                       json={"post_id": post["id"], "count": 3}).status_code == 400


def test_batch_with_accounts_requires_texts(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    account = make_account(client)
    r = client.post("/api/v1/simulation/generate/batch", json={
        "post_id": post["id"], "count": 2, "account_ids": [account["id"]]})
    assert r.status_code == 400 and "тексты" in r.json()["detail"]


def test_mass_generation_runs_agents_and_accounts_together(
        client, session_factory, monkeypatch, world):
    """The real batch runner, with the LLM stubbed and its own DB session."""
    calls = {"n": 0}

    def fake_generation(payload, timeout=None):
        calls["n"] += 1
        return {"status": "ok", "text": f"реплика агента {calls['n']}", "prompt": "P",
                "guardrail": "passed"}

    monkeypatch.setattr(sim_generator, "request_generation", fake_generation)
    monkeypatch.setattr("app.database.SessionLocal", session_factory)

    channel = make_channel(client)
    post = make_post(client, channel["id"])
    p1, p2 = make_persona(client, "b1", "Первый"), make_persona(client, "b2", "Второй")
    acc = make_account(client)

    started: list[int] = []
    monkeypatch.setattr(sim_generator, "run_job_async", lambda job_id: started.append(job_id))
    job = client.post("/api/v1/simulation/generate/batch", json={
        "post_id": post["id"], "count": 6, "mode": "generate_publish",
        "persona_ids": [p1["id"], p2["id"]], "account_ids": [acc["id"]],
        "account_texts": ["ну наконец-то", "посмотрим"], "order": "round_robin"}).json()
    assert job["total"] == 6 and started == [job["id"]]

    sim_generator._run_job(job["id"])                 # run it inline, deterministically

    db = session_factory()
    try:
        finished = db.get(SimJob, job["id"])
        assert finished.status == "done"
        assert finished.done == 6 and finished.failed == 0
        comments = db.query(SimComment).filter(SimComment.post_id == post["id"]).all()
        assert len(comments) == 6
        assert all(c.status == "published" for c in comments)
        # round-robin really mixed AI agents and manual accounts
        assert {c.author_kind for c in comments} == {"persona", "account"}
        assert {c.origin for c in comments} == {"ai", "manual"}
        assert sum(1 for c in comments if c.author_kind == "persona") == 4
        events = db.query(SimEvent).filter(SimEvent.job_id == job["id"]).all()
        assert len(events) >= 6
    finally:
        db.close()


def test_mass_generation_records_engine_failures(client, session_factory, monkeypatch):
    monkeypatch.setattr(sim_generator, "request_generation", lambda payload, timeout=None: {
        "status": "error", "text": "", "reason": "redis_unavailable"})
    monkeypatch.setattr("app.database.SessionLocal", session_factory)
    monkeypatch.setattr(sim_generator, "run_job_async", lambda job_id: None)

    channel = make_channel(client)
    post = make_post(client, channel["id"])
    persona = make_persona(client)
    job = client.post("/api/v1/simulation/generate/batch", json={
        "post_id": post["id"], "count": 2, "persona_ids": [persona["id"]]}).json()

    sim_generator._run_job(job["id"])
    db = session_factory()
    try:
        finished = db.get(SimJob, job["id"])
        assert finished.status == "done" and finished.failed == 2 and finished.done == 0
        assert all(c.status == "error" for c in db.query(SimComment).all())
    finally:
        db.close()


def test_batch_no_production_rate_limits_apply(client, session_factory, monkeypatch):
    """50 comments on one post in one run — impossible under production caps."""
    monkeypatch.setattr(sim_generator, "request_generation",
                        lambda payload, timeout=None: {"status": "ok", "text": "ок", "prompt": ""})
    monkeypatch.setattr("app.database.SessionLocal", session_factory)
    monkeypatch.setattr(sim_generator, "run_job_async", lambda job_id: None)

    channel = make_channel(client)
    post = make_post(client, channel["id"])
    persona = make_persona(client)
    job = client.post("/api/v1/simulation/generate/batch", json={
        "post_id": post["id"], "count": 50, "persona_ids": [persona["id"]]}).json()
    sim_generator._run_job(job["id"])

    db = session_factory()
    try:
        assert db.query(SimComment).filter(SimComment.post_id == post["id"]).count() == 50
    finally:
        db.close()


# ── Activity feed ──────────────────────────────────────────────────────────

def test_events_are_written_for_every_action_and_filterable(client):
    channel = make_channel(client)
    post = make_post(client, channel["id"])
    account = make_account(client)
    client.post("/api/v1/simulation/comments", json={
        "post_id": post["id"], "author_kind": "account", "account_id": account["id"],
        "text": "коммент"})

    kinds = {e["kind"] for e in client.get("/api/v1/simulation/events").json()["events"]}
    assert {"channel", "post", "account", "comment"} <= kinds

    only_posts = client.get("/api/v1/simulation/events?kind=post").json()["events"]
    assert only_posts and all(e["kind"] == "post" for e in only_posts)

    by_post = client.get(f"/api/v1/simulation/events?post_id={post['id']}").json()["events"]
    assert all(e["post_id"] == post["id"] for e in by_post)

    assert client.delete("/api/v1/simulation/events").status_code == 200
    assert client.get("/api/v1/simulation/events").json()["events"] == []


def test_inspector_dumps_raw_state(client):
    channel = make_channel(client)
    out = client.get(f"/api/v1/simulation/inspect/channel/{channel['id']}").json()
    assert out["table"] == "sim_channels" and out["data"]["username"] == "@test_channel"
    assert client.get("/api/v1/simulation/inspect/nope/1").status_code == 400


def test_health_reports_the_isolated_queue(client):
    body = client.get("/api/v1/simulation/health").json()
    assert body["isolated"] is True and body["queue"] == "queue:sim_gen"
