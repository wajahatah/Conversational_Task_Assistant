# Progress-Aware Conversational Task Assistant
## Scalable System Architecture

---

## 1. Overview

This system is a **progress-aware conversational task assistant** designed for daily task management.  
It uses **rule-based state inference** and **event-driven intervention logic** to guide users toward task completion.

Core principles:
- Deterministic task reasoning (no ML for state logic)
- LLM used only for natural language generation (optional layer)
- Event-driven architecture
- Platform-agnostic via Adapter Layer
- Scalable, modular components

---

## 2. High-Level Architecture

### Components:

1. Platform Adapter Layer ← NEW
2. API Gateway
3. User Registration Service ← NEW
4. Task Service
5. State Inference Engine
6. Intervention Engine
7. Scheduler / Event Processor
8. Notification Service
9. User Interaction Handler
10. Conversation Orchestrator
11. Database
12. LLM Service (Optional)

---

## 3. System Components

### 3.0 Platform Adapter Layer (NEW)

Abstracts differences between Telegram and Slack.

Responsibilities:
- Parse platform-specific webhook → Normalized Message format
- Convert system actions → platform-specific messages, polls, buttons
- Track message_id per platform (for poll edits/deletions)
- Route normalized messages to API Gateway

Supported Platforms:
- Telegram Bot API
- Slack Events API

---

### 3.1 API Gateway

Handles all incoming requests from the Platform Adapter.  
Routes to appropriate services: Registration, Task, or Interaction Handler.

---

### 3.2 User Registration Service (NEW)

Handles first-time user onboarding when an unknown platform_id is detected.

Flow:
1. Unknown platform_id received
2. Bot sends welcome message
3. Collects sequentially: Name → Email → Phone → Timezone → Summary Time
4. Creates user record in DB
5. Activates user account

Timezone: Accepts IANA timezone string (e.g., "Asia/Karachi") or UTC offset.

---

### 3.3 Task Service

Manages task lifecycle: create, update, close, extend.  
Each task is scoped to a single day via `task_date`.

---

### 3.4 State Inference Engine (CORE LOGIC)

Determines current task state using percentage-based time thresholds.

Inputs: start_time, deadline, last_response_time, ignored_prompts, extensions_count  
Output: { state, reason }

---

### 3.5 Intervention Engine

Decides when and how to act given current task state.

Checks: cooldown, state, ignored_prompts count, extensions_count  
Output: { action, type }

---

### 3.6 Scheduler / Event Processor

Two independent jobs:
- **Task Evaluation Job**: runs every 5–10 minutes, triggers state inference cycle
- **Daily Summary Job**: runs at each user's configured summary time (user-local time)

---

### 3.7 Notification Service

Sends messages and polls via the Platform Adapter.

---

### 3.8 User Interaction Handler

Processes poll responses and free-text messages.  
Maps responses to system actions and updates task state.

---

### 3.9 Conversation Orchestrator

Bridge between rule engine and LLM.  
Maintains conversation context during multi-turn HELP flows.

---

### 3.10 LLM Service (Optional)

Used only for HELP responses and natural language message generation.  
Cannot override any system rule or state transition.

---

### 3.11 Database

Stores all persistent data: users, tasks, interactions, conversation history, settings.

---

## 4. Database Schema

### Users
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| name | VARCHAR | Collected during registration |
| email | VARCHAR | Collected during registration |
| phone | VARCHAR | Collected during registration |
| platform | ENUM | telegram / slack |
| platform_id | VARCHAR | Unique per platform |
| timezone | VARCHAR | IANA string (e.g., Asia/Karachi) |
| summary_trigger_time | TIME | User's preferred daily summary time |
| registered_at | TIMESTAMP | |
| created_at | TIMESTAMP | |

---

### Tasks
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → Users |
| title | VARCHAR | |
| description | TEXT | Optional |
| task_date | DATE | Scopes task to one day |
| start_time | TIMESTAMP | Task start |
| deadline | TIMESTAMP | Task end |
| state | ENUM | NOT_STARTED / ACTIVE / AT_RISK / STALLED / COMPLETED / DROPPED |
| extensions_count | INT | Max 2 |
| ignored_prompts | INT | Max 3 |
| last_response_time | TIMESTAMP | |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | |

---

### Interactions
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| task_id | UUID | FK → Tasks |
| user_id | UUID | FK → Users |
| type | ENUM | poll / reminder / message / system |
| content | TEXT | Sent message content |
| response_type | ENUM | DONE / IN_PROGRESS / DROP / NEED_HELP / null |
| message_id | VARCHAR | Platform message ID (for edits/deletions) |
| created_at | TIMESTAMP | |

---

### ConversationHistory (NEW)
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → Users |
| task_id | UUID | FK → Tasks (nullable — for general chat) |
| role | ENUM | user / assistant |
| message | TEXT | |
| created_at | TIMESTAMP | |

---

### UserSettings (NEW)
| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → Users |
| summary_trigger_time | TIME | Adjustable summary time (overrides default) |
| max_extensions | INT | Default: 2 |
| max_ignored_prompts | INT | Default: 3 |
| nudge_cooldown_minutes | INT | Cooldown between nudges |
| updated_at | TIMESTAMP | |

---

## 5. User Registration Flow (NEW)

**Trigger**: First message received from an unknown platform_id.

Steps:
1. Detect unknown platform_id
2. Send: *"Hi! I'm your Task Assistant. Let's get you set up."*
3. Collect in sequence:
   - Name
   - Email
   - Phone number
   - Timezone (IANA or UTC offset)
   - Preferred daily summary time
4. Persist user + UserSettings records
5. Confirm: *"You're all set! Send me your tasks for today."*

---

## 6. State Inference Rules

### States

| State | Condition |
|-------|-----------|
| NOT_STARTED | current_time < start_time |
| ACTIVE | start_time passed, user responded recently |
| AT_RISK | approaching deadline (see thresholds below) |
| STALLED | ignored_prompts ≥ 3 OR extensions_count ≥ 2 |
| COMPLETED | user confirmed DONE |
| DROPPED | user selected DROP |

### Percentage-Based AT_RISK Thresholds

Let `total_duration = deadline − start_time`  
Let `time_remaining = deadline − current_time`

| Trigger | Condition | Intervention |
|---------|-----------|--------------|
| AT_RISK_1 | time_remaining ≤ 35% of total_duration | First nudge + status poll |
| AT_RISK_2 | time_remaining ≤ 10% of total_duration | Urgent nudge + decision poll |

---

## 7. Intervention Logic

| State | Action |
|-------|--------|
| NOT_STARTED | 1 reminder at start_time |
| ACTIVE | No action |
| AT_RISK_1 | Nudge + status poll |
| AT_RISK_2 | Urgent nudge + decision poll |
| STALLED | 1 escalation message, then stop |
| COMPLETED / DROPPED | No action |

Constraints:
- Max 2 task extensions
- Max 3 ignored prompts before STALLED
- Cooldown enforced between nudges (configurable in UserSettings)

---

## 8. Poll System

### Options
- DONE
- IN_PROGRESS
- DROP
- NEED_HELP

### Response Mapping
| Response | Outcome |
|----------|---------|
| DONE | → COMPLETED |
| IN_PROGRESS | Extend (if extensions < 2) / Assist |
| DROP | → DROPPED |
| NEED_HELP | → LLM Help Flow (conversational mode) |

---

## 9. Event Flow

1. Task created by user
2. Stored in DB (state: NOT_STARTED)
3. Task Evaluation Job runs (every 5–10 min)
4. State Inference Engine evaluates
5. Intervention Engine decides action
6. If action required:
   - Conversation Orchestrator formats message
   - (Optional) LLM generates response
   - Platform Adapter sends notification
7. Interaction logged
8. User responds → Interaction Handler → Task updated

---

## 10. Daily Summary

**Trigger**: Configurable per-user (stored in UserSettings.summary_trigger_time)  
**Default**: 08:00 user local time  
**Adjustable**: Yes — user can update via command or bot prompt

### Content
- ✅ Completed tasks (previous day)
- ⚠️ Stalled / Dropped tasks (previous day)
- 📋 Upcoming tasks (today)
- 📊 Behavioral insight (completion rate, streak)

---

## 11. Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python) |
| Database | PostgreSQL |
| Cache / Queue | Redis |
| Task Scheduler | Celery + Celery Beat |
| Telegram | Telegram Bot API |
| Slack | Slack Events API |
| LLM | OpenAI GPT-4 |
| Timezone | pytz / zoneinfo |

---

## 12. Constraints

- Max 2 extensions per task
- Max 3 ignored prompts → STALLED
- Cooldown enforced between nudges (configurable)
- Poll-based structured interaction
- LLM cannot override system state decisions
- One-day task validity (scoped by task_date)
- Platform API rate limits respected (Telegram: 30 msg/sec; Slack: tier-based)

---

## END
