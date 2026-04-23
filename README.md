# 🤖 Conversational Task Assistant

A smart, event-driven conversational task manager for Telegram. The bot lets you
add tasks in plain natural language, tracks your progress throughout the day,
sends risk-aware nudges as deadlines approach, offers LLM-powered help when
you're stuck, and delivers an automated morning briefing every day.

---

## 📋 Table of Contents

1. [System Requirements](#1-system-requirements)
2. [Clone the Repository](#2-clone-the-repository)
3. [Create the Python Environment](#3-create-the-python-environment)
4. [Install Python Dependencies](#4-install-python-dependencies)
5. [Spin Up Infrastructure with Docker](#5-spin-up-infrastructure-with-docker)
6. [Configure Environment Variables](#6-configure-environment-variables)
7. [Create a Telegram Bot](#7-create-a-telegram-bot)
8. [Create a Discord Bot](#8-create-a-discord-bot)
9. [Choose & Configure an LLM Provider](#9-choose--configure-an-llm-provider)
   - [Option A — Ollama (Local, Free)](#option-a--ollama-local-free)
   - [Option B — HuggingFace (Local, Free)](#option-b--huggingface-local-free)
   - [Option C — OpenAI](#option-c--openai)
   - [Option D — Google Gemini](#option-d--google-gemini)
   - [Option E — Anthropic Claude](#option-e--anthropic-claude)
10. [Run Database Migrations](#10-run-database-migrations)
11. [Start the Application](#11-start-the-application)
12. [Test the Bot](#12-test-the-bot)
13. [Verify Everything Is Working](#13-verify-everything-is-working)
14. [Bot Commands Reference](#14-bot-commands-reference)
15. [Architecture Overview](#15-architecture-overview)
16. [Troubleshooting](#16-troubleshooting)
17. [Switching Between Platforms](#17-switching-between-platforms)

---

## 1. System Requirements

Before you begin, make sure the following tools are installed on your machine.

| Tool | Minimum Version | Download |
|------|----------------|---------|
| Python | 3.12+ | https://www.python.org/downloads/ |
| Git | Any recent | https://git-scm.com/downloads |
| Docker Desktop | 4.x+ | https://www.docker.com/products/docker-desktop/ |
| Docker Compose | V2 (bundled with Docker Desktop) | *(included above)* |
| uv *(recommended)* | 0.4+ | https://docs.astral.sh/uv/getting-started/installation/ |

**Verify your installations:**

```bash
python --version       # Python 3.12.x
git --version          # git version 2.x
docker --version       # Docker version 26.x
docker compose version # Docker Compose version v2.x
```

> **Windows users:** All commands below work in PowerShell or Command Prompt.
> Replace `cp` with `copy` and `/` with `\` in paths if you are not using Git Bash.

---

## 2. Clone the Repository

```bash
git clone https://github.com/wajahatah/Conversational_Task_Assistant.git
cd Conversational_Task_Assistant
```

Your directory should look like this:

```
Conversational_Task_Assistant/
├── app/
│   ├── database/
│   ├── handlers/
│   ├── llm/
│   ├── platform/
│   ├── scheduler/
│   ├── services/
│   ├── config.py
│   └── main.py
├── docker/
│   ├── docker-compose.yml   ← full infrastructure stack
│   └── .env.docker          ← docker-only settings
├── tests/
├── .env.example             ← template for your .env
├── alembic.ini
├── pyproject.toml           ← project metadata & dependencies (uv)
├── uv.lock                  ← pinned dependency lock file
├── requirements.txt         ← legacy pip install list
└── run_polling.py
```

---

## 3. Create the Python Environment

Creating a virtual environment isolates project dependencies from your system Python.

```bash
# Create the virtual environment
python -m venv venv

# Activate it
# Windows (PowerShell):
venv\Scripts\Activate.ps1

# Windows (Command Prompt):
venv\Scripts\activate.bat

# macOS / Linux:
source venv/bin/activate
```

You should see `(venv)` at the start of your terminal prompt.

> **Tip:** You must activate the virtual environment every time you open a new
> terminal window before running any project commands.

---

## 4. Install Python Dependencies

Two methods are available. **uv is recommended** — it is significantly faster than
pip and uses the pinned `uv.lock` file to guarantee reproducible installs.

---

### Method A — uv (Recommended)

[uv](https://docs.astral.sh/uv/) is a fast Python package manager written in Rust.

**Install uv** (one-time, run outside the virtual environment):

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Install all project dependencies from the lock file:**

```bash
# Creates .venv automatically and installs all pinned packages
uv sync

# Also install the dev group (pytest etc.) — included by default
uv sync --group dev
```

**Activate the environment created by uv:**

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (Command Prompt)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate
```

**Optional — HuggingFace local inference extra:**

```bash
# CPU only
uv sync --extra huggingface

# NVIDIA GPU (CUDA 12.4) — override torch index
uv sync --extra huggingface --index-url https://download.pytorch.org/whl/cu124
```

---

### Method B — pip (Classic)

With the virtual environment active (see Step 3):

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

**If you plan to use a local LLM (HuggingFace), also run:**

```bash
# CPU only
pip install transformers torch accelerate

# NVIDIA GPU (CUDA 12.4)
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install transformers accelerate
```

---

## 5. Spin Up Infrastructure with Docker

All backing services (PostgreSQL, Redis, Kafka) are managed through the
`docker/` folder so they don't conflict with anything else on your machine.

### 5.1 — Copy the Docker environment file

```bash
# Windows
copy docker\.env.docker docker\.env

# macOS / Linux
cp docker/.env.docker docker/.env
```

Open `docker/.env` and adjust passwords if you want (defaults are fine for local dev).

### 5.2 — Start all services

```bash
cd docker
docker compose up -d
cd ..
```

This starts:

| Container | Purpose | Default Port |
|-----------|---------|-------------|
| `task_postgres` | PostgreSQL database | `5432` |
| `task_redis` | Redis (Celery broker + cache) | `6379` |
| `task_zookeeper` | Required by Kafka | `2181` |
| `task_kafka` | Event streaming | `9092` |
| `task_pgadmin` | Web-based DB GUI (optional) | `5050` |

### 5.3 — Verify all containers are healthy

```bash
docker compose -f docker/docker-compose.yml ps
```

All containers should show `healthy` or `running` status.  
Wait about 30 seconds after first start for Kafka and Zookeeper to initialise.

### 5.4 — Access pgAdmin (optional)

Open your browser at **http://localhost:5050**

- Email: `admin@local.dev`
- Password: `admin`

To connect to PostgreSQL inside pgAdmin:
- Host: `task_postgres`
- Port: `5432`
- Username: `task_user`
- Password: `task_password`

---

## 6. Configure Environment Variables

The application reads all its settings from a `.env` file in the **project root**.

### 6.1 — Copy the template

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

### 6.2 — Open `.env` and fill in the required values

The file is split into sections. Here are the **required** ones:

```env
# ── Bot Platform (REQUIRED - Choose at least one) ───────────────────────────
TELEGRAM_BOT_TOKEN=your_bot_token_here   # See Step 7 below
DISCORD_BOT_TOKEN=your_discord_token_here # See Step 8 below

# ── Database — must match docker/.env ────────────────────────────────────────
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=task_user
POSTGRES_PASSWORD=task_password
POSTGRES_DB=task_assistant

# ── Redis — must match docker/.env ───────────────────────────────────────────
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# ── LLM Provider (choose ONE, see Step 9) ────────────────────────────────────
LLM_PROVIDER=ollama                      # ollama | huggingface | openai | gemini | anthropic
```

The rest of the settings can stay at their defaults for now and be adjusted later.

---

## 7. Create a Telegram Bot

You need a Telegram Bot Token to connect the application to Telegram.

### Step-by-step:

1. Open Telegram and search for **@BotFather** (official account, blue checkmark).
2. Send the command: `/newbot`
3. BotFather will ask for a **name** (display name, e.g. `My Task Assistant`).
4. Then a **username** — must end in `bot` (e.g. `mytask_assistant_bot`).
5. BotFather replies with your token:
   ```
   Done! Use this token to access the HTTP API:
   7123456789:AAF_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
6. Copy that token and paste it into your `.env` file:
   ```env
   TELEGRAM_BOT_TOKEN=7123456789:AAF_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

> **Keep your token secret.** Anyone with it can control your bot.
> Never commit `.env` to git (it is already in `.gitignore`).

### Optional: Disable privacy mode for group chats

If you plan to add the bot to a group, send BotFather:
```
/setprivacy → @your_bot_username → Disable
```

---

## 8. Create a Discord Bot

You need a Discord Bot Token to connect the application to Discord.

### Step-by-step:

1.  Open the [Discord Developer Portal](https://discord.com/developers/applications).
2.  Click **New Application** and give it a name (e.g., `My Task Assistant`).
3.  In the left sidebar, click **Bot**.
4.  Click **Reset Token** (or **Copy Token**) to get your bot token.
5.  **Enable Intents (CRITICAL)**: Scroll down to the **Privileged Gateway Intents** section and enable **MESSAGE CONTENT INTENT**.
    - This allows the bot to read your natural language tasks.
6.  **Invite to Server**:
    - Go to **OAuth2 → URL Generator**.
    - Select the `bot` scope.
    - Select permissions: `Send Messages`, `Read Message History`.
    - Copy the generated URL and open it in your browser to authorize the bot.
7.  Copy that token and paste it into your `.env` file:
    ```env
    DISCORD_BOT_TOKEN=MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.XxxxxX.xxxxxxxxxxxxxxxxxxxxxxxxxxx
    ```

> **Keep your token secret.** Anyone with it can control your bot.
> Never commit `.env` to git (it is already in `.gitignore`).

> **Note:** If both `TELEGRAM_BOT_TOKEN` and `DISCORD_BOT_TOKEN` are set, the application will prioritize Telegram. To use Discord, leave `TELEGRAM_BOT_TOKEN` empty or unset.

---

## 9. Choose & Configure an LLM Provider

The assistant uses an LLM to parse natural language tasks and power the help
conversations. Pick **one** provider and configure it in `.env`.

---

### Option A — Ollama (Local, Free)

Best for: privacy-conscious users with a reasonably modern laptop/desktop.  
No API key needed. Runs 100% on your machine.

**Install Ollama:**

- **Windows/macOS:** Download from https://ollama.com/download and run the installer.
- **Linux:**
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```

**Pull a model:**

```bash
# Lightweight (4 GB RAM) — recommended for low-end machines
ollama pull llama3.2

# Mid-range (8 GB RAM)
ollama pull mistral

# High quality (16 GB RAM)
ollama pull llama3.1:8b
```

**Verify Ollama is running:**

```bash
ollama list        # shows downloaded models
curl http://localhost:11434/api/tags   # should return JSON
```

```cmd
ollama list
curl http://localhost:11434/api/tags
```

**Set in `.env`:**

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
```

---

### Option B — HuggingFace (Local, Free)

Best for: users who want to use specific academic or open-source models.  
Requires extra Python packages (installed in Step 4).

**Set in `.env`:**

```env
LLM_PROVIDER=huggingface
HF_MODEL_NAME=mistralai/Mistral-7B-Instruct-v0.2
HF_DEVICE=cpu       # use 'cuda' if you have an NVIDIA GPU
```

> **First run:** The model (several GB) will download automatically from
> Hugging Face Hub into `~/.cache/huggingface/`. This takes time once.

**HuggingFace Inference API (cloud, free tier):**

If your machine doesn't have enough RAM, you can use the HuggingFace cloud API:

1. Sign up at https://huggingface.co/
2. Go to **Settings → Access Tokens → New token** (read scope is enough)
3. Add to `.env`:
   ```env
   LLM_PROVIDER=huggingface
   HF_MODEL_NAME=mistralai/Mistral-7B-Instruct-v0.2
   HF_API_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

---

### Option C — OpenAI

Best for: highest quality results with minimal setup.

1. Sign up / log in at https://platform.openai.com/
2. Go to **API Keys → Create new secret key**
3. Add credits to your account (usage-based billing)
4. Set in `.env`:
   ```env
   LLM_PROVIDER=openai
   OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   OPENAI_MODEL=gpt-4o          # or gpt-4-turbo, gpt-3.5-turbo
   OPENAI_MAX_TOKENS=1024
   OPENAI_TEMPERATURE=0.7
   ```

---

### Option D — Google Gemini

1. Go to https://aistudio.google.com/app/apikey
2. Click **Create API Key**
3. Set in `.env`:
   ```env
   LLM_PROVIDER=gemini
   GEMINI_API_KEY=AIza_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   GEMINI_MODEL=gemini-1.5-flash    # or gemini-1.5-pro
   ```

---

### Option E — Anthropic Claude

1. Sign up at https://console.anthropic.com/
2. Go to **API Keys → Create Key**
3. Set in `.env`:
   ```env
   LLM_PROVIDER=anthropic
   ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
   ```

---

## 10. Run Database Migrations

Alembic creates all necessary tables inside the PostgreSQL container.

Make sure the containers are running (`docker compose -f docker/docker-compose.yml ps`)
and your virtual environment is activated, then run from the **project root** to generate the initial migration:

```bash
alembic revision --autogenerate -m "initial_schema"
```

Then, apply the migration to create the tables:

```bash
alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Context impl PostgreSQLImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> xxxx, initial_schema
```

**Verify the tables were created:**

```bash
docker exec -it task_postgres psql -U task_user -d task_assistant -c "\dt"
```

Expected table list:
```
 Schema |         Name         | Type  |   Owner
--------+----------------------+-------+-----------
 public | alembic_version      | table | task_user
 public | conversation_history | table | task_user
 public | interactions         | table | task_user
 public | tasks                | table | task_user
 public | user_settings        | table | task_user
 public | users                | table | task_user
```

---

## 10. Start the Application

The application needs **two terminals running at the same time**. Activate the
virtual environment in each.

### Terminal 1 — Celery Worker + Beat Scheduler

This process evaluates task states and dispatches nudges/summaries on a schedule.

```bash
# Windows (Requires two separate terminals)
# Terminal A (Worker):
venv\Scripts\activate.bat
celery -A app.scheduler.celery_app worker --pool=solo --loglevel=info

# Terminal B (Beat Scheduler):
venv\Scripts\activate.bat
celery -A app.scheduler.celery_app beat --loglevel=info

# macOS / Linux (Can be run together)
source venv/bin/activate
celery -A app.scheduler.celery_app worker --beat --loglevel=info
```

You should see:
```
[tasks]
  . app.scheduler.jobs.evaluate_all_tasks
  . app.scheduler.jobs.dispatch_daily_summaries

[2026-04-20 ...] celery@hostname ready.
```

### Terminal 2 — Bot Runner (Choose ONE)

This process listens to the platform (Telegram or Discord) and routes all incoming messages.

#### Option A — Telegram Bot (Long-Polling)

```bash
# Windows
venv\Scripts\activate.bat
python run_polling.py

# macOS / Linux
source venv/bin/activate
python run_polling.py
```

#### Option B — Discord Bot

```bash
# Windows
venv\Scripts\activate.bat
python run_discord.py

# macOS / Linux
source venv/bin/activate
python run_discord.py
```

### Terminal 3 (Optional) — FastAPI Dev Server

Exposes a REST API and interactive Swagger docs. Useful for debugging.

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open: http://localhost:8000/docs

---

## 12. Test the Bot

1. Open your chosen chat app (Telegram or Discord).
2. Search for your bot or DM it directly.
3. Send `/start`.

**Registration flow:**

The bot will walk you through a one-time setup:

| Step | Bot asks | Example reply |
|------|----------|--------------|
| 1 | Your full name | `Ali Hassan` |
| 2 | Your email | `ali@example.com` |
| 3 | Phone number | `+92 300 1234567` or `skip` |
| 4 | Your timezone | `Asia/Karachi` |
| 5 | Daily summary time | `08:00` |

**Add your first task:**

After registration, just type naturally:

```
Finish the project report from 2pm to 5pm
```

The bot will confirm:
```
📋 Task added!
Finish the project report
🕐 Start: 14:00
⏰ Deadline: 17:00
I'll remind you when it's time. Good luck! 💪
```

**When the start time arrives**, the bot sends a poll:
- ✅ Done
- 🔄 In Progress
- ❌ Drop Task
- 🆘 Need Help

---

## 12. Verify Everything Is Working

Run these quick checks from the project root with the virtual environment active:

**Check PostgreSQL connection:**

```bash
python -c "
import asyncio
from sqlalchemy import text
from app.database.session import AsyncSessionLocal
async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text('SELECT 1'))
        print('PostgreSQL: OK ✅', result.scalar())
asyncio.run(check())
"
```

```cmd
python -c "import asyncio; from sqlalchemy import text; from app.database.session import AsyncSessionLocal; asyncio.run((lambda: (lambda db=None: None)())())"
```
> **CMD tip:** Multi-line scripts are easier to run from a file. Save the code above to `check_pg.py` and run `python check_pg.py`.

**Check Redis connection:**

```bash
python -c "
import redis
r = redis.from_url('redis://localhost:6379/0')
r.ping()
print('Redis: OK ✅')
"
```

```cmd
python -c "import redis; r = redis.from_url('redis://localhost:6379/0'); r.ping(); print('Redis: OK')"
```

**Check LLM provider:**

```bash
python -c "
import asyncio
from app.llm import get_llm_provider
async def check():
    llm = get_llm_provider()
    print(f'LLM Provider: {llm.provider_name} ✅')
    print(f'Model: {llm.model_name}')
asyncio.run(check())
"
```

```cmd
python -c "import asyncio; from app.llm import get_llm_provider; llm = get_llm_provider(); print('LLM Provider:', llm.provider_name); print('Model:', llm.model_name)"
```
> **CMD tip:** For the async version, save the bash script above to `check_llm.py` and run `python check_llm.py`.

**Check Kafka (optional):**

```bash
docker exec -it task_kafka kafka-topics --bootstrap-server localhost:9092 --list
# If Kafka is healthy this returns (possibly empty) list without errors
```

---

## 13. Bot Commands Reference

| Command | Description |
|---------|-------------|
| `/start` | Start the bot / show welcome message |
| `/help` | Display all available commands |
| `/tasks` | View all your tasks for today with their current state |
| `/summary` | Receive your daily summary right now |

**Task states shown in `/tasks`:**

| Emoji | State | Meaning |
|-------|-------|---------|
| ⏳ | NOT_STARTED | Task hasn't started yet |
| 🔄 | ACTIVE | Task is in progress |
| ⚠️ | AT_RISK | Deadline is approaching, check-in sent |
| 🔴 | STALLED | Task has received no response — escalated |
| ✅ | COMPLETED | Task marked done |
| ❌ | DROPPED | Task manually dropped |

---

## 15. Architecture Overview

```
Platform User (Telegram/Discord)
     │
     ▼
run_polling.py / run_discord.py ──► webhook_handler.py
                                       │
                            ┌──────────▼──────────────┐
                            │                         │
                            ▼                         ▼
                registration.py             interaction_handler.py
                (new users)                 (registered users)
                                                      │
                                       ┌──────────────┼──────────────┐
                                       ▼              ▼               ▼
                                handle_command  handle_text    handle_poll_response
                                                     │
                                             task_service.py ──► LLM (parse task)
                                             orchestrator.py ──► LLM (help chat)

Celery Beat (every 5 min)
     │
     ▼
evaluate_all_tasks()
     │
     ├──► state_engine.py      (infer: ACTIVE / AT_RISK / STALLED)
     ├──► intervention_engine.py (decide: poll / reminder / escalation)
     └──► notification_service.py (send + log)

Celery Beat (every minute)
     │
     ▼
dispatch_daily_summaries()
     └──► summary_service.py ──► LLM (generate summary)
```

**Infrastructure:**

| Component | Technology |
|-----------|-----------|
| Bot messaging | python-telegram-bot v21 / discord.py v2.3 |
| Web framework | FastAPI + uvicorn |
| Database | PostgreSQL 16 (asyncpg + SQLAlchemy) |
| Task queue | Celery + Redis |
| Event bus | Kafka (future use) |
| LLM | Pluggable: Ollama / HuggingFace / OpenAI / Gemini / Anthropic |

---

## 16. Troubleshooting

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `connection refused :5432` | Postgres container not running | `docker compose -f docker/docker-compose.yml up -d postgres` |
| `connection refused :6379` | Redis container not running | `docker compose -f docker/docker-compose.yml up -d redis` |
| `alembic upgrade head` fails | DB user doesn't have schema permission | Run the SQL in Step 10 again |
| `TELEGRAM_BOT_TOKEN invalid` | Wrong token in `.env` | Copy from BotFather, no extra spaces |
| `DISCORD_BOT_TOKEN invalid` | Wrong token in `.env` | Copy from Discord Portal |
| Bot doesn't respond | Runner not running | Ensure `run_polling.py` or `run_discord.py` is running |
| No nudges/summaries | Celery not running | Open Terminal 1, run the celery command |
| HuggingFace slow first start | Model downloading (~7 GB) | Wait — only happens once |
| `No module named 'transformers'` | Extra deps not installed | `pip install transformers torch accelerate` |
| `permission denied on schema public` | Missing grant in Postgres | `GRANT ALL ON SCHEMA public TO task_user;` in psql |
| Kafka container exits | Zookeeper not ready yet | Wait 30s then `docker compose up -d kafka` |

### Useful Docker Commands

```bash
# Check all container statuses
docker compose -f docker/docker-compose.yml ps

# View logs for a specific service
docker compose -f docker/docker-compose.yml logs postgres
docker compose -f docker/docker-compose.yml logs redis
docker compose -f docker/docker-compose.yml logs kafka

# Restart a single service
docker compose -f docker/docker-compose.yml restart postgres

# Stop everything (keeps data)
docker compose -f docker/docker-compose.yml down

# Wipe all data and start fresh
docker compose -f docker/docker-compose.yml down -v
docker compose -f docker/docker-compose.yml up -d
```

### Reset the Database

If you need a clean slate:

```bash
# Drop and recreate all tables
docker exec -it task_postgres psql -U task_user -d task_assistant -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
alembic upgrade head
```

---

## 17. Switching Between Platforms

If you've already set up the project and want to switch from Telegram to Discord (or vice-versa), you **do not** need to re-install anything or reset your database. The application is designed to be multi-platform.

### To switch platforms:

1.  **Update `.env`**:
    - To use **Discord**: Ensure `DISCORD_BOT_TOKEN` is set and `TELEGRAM_BOT_TOKEN` is **empty or commented out** (Telegram has priority if both are set).
    - To use **Telegram**: Ensure `TELEGRAM_BOT_TOKEN` is set.
2.  **Switch the Runner**:
    - Stop the current bot process (Ctrl+C).
    - Run the other script: `python run_discord.py` or `python run_polling.py`.
3.  **Registration**:
    - Since your Telegram ID and Discord ID are different, the bot will ask you to register again on the new platform.
    - Your existing tasks from the previous platform will stay in the database but will not be visible on the new platform.

> **Note**: The Celery worker and database are shared, so scheduled tasks for the previous platform will continue to trigger in the background until the tasks are completed or deleted.

---

> For additional integration details (existing Docker environments, GPU setup,
> production webhook deployment), see [`DEVELOPER_SETUP.md`](./DEVELOPER_SETUP.md).
