# Conversational Task Assistant — Setup & Run Guide

## Prerequisites

- Python 3.12
- Docker Desktop running (with your existing containers)
- Telegram Bot Token (from @BotFather)

---

## Step 1: Find Your Docker Container Connection Details

Since you already have PostgreSQL, Redis, Kafka, and Milvus running as Docker containers,
first get the ports they are exposed on:

```powershell
docker ps
```

Look for your PostgreSQL and Redis containers and note:
- PostgreSQL: the host port (commonly `5432`)
- Redis: the host port (commonly `6379`)

To get the container name and port mappings specifically:
```powershell
docker ps --format "table {{.Names}}\t{{.Ports}}"
```

---

## Step 2: Create the App Database in Your Existing PostgreSQL Container

Connect to your running PostgreSQL container (replace `your_postgres_container` with the actual container name from `docker ps`):

```powershell
docker exec -it your_postgres_container psql -U postgres
```

Once inside the PostgreSQL prompt, run:

```sql
-- Create the database
CREATE DATABASE task_assistant;

-- Create the app user
CREATE USER task_user WITH PASSWORD 'task_password';

-- Grant all privileges
GRANT ALL PRIVILEGES ON DATABASE task_assistant TO task_user;

-- Connect to the new database and grant schema privileges
\c task_assistant
GRANT ALL ON SCHEMA public TO task_user;

-- Exit
\q
```

> **Note:** If your PostgreSQL container has a different default superuser (not `postgres`),
> replace `-U postgres` with that username.

---

## Step 3: Configure Your .env File

Open `.env` and update these values:

```env
# ─── Your Telegram Bot Token ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_actual_token_from_botfather

# ─── PostgreSQL — match your running container ────────────────────────────────
POSTGRES_HOST=localhost
POSTGRES_PORT=5432              # change if your container maps to a different port
POSTGRES_USER=task_user
POSTGRES_PASSWORD=task_password
POSTGRES_DB=task_assistant

# ─── Redis — match your running container ─────────────────────────────────────
REDIS_HOST=localhost
REDIS_PORT=6379                 # change if your container maps to a different port
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# ─── LLM Provider ─────────────────────────────────────────────────────────────
# Option A: HuggingFace local transformers (loads model in Python process)
LLM_PROVIDER=huggingface
HF_MODEL_NAME=mistralai/Mistral-7B-Instruct-v0.2   # any HF model name
HF_DEVICE=cpu                  # use 'cuda' if you have a GPU

# Option B: HuggingFace TGI running in Docker (HTTP server — use ollama provider)
# LLM_PROVIDER=ollama
# OLLAMA_BASE_URL=http://localhost:8080   # your TGI container port
# OLLAMA_MODEL=your-model-name
```

---

## Step 4: Install Python Dependencies

```powershell
# Core dependencies
pip install -r requirements.txt

# For HuggingFace local inference (Option A above)
pip install transformers torch accelerate
```

> **GPU Users (CUDA):** Install CUDA-enabled PyTorch instead:
> ```powershell
> pip install torch --index-url https://download.pytorch.org/whl/cu124
> pip install transformers accelerate
> ```

---

## Step 5: Run the Database Migration

This creates all tables in your `task_assistant` database:

```powershell
alembic upgrade head
```

You should see:
```
INFO  [alembic.runtime.migration] Running upgrade  -> xxxx, initial_schema
```

Verify tables were created:
```powershell
docker exec -it your_postgres_container psql -U task_user -d task_assistant -c "\dt"
```

Expected output:
```
        List of relations
 Schema |         Name         | Type  |   Owner
--------+----------------------+-------+-----------
 public | conversation_history | table | task_user
 public | interactions         | table | task_user
 public | tasks                | table | task_user
 public | user_settings        | table | task_user
 public | users                | table | task_user
```

---

## Step 6: Run the Application (3 Terminals)

### Terminal 1 — Celery Worker + Beat Scheduler
```powershell
celery -A app.scheduler.celery_app worker --beat --loglevel=info
```

Starts:
- **Worker**: processes background tasks
- **Beat**: triggers task evaluation every 5 min + per-user daily summaries

### Terminal 2 — Telegram Bot (Long Polling)
```powershell
python run_polling.py
```

Long-polling mode — no public URL or ngrok needed.

### Terminal 3 (Optional) — FastAPI Server
```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API docs: http://localhost:8000/docs

---

## Step 7: Test the Bot

1. Open Telegram and find your bot by username
2. Send `/start`
3. Complete registration: Name → Email → Phone → Timezone → Summary Time
4. Send a task in natural language:
   > *"Finish the project report from 2pm to 4pm"*
5. The bot confirms and starts tracking

---

## Verification Commands

### Check PostgreSQL:
```powershell
python -c "import asyncio; from app.database.session import engine; from sqlalchemy import text; loop = asyncio.new_event_loop(); conn = loop.run_until_complete(engine.begin().__aenter__()); print('PostgreSQL: OK')"
```

### Check Redis:
```powershell
python -c "import redis; r = redis.from_url('redis://localhost:6379/0'); r.ping(); print('Redis: OK')"
```

### Check LLM Provider:
```powershell
python -c "
import asyncio
from app.llm import get_llm_provider
async def test():
    llm = get_llm_provider()
    print(f'Provider: {llm.provider_name} | Model: {llm.model_name}')
asyncio.run(test())
"
```

---

## Common Issues

| Problem | Fix |
|---------|-----|
| `connection refused :5432` | Check `POSTGRES_PORT` in .env matches your container's exposed port |
| `connection refused :6379` | Check `REDIS_PORT` matches your Redis container |
| `alembic upgrade head` fails | Re-run Step 2 SQL commands to create DB + user |
| HuggingFace slow first start | Model downloads on first run (can be several GB) |
| `TELEGRAM_BOT_TOKEN invalid` | Copy token exactly from BotFather — no extra spaces |
| `No module named 'transformers'` | Run `pip install transformers torch accelerate` |
| `permission denied` on schema | Run `GRANT ALL ON SCHEMA public TO task_user;` in psql |

---

## What About Kafka and Milvus?

Your existing Kafka and Milvus containers are **not used by this app yet** — no configuration needed for them now.

They are planned for future upgrades:
- **Kafka**: event streaming for high-volume multi-user deployments
- **Milvus**: vector similarity search for semantic task recommendations

---

## Quick Reference: Key .env Variables

| Variable | Example Value | Description |
|----------|---------------|-------------|
| `TELEGRAM_BOT_TOKEN` | `7123456789:AAF...` | From @BotFather |
| `POSTGRES_PORT` | `5432` | Your container's exposed port |
| `REDIS_PORT` | `6379` | Your container's exposed port |
| `LLM_PROVIDER` | `huggingface` | Active LLM backend |
| `HF_MODEL_NAME` | `mistralai/Mistral-7B-Instruct-v0.2` | Model from huggingface.co |
| `HF_DEVICE` | `cpu` or `cuda` | Inference device |
| `TASK_EVALUATION_INTERVAL_MINUTES` | `5` | How often bot checks task states |
| `DEFAULT_SUMMARY_TIME` | `08:00` | Default daily briefing time |
