# Progress-Aware Conversational Task Assistant
## Scalable System Architecture

---

## 1. Overview

This system is a **progress-aware conversational task assistant** designed for daily task management.
It uses **rule-based state inference** and **event-driven intervention logic** to guide users toward
task completion across **Telegram** and **Discord**.

Core principles:
- Deterministic task reasoning (no ML for state logic)
- LLM used only for natural-language generation (parsing tasks, HELP flow, daily summary)
- Event-driven evaluation cycles
- Platform-agnostic via the Adapter Layer
- Per-user clock-offset calibration so reminders fire at the user's wall-clock time

---

## 2. High-Level Architecture

### Components

1. Platform Adapter Layer (Telegram + Discord)
2. Webhook / Update Handler (entry point)
3. User Registration Service
4. Task Service
5. State Inference Engine
6. Intervention Engine
7. Scheduler / Event Processor
8. Notification Service
9. User Interaction Handler
10. Conversation Orchestrator
11. LLM Service (pluggable: OpenAI / Gemini / Anthropic / Ollama / HuggingFace)
12. Database (PostgreSQL)

---

## 3. System Components

### 3.0 Platform Adapter Layer

Abstracts differences between Telegram and Discord. The rest of the system only consumes
`NormalizedMessage` and emits `OutboundMessage` — never platform-specific payloads.

Responsibilities:
- Parse platform payload → `NormalizedMessage` (`platform`, `platform_id`, `message_type`,
  `text`, `command`, `poll_response`, `poll_message_id`, `raw`)
- Convert system actions → platform-specific messages and inline-button polls
- Track `message_id` per platform so poll responses can be matched to the original Interaction
- Route normalized messages to the Update Handler

Supported platforms:
- **Telegram** (long-poll or webhook) — polls rendered as inline-keyboard buttons
- **Discord** (persistent WebSocket via `discord.py`) — polls rendered as Discord buttons

Selection priority (in `app/platform/__init__.py`): Telegram if `TELEGRAM_BOT_TOKEN` is set,
else Discord if `DISCORD_BOT_TOKEN` is set. Only one should be active at a time.

---

### 3.1 Webhook / Update Handler

Single entry point for both platforms. Accepts a raw payload, asks the active adapter to
parse it, then routes by user state:

- Unknown `platform_id` → User Registration Service (start)
- `registration_state != null` → Registration Service (continue) or restart on `/start`
- Fully registered → Interaction Handler (poll response, command, or text)

---

### 3.2 User Registration Service

Handles first-time onboarding and re-registration. State is tracked on the `User` row via
`registration_state` (null = registered).

Sequential steps:
1. **Name**
2. **Email** (regex-validated)
3. **Phone** (optional — user can type `skip`)
4. **Timezone** — IANA string (`Asia/Karachi`) or UTC offset (`UTC+5`, `UTC-4:30`)
5. **Daily summary time** — `HH:MM` 24-hour, default `08:00`
6. **Current local time** — used to compute `clock_offset_seconds` (calibrates the user's
   real wall-clock against server UTC; clamped to ±12 hours)

The clock-offset calibration is what makes reminders fire at the user's actual local time
even when the server clock or the user's stated timezone is slightly off.

Re-registration: the `/register` command resets `registration_state` and re-runs the flow,
preserving the existing user's tasks and history.

---

### 3.3 Task Service

Manages task lifecycle: create, list, extend, mark no-more-action.
Each task is scoped to a single calendar day via `task_date`.

- `create_task_from_nl()` — sends free-text to the LLM provider's `parse_task()`, validates
  (deadline > start, both same day, ≥5 minutes), and persists.
- `get_active_tasks()` — non-terminal, today, `no_more_action=false`.
- `extend_task()` — bumps deadline and `extensions_count` (used on AT_RISK_2 IN_PROGRESS).
- `increment_ignored_prompts()` / `mark_no_more_action()` — bookkeeping.

---

### 3.4 State Inference Engine (CORE LOGIC)

Pure function over a Task + UserSettings. No LLM, no I/O.

Inputs: `start_time`, `deadline`, `now` (adjusted by `clock_offset_seconds`),
`last_response_time`, `ignored_prompts`, `extensions_count`.

Output: `{ state, trigger, pct_remaining, reason }` where `trigger` is one of
`AT_RISK_1` / `AT_RISK_2` / null.

---

### 3.5 Intervention Engine

Decides *what action* to take given the inferred state. Enforces cooldowns and prevents
duplicate escalations via `Task.no_more_action`.

Outputs an `InterventionDecision` with one of:
`SEND_START_REMINDER`, `SEND_STATUS_POLL`, `SEND_URGENT_POLL`,
`SEND_ESCALATION`, `SEND_FINAL_POLL`, `NO_ACTION`.

---

### 3.6 Scheduler / Event Processor

Two independent jobs. They are implemented as Celery tasks and also exposed as plain async
functions so the Discord runner can drive them in-process without Celery.

- **Task evaluation** — every `TASK_EVALUATION_INTERVAL_MINUTES` (default 5).
  Iterates active tasks → state engine → intervention engine → notification.
- **Daily summary dispatcher** — every minute, per-user check.
  For each user, converts UTC to the user's local time via `clock_offset_seconds`; if the
  hour:minute matches `summary_trigger_time`, dispatches that user's summary.

Two execution modes:
- **Telegram**: Celery worker + Celery Beat run the jobs (`run_polling.py` runs the bot).
- **Discord**: `run_discord.py` owns the event loop and runs both jobs as in-process
  `asyncio` tasks every 60 seconds (no Celery required).

---

### 3.7 Notification Service

Builds the message/poll for each intervention type, sends it via the platform adapter,
and records an `Interaction` row (with `message_id`, `type`, `content`). Updates
`task.last_response_time` for cooldown tracking.

---

### 3.8 User Interaction Handler

Processes poll responses, commands, and free-text:

- **Poll response**: looks up the original `Interaction` by `message_id`, records
  `response_type`, and applies the action (see §7).
- **Command**: dispatches to `/start`, `/help`, `/register`, `/tasks`, `/summary`.
- **Free-text**: if the user has an active HELP session on a task, routes to the
  Conversation Orchestrator; otherwise treats it as a new task and calls
  `create_task_from_nl()`.

---

### 3.9 Conversation Orchestrator

Bridge between rule engine and LLM. On `NEED_HELP`, fetches recent task-scoped
`ConversationHistory` (last 10 turns), builds a system prompt with task context (title,
deadline, % time remaining), calls the LLM, persists both turns, and replies. The user's
next free-text message continues the same session.

---

### 3.10 LLM Service

Pluggable behind a single `BaseLLMProvider` interface (`generate`, `parse_task`,
`health_check`). Selected at startup via `LLM_PROVIDER`. Five providers ship today:

- **OpenAI** (`openai_provider.py`)
- **Gemini** (`gemini_provider.py`)
- **Anthropic** (`anthropic_provider.py`)
- **Ollama** (`ollama_provider.py`) — local
- **HuggingFace** (`huggingface_provider.py`) — local

LLM is **never** allowed to change task state, deadlines, or extension counts.

---

### 3.11 Database

PostgreSQL via SQLAlchemy async + asyncpg. Migrations managed by Alembic.

---

## 4. Database Schema

### Users
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| name | VARCHAR | From registration |
| email | VARCHAR | From registration |
| phone | VARCHAR | Optional |
| platform | ENUM | `telegram` / `discord` (`slack` reserved, no adapter) |
| platform_id | VARCHAR | Unique, indexed (Telegram chat_id or Discord user_id) |
| timezone | VARCHAR | IANA string, default `UTC` |
| summary_trigger_time | TIME | Default `08:00` |
| registration_state | VARCHAR | Onboarding step; null when complete |
| registered_at | TIMESTAMP | Set when registration finishes |
| created_at | TIMESTAMP | |

### Tasks
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → Users |
| title | VARCHAR | |
| description | TEXT | Optional |
| task_date | DATE | One-day scope |
| start_time | TIMESTAMPTZ | |
| deadline | TIMESTAMPTZ | |
| state | ENUM | `NOT_STARTED` / `ACTIVE` / `AT_RISK` / `STALLED` / `COMPLETED` / `DROPPED` |
| extensions_count | INT | Default 0, max from UserSettings (default 2) |
| ignored_prompts | INT | Default 0, max from UserSettings (default 3) |
| last_response_time | TIMESTAMPTZ | Cooldown anchor |
| no_more_action | BOOLEAN | Set true after ESCALATION or FINAL_POLL — prevents repeats |
| created_at / updated_at | TIMESTAMPTZ | |

### Interactions
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| task_id | UUID | FK → Tasks |
| user_id | UUID | FK → Users |
| type | ENUM | `POLL` / `URGENT_POLL` / `REMINDER` / `MESSAGE` / `SYSTEM` |
| content | TEXT | The text that was sent |
| response_type | ENUM | `DONE` / `IN_PROGRESS` / `DROP` / `NEED_HELP` / `STALL` / null |
| message_id | VARCHAR | Platform message id — used to match poll clicks back to this row |
| created_at | TIMESTAMP | |

### ConversationHistory
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → Users |
| task_id | UUID | FK → Tasks (nullable) |
| role | ENUM | `user` / `assistant` |
| message | TEXT | |
| created_at | TIMESTAMP | |

### UserSettings
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → Users (unique — one settings row per user) |
| summary_trigger_time | TIME | Default `08:00` |
| max_extensions | INT | Default 2 |
| max_ignored_prompts | INT | Default 3 |
| nudge_cooldown_minutes | INT | Default 30 (currently superseded by mini-cooldown) |
| clock_offset_seconds | INT | Calibrated during registration; clamped ±12h |
| updated_at | TIMESTAMPTZ | |

---

## 5. User Registration Flow

**Trigger**: First message from an unknown `platform_id`, or the `/register` command.

```
[Unknown platform_id]
        ↓
[Send welcome + ask for Name]
        ↓
[Email]   →   [Phone (or "skip")]   →   [Timezone (IANA or UTC±H[:MM])]
        ↓                       ↓                          ↓
                  [Summary time HH:MM]   →   [Current local time]
                                                         ↓
                          [Compute clock_offset_seconds]
                                                         ↓
                  [Persist User + UserSettings; registration_state = null]
                                                         ↓
                                  ["You're all set!"]
```

Re-registration via `/register` resets `registration_state` and re-runs the flow without
deleting the user's tasks.

---

## 6. State Inference Rules

### States

| State | Condition |
|-------|-----------|
| NOT_STARTED | `now < start_time` |
| ACTIVE | start time passed, not yet AT_RISK or STALLED |
| AT_RISK | `pct_remaining ≤ AT_RISK_THRESHOLD_1` (default 35%) |
| STALLED | `ignored_prompts ≥ max_ignored_prompts` OR `extensions_count ≥ max_extensions` |
| COMPLETED | user responded `DONE` |
| DROPPED | user responded `DROP` |

### Percentage thresholds

```
total_duration = deadline − start_time
time_remaining = deadline − now            # 'now' adjusted by clock_offset_seconds
pct_remaining  = time_remaining / total_duration × 100
```

| Trigger | Condition | Intervention |
|---------|-----------|--------------|
| AT_RISK_1 | `pct_remaining ≤ AT_RISK_THRESHOLD_1` (default 35%) | Status poll |
| AT_RISK_2 | `pct_remaining ≤ AT_RISK_THRESHOLD_2` (default 10%) | Urgent poll w/ extension |

Resolution priority (highest first): COMPLETED → DROPPED → STALLED → AT_RISK_2 → AT_RISK_1 →
NOT_STARTED → ACTIVE.

---

## 7. Intervention Logic

| Situation | Action |
|-----------|--------|
| `NOT_STARTED → ACTIVE` boundary crossed | `SEND_START_REMINDER` (fires once) |
| ACTIVE | `NO_ACTION` |
| AT_RISK_1, cooldown clear | `SEND_STATUS_POLL` (DONE / IN_PROGRESS / DROP / NEED_HELP) |
| AT_RISK_2, cooldown clear | `SEND_URGENT_POLL` (same options; IN_PROGRESS extends) |
| STALLED, no prior extension | `SEND_ESCALATION` once, then `no_more_action=true` |
| STALLED, after extension | `SEND_FINAL_POLL` (DONE / STALL), then `no_more_action=true` |
| COMPLETED / DROPPED / `no_more_action` | `NO_ACTION` |

Constraints:
- Max 2 task extensions (`UserSettings.max_extensions`)
- Max 3 ignored prompts before STALLED (`UserSettings.max_ignored_prompts`)
- **Mini-cooldown** between AT_RISK nudges: 10% of total task duration (min 2 minutes).
  This replaces the older flat 30-minute cooldown so short tasks still get nudged.

---

## 8. Poll System

### Standard polls (AT_RISK_1, AT_RISK_2)

| Option | Effect |
|--------|--------|
| ✅ DONE | → COMPLETED |
| 🔄 IN_PROGRESS | AT_RISK_1: acknowledge only. AT_RISK_2: extend deadline by 50% of original duration (min 5 min) and increment `extensions_count`. |
| ❌ DROP | → DROPPED |
| 🆘 NEED_HELP | Enter HELP flow via Conversation Orchestrator |

### Final poll (after an extension's deadline passes)

| Option | Effect |
|--------|--------|
| ✅ DONE | → COMPLETED |
| 🔴 STALL | → STALLED |

Polls are matched back to their originating Interaction by `message_id` so a single
button click only affects the correct task.

---

## 9. Event Flow

1. User adds a task in natural language.
2. LLM `parse_task()` extracts title / start / deadline.
3. Task stored (state `NOT_STARTED`).
4. Evaluation job runs every 5 min (Celery for Telegram, in-process loop for Discord).
5. State inference produces `{ state, trigger }`.
6. Intervention engine picks an action; cooldown and `no_more_action` are honored.
7. Notification service formats and dispatches via the platform adapter; logs an Interaction.
8. User responds via inline button → Interaction Handler updates the task and may extend or
   mark COMPLETED / DROPPED / STALLED.

---

## 10. Daily Summary

**Trigger**: Per-user, at `UserSettings.summary_trigger_time` in the user's local time
(driven by `clock_offset_seconds`). The dispatcher runs every minute and matches users
whose local hour:minute equals their configured summary time.

**Default**: `08:00` user-local. Adjustable during registration or via `/register`.

**Content** (LLM-generated, plain-text fallback if the LLM fails):
- ✅ Yesterday's completed tasks
- 🔴 Yesterday's stalled / dropped tasks
- 📋 Today's upcoming tasks
- 📊 Yesterday's completion rate

`/summary` lets users trigger the same summary on demand.

---

## 11. Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (async) |
| Database | PostgreSQL 16 (SQLAlchemy async + asyncpg) |
| Migrations | Alembic |
| Cache / Broker | Redis |
| Task scheduler | Celery + Celery Beat (Telegram); in-process asyncio loops (Discord) |
| Telegram | python-telegram-bot v21 |
| Discord | discord.py v2 |
| LLM | Pluggable: OpenAI / Gemini / Anthropic / Ollama / HuggingFace |
| Timezone | `zoneinfo` + per-user `clock_offset_seconds` calibration |

> Note: `docker-compose.yml` also brings up Kafka + Zookeeper. They are reserved for future
> cross-service event streaming and are not currently consumed by the application.

---

## 12. Constraints & Guardrails

- One-day task scope (`task_date`)
- Max 2 extensions per task; max 3 ignored prompts before STALLED
- Mini-cooldown (10% of duration, min 2 min) between AT_RISK nudges
- `no_more_action` flag prevents duplicate escalation / final-poll messages
- LLM cannot override state transitions, deadlines, or extension limits
- Platform priority: Telegram if its token is set, otherwise Discord
- Platform rate limits respected (Telegram 30 msg/sec; Discord per-channel limits)

---

## END
