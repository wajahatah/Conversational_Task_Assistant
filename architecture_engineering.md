# Progress-Aware Conversational Task Assistant
## Engineering-Grade Architecture Specification

---

## 1. Design Philosophy

This system separates **decision intelligence** from **language intelligence**:

- **Deterministic Layer (Core Brain)**  
  Handles:
  - Task state inference (percentage-based, rule-driven)
  - Intervention decisions
  - System constraints and guardrails

- **LLM Layer (Interaction Brain)**  
  Handles:
  - Natural language conversations
  - HELP flow responses
  - Message generation

⚠️ Critical Rule:  
LLM must NOT control:
- state transitions
- deadlines
- extensions
- system decisions

---

## 2. Core Intelligence Model

### 2.1 Dual-Layer Intelligence

| Layer | Responsibility |
|-------|----------------|
| Rule Engine | What to do & when |
| LLM | How to say it |

### 2.2 Platform Abstraction Model

All platform-specific behavior is isolated in the Platform Adapter.  
The rest of the system works only with Normalized Messages.

```
[Telegram / Slack] → Platform Adapter → Normalized Message → API Gateway
[System Action]    → Platform Adapter → Platform-Specific Output
```

---

## 3. System Components (Detailed)

### 3.0 Platform Adapter Layer (NEW)

Decouples the system from platform-specific APIs.

#### Inbound (Receiving):
- Parses Telegram update / Slack event payload
- Extracts: `platform_id`, `message_text`, `poll_response`, `message_id`
- Normalizes into:

```json
{
  "platform": "telegram",
  "platform_id": "123456789",
  "message_id": "msg_abc",
  "type": "poll_response",
  "payload": "DONE"
}
```

#### Outbound (Sending):
- Converts system actions to platform-native format
- Telegram: sends `sendMessage`, `sendPoll`, `editMessageText`
- Slack: sends `chat.postMessage`, Block Kit buttons

---

### 3.1 API Gateway

- Receives normalized messages from Platform Adapter
- Routes to:
  - User Registration Service (unknown platform_id)
  - Task Service (task creation/updates)
  - Interaction Handler (poll responses, messages)

---

### 3.2 User Registration Service (NEW)

#### Trigger:
First message from an unknown `platform_id`.

#### Registration Flow:

```
[Unknown platform_id detected]
        ↓
[Send Welcome Message]
        ↓
[Collect Name]
        ↓
[Collect Email]
        ↓
[Collect Phone]
        ↓
[Collect Timezone — IANA string or UTC offset]
        ↓
[Collect Summary Trigger Time — default: 08:00]
        ↓
[Create User + UserSettings in DB]
        ↓
[Confirm: "You're all set!"]
```

#### State machine for registration:
Uses a `registration_state` field to track progress across multiple messages.

---

### 3.3 Task Service

- CRUD operations on tasks
- Validates: task_date = today only
- Enforces: max 2 extensions
- Manages state field transitions (initiated by State Inference Engine)

---

### 3.4 State Inference Engine

#### Inputs:
- `start_time`, `deadline`, `current_time`
- `last_response_time`
- `ignored_prompts` count
- `extensions_count`

#### Percentage-Based AT_RISK Logic:

```python
total_duration = deadline - start_time  # in seconds
time_remaining = deadline - current_time

pct_remaining = (time_remaining / total_duration) * 100

if pct_remaining <= 10:
    trigger = "AT_RISK_2"   # Urgent — decision poll
elif pct_remaining <= 35:
    trigger = "AT_RISK_1"   # Warning — status poll
```

#### Output:

```json
{
  "state": "AT_RISK",
  "trigger": "AT_RISK_1",
  "pct_remaining": 28.4,
  "reason": "35% threshold crossed, no recent response"
}
```

#### Full State Resolution:

| Priority | State | Condition |
|----------|-------|-----------|
| 1 | COMPLETED | explicit DONE response |
| 2 | DROPPED | explicit DROP response |
| 3 | STALLED | ignored_prompts ≥ 3 OR extensions_count ≥ 2 |
| 4 | AT_RISK | pct_remaining ≤ 35% |
| 5 | ACTIVE | start_time passed, response ≤ cooldown window |
| 6 | NOT_STARTED | current_time < start_time |

---

### 3.5 Intervention Engine

#### Responsibilities:
- Check cooldown (time since last interaction)
- Validate action eligibility (not STALLED/COMPLETED/DROPPED)
- Select intervention type based on state + trigger

#### Decision Table:

| State | Trigger | Cooldown Clear? | Action |
|-------|---------|-----------------|--------|
| NOT_STARTED | start_time reached | — | Send start reminder |
| AT_RISK | AT_RISK_1 | Yes | Send status poll |
| AT_RISK | AT_RISK_2 | Yes | Send urgent + decision poll |
| STALLED | — | — | Send 1 escalation, mark no-more-action |
| ACTIVE | — | — | No action |
| COMPLETED / DROPPED | — | — | No action |

#### Output:

```json
{
  "action": "SEND_POLL",
  "type": "AT_RISK_CHECK",
  "urgency": "HIGH"
}
```

---

### 3.6 Conversation Orchestrator

Bridge between deterministic logic and LLM.

#### Responsibilities:
- Convert system action → structured prompt for LLM
- Inject task context and conversation history
- Manage HELP mode session lifecycle
- Return control to rule engine after HELP flow ends

#### HELP Mode Flow:

```
[User selects NEED_HELP]
        ↓
[Orchestrator: fetch task context + ConversationHistory]
        ↓
[LLM: begin conversational assistance]
        ↓
[Multi-turn conversation stored in ConversationHistory]
        ↓
[User signals done / timeout]
        ↓
[Orchestrator: return control to Intervention Engine]
```

---

### 3.7 LLM Service

#### Responsibilities:
- Generate natural language responses
- Drive HELP flow conversations

#### Input:

```json
{
  "intent": "HELP",
  "task_context": {
    "title": "Finish project report",
    "deadline": "2026-04-09T17:00:00",
    "pct_remaining": 28.4
  },
  "conversation_history": [
    { "role": "user", "message": "I don't know where to start" }
  ]
}
```

#### Output:

```json
{
  "message": "Let's break this down. What section feels most overwhelming right now?"
}
```

---

### 3.8 Scheduler

Two independent Celery Beat jobs:

#### Task Evaluation Job:
- Frequency: every 5–10 minutes
- Action: fetch all active tasks → run State Inference → run Intervention Engine

#### Daily Summary Job:
- Frequency: per-user, runs at `UserSettings.summary_trigger_time` (user local time)
- Action: generate and send daily summary for each user
- Adjustable: user can update via bot command (e.g., `/set_summary_time 09:00`)

---

### 3.9 Notification Service

- Accepts system action + formatted message
- Passes to Platform Adapter for delivery
- Logs `message_id` returned from platform into Interactions table

---

### 3.10 Interaction Handler

- Receives normalized poll responses and free-text
- Maps to system action:

| Response | Action |
|----------|--------|
| DONE | Mark COMPLETED |
| IN_PROGRESS | Extend task (if allowed) |
| DROP | Mark DROPPED |
| NEED_HELP | Trigger LLM help flow via Orchestrator |

- Updates `last_response_time` and logs to Interactions

---

## 4. Data Model

### Users

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| name | VARCHAR | From registration |
| email | VARCHAR | From registration |
| phone | VARCHAR | From registration |
| platform | ENUM | telegram / slack |
| platform_id | VARCHAR | Unique identifier per platform |
| timezone | VARCHAR | IANA string |
| summary_trigger_time | TIME | Default: 08:00 |
| registration_state | VARCHAR | Tracks onboarding step (null = complete) |
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
| task_date | DATE | One-day scope |
| start_time | TIMESTAMP | |
| deadline | TIMESTAMP | |
| state | ENUM | NOT_STARTED / ACTIVE / AT_RISK / STALLED / COMPLETED / DROPPED |
| extensions_count | INT | Default: 0, Max: 2 |
| ignored_prompts | INT | Default: 0, Max: 3 |
| last_response_time | TIMESTAMP | |
| no_more_action | BOOLEAN | Set true after STALLED escalation |
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
| content | TEXT | |
| response_type | ENUM | DONE / IN_PROGRESS / DROP / NEED_HELP / null |
| message_id | VARCHAR | Platform message ID |
| created_at | TIMESTAMP | |

---

### ConversationHistory (NEW)

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → Users |
| task_id | UUID | FK → Tasks (nullable) |
| role | ENUM | user / assistant |
| message | TEXT | |
| created_at | TIMESTAMP | |

Retention: configurable (default: 30 days)

---

### UserSettings (NEW)

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | Primary key |
| user_id | UUID | FK → Users |
| summary_trigger_time | TIME | Adjustable daily summary time |
| max_extensions | INT | Default: 2 |
| max_ignored_prompts | INT | Default: 3 |
| nudge_cooldown_minutes | INT | Min time between nudges |
| updated_at | TIMESTAMP | |

---

## 5. State Machine (Strict)

### States:
- NOT_STARTED
- ACTIVE
- AT_RISK
- STALLED
- COMPLETED
- DROPPED

### Transitions:

```text
NOT_STARTED ──start_time──→ ACTIVE
ACTIVE ──35% remaining──→ AT_RISK
AT_RISK ──DONE response──→ COMPLETED
AT_RISK ──DROP response──→ DROPPED
AT_RISK ──ignored×3 or ext×2──→ STALLED
ACTIVE ──ignored×3──→ STALLED
```

Terminal states (no further transitions):
- COMPLETED
- DROPPED
- STALLED

---

## 6. Event-Driven Flow

### Task Evaluation Cycle (every 5–10 min):

```
1. Scheduler triggers
2. Fetch active tasks (state ≠ COMPLETED, DROPPED)
3. Run State Inference Engine
4. Run Intervention Engine
5. If action required:
     → Conversation Orchestrator
     → LLM (if HELP intent)
     → Notification Service
     → Platform Adapter → User
6. Log interaction
7. Update task state
```

### Daily Summary Cycle (per user schedule):

```
1. Celery Beat triggers at user's summary_trigger_time
2. Fetch yesterday's tasks for user
3. Generate summary (completed, stalled, dropped)
4. Fetch today's tasks
5. Compose + send summary via Platform Adapter
```

---

## 7. Poll System (Structured Control)

### Options:
- DONE
- IN_PROGRESS
- DROP
- NEED_HELP

### Mapping:

| Response | Next State | Notes |
|----------|------------|-------|
| DONE | COMPLETED | Terminal |
| IN_PROGRESS | ACTIVE (extended) | Max 2 extensions |
| DROP | DROPPED | Terminal |
| NEED_HELP | ACTIVE (help mode) | LLM takes over |

---

## 8. HELP Flow (LLM-Driven)

### Flow:

```
1. User selects NEED_HELP
2. Orchestrator fetches task context + conversation history
3. System enters conversational mode
4. LLM interacts freely (multi-turn)
5. All messages saved to ConversationHistory
6. User signals done → control returns to Intervention Engine
```

### Boundaries:
- LLM cannot change task state
- LLM cannot modify deadlines
- LLM cannot extend tasks

---

## 9. Guardrails (CRITICAL)

| Rule | Value |
|------|-------|
| Max extensions | 2 |
| Max ignored prompts | 3 |
| State decisions | Rule Engine only |
| LLM scope | Message generation + HELP flow only |
| Cooldown | Configurable in UserSettings |
| Task scope | One day only (task_date) |
| Platform rate limits | Telegram: 30 msg/sec; Slack: tier-based |

---

## 10. Why This Architecture Is Sound

1. **Deterministic core**: state transitions never depend on LLM output
2. **Platform independence**: swap Telegram ↔ Slack without touching business logic
3. **Behavioral memory**: ConversationHistory enables context-aware HELP flows
4. **User-centric scheduling**: per-user summary times respect timezones
5. **Gradual escalation**: percentage thresholds scale to task length naturally

---

## 11. Future Intelligence Upgrades

- Confidence-based inference (probabilistic state scoring)
- User behavior modeling (completion rate, peak productivity windows)
- Semantic task understanding (auto-priority from task description)
- Auto-clustering of related tasks
- ML-based deadline suggestions

---

## END
