# Notification System

Notifications deliver quiz-ready alerts to connected users over WebSocket. The full lifecycle is: creation during quiz generation → polling by the scheduler → WebSocket push to the client → status update.

---

## Database Schema

**Table:** `notifications`

| Column             | Type            | Nullable | Default     | Description                                      |
|--------------------|-----------------|----------|-------------|--------------------------------------------------|
| `id`               | `INTEGER`       | No       | autoincr.   | Primary key                                      |
| `user_id`          | `INTEGER`       | No       | —           | FK → `users.id` (CASCADE DELETE), indexed        |
| `status`           | `VARCHAR(32)`   | No       | `"pending"` | `"pending"` \| `"complete"` \| `"canceled"`      |
| `target_datetime`  | `DATETIME(tz)`  | No       | —           | UTC timestamp when the notification becomes due, indexed |
| `content`          | `VARCHAR(2048)` | No       | —           | Human-readable notification text                 |
| `quiz_id`          | `INTEGER`       | Yes      | `NULL`      | FK → `quizzes.id` (SET NULL on quiz deletion)    |
| `created_at`       | `DATETIME(tz)`  | No       | now         | Inherited from `_Timestamps` mixin               |
| `updated_at`       | `DATETIME(tz)`  | No       | now         | Inherited from `_Timestamps` mixin, auto-updated |

**Source:** [`agent/models.py`](../agent/models.py) — `Notification` class.

### Status transitions

```
pending ──► complete   (scheduler delivered successfully)
pending ──► canceled   (set externally; scheduler skips these)
```

A notification stays `pending` if the user is offline at dispatch time — it will be retried on the next scheduler tick.

---

## Creation

Notifications are created as part of the `generate_quizzes_for_user_events(user_id)` workflow in [`agent/quiz_workflow.py`](../agent/quiz_workflow.py).

For every calendar event belonging to the user:
1. An LLM generates a `Quiz` record.
2. A `Notification` is created pointing to that quiz, with `target_datetime` set to `event.end_datetime` — so the alert fires when the event ends.

```python
notif = Notification(
    user_id=user_id,
    status="pending",
    target_datetime=event.end_datetime,       # fires when the event ends
    content=f"Time to test your knowledge on '{event.name}'! "
             "Your personalised quiz is ready.",
    quiz_id=quiz.id,
)
```

---

## Scheduler pickup

**File:** [`agent/scheduler.py`](../agent/scheduler.py)

`start_scheduler()` (called at app startup in [`main.py`](../main.py)) launches two asyncio tasks. One of them — `_notification_dispatch_loop()` — wakes every **60 seconds** and calls `dispatch_due_notifications()`.

```
App startup
  └─ start_scheduler()
       └─ _notification_dispatch_loop()   ← runs forever
            │  sleep 60 s
            └─ dispatch_due_notifications()
```

### Querying due notifications

`dispatch_due_notifications()` is implemented in [`agent/quiz_workflow.py`](../agent/quiz_workflow.py) as `_dispatch_due_notifications_impl`. It queries:

```sql
SELECT * FROM notifications
WHERE status = 'pending'
  AND target_datetime <= NOW();
```

Any notification that is `pending` and whose `target_datetime` is in the past (or now) is eligible for delivery.

---

## Delivery

For each due notification the dispatcher:

1. **Checks connectivity** — calls `ws_manager.is_connected(user_id)`. If the user has no active WebSocket, the notification is **skipped** and left `pending`; it will be retried on the next tick.

2. **Builds the quiz payload** — if `quiz_id` is set, fetches the `Quiz` row and serialises it:
   ```json
   {
     "id": 42,
     "title": "Photosynthesis Basics",
     "topic": "Biology",
     "estimated_duration_minutes": 5,
     "questions": [ ... ]
   }
   ```
   If there is no associated quiz, the `"quiz"` key is `null`.

3. **Sends over WebSocket** — calls `ws_manager.send(user_id, payload)`. The full JSON message sent to the client:
   ```json
   {
     "type": "notification",
     "notification_id": 7,
     "content": "Time to test your knowledge on 'Intro to ML'! Your personalised quiz is ready.",
     "quiz": { ... } | null
   }
   ```

4. **Marks complete** — only if `send()` returns `True` (i.e. the message was written to the socket without error) is `status` updated to `"complete"`. If the send fails (race: socket dropped between the connectivity check and the write), the notification remains `pending` and retries next tick.

5. **Commits** — a single `session.commit()` is issued after processing all due notifications for that tick, writing all status changes atomically.

---

## WebSocket transport

**File:** [`app/connection_manager.py`](../app/connection_manager.py)

`ConnectionManager` maintains a `dict[user_id → WebSocket]`. One connection per user is supported; a new connection replaces any previous one.

Clients connect to:

```
ws://<host>/ws?token=<auth_token>
```

The token is validated server-side (`routes_ws.py`). Once connected, the socket is kept alive until the client disconnects — the server echoes any text frames back as `{"type": "ack", "echo": ...}`. Notifications arrive as unsolicited server-push frames.

**File:** [`app/routes_ws.py`](../app/routes_ws.py)

---

## End-to-end flow

```
Quiz generation
  └─ Notification created (status=pending, target_datetime=event.end_datetime)

         ▼  (scheduler tick — every 60 s)

Scheduler queries pending notifications where target_datetime ≤ now
  ├─ user offline?  → leave pending, retry next tick
  └─ user online?
       ├─ fetch Quiz if quiz_id set
       ├─ send WebSocket JSON  {"type":"notification", ...}
       ├─ success?  → status = "complete"
       └─ failure?  → leave pending, retry next tick
```

---

## No REST API

There are no HTTP endpoints for notifications. All delivery is WebSocket push. The client must maintain a persistent WebSocket connection to receive notifications in real time; missed notifications (user was offline) are held in the database and delivered on the next scheduler tick after the user reconnects.
