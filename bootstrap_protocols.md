# MORPHEUS: РЕФЕРЕНС ИНИЦИАЛИЗАЦИИ И СЕТЕВЫХ ПРОТОКОЛОВ

Документ содержит жесткие требования к структуре репозитория, конфигурации Docker Compose и контрактам обмена данными между микросервисами проекта MORPHEUS. Предназначен для ИИ-агентов генерации кода.

## 1. Схема файловой структуры проекта (Monorepo Layout)

ИИ-агент обязан строго придерживаться следующей структуры директорий при инициализации проекта:

```text
morpheus/
├── docker-compose.yml
├── .env.example
├── global_config/
│   └── personalities/
│       ├── agent_001_pavel.yaml
│       └── agent_002_elena.yaml
├── data_lake/
│   ├── raw_media/
│   └── logs/
├── daedalus/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── rbac.py
│       └── db_explorer.py
├── huginn/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── router.py
│       └── scrapers/
├── orpheus/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py
│       ├── media_enricher.py
│       └── guardrails.py
├── muninn/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       └── main.py
└── myrmidon/
    ├── Dockerfile
    ├── requirements.txt
    └── app/
        ├── main.py
        ├── proxy_manager.py
        └── mobile_drivers/

```

---

## 2. Шаблон базовой инфраструктуры (docker-compose.yml)

Конфигурация оркестрации сервисов. Использовать внутреннюю сеть Docker `morpheus_net` для изоляции от внешнего контура.

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: morpheus-postgres
    environment:
      POSTGRES_USER: ${DB_USER:-morpheus_admin}
      POSTGRES_PASSWORD: ${DB_PASSWORD:-morpheus_secure_pass}
      POSTGRES_DB: morpheus_db
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks:
      - morpheus_net

  redis:
    image: redis:7-alpine
    container_name: morpheus-redis
    ports:
      - "6379:6379"
    networks:
      - morpheus_net

  muninn:
    build: ./muninn
    container_name: morpheus-muninn
    ports:
      - "8002:8002"
    volumes:
      - chromadata:/app/chroma_db
    networks:
      - morpheus_net
    depends_on:
      - redis

  daedalus:
    build: ./daedalus
    container_name: morpheus-daedalus
    ports:
      - "8000:8000"
    volumes:
      - ./global_config:/app/global_config
    networks:
      - morpheus_net
    depends_on:
      - postgres

  huginn:
    build: ./huginn
    container_name: morpheus-huginn
    volumes:
      - ./data_lake:/app/data_lake
    networks:
      - morpheus_net
    depends_on:
      - redis

  orpheus:
    build: ./orpheus
    container_name: morpheus-orpheus
    ports:
      - "8001:8001"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    volumes:
      - ./data_lake:/app/data_lake
      - ./global_config:/app/global_config
    networks:
      - morpheus_net
    depends_on:
      - redis
      - muninn

  myrmidon:
    build: ./myrmidon
    container_name: morpheus-myrmidon
    volumes:
      - ./global_config:/app/global_config
    networks:
      - morpheus_net
    depends_on:
      - redis
      - postgres

volumes:
  pgdata:
  chromadata:

networks:
  morpheus_net:
    driver: bridge

```

---

## 3. Контракты обмена данными и API Протоколы

### 3.1. Шина данных Redis (Очереди Celery/PubSub)

#### Очередь сырых инфоповодов (`queue:raw_events`)

Формат задачи, которую **HUGINN** отправляет в Redis для **ORPHEUS**:

```json
{
  "event_id": "uuid4_string",
  "source_platform": "telegram",
  "source_target": "@channel_username",
  "post_id": "12345",
  "text_content": "Текст публикации, если есть",
  "media_type": "video", 
  "media_path": "/app/data_lake/raw_media/video_123.mp4",
  "layers": {
    "state": "Uzbekistan",
    "city": "Tashkent",
    "personal_tags": ["infrastructure", "urban"]
  },
  "timestamp": 1780732400
}

```

#### Очередь исполнительных задач (`queue:execution_tasks`)

Формат команды, которую **ORPHEUS** генерирует для воркеров **MYRMIDON**:

```json
{
  "task_id": "uuid4_string",
  "agent_id": "001",
  "target_platform": "instagram",
  "action_type": "comment",
  "target_url": "[https://www.instagram.com/p/C](https://www.instagram.com/p/C)_...",
  "text_to_publish": "Сгенерированный локальной ИИ текст ответа.",
  "parent_post_context": "Контекст поста для логов",
  "execution_delay_sec": 45
}

```

### 3.2. Внутренний REST API сервиса памяти MUNINN (ChromaDB Wrapper)

#### Поиск ассоциаций: `POST http://muninn:8002/api/v1/memory/search`

* **Payload:**

```json
{
  "agent_id": "001",
  "opponent_id": "@user_nickname",
  "query_text": "Текущий текст дискуссии для семантического поиска"
}

```

* **Response:**

```json
{
  "status": "success",
  "matches": [
    {
      "text": "Вырезка из прошлого спора: Бот утверждал Х, оппонент отвечал Y",
      "distance": 0.31
    }
  ]
}

```

#### Сохранение воспоминания: `POST http://muninn:8002/api/v1/memory/save`

* **Payload:**

```json
{
  "agent_id": "001",
  "opponent_id": "@user_nickname",
  "dialog_summary": "Краткая выжимка фактов из текущего диалога для сохранения в вектор"
}

```

---

## 4. Глобальные переменные окружения (.env.example)

```env
# База данных PostgreSQL
DB_USER=morpheus_admin
DB_PASSWORD=morpheus_secure_pass
DB_HOST=postgres
DB_PORT=5432

# Брокер Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Настройки ИИ-моделей (Ollama URL внутри контейнеров)
OLLAMA_BASE_URL=[http://host.docker.internal:11434](http://host.docker.internal:11434)
TEXT_MODEL_NAME=qwen2.5:3b
VISION_MODEL_NAME=moondream:latest

# Режимы работы системы
ENVIRONMENT=development # меняется на production при миграции на сервер
LOG_LEVEL=INFO

```
