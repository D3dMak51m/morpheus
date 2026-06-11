# CLAUDE.md — PROJECT MORPHEUS CORE RUNBOOK

## 1. ARCHITECTURE OVERVIEW & MICROSERVICES
PROJECT MORPHEUS is a persistent, containerized multi-agent AI swarm control center built on a decoupled identity pool, asynchronous message queues, and isolated Android Virtual Devices (AVD)[cite: 2, 3].

*   **DAEDALUS**: Command Center & Admin Portal. FastAPI backend (Python 3.11), React 18 + TypeScript + Vite frontend[cite: 2, 3]. Manages accounts, virtual device mapping, dynamic landscape monitoring, and LLM persona synthesis[cite: 2, 3].
*   **MYRMIDON**: Swarm Execution Layer. Python automation workers orchestrated via Appium, UiAutomator2, and `pure-python-adb`[cite: 2, 3]. Operates isolated Android эмуляторы[cite: 2, 3].
*   **HUGINN**: Intelligence Gathering Layer. Python async daemon utilizing `curl_cffi` (browser impersonation) and Telethon[cite: 2, 3]. Streams live intercepted news straight into Daedalus[cite: 2, 3].
*   **ORPHEUS**: Cognitive brain. Processes raw data streams, evaluates historical dialectics, and runs local LLM inference engines (`qwen2.5:3b` via Ollama)[cite: 2, 3].
*   **MUNINN**: Strategic semantic and associative storage node[cite: 2, 3].
*   **DATA STRATUM**: PostgreSQL (`morpheus_db`) for hard state; Redis for asynchronous broker communication (`queue:raw_events`, `queue:execution_tasks`)[cite: 2, 3].

---

## 2. STRICT OPERATIONAL & OPSEC MANDATES
Every modification to the codebase MUST strictly enforce these guidelines. Zero exceptions tolerated.

### 2.1. Android Interaction & Typing
*   **NO HIGH-LEVEL INJECTIONS**: `element.send_keys()` is strictly banned[cite: 2]. Using it exposes automation flags to anti-fraud systems[cite: 2].
*   **BIOMETRIC TYPING**: Text input must be executed character-by-character using coordinates tapping via W3C ActionChains or native Appium `UnicodeIME` state machine mechanics[cite: 2, 3].
*   **HUMANIZED DELAYS**: Implement Gaussian distribution delays between keystrokes ($\Delta t$):
    $$\Delta t = \max(0.04, 0.12 + 0.03 \cdot N(0,1)) \text{ seconds}$$
*   **GBOARD PUNCTUATION TRANSITIONS**: When typing non-alphanumeric symbols (`.`, `,`, `!`, `?`, `/`, `_`), the driver must explicitly tap the `?123` layout key, enter coordinates, and return to avoid char omission[cite: 3].

### 2.2. Virtual Device Fleet Control
*   **1 ACCOUNT = 1 DEVICE PROFILES**: Account swapping (logout/login rotation) on a single Android OS instance is an instaban pattern. Each social profile is hard-locked to a unique `device_id`[cite: 2, 3].
*   **HARDWARE SPOOFING LIFECYCLE**: When modifying `/system/build.prop`, you must discard the active ADB socket, execute `device.reboot()`, run an explicit polling state machine to re-query `client.devices()` to catch the fresh authenticated handle, and wait until `sys.boot_completed == 1` before yielding control to Appium[cite: 3].
*   **TELNET CONSOLE SNAPSHOTS**: Loading/saving device states (`idle_snap`) must be performed using a direct host socket connection over port `5554+` using `os.getenv("EMULATOR_CONSOLE_TOKEN")` for authentication[cite: 3]. Do NOT execute `emu` commands inside `device.shell()`[cite: 2, 3].

---

## 3. TECH STACK & SYSTEM CONTRACTS
*   **Backend Coding Standards**: Python 3.11 with strict type hinting (`typing`). Database transactions must run via SQLAlchemy 2.0 AsyncSession async blocks[cite: 2, 3].
*   **Frontend Coding Standards**: React 18, TypeScript, custom strict layouts. Never use conditional unmounting for primary administration tabs—utilize CSS `display: none` styling to preserve text areas and local view states[cite: 2, 3].
*   **Internal Communication**: Secured via `X-Internal-Token` headers and explicit Bearer JWT validations mapped across RBAC permission parameters[cite: 2, 3].

---

## 4. CODEBASE PATH MAP
*   `daedalus/app/models.py` — Database schema (SQLAlchemy ORM structural layer)[cite: 2, 3].
*   `daedalus/app/router_auth_factory.py` — Pyrogram in-memory 2FA handshakes & cookie importers[cite: 2, 3].
*   `daedalus/app/genesis_engine.py` — Local Ollama LLM persona synthesizer[cite: 2, 3].
*   `daedalus/frontend/src/components/` — React admin interfaces (AuthFactory, SoulsContext, DeviceGrid)[cite: 2, 3].
*   `myrmidon/app/adb_supervisor.py` — Reboot-resilient hardware management & telnet orchestrator[cite: 3].
*   `myrmidon/app/drivers/mobile_base.py` — Low-level W3C typing coordinate drivers and layout matrix[cite: 2, 3].
*   `huginn/app/main.py` & `/scrapers/` — Async скрапинг-воркеры (`curl_cffi`, Telethon)[cite: 2, 3].

---

## 5. CORE PRODUCTION COMMANDS

### 5.1. Containers Deployment & Management
```bash
# Build and update targeted microservices
docker compose build daedalus myrmidon huginn

# Deploy fleet containers in background mode
docker compose up -d

# Restart atomic worker nodes
docker compose restart myrmidon

# Tail live systems execution streams
docker compose logs -f --tail=100 myrmidon

```

### 5.2. Testing & Diagnostics Checks

```bash
# Check global system health-check response
curl -s http://localhost:8000/api/v1/health

# Trigger forced target catalog broadcast across Huginn networks
curl -X POST http://localhost:8000/api/v1/huginn/force-sync

```