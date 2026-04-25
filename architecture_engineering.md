# Progress-Aware Conversational Task Assistant
## Engineering-Grade Architecture Specification

---

## 1. Design Philosophy

The system separates **decision intelligence** from **language intelligence**:

- **Deterministic Layer (Core Brain)**
  - Task state inference (percentage-based, rule-driven)
  - Intervention decisions and cooldowns
  - System constraints and guardrails
  - Per-user time calibration via `clock_offset_seconds`

- **LLM Layer (Interaction Brain)**
  - Natural-language task parsing (free-text → structured `ParsedTask`)
  - Multi-turn HELP conversations
  - Daily-summary generation

⚠️ Critical rule — the LLM must NOT control:
- state transitions
- deadlines
- extension counts
- system decisions

---

## 2. Core Intelligence Model

### 2.1 Dual-Layer Intelligence

| Layer | Responsibility |
|-------|----------------|
| Rule Engine | What to do & when |
| LLM | How to say it (and how to read free-text input) |

### 2.2 Platform Abstraction Model

All platform-specific behavior lives in `app/platform/`. The rest of the system only ever
sees `NormalizedMessage` (inbound) and `OutboundMessage` (outbound).

```
[Telegram / Discord]    →    Platform Adapter    →    NormalizedMessage    →    Update Handler
[System Action]         →    Platform Adapter    →    Platform-specific output (text / inline buttons)
```

Active adapter is selected by `app/platform/__init__.py::get_platform_adapter()`:
Telegram if `TELEGRAM_BOT_TOKEN` is set, otherwise Discord.

---

## 3. System Components (Detailed)

### 3.0 Platform Adapter Layer

**Files**: `app/platform/base.py`, `telegram_adapter.py`, `discord_adapter.py`.

**Inbound** — `parse_incoming(payload) → NormalizedMessage | None`:

```json
{
  "platform": "telegram",
  "platform_id": "123456789",
  "message_type": "POLL_RESPONSE",
  "text": null,
  "command": null,
  "poll_response": "DONE",
  "poll_message_id": "msg_abc",
  "raw": { "...": "..." }
}
```

`message_type` is one of `TEXT` / `POLL_RESPONSE` / `COMMAND`.

**Outbound** — `send_message(OutboundMessage)` and `send_poll(platform_id, question, options)`:

- **Telegram**: uses `python-telegram-bot` v21, MarkdownV2-escaped text, polls rendered as
  inline-keyboard buttons (2 buttons per row, max 4).
- **Discord**: uses `discord.py` v2 over a persistent WebSocket. Polls are rendered as
  Discord component buttons (DONE green, IN_PROGRESS primary, DROP danger, NEED_HELP grey;
  10-minute timeout). Telegram MarkdownV2 is converted to Discord-flavored Markdown.

Both adapters return the platform `message_id` so that subsequent button clicks can be
matched back to the original `Interaction` row.

---

### 3.1 Update Handler

**File**: `app/handlers/webhook_handler.py`.

`/_process_update()` is the single entry point used by both `run_polling.py` (Telegram)
and `run_discord.py` (Discord), as well as the FastAPI `/webhook/telegram` route.

Routing:

```
parse → NormalizedMessage
        ↓
look up User by platform_id
        ├─ unknown                  → start_registration()
        ├─ registration_state set
        │     ├─ /start             → restart_registration()
        │     └─ otherwise          → process_registration_step()
        └─ fully registered
              ├─ POLL_RESPONSE      → handle_poll_response()
              ├─ COMMAND            → handle_command()
              └─ TEXT
                    ├─ active HELP  → orchestrator.handle_help_request()
                    └─ otherwise    → task_service.create_task_from_nl()
```

---

### 3.2 User Registration Service

**File**: `app/services/registration.py`.

Triggered by:
- First message from an unknown `platform_id`, or
- The `/register` command at any time (re-registration).

Sequential `registration_state` values:

| State | Prompt | Validation |
|-------|--------|------------|
| `awaiting_name` | "What's your name?" | non-empty |
| `awaiting_email` | "Email?" | regex |
| `awaiting_phone` | "Phone? (or 'skip')" | optional |
| `awaiting_timezone` | "Timezone? (IANA or UTC offset)" | `zoneinfo` lookup or `UTC±H[:MM]` parser |
| `awaiting_summary_time` | "Daily summary time? (HH:MM)" | 24-hour parse |
| `awaiting_current_time` | "What time is it for you right now?" | computes `clock_offset_seconds`, clamped ±43200s |

On completion: `registration_state = null`, `registered_at = now()`, and a confirmation
message is sent. `UserSettings` is created with sensible defaults
(`max_extensions=2`, `max_ignored_prompts=3`, `nudge_cooldown_minutes=30`,
`clock_offset_seconds` from step 6).

Why a clock-offset step? Even with the correct IANA timezone, users' device clocks can
drift; calibrating against the user-reported wall-clock at registration time keeps the
intervention timing aligned with the user's lived experience.

---

### 3.3 Task Service

**File**: `app/services/task_service.py`.

- `create_task_from_nl(user, text)` — calls `llm.parse_task()` (which returns ISO-8601 +
  timezone). Validates: `start < deadline`, both same calendar day, duration ≥ 5 minutes.
- `get_active_tasks()` — non-terminal, today, `no_more_action=false`. Used by the scheduler.
- `get_tasks_for_user_today()` / `get_tasks_for_user_by_date()` — used by `/tasks` and the
  daily-summary service.
- `extend_task()` — bumps the deadline and `extensions_count`. The extension amount is
  decided by the caller (50% of original duration on AT_RISK_2 IN_PROGRESS, min 5 min).
- `mark_no_more_action()` — set after ESCALATION or FINAL_POLL is sent.
- `increment_ignored_prompts()` — bumped when a poll goes unanswered for a full evaluation
  cycle.

---

### 3.4 State Inference Engine

**File**: `app/services/state_engine.py`.

Pure function; no I/O. Uses an "effective now" computed as
`datetime.now(UTC) + timedelta(seconds=user_settings.clock_offset_seconds)`.

```python
total_duration = (deadline - start_time).total_seconds()
time_remaining = (deadline - effective_now).total_seconds()
pct_remaining  = (time_remaining / total_duration) * 100
```

Resolution priority (top wins):

| Priority | State | Condition |
|----------|-------|-----------|
| 1 | COMPLETED | already terminal |
| 2 | DROPPED | already terminal |
| 3 | STALLED | `ignored_prompts ≥ max_ignored_prompts` OR `extensions_count ≥ max_extensions` |
| 4 | AT_RISK (trigger=AT_RISK_2) | `pct_remaining ≤ AT_RISK_THRESHOLD_2` (default 10%) |
| 5 | AT_RISK (trigger=AT_RISK_1) | `pct_remaining ≤ AT_RISK_THRESHOLD_1` (default 35%) |
| 6 | NOT_STARTED | `effective_now < start_time` |
| 7 | ACTIVE | otherwise |

Output:

```json
{
  "state": "AT_RISK",
  "trigger": "AT_RISK_1",
  "pct_remaining": 28.4,
  "reason": "35% threshold crossed"
}
```

`should_transition(current, new)` blocks transitions out of terminal states.

---

### 3.5 Intervention Engine

**File**: `app/services/intervention_engine.py`.

```
                                State + trigger
                                       │
            ┌──────────────────────────┼─────────────────────────┐
            ▼                          ▼                          ▼
     COMPLETED / DROPPED          STALLED               AT_RISK / NOT_STARTED / ACTIVE
            │                       │                            │
            └─ NO_ACTION            ├─ no_more_action=true       ├─ NOT_STARTED→ACTIVE crossing
                                    │     → NO_ACTION            │     → SEND_START_REMINDER (once)
                                    │                            ├─ AT_RISK_1, cooldown clear
                                    ├─ extensions_count > 0      │     → SEND_STATUS_POLL
                                    │     → SEND_FINAL_POLL      ├─ AT_RISK_2, cooldown clear
                                    └─ otherwise                 │     → SEND_URGENT_POLL
                                          → SEND_ESCALATION      └─ ACTIVE → NO_ACTION
```

**Cooldown** is anchored on `task.last_response_time` and uses a *mini-cooldown* equal to
10% of the task's total duration (minimum 2 minutes). This replaces the older flat
30-minute cooldown so a 30-minute task can still receive both AT_RISK_1 and AT_RISK_2
nudges. `nudge_cooldown_minutes` in `UserSettings` is preserved on the row but unused by
the current engine.

After `SEND_ESCALATION` or `SEND_FINAL_POLL`, the caller sets `task.no_more_action=true`
to make the action idempotent across evaluation cycles.

---

### 3.6 Conversation Orchestrator

**File**: `app/services/orchestrator.py`.

`handle_help_request(db, task, platform_id, user_message=None)`:

1. Fetch the last 10 turns of `ConversationHistory` scoped to this task.
2. Build the HELP system prompt, injecting `task.title`, `task.deadline`, `pct_remaining`.
3. Call `llm.generate(prompt, history, system_prompt)`.
4. Persist the user turn (if any) and the assistant turn.
5. Send the assistant turn via the platform adapter.

The session is *implicit*: a user is "in HELP mode" for a given task if their most
recent Interaction on that task is a `NEED_HELP` poll response. The next free-text
message routes to the orchestrator; any new task creation breaks the session.

The LLM is sandboxed — it can talk, but cannot mutate task state.

---

### 3.7 LLM Service

**Files**: `app/llm/base.py`, `factory.py`, `prompts.py`, `<provider>_provider.py`.

`BaseLLMProvider` interface:

```python
async def generate(prompt, history=None, system_prompt=None) -> LLMResponse
async def parse_task(user_message, user_timezone, current_date) -> ParsedTask
async def health_check() -> bool
```

Providers (selected via `LLM_PROVIDER` env var, default `openai`):

| Provider | Module | Default model |
|----------|--------|---------------|
| OpenAI | `openai_provider.py` | `gpt-4o` |
| Gemini | `gemini_provider.py` | `gemini-1.5-pro` |
| Anthropic | `anthropic_provider.py` | `claude-3-5-sonnet-20241022` |
| Ollama (local) | `ollama_provider.py` | `llama3.2` |
| HuggingFace (local) | `huggingface_provider.py` | `mistralai/Mistral-7B-Instruct-v0.2` |

System prompts in `prompts.py`:
- `TASK_PARSE_SYSTEM` — extract `{title, start_time, deadline, description, confidence}`
  as JSON. ISO-8601 with timezone, accepts `8.30pm` and `8:30pm`. Defaults: today, 1 hour.
- `HELP_SYSTEM` — concise, 3 sentences max unless asked.
- `DAILY_SUMMARY_SYSTEM` — motivating morning briefing, sparse emoji.
- `DAILY_SUMMARY_USER` — task lists + completion rate, under 150 words.

---

### 3.8 Scheduler

**Files**: `app/scheduler/celery_app.py`, `jobs.py`.

Two jobs, each implemented as both a Celery task and a plain async function:

| Job | Cadence | Behavior |
|-----|---------|----------|
| `evaluate_all_tasks` | every `TASK_EVALUATION_INTERVAL_MINUTES` (default 5) | Iterates active tasks, runs state engine + intervention engine + dispatch |
| `dispatch_daily_summaries` | every minute | For each registered user, computes their local time via `clock_offset_seconds` and dispatches if `(hour, minute) == summary_trigger_time` |

**Execution modes**:
- **Telegram**: Celery worker + Celery Beat. The bot itself runs in `run_polling.py`.
- **Discord**: `run_discord.py` owns the asyncio event loop and runs both jobs as
  in-process `asyncio.create_task` loops sleeping 60 seconds. Celery is not required.

Windows note: the jobs use a private `_jobs_engine` configured with `NullPool` and call a
freshly-created event loop per Celery invocation to avoid asyncpg's incompatibility with
the Windows ProactorEventLoop.

---

### 3.9 Notification Service

**File**: `app/services/notification_service.py`.

`dispatch_intervention(db, task, decision, platform_id)`:
1. Build the message text and (if applicable) poll options for `decision.action`.
2. Call the active adapter's `send_message` / `send_poll`.
3. Persist an `Interaction` row with `type`, `content`, and the `message_id` returned.
4. Update `task.last_response_time` so cooldown tracking moves forward.

| Action | Type | Message |
|--------|------|---------|
| SEND_START_REMINDER | REMINDER | "⏰ Time to start: <task>" |
| SEND_STATUS_POLL | POLL | "📊 Quick check-in" + 4 options |
| SEND_URGENT_POLL | URGENT_POLL | "🚨 Urgent" + 4 options |
| SEND_ESCALATION | SYSTEM | "🔴 Task stalled, no further reminders" |
| SEND_FINAL_POLL | POLL | "⌛ Time's up" + DONE/STALL |

Plain-text helper `send_plain_message(platform_id, text)` is also exposed for ad-hoc
acknowledgements (e.g. "✅ Task marked done").

---

### 3.10 Interaction Handler

**File**: `app/handlers/interaction_handler.py`.

```python
handle_poll_response(db, user, msg):
    interaction = lookup_by_message_id(msg.poll_message_id)   # binds click ↔ task
    interaction.response_type = msg.poll_response
    match msg.poll_response:
        case DONE:        update_task_state(task, COMPLETED) + ack
        case IN_PROGRESS: extend if interaction.type == URGENT_POLL else acknowledge
        case DROP:        update_task_state(task, DROPPED) + ack
        case NEED_HELP:   orchestrator.handle_help_request(task)
        case STALL:       update_task_state(task, STALLED) + ack       # FINAL_POLL only
```

Free-text routes to the orchestrator (active HELP) or `create_task_from_nl()`. Commands:

| Command | Behavior |
|---------|----------|
| `/start`, `/help` | Show command list and intro. During registration `/start` restarts the flow. |
| `/register` | Reset `registration_state` and re-run onboarding. |
| `/tasks` | List today's tasks with state emoji (`⏳ ACTIVE`, `⚠️ AT_RISK`, `🔴 STALLED`, `✅ COMPLETED`, `❌ DROPPED`). |
| `/summary` | Send the daily summary immediately. |

Times are rendered in the user's `timezone` via `zoneinfo.ZoneInfo`.

---

## 4. Data Model

See `app/database/models.py` for the SQLAlchemy definitions. Tables:

### Users

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| name / email / phone | VARCHAR | Phone optional |
| platform | ENUM | `telegram` / `discord` (`slack` reserved, no adapter) |
| platform_id | VARCHAR | Unique, indexed |
| timezone | VARCHAR | IANA, default `UTC` |
| summary_trigger_time | TIME | Default `08:00` |
| registration_state | VARCHAR | Onboarding step; null = complete |
| registered_at | TIMESTAMP | Set when registration completes |
| created_at | TIMESTAMP | |

### Tasks

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK → Users |
| title / description | TEXT | |
| task_date | DATE | One-day scope |
| start_time / deadline | TIMESTAMPTZ | |
| state | ENUM | `NOT_STARTED` / `ACTIVE` / `AT_RISK` / `STALLED` / `COMPLETED` / `DROPPED` |
| extensions_count | INT | Default 0, max 2 |
| ignored_prompts | INT | Default 0, max 3 |
| last_response_time | TIMESTAMPTZ | Cooldown anchor |
| no_more_action | BOOLEAN | Set after ESCALATION / FINAL_POLL |
| created_at / updated_at | TIMESTAMPTZ | |

### Interactions

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| task_id / user_id | UUID | FKs |
| type | ENUM | `POLL` / `URGENT_POLL` / `REMINDER` / `MESSAGE` / `SYSTEM` |
| content | TEXT | |
| response_type | ENUM | `DONE` / `IN_PROGRESS` / `DROP` / `NEED_HELP` / `STALL` / null |
| message_id | VARCHAR | Platform message id (for matching button clicks) |
| created_at | TIMESTAMP | |

### ConversationHistory

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK → Users |
| task_id | UUID | FK → Tasks (nullable) |
| role | ENUM | `user` / `assistant` |
| message | TEXT | |
| created_at | TIMESTAMP | |

Retention is currently policy-only (no automatic prune); orchestrator only fetches the
last 10 turns into LLM context.

### UserSettings

| Field | Type | Notes |
|-------|------|-------|
| id | UUID | PK |
| user_id | UUID | FK → Users (unique) |
| summary_trigger_time | TIME | Default `08:00` |
| max_extensions | INT | Default 2 |
| max_ignored_prompts | INT | Default 3 |
| nudge_cooldown_minutes | INT | Default 30 (legacy; mini-cooldown used today) |
| clock_offset_seconds | INT | Calibrated at registration; clamped ±12h |
| updated_at | TIMESTAMPTZ | |

---

## 5. State Machine (Strict)

### States
- NOT_STARTED
- ACTIVE
- AT_RISK
- STALLED
- COMPLETED
- DROPPED

### Transitions

```
NOT_STARTED ──start_time reached──→ ACTIVE
ACTIVE       ──pct_remaining ≤ 35──→ AT_RISK
AT_RISK      ──pct_remaining ≤ 10──→ AT_RISK (trigger=AT_RISK_2)
AT_RISK      ──DONE────────────────→ COMPLETED
AT_RISK      ──DROP────────────────→ DROPPED
AT_RISK      ──ignored×3 / ext×2──→ STALLED
ACTIVE       ──ignored×3──────────→ STALLED
STALLED      ──FINAL_POLL DONE────→ COMPLETED
STALLED      ──FINAL_POLL STALL───→ STALLED (terminal)
```

Terminal: COMPLETED, DROPPED, STALLED-after-final.

---

## 6. Event-Driven Flow

### Task evaluation cycle

```
1. Scheduler triggers (Celery Beat or Discord asyncio loop)
2. Fetch active tasks (state ≠ COMPLETED/DROPPED, no_more_action=false)
3. Run State Inference Engine
4. Run Intervention Engine
5. If action required:
     → Notification Service builds message/poll
     → Platform Adapter dispatches
     → Interaction logged with message_id
6. If state changed: persist new state
7. If action ∈ {SEND_ESCALATION, SEND_FINAL_POLL}: set no_more_action=true
```

### Daily summary cycle

```
1. Every minute, for each registered user:
2.   local_now = utcnow + clock_offset_seconds
3.   if (local_now.hour, local_now.minute) == summary_trigger_time:
4.       fetch yesterday + today's tasks
5.       LLM-generate (or fallback plaintext)
6.       send via Platform Adapter
```

---

## 7. Poll System

### Standard polls (AT_RISK_1, AT_RISK_2)

| Option | Effect |
|--------|--------|
| DONE | → COMPLETED |
| IN_PROGRESS | AT_RISK_1: ack only. AT_RISK_2: extend by 50% of original duration (min 5 min); increments `extensions_count`. |
| DROP | → DROPPED |
| NEED_HELP | Enters multi-turn HELP via Orchestrator |

### Final poll (after extension's deadline passes)

| Option | Effect |
|--------|--------|
| DONE | → COMPLETED |
| STALL | → STALLED (terminal) |

Polls are matched back to their originating Interaction by `message_id`, ensuring a click
on an old poll only updates the correct task even if the user has multiple tasks with
pending nudges.

---

## 8. HELP Flow (LLM-Driven)

```
User clicks NEED_HELP
        ↓
Orchestrator: load last 10 turns from ConversationHistory (scoped to task_id)
        ↓
LLM.generate(prompt, history, HELP_SYSTEM with task context)
        ↓
Save user + assistant turns
        ↓
Send assistant message to user
        ↓
Next free-text message → continue session
        ↓
Any new task creation breaks the session (implicit "done")
```

Boundaries:
- LLM cannot change task state.
- LLM cannot modify deadlines or extension counts.
- HELP context is always task-scoped — no cross-task leakage.

---

## 9. Guardrails (CRITICAL)

| Rule | Value |
|------|-------|
| Max extensions | 2 (`UserSettings.max_extensions`) |
| Max ignored prompts | 3 (`UserSettings.max_ignored_prompts`) |
| State decisions | Rule engine only |
| LLM scope | Task parsing + HELP flow + summary generation |
| Mini-cooldown between AT_RISK nudges | 10% of task duration, min 2 min |
| `no_more_action` | Set after ESCALATION / FINAL_POLL — prevents repeats |
| Task scope | One day only (`task_date`) |
| Clock-offset clamp | ±12 hours |
| Platform priority | Telegram > Discord (per token presence) |
| Platform rate limits | Telegram 30 msg/sec; Discord per-channel limits |

---

## 10. Why This Architecture Is Sound

1. **Deterministic core**: state transitions never depend on LLM output.
2. **Platform independence**: adapter layer abstracts Telegram/Discord; adding Slack later
   is a single new adapter.
3. **Two execution modes**: Telegram via Celery+Beat, Discord via in-process asyncio loops —
   the same job code runs in both.
4. **User-centric scheduling**: per-user `summary_trigger_time` plus calibrated
   `clock_offset_seconds` mean reminders fire at the user's actual wall-clock, not the
   server's.
5. **Idempotent escalation**: `no_more_action` and `message_id`-keyed Interactions make
   retries and overlapping evaluation cycles safe.
6. **Behavioral memory**: ConversationHistory enables coherent multi-turn HELP flows.
7. **Pluggable LLM**: 5 providers behind one interface — local (Ollama, HuggingFace) or
   hosted (OpenAI, Gemini, Anthropic).

---

## 11. Future Intelligence Upgrades

- Confidence-weighted state inference (probabilistic instead of threshold-based)
- User behavior modeling (completion-rate, peak-hours, streaks)
- Auto-priority and auto-clustering from task descriptions
- ML-based deadline suggestions
- Kafka-backed event bus (already provisioned in `docker-compose.yml`) to decouple
  evaluation from notification at scale

---

## END
