# PROJECT MORPHEUS — Stage 21 Walkthrough

## Контекстный RAG-движок и бифуркация пайплайна

Документ описывает все изменения, внесённые в рамках Stage 21. Цель этапа —
радикально разделить **эпистемологию** (что рой *знает*) и **тактику** (против
чего рой *действует*):

```
HUGINN  →  scrape news  →  embed (nomic-embed-text)  →  cosine-дедупликация
                                                        →  MUNINN (KnowledgeFact, по слоям)

ORPHEUS →  mission против SocialPostTarget  →  RAG-поиск в MUNINN по подпискам агента
                                            →  инъекция фактов в промпт  →  MYRMIDON
```

---

## 0. Карта слоёв (Landscape Layers)

Единый словарь из 5 когнитивных слоёв, общий для HUGINN, MUNINN, AgentProfile и ORPHEUS:

```
global · regional · state · city · personal
```

Определён один раз в `daedalus/app/models.py` как `LANDSCAPE_LAYERS` и
переиспользуется во всех валидаторах и фронтенде.

---

## TASK 1 — Векторная инфраструктура и БД

### `docker-compose.yml`
- **PostgreSQL**: образ `postgres:15-alpine` → **`pgvector/pgvector:pg16`** (нативная
  поддержка типа `vector` для cosine-поиска).
- **daedalus**: добавлен `extra_hosts: host.docker.internal:host-gateway` — нужен
  для эмбеддинга вручную внесённых фактов через хостовый Ollama.
- **huginn**: добавлен `extra_hosts` (доступ к Ollama для эмбеддинга новостей) и
  монтирование `./global_config`.

### `.env`
- Добавлены переменные эмбеддинга:
  ```
  EMBED_MODEL_NAME=nomic-embed-text   # модель эмбеддинга (768-мерные вектора)
  EMBED_DIM=768
  ```
  ⚠️ Ollama — **хостовый**, не контейнер. Перед работой нужно один раз выполнить
  `ollama pull nomic-embed-text` на хосте.

### `daedalus/requirements.txt`
- Добавлено `pgvector==0.3.2` (SQLAlchemy-биндинги для типа `Vector` и оператора
  cosine-расстояния).

### `daedalus/app/models.py`
- Импорт `from pgvector.sqlalchemy import Vector`; константы `EMBED_DIM` и
  `LANDSCAPE_LAYERS`.
- **Новая модель `KnowledgeFact`** (таблица `knowledge_facts`):
  - `content`, `source_url`, `landscape_layer`, `embedding Vector(768)`;
  - `sources` (JSONB — список всех URL кластера), `source_count` (размер кластера);
  - `timestamp`, `created_at`, `updated_at`.
- **Новая модель `SocialPostTarget`** (таблица `social_post_targets`):
  `author`, `content`, `platform`, `url`, `status`.
- `AgentProfile` → новое поле **`context_subscriptions`** (JSONB-массив слоёв,
  по умолчанию `["global"]`).
- `ScrapingLandscape` → новое обязательное поле **`landscape_layer`** (default `global`).
- `Mission` → новое поле **`forced_context`** (Text, опционально).

### `daedalus/app/database.py`
- В `init_tables()`:
  - перед `create_all` выполняется `CREATE EXTENSION IF NOT EXISTS vector`;
  - после — создаётся **IVFFlat cosine-индекс** `idx_knowledge_facts_embedding`
    (`vector_cosine_ops`, lists=100) для быстрого приближённого ANN-поиска.

### `daedalus/app/landscape.py`
- `LandscapeCreateRequest` → поле `landscape_layer` стало **обязательным** с
  валидатором против `LANDSCAPE_LAYERS`.
- `LandscapeUpdateRequest`/`LandscapeResponse` дополнены `landscape_layer`.
- Эндпоинт `internal/sync-targets` теперь отдаёт `landscape_layer` по каждой цели,
  чтобы HUGINN знал слой для тегирования фактов.

---

## TASK 2 — Эмбеддинг HUGINN и кластеризация MUNINN

### `huginn/app/knowledge_ingest.py` (новый файл)
- `generate_embedding(text)` — POST к хостовому Ollama `/api/embeddings`
  (`nomic-embed-text`), проверка размерности 768.
- `ingest_knowledge(text, source_url, landscape_layer)` — эмбеддит и отправляет в
  Daedalus `/api/v1/knowledge/internal/ingest`. **Fail-soft**: любая ошибка сети/
  эмбеддинга логируется и проглатывается, чтобы не блокировать основной пайплайн.

### `huginn/app/scrapers/web_scraper.py`
- Из цели достаётся `landscape_layer` (`target_layer`).
- После публикации события вызывается `ingest_knowledge(...)` с заголовком статьи.

### `huginn/app/scrapers/tg_scraper.py`
- Из цели достаётся `channel_layer`.
- После публикации текст поста уходит в `ingest_knowledge(...)` (с реальным
  `https://t.me/<channel>/<post_id>` в качестве source_url).

### `daedalus/app/router_knowledge.py` (новый файл) — сторона дедупликации
- `POST /internal/ingest` (token) — **dedup-or-insert**:
  - ищет ближайший факт **в том же слое** по cosine-расстоянию (pgvector);
  - если `similarity ≥ 0.85` → **MERGE** (добавляет source_url, инкремент
    `source_count`, обновляет `updated_at`); канонический эмбеддинг/контент
    первого вхождения сохраняется ради стабильности;
  - иначе → **INSERT** нового кластера с тегом слоя.
- `daedalus/app/embeddings.py` (новый) — серверный helper эмбеддинга для ручной
  инъекции через UI.

---

## TASK 3 — RAG-движок ORPHEUS

### `orpheus/app/rag.py` (новый файл)
- `_embed(text)` — эмбеддинг поста через Ollama `nomic-embed-text`.
- `fetch_fresh_context(post_text, subscriptions, forced_context)`:
  - если задан **forced_context** → возвращается дословно, векторный поиск
    **полностью пропускается**;
  - иначе эмбеддит пост и вызывает Daedalus `/api/v1/knowledge/internal/rag-search`
    с подписками агента; отбрасывает факты ниже `RAG_MIN_SIMILARITY` (0.5).
- **Fail-soft**: при сбое возвращается пустой, но валидный контекст.

### `daedalus/app/router_knowledge.py` — сторона поиска
- `POST /internal/rag-search` (token) — векторный поиск по `knowledge_facts`,
  **фильтр строго по слоям из подписок агента**, сортировка по cosine-similarity,
  топ-N.

### `orpheus/app/persona.py`
- В `assemble_prompt` добавлено получение `context_subscriptions` из профиля и
  `forced_context` из события, затем вызов `fetch_fresh_context(...)`.
- В тело промпта добавлен блок:
  ```
  [Fresh Context Memory — Verified Facts (subscribed layers: ...)]
  ```
  с инструкцией использовать факты как ground truth, не цитируя дословно.

### `daedalus/app/souls.py`
- `context_subscriptions` добавлено в Create/Update/Response-схемы (с валидатором)
  и в `internal/profiles` (ORPHEUS получает подписки через кэш профилей).

### Forced Context для миссий
- `daedalus/app/router_missions.py` — `forced_context` в Create/Update/Response и
  в обработчиках.
- `daedalus/app/mission_control.py` — `forced_context` прокидывается в задачу и
  **вплетается в alpha-нарратив** (`_compose_payload_text`), давая реальный эффект
  в детерминированном пути миссий.

---

## TASK 4 — Когнитивный командный центр (Frontend)

### `daedalus/frontend/src/components/LandscapeManager.tsx`
- Константа `LAYERS`; стейт `newLayer`.
- **Обязательный дропдаун «Landscape Layer»** в модалке добавления источника.
- Новая колонка «Layer» с цветным `layer-pill` в таблице.

### `daedalus/frontend/src/components/SoulsContext.tsx`
- Поле `context_subscriptions` в интерфейсе/нормализации/сохранении.
- Хелпер `toggleSubscription`.
- В табе «Mission & Stance» — секция **«Context Subscriptions (RAG)»** с
  чекбоксами-пилюлями (Global/Regional/State/City/Personal).

### `daedalus/frontend/src/components/MissionDeck.tsx`
- Стейт `forcedContext`; сброс в `resetForm`; отправка `forced_context` в POST.
- Новое поле **«Forced Context (optional)»** — textarea с пояснением, что при
  заполнении ORPHEUS пропускает векторный поиск.

### `daedalus/frontend/src/components/MuninnExplorer.tsx` + `.css` (новые)
- Новый таб-дашборд памяти роя:
  - карточки статистики кластеров по слоям (кликабельный фильтр);
  - таблица `KnowledgeFacts` (слой, контент кластера, `×N` источников, дата);
  - кнопка **«Manually Inject Fact»** (модалка: контент + слой + source_url) —
    Daedalus эмбеддит и кластеризует через тот же путь дедупликации;
  - удаление факта.
- Глобальный стиль `.layer-pill` (используется и в LandscapeManager).

### `daedalus/frontend/src/App.tsx`
- Импорт `MuninnExplorer`, иконка `Brain`.
- Новый `activeView: 'muninn'`, кнопка навигации «Muninn Memory» в группе
  GATHERING, рендер компонента (через `display:none`-таб, сохраняющий state).

---

## Новые / изменённые API-эндпоинты

| Метод | Путь | Назначение | Доступ |
|------|------|-----------|--------|
| POST | `/api/v1/knowledge/internal/ingest` | dedup-or-insert факта | internal token |
| POST | `/api/v1/knowledge/internal/rag-search` | векторный поиск по слоям | internal token |
| GET  | `/api/v1/knowledge/facts` | список кластеров (фильтр по слою) | JWT `monitoring:view` |
| GET  | `/api/v1/knowledge/stats` | счётчики по слоям | JWT `monitoring:view` |
| POST | `/api/v1/knowledge/facts/inject` | ручная инъекция (серверный эмбеддинг) | JWT `agents:manage` |
| DELETE | `/api/v1/knowledge/facts/{id}` | удалить факт | JWT `agents:manage` |

---

## Миграция БД (pg15 → pg16)

Формат данных на диске у pg15 и pg16 несовместим, поэтому том `morpheus_pgdata`
был переинициализирован.

- Перед миграцией снят полный SQL-дамп: `backups/morpheus_db_pg15_20260612_115616.sql`.
- Выбран сценарий **fresh reset**: `docker compose down -v && docker compose up -d --build`.
- Итог: пустая БД, схема пересоздана, SuperAdmin пересеян, расширение `vector`
  и IVFFlat-индекс инициализированы автоматически.

---

## Верификация (выполнено вживую)

- PostgreSQL **16.14**, расширение **pgvector 0.8.2**.
- Созданы таблицы `knowledge_facts`, `social_post_targets`; колонка `embedding`
  имеет тип vector; ANN-индекс `idx_knowledge_facts_embedding` на месте.
- Новые колонки подтверждены: `agent_profiles.context_subscriptions`,
  `missions.forced_context`, `scraping_landscape.landscape_layer`.
- Все 7 контейнеров запущены, импорт-ошибок нет.
- Фронтенд: `tsc && vite build` без ошибок; SPA отдаётся `HTTP 200`; компонент
  Muninn присутствует в собранном бандле.
- **E2E-тест RAG** (синтетический 768-вектор):
  - вставка → факт #1 (city);
  - идентичный вектор → **MERGED** в #1, `source_count=2`, similarity 1.0;
  - другой вектор/слой → новый факт #2 (global);
  - RAG по `["city","state"]` → возвращает city-факт;
  - RAG по `["global"]` → **корректно отфильтровывает** city-факт.
  - Тестовые строки затем удалены.

---

## Как протестировать пайплайн и внести первую память

1. **Разовая настройка хоста:** `ollama pull nomic-embed-text`
   (без модели HUGINN молча пропускает ингест, а ручная инъекция вернёт `HTTP 503`).
2. **Первая память:** таб **Muninn Memory → Manually Inject Fact** — например,
   контент *«Tashkent's Yunusabad metro line was extended on 2026-06-01»*, слой `city`.
3. **Подписка агента:** **Souls → Mission & Stance → Context Subscriptions** —
   включить `city`.
4. **Поток:** по мере скрейпинга источников факты кластеризуются в Muninn Explorer
   (`×N` — слитые источники). Когда ORPHEUS отвечает на подходящий пост,
   `docker logs morpheus-orpheus` покажет *«Injected N fresh-context fact(s)»*, а в
   промпте появится блок `[Fresh Context Memory ...]`.

---

## Сводка новых файлов

```
daedalus/app/embeddings.py            # серверный helper эмбеддинга
daedalus/app/router_knowledge.py      # ingest/rag-search/facts/inject/stats
huginn/app/knowledge_ingest.py        # эмбеддинг + отправка в Daedalus
orpheus/app/rag.py                    # RAG-получение Fresh Context
daedalus/frontend/src/components/MuninnExplorer.tsx
daedalus/frontend/src/components/MuninnExplorer.css
walkthrough.md                        # этот документ
backups/morpheus_db_pg15_*.sql        # дамп БД перед миграцией
```

---

# PROJECT MORPHEUS — Stage 22 Walkthrough

## Глубокая авто-классификация знаний и принудительная изоляция пайплайна

Этап устраняет «утечку пайплайна» (генерические скрейперы слали новости в
очередь исполнения) и заменяет ручной одиночный слой на LLM-авто-классификацию
с множественными слоями.

```
HUGINN (RSS/Web/TG)  →  raw text + default_layers  →  DAEDALUS
DAEDALUS ingest:  qwen2.5:3b классификация (layers/categories/tags)
               →  nomic-embed-text эмбеддинг  →  pgvector cosine-дедуп  →  MUNINN
ORPHEUS RAG:  пересечение массивов landscape_layers ∩ context_subscriptions (JSONB ?|)
```

## TASK 1 — Мульти-слойная схема
- `models.py`: `KnowledgeFact.landscape_layer` (String) → **`landscape_layers`** (JSONB-массив);
  добавлены **`categories`** и **`tags`** (JSONB-массивы). `ScrapingLandscape.landscape_layer`
  → **`default_layers`** (JSONB-массив).
- `database.py`: добавлен **GIN-индекс** `idx_knowledge_facts_layers` для быстрого `?|`.
- Pydantic-схемы (`landscape.py`, `router_knowledge.py`) переведены на `List[str]`
  с валидацией ≥1 слоя.

## TASK 2 — LLM авто-классификатор
- Новый `daedalus/app/classifier.py` — async `auto_classify_text(text)`:
  вызывает Ollama `qwen2.5:3b` с `format:"json"`, строгим системным промптом и
  закрытым множеством слоёв; результат санитизируется (только валидные слои,
  дедуп, лимиты на categories/tags). **Fail-soft** → пустые массивы при сбое.
- `router_knowledge.py` `/internal/ingest` теперь **async**: сначала
  классификация → затем эмбеддинг (DAEDALUS генерирует сам) → дедуп/вставка.
  Слои = LLM-слои ∪ `default_layers` источника. При мердже массивы
  layers/categories/tags **объединяются** (union).

## TASK 3 — Принудительная изоляция (HUGINN)
- `web_scraper.py`, `tg_scraper.py`, `test_rss.py` (теперь полноценный RSS-скрейпер)
  **больше не вызывают** `publish_raw_event` (очередь исполнения / News Hub).
  Они шлют **только** в `/api/v1/knowledge/internal/ingest` с `default_layers`.
- TG-скрейпер перестал качать медиа (знаниям нужен только текст).
- RSS-скрейпер подключён в `main.py` (новая платформа `rss`), `feedparser.parse`
  выполняется в треде. `Dockerfile` копирует `test_rss.py` в образ.
- `gamma_noise` и `social_feed`/`scouting` оставлены как есть (легитимные
  источники для исполнения/таргетов).

## TASK 4 — Пересечение массивов в RAG
- `router_knowledge.py` `/internal/rag-search`: фильтр через JSONB-оператор
  **`?|`** — факт валиден, если ЛЮБОЙ из его `landscape_layers` пересекается с
  подписками агента (`KnowledgeFact.landscape_layers.op('?|')(array(subs))`).
- `orpheus/app/rag.py`: формирование Fresh Context учитывает массив слоёв и
  категории (`[STATE/CITY · politics | relevance ...]`).

## TASK 5 — Модернизация UI
- `MuninnExplorer.tsx`: `landscape_layers`/`categories`/`tags` рендерятся как
  цветные чипы; модалка ручной инъекции — **мультивыбор слоёв** (тоглы).
- `LandscapeManager.tsx`: `default_layers` — **группа чекбоксов** (мультивыбор);
  добавлена платформа `rss`; в таблице слои показаны чипами.

## Как структурирован промпт классификации
Системный промпт (`classifier.py`) задаёт роль «intelligence analyst» и требует
ВЕРНУТЬ ТОЛЬКО JSON ровно с тремя ключами:
```json
{ "layers": [...], "categories": [...], "tags": [...] }
```
- `layers` — из ЗАКРЫТОГО набора `global|regional|state|city|personal`, можно
  несколько (город+страна ⇒ `["state","city"]`); запрещено выдумывать слои.
- `categories` — широкие темы (politics, economy, infrastructure, security…).
- `tags` — конкретные сущности/места/персоны (lowercase).
Параметры запроса: `format:"json"`, `temperature:0.1`, `keep_alive:0` (выгрузка
из VRAM), таймаут 60с. Ответ парсится `json.loads` и санитизируется.

## Верификация (вживую, с реальным LLM)
- Схема: `landscape_layers/categories/tags` = jsonb, `default_layers` = jsonb;
  индексы IVFFlat + GIN созданы; 7 контейнеров подняты; импорт-ошибок нет.
- qwen2.5:3b классифицировал русский текст про метро Ташкента →
  `layers=["state","city"]`, `categories=["transportation","politics"]`, теги.
- Идентичный вектор → **MERGE** с union categories (+infrastructure) и tags.
- RAG `?|`: подписка `["state"]` находит факт `["state","city"]`; `["personal"]`
  → пусто. Фронтенд `tsc && vite build` без ошибок.
