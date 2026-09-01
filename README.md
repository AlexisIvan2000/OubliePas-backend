# OubliePas API

Subscription and bill tracker. This repository is the FastAPI backend; the React
single-page app lives in the sibling repository `../frontend`.

A **commitment** is the recurring thing you track: a subscription or an invoice.
An **occurrence** is one dated instalment of it. The service stores both, warns
you before a due date, tells you when a payment is overdue, and tells you when a
free trial or a cancellation notice is about to close. It sends by email and by
web push, in the account's own time zone.

## Contents

- [Requirements](#requirements)
- [Getting started](#getting-started)
- [Commands](#commands)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Authentication](#authentication)
- [Database](#database)
- [The commitments domain](#the-commitments-domain)
- [Reminders](#reminders)
- [Push notifications](#push-notifications)
- [Time zones](#time-zones)
- [Avatars and object storage](#avatars-and-object-storage)
- [Rate limiting](#rate-limiting)
- [Observability](#observability)
- [API surface](#api-surface)
- [Tests](#tests)
- [Deployment](#deployment)
- [Traps that have already cost time](#traps-that-have-already-cost-time)

## Requirements

- Python 3.13, the version the suite runs on. Nothing pins it, so a deployment
  platform will pick its own default unless you tell it otherwise.
- PostgreSQL, two databases: one for development, one for tests.
- A [Resend](https://resend.com) API key for outgoing mail.
- Optional: Redis for rate limiting, S3-compatible object storage for avatars, a
  VAPID key pair for web push.

## Getting started

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
cp .env.example .env          # then fill it in
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m uvicorn app:app --reload
```

The virtualenv is not activated by default; call its interpreter explicitly, as
above. On Linux and macOS the interpreter is `.venv/bin/python`.

Interactive API documentation is served at `/docs` **only when `DEBUG=true`**.
The schema describes the whole surface of an API whose single client already
knows it, so nothing is gained by publishing it in production.

## Commands

```bash
.venv/Scripts/python.exe -m pytest                        # the whole suite
.venv/Scripts/python.exe -m pytest tests/unit -q          # unit only
.venv/Scripts/python.exe -m pytest tests/integration/test_commitments_api.py -k TestSummary

.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "message"
.venv/Scripts/python.exe -m alembic -x db_url=<url> upgrade head   # another database

.venv/Scripts/python.exe -m jobs.daily                    # the daily job
.venv/Scripts/python.exe scripts/generate_vapid_keys.py   # a fresh VAPID pair
```

`jobs.daily` prints a JSON report and exits 1 if any email send failed:

```json
{"date": "2026-08-28", "occurrences_generated": 12, "purged": 0,
 "reminders_purged": 3, "users": 5, "emails_sent": 7, "push_sent": 4,
 "push_failed": 0, "occurrences": 4, "overdue": 2, "actions": 1,
 "weekly_sent": 1, "weekly_skipped": 0, "weekly_failed": 0,
 "skipped": 0, "failed": 0}
```

`emails_sent` is a floor, not the bill: the Resend quota also counts
transactional mail (verification, password reset, address change), which does
not pass through this job. The Resend dashboard is the authority.

Push failures never change the exit code. Push is the fast channel, not the
reliable one, and a sleeping phone must not fail a run where every email went
out.

There is no linter or formatter configured.

## Configuration

Every variable is documented in `.env.example`. Three make the app refuse to
start when missing: `JWT_SECRET_KEY`, `RESEND_API_KEY`, and `DATABASE_URL` (or
`DB_URL`, which wins if both are set). A `postgresql://` URL is rewritten to
`postgresql+asyncpg://` at import.

Three boot-time checks in `core/config.py` exist to fail loudly rather than have
you debug a silent behaviour later:

- `check_cookie_policy` rejects `COOKIE_SAMESITE=none` without `COOKIE_SECURE`.
  Browsers drop such a cookie without a word.
- `check_cors_policy` rejects `CORS_ORIGINS=*`, which browsers refuse whenever
  credentials are allowed.
- `check_vapid_keys` refuses to boot on a key pair that is present but
  unreadable or mismatched. Absent keys boot fine, since push is a channel and
  not a condition. A mismatched pair raises nowhere at runtime: the push service
  rejects in silence, and nobody learns the reminders stopped.

Two settings decide whether authentication works at all in production:

| Front and API are on | `COOKIE_SAMESITE` | `COOKIE_DOMAIN` |
|---|---|---|
| different registrable domains | `none` (with `COOKIE_SECURE=true`) | empty |
| the same domain | `lax` | `.yourdomain.com` |

Get this wrong and login succeeds, then the session dies fifteen minutes later
with no error anywhere.

`TRUSTED_PROXY_COUNT` is the number of trusted proxies in front of the app, and
Railway places exactly one. At `0` the rate limiter keys on the TCP peer, which
is the proxy itself: every visitor then shares a single bucket. Never set it
above the real number of hops, since each extra hop makes one more entry of
`X-Forwarded-For` forgeable by the client.

Without `REDIS_URL` the rate-limit counters live in process memory. They reset
on every redeploy and each replica keeps its own, which multiplies the effective
limit by the number of instances.

## Architecture

```
api/v1/client/*.py  ->  services/**  ->  repositories/*.py  ->  models/db/*.py
```

Routers stay thin. They resolve dependencies, call one service method, and
return a Pydantic response model. Every service and repository is constructed in
`api/dependencies.py` as an `Annotated[..., Depends(...)]` alias
(`CommitmentServiceDep`, `CurrentUserDep`, `PushRepoDep`), so new wiring goes
there rather than inside a route.

### The tree

```
app.py                     application, middleware stack, exception handlers,
                           logging setup, the lifespan that runs migrations
pytest.ini                 markers (unit, integration, e2e) and test paths
alembic.ini                migration config, URL supplied at runtime
requirements.txt           25 runtime packages, curated by hand
requirements-dev.txt       the above plus pytest
.env.example               every variable, documented, no real value

api/
  dependencies.py          every injectable, as Annotated aliases. New wiring
                           goes here, never inside a route
  responses.py             builds the user payload field by field. A load-bearing
                           file: see the traps at the end
  router.py (v1/)          mounts the four client routers under /v1
  v1/client/auth.py        register, verify, login, Google, refresh, logout
  v1/client/user.py        profile, avatar, password, email change, deletion
  v1/client/commitments.py commitments, occurrences, batches, trash, summary
  v1/client/push.py        VAPID key, subscriptions, test notification
  v1/admin/                empty, reserved for a future admin surface
  middlewares/
    request_context.py     binds a request id and a caller for the whole
                           request, writes the access log. Added last, so it
                           wraps everything
    security_headers.py    HSTS, CSP, nosniff, frame and referrer policy,
                           permissions policy, and the two cross-origin
                           isolation headers. Relaxes CSP on /docs only
                           when DEBUG is on
    server_error.py        catches what escapes above the envelope and returns
                           a fixed body, so no traceback reaches a client

core/
  config.py                reads the environment, fails fast on the three
                           required variables, holds the three boot-time checks
  database.py              async engine, session factory, and the get_session
                           that commits through an AppException
  security.py              argon2 hashing, JWT creation and decoding, token
                           hashing, one-time-code generation
  clock.py                 the only clock read in the project, and the two
                           notions of today
  cookies.py               issues and clears the refresh cookie, one place for
                           the SameSite and domain rules
  exceptions.py            every AppException, each with its code and status
  rate_limit.py            slowapi setup, the limit constants, the IP resolver
  observability.py         request id and caller in context, the logging filter
  migrations.py            runs alembic from lifespan under a blocking lock
  validators.py            currency normalisation, disposable email blocklist

models/
  db/base.py               the declarative base
  db/user_db.py            users, refresh_tokens, verification_attempts,
                           push_subscriptions
  db/commitments_db.py     commitments, commitment_occurrences,
                           occurrence_reminders, weekly_digests, and every
                           domain constant the schemas and CHECKs import
  schemas/auth_schema.py   registration, login, tokens, the user response
  schemas/user_schema.py   profile update, password and email changes
  schemas/commitment_schema.py  commitments, occurrences, batches, the summary
  schemas/push_schema.py   subscription payloads, capped at 2048 characters

repositories/
  auth_repository.py       users: lookup, creation, verification, counters
  refresh_token_repository.py  session rows, always by hash
  commitment_repository.py commitments and occurrences. Every user-facing read
                           passes through _live() or _live_occurrences()
  push_repository.py       subscriptions, the upsert on endpoint, the cap
  digest_repository.py     which accounts already got which week

services/
  authentication/
    email_password.py      register, verify, login, lockout, password reset
    google_auth.py         OAuth exchange, account matching, linking
    tokens.py              issues a token pair and stores the refresh hash
  commitments/
    commitment_service.py  the rules: ceiling, editing, settlement, summary
    occurrence_generator.py  materialises occurrences, horizon and floor
    action_window.py       trial and cancellation deadlines, computed never stored
  notifications/
    reminder_service.py    groups by account, applies the switches, sends
    reminder_window.py     the only definition of the three windows
    weekly_digest.py       the Monday recap, with no guard on Monday
  pushing/
    push_sender.py         RFC 8291 encryption, VAPID signature, the POST
    endpoint_policy.py     which addresses the server is allowed to call
  emailing/
    email_sender.py        Resend client, one method per message
    messages.py            every string, in French and English
    layout.py              the shared HTML shell. No images, ever
    otp_service.py         issues, throttles and checks one-time codes
  user_profile/
    user_profile.py        profile updates, password and email changes, deletion
    avatar_service.py      upload rules: type by magic bytes, size cap
    image_sanitizer.py     re-encodes through Pillow, strips EXIF, caps pixels
  storage/
    object_storage.py      S3-compatible client, signed read URLs

jobs/
  daily.py                 purge, generate, send, under an advisory lock

scripts/
  generate_vapid_keys.py   a fresh pair in raw base64url, or --from-pem
  purge_refresh_tokens.py  drops expired session rows

alembic/
  env.py                   takes a live connection from config.attributes, or
                           opens its own engine when there is none
  versions/                18 migrations, oldest first

tests/
  conftest.py              the test database, the mail and push boxes, the
                           rate-limit switch
  unit/                    pure logic, no database
  integration/             end to end through the HTTP API
  e2e/                     read-only checks against a live deployment
```

### Errors

Every failure is an `AppException` subclass in `core/exceptions.py`, carrying
`status_code`, `code` and `message`. The handler in `app.py` renders it as:

```json
{"detail": {"code": "COMMITMENT_LIMIT_REACHED", "message": "...", "type": "subscription", "limit": 25}}
```

Validation failures use the same envelope with an extra `errors` list of field
names. The frontend switches on `code` and never on the message, so wording can
change language without breaking a client. **Never raise a bare
`HTTPException`.**

### The session

`get_session` in `core/database.py` **commits even when an `AppException`
propagates**. That is deliberate: a failed one-time-code attempt still has to
persist its counter, or an attacker could retry forever by always failing. Only
unexpected exceptions roll back.

### Route order

Literal paths (`/trash`, `/restore`, `/occurrences/late`, `/batch-status`) are
registered before `/{commitment_id}`, or FastAPI matches the parameterised route
first and tries to parse `trash` as a UUID.

## Authentication

Two ways in, one session model.

### Email and password

1. `POST /v1/auth/register` creates the account and mails a six-digit code. It
   returns a message, never a token: an unverified account cannot obtain one.
   Disposable email domains are refused, including on the Google path.
2. `POST /v1/auth/verify-email` checks the code, marks the account verified and
   issues the session.
3. `POST /v1/auth/login` refuses an unverified, disabled or locked-out account.

Passwords are hashed with **argon2** (`argon2-cffi`), capped at 128 characters,
and rehashed transparently when the parameters change. A login attempt against
an unknown address still runs `dummy_verify_async`, so a missing account and a
wrong password take the same time and cannot be told apart.

**Verification is enforced where tokens are issued, not where they are read.**
`get_current_user` never looks at `is_verified`. It does not have to, because
each of the four paths that hands out a token settles it first: `login` raises
`EmailNotVerified`, `verify-email` verifies before issuing, Google sets
`is_verified` on sign-in, and `refresh` needs a token one of those three
produced. Nothing sets the flag back to false. Add a fifth path that issues a
token and the guard is gone, with nothing downstream to catch it.

### Google

`POST /v1/auth/google/start` returns an authorization URL; the client comes back
to `POST /v1/auth/google` with the code and PKCE verifier. A Google account is
verified on arrival, and reuses the same repositories, the same session model
and the same disposable-domain filter. `google_sub` is stored and unique, so a
Google identity maps to exactly one account.

An account can hold both: someone who signed up with a password and later used
Google keeps one row, and `POST /v1/users/me/set-password` lets a Google-only
account add a password without knowing an old one.

### Sessions

| | |
|---|---|
| Access token | bearer JWT, `HS256`, 15 minutes, carries `sub` and `role` |
| Refresh token | opaque, 30 days, httpOnly cookie, stored **hashed** (SHA-256) in `refresh_tokens` |

The refresh token is never stored in clear text: a database dump does not hand
over live sessions. `POST /v1/auth/refresh` reads the cookie, verifies the hash,
and rotates. `POST /v1/auth/logout` revokes the row and clears the cookie.

### One-time codes

Verification, password reset and email change all go through
`services/emailing/otp_service.py`. Codes expire after **15 minutes**, and
resending is capped at **5 per hour** per account.

Attempts are counted per user **and per kind** in `verification_attempts`, so a
failed reset cannot lock out a verification. The counter is incremented **before**
the code is compared: an attempt that crashes mid-check still costs the caller
one try.

### Lockout

Twenty failed logins within one hour lock the account for the rest of that
window. A locked account gets the same `InvalidCredentials` answer as a wrong
password, so probing cannot tell the two apart.

### Email change

`POST /v1/users/me/change-email` stores the target in `pending_email` and mails
a code to it. The address only moves on `confirm-email-change`. The current
address keeps working until then, so a typo cannot lock anyone out of their own
account.

## Database

Eight tables. Everything hangs off `users`, and every foreign key to it cascades
on delete, so `POST /v1/users/me/delete` really removes the account.

```
                          users
                            |
      +----------+----------+----------+------------------+
      |          |          |          |                  |
 refresh_   verification_  push_    weekly_          commitments
  tokens      attempts   subscript.  digests               |
                                                  commitment_occurrences
                                                           |
                                                  occurrence_reminders
```

`commitment_occurrences` carries `user_id` **as well as** `commitment_id`. The
denormalisation is deliberate: every dashboard query filters by user and date,
and the `ix_occurrences_user_due` index would otherwise need a join to be usable.

### `users`

Identity, credentials, preferences.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | primary key |
| `first_name`, `last_name` | varchar(100) | last name optional |
| `email` | varchar(255) | unique, indexed |
| `password_hash` | text | null for a Google-only account |
| `is_verified` | bool | |
| `verification_code_hash`, `..._expires_at` | | signup code |
| `pending_email` | varchar(255) | target of an address change |
| `reset_code_hash`, `..._expires_at` | | password reset |
| `email_change_code_hash`, `..._expires_at` | | address change |
| `last_code_sent_at`, `code_resend_count` | | resend throttle |
| `failed_login_count`, `last_failed_login_at` | | lockout window |
| `avatar_url`, `avatar_key` | text | remote URL (Google) or object key (uploaded) |
| `currency` | varchar(3) | |
| `locale` | varchar(5) | `fr` or `en`, CHECK constraint |
| `timezone` | varchar(64) | IANA name, defaults to `UTC` |
| `reminder_email_enabled` | bool | channel switch |
| `reminder_push_enabled` | bool | channel switch |
| `reminder_notice_enabled` | bool | family switch |
| `reminder_overdue_enabled` | bool | family switch |
| `reminder_action_enabled` | bool | family switch |
| `reminder_weekly_enabled` | bool | weekly digest |
| `default_reminder_days` | int | 0 to 30, CHECK constraint |
| `google_sub` | varchar(255) | unique, indexed |
| `role` | varchar(20) | `user`, `admin`, `super_admin`, CHECK constraint |
| `is_active`, `deactivated_at` | | reserved for a future admin surface |

`timezone` carries no CHECK constraint, unlike `locale`. The IANA list has
hundreds of entries and changes with the tz database; a constraint frozen in a
migration would one day refuse a perfectly valid zone. The input schema
validates instead, against the database actually installed.

`is_active` and `deactivated_at` are reserved. Do not read or write them in
feature work.

### `commitments`

The recurring thing.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | primary key |
| `user_id` | UUID | to `users.id`, cascade, indexed |
| `title` | varchar(100) | |
| `type` | varchar(20) | `subscription` or `invoice` |
| `category` | varchar(50) | free text, defaults to `other` |
| `amount` | numeric(10,2) | CHECK `> 0` |
| `frequency` | varchar(20) | `weekly`, `monthly`, `quarterly`, `yearly`, `oneoff` |
| `starts_on` | date | first due date |
| `ends_on` | date | optional, CHECK `>= starts_on` |
| `trial_ends_on` | date | optional, CHECK `<= starts_on` |
| `cancellation_notice_days` | int | optional, CHECK between 1 and 60 |
| `reminder_days_before` | int | CHECK between 0 and 30 |
| `is_reminder_enabled` | bool | per-line switch |
| `status` | varchar(20) | `active`, `paused`, `archived` |
| `notes` | text | optional |
| `deleted_at` | timestamptz | soft delete, indexed |

Indexes: `(user_id)`, `(user_id, status)`, `(status)`, `(type)`,
`(deleted_at)`.

Every one of those enumerations exists twice, as a Python tuple in
`models/db/commitments_db.py` and as a CHECK constraint built from that same
tuple. They cannot drift.

### `commitment_occurrences`

One dated instalment. **Materialised rows, not computed on read.**

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | primary key |
| `commitment_id` | UUID | to `commitments.id`, cascade, indexed |
| `user_id` | UUID | to `users.id`, cascade, indexed |
| `due_date` | date | |
| `amount` | numeric(10,2) | CHECK `> 0`, copied at generation |
| `status` | varchar(20) | `pending`, `paid`, `skipped` |
| `paid_at` | timestamptz | when the button was clicked |
| `paid_on` | date | the day the user says they paid |

Unique on `(commitment_id, due_date)`: regenerating is idempotent, and a second
pass of the generator cannot double an instalment.

Indexes: `(user_id, due_date)` for every dashboard read, `(due_date, status)`
for the reminder sweep, which scans across all users.

`amount` is copied rather than read through the commitment. Raising a
subscription's price must not rewrite what last month actually cost.

### `occurrence_reminders`

The log that makes a reminder go out exactly once.

| Column | Type | Notes |
|---|---|---|
| `occurrence_id` | UUID | to `commitment_occurrences.id`, cascade, indexed |
| `kind` | varchar(20) | `notice`, `overdue`, `action_required` |
| `channel` | varchar(10) | `email` or `push` |
| `sent_at` | timestamptz | |

Unique on `(occurrence_id, kind, channel)`. The key holds no hour and no time
zone, which is exactly why a second pass on the same day sends nothing, and why
a replayed past date sends nothing either. The channel is part of the key, or
turning push on would silence email.

Rows are purged after `PURGE_REMINDERS_AFTER_DAYS` (45), which is the overdue
window of 30 days plus a 15-day margin.

### `weekly_digests`

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID | to `users.id`, cascade |
| `week_start` | date | the Monday of that week |
| `channel` | varchar(10) | `email` or `push` |

Unique on `(user_id, week_start, channel)`. That key alone makes the send
unique, which is what lets a failed Monday be retried the next day.

### `refresh_tokens`

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID | to `users.id`, cascade, indexed |
| `token_hash` | varchar(64) | SHA-256, unique, indexed |
| `expires_at` | timestamptz | |
| `revoked`, `revoked_at` | | logout marks rather than deletes |
| `device_info` | varchar(255) | user agent, truncated |

### `verification_attempts`

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID | to `users.id`, cascade |
| `kind` | varchar(20) | `verification`, `reset`, `email_change` |
| `count` | int | |

Unique on `(user_id, kind)`. Per kind, so a failed reset cannot lock out a
verification.

### `push_subscriptions`

One row per browser that accepted notifications.

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID | to `users.id`, cascade, indexed |
| `endpoint` | text | **unique across the whole table** |
| `p256dh`, `auth` | varchar(255) | the browser's encryption keys, base64url |
| `user_agent` | varchar(255) | truncated |
| `created_at`, `last_seen_at` | timestamptz | |

The endpoint identifies the subscription on its own and the browser hands back
the same one on every re-subscribe, so it serves as the upsert key rather than a
`(user, device)` pair that neither side can build.

**That column is a bearer credential.** It is unique table-wide and the upsert
reassigns `user_id`, which is what lets a shared device change hands, and means
whoever learns an endpoint takes the subscription over. The victim would see
nothing, since the client reads the browser's own subscription and not this
table. So it is logged nowhere: refusals record the host and the reason, never
the address.

`MAX_PUSH_SUBSCRIPTIONS_PER_USER` (10) bounds the table, the least recently seen
device making room. Without it an account could register thirty addresses an
hour, forever, and the daily job would post to each one.

## The commitments domain

### Types and frequencies

A **subscription** is a recurring service. An **invoice** is a fixed cost owed
anyway. Same shape, different meaning, and the distinction runs down to the
reminder wording.

Five frequencies: `weekly`, `monthly`, `quarterly`, `yearly`, `oneoff`.

### Generation

`OccurrenceGenerator` writes occurrences ahead of time, and both `sync` and
`resync` **require** the day to work from. An optional parameter would be a
server clock coming back through the side door, with no call site changing and
nothing to signal it.

- **Horizon.** `horizon_days(frequency)` returns at least 90 days, but never
  less than one full period plus 30 days: yearly gets 396, quarterly 122. A flat
  90-day window left annual commitments with no row for most of the year, so
  their next due date rendered as null everywhere.
- **Floor.** Recurring frequencies never generate rows before today, or a
  subscription backdated two years would fabricate two years of history.
  `oneoff` is the one exception: a bill dated yesterday really existed, so it is
  kept.

The floor is the owner's day, not the server's. `sync_all_active` reads the time
zone alongside each row, through a join, rather than one query per user.

### Editing

- `RESCHEDULING_FIELDS` (amount, frequency, starts_on, ends_on, status) trigger
  `generator.resync`, which deletes pending occurrences from today onward and
  regenerates.
- `ACTION_FIELDS` (trial_ends_on, cancellation_notice_days) only clear the
  `action_required` reminder log, so it can fire again.

### Payment dates

`paid_at` is the timestamp of the click. `paid_on` is the date the user says
they paid, defaulting to today and refused if in the future. The schedule does
not shift when a payment is late: that is a product decision, not an oversight.

### Action windows

`services/commitments/action_window.py` computes the trial-end and
cancellation-notice deadlines on the fly from the commitment. They are never
stored. A trial window opens `max(reminder_days_before, 3)` days before the
deadline, since three days is the least useful warning for something that costs
money if missed.

### The ceiling

An account tracks at most `MAX_COMMITMENTS_PER_TYPE` (25) of each type. Only
`COUNTED_STATUSES` (`active`, `paused`) count, so archiving frees a slot while
keeping the history.

- `_guard_limit` refuses a creation.
- `_guard_entry` closes the two side doors: un-archiving above the ceiling, and
  changing a commitment's type into a full bucket.
- `_guard_restore` caps the trash exception at `RESTORE_CEILING_FACTOR` (2)
  times the ceiling, per type.

Restoring is allowed to exceed the ceiling, and the counter says `26 / 25`
rather than pretending, because refusing would make the trash a trap: you would
throw things in to make room and never get them back. That permission has a
roof, though. Without one the cycle create 25, empty into the trash, create 25
again, restore the lot added 25 lines per turn without bound, and the ceiling
stopped bounding anything, along with the occurrences the daily job generates
behind them for everyone. Two times the ceiling still lets a full trash come
back in one go. Archived rows count in no population, so they return whatever
the room left. The refusal precedes the write: an oversized batch releases
nothing at all, not even its first lines.

### Soft delete

Deleting sets `deleted_at`; the daily job purges after `PURGE_AFTER_DAYS` (30).
Every read in `CommitmentRepository` goes through `_live()` or
`_live_occurrences()`, which apply the `deleted_at IS NULL` guard.

**Do not write a bare `select(Commitment)` on a user-facing path.** Fourteen
queries route through those two helpers precisely so the guard cannot be
forgotten in one of them and leak a deleted amount back into a total.

`DELETE /v1/commitments/trash` is the one erasure with no way back.

### Batch operations

`PATCH /batch-status`, `POST /batch-delete` and `POST /restore` all take at most
`MAX_BATCH_IDS` (200) identifiers. Every one is scoped by user in SQL. An
identifier belonging to somebody else falls into neither the `changed` nor the
`blocked` list, so the response is no existence oracle.

## Reminders

Three families, keyed by `(occurrence_id, kind, channel)`:

| kind | trigger |
|---|---|
| `notice` | due date approaching, `reminder_days_before` ahead |
| `overdue` | past due by `OVERDUE_REMINDER_DAYS` (3) and still unpaid, up to 30 days |
| `action_required` | a trial or cancellation-notice deadline is open |

`ReminderService._dispatch` skips an account unless it is active, verified, has
the channel switch on (`FAMILY_SWITCH` maps each family to its own preference)
**and** the family switch on. It commits after each account, so a mid-run
failure does not resend to those already reached. A failed send is logged and
left unmarked, and the next run retries it.

### The window

`services/notifications/reminder_window.py` is the only place the windows are
defined:

| | |
|---|---|
| `bounds(kind, day)` | the bounds of a family for a given day |
| `query_bounds(kind, day)` | the same, widened by one day on each side |
| `is_due(kind, occurrence, commitment, day)` | the same bounds, exact, with that person's day |

The SQL casts wide, the predicate decides. One day of margin because from UTC-12
to UTC+14 nobody's day is ever more than a day away from the server's; the
commitment's own lead time is widened in the query too, or an account to the
east would be cut before its date could rule.

**The bounds are an interval, never a date equality.** That is what makes a
skipped pass recoverable: what the predicate sets aside is not lost, the next
pass finds it again.

### The daily pass

`jobs/daily.py` runs purge, then generate, then send, guarded by a Postgres
advisory lock (`pg_try_advisory_lock`) so two workers cannot both fire. Railway
runs it at `0 12 * * *`, scheduled in the dashboard.

The job starts from a single instant, and every account reads it in its own
calendar. Midday UTC lands at:

```
Honolulu   UTC-10   02:00                  <- night
Vancouver  UTC-07   05:00
Moncton    UTC-03   09:00
London     UTC+01   13:00
Paris      UTC+02   14:00
Kolkata    UTC+05:30 17:30
Tokyo      UTC+09   21:00
Auckland   UTC+12   00:00, next day        <- night
Kiritimati UTC+14   02:00, next day        <- night
```

Most of the Americas get it in the morning, Europe in the afternoon, Asia in the
evening. Two edges get it at night, not one: the far east has already turned the
page, which the per-zone selection accounts for, and Hawaii in the west has not.
Both are accepted. A single send hour cannot suit everyone, and moving it only
picks different losers. The day it matters, the job runs more often rather than
that constant moving.

### The weekly digest

`services/notifications/weekly_digest.py` has **no guard on Monday, and must not
gain one.** The `(user_id, week_start, channel)` key alone makes the send
unique, which is also what lets a failed Monday be retried the next day. The
window runs from the **run date** to Sunday, never from Monday: a Tuesday
catch-up would otherwise announce due dates already covered by `overdue`.

Email only. Offering it with the email channel off would promise a send with no
way out.

### The quota alert

When a run sends more than `RESEND_DAILY_ALERT_THRESHOLD` (80, that is 80% of
the free Resend tier), the job emails `OPERATOR_EMAIL` once. The alert is never
counted in `emails_sent` and its own failure never fails the job: it is the
thermometer, not the patient. Its body is a date and an integer, both produced
by the server, so no user data can reach it.

## Push notifications

Web push, standards only, no `pywebpush`. That library pulls 19 transitive
packages and is synchronous; `py-vapid` for the signature, `http-ece` for the
RFC 8291 encryption and `httpx` for the POST is five packages and stays async.

### Sending

`services/pushing/push_sender.py :: send` must pass a **fresh ephemeral key** to
`http_ece.encrypt`. `private_key=None` raises before any connection opens, so
every push failed in a few milliseconds and no test caught it, because the
`pushbox` fixture replaces `send` wholesale and `http_ece` never ran.
`tests/unit/test_push_sender.py` fakes only the network for that reason.

`_headers` signs the push service **origin**, not the full endpoint: a token
signed for one address is refused by the others. Only `404` and `410` mean a
dead address; anything else raises, and a `403` means the VAPID pair changed
under an existing subscription.

Notification bodies never carry an amount. A locked screen is a public place.

### Which addresses are allowed

`services/pushing/endpoint_policy.py` is why the service cannot be pointed at
anything it likes. `subscription.endpoint` used to be an unchecked string, and
`POST /v1/push/test` made the server POST to it on demand: an ordinary account
could probe the private network and read the outcome in its own response.

The allowlist names the four push services (Google FCM, Mozilla, Microsoft WNS,
Apple). The floor underneath it knows nothing about services and only refuses
what an API must never call: `https` only, no IP literal, no internal name, port
443 only. It is checked **twice**, at the route (answering
`PUSH_ENDPOINT_REFUSED`) and again in `send`, for rows written before the list
and for the cron, which reads the table without passing the route. A refused
address is reported `gone` rather than raised, so the caller that already
handles dead addresses drops the row instead of ringing forever.

`refusal_reason` reads `parts.hostname`, never `netloc`:
`https://fcm.googleapis.com@internal/` has the allowed host as its netloc and the
real target as its hostname.

### Keys

The VAPID pair is generated once with `scripts/generate_vapid_keys.py`, in raw
base64url. A PEM file is refused: `py_vapid` signs from `from_raw`, which will
not read one, and the API stops at boot rather than discovering it at the first
click. Keys are stripped once, at read time, since the check used to strip
before parsing while the signature did not, and a key pasted with a newline
booted clean then failed on the first send.

**Losing the pair invalidates every existing subscription.** Browsers memorised
it; changing pairs forces every user to switch notifications back on, and none
of them is told. Back it up like `JWT_SECRET_KEY`.

## Time zones

`users.timezone` holds an IANA name and defaults to `UTC`. The browser captures
it at signup, since the server could only guess it from an IP address and guess
wrong. Accounts created before the column catch up silently at their next sign
in, and only those left at the default: a zone already chosen is never
overwritten by a laptop on a trip.

`core/clock.py` holds the two notions of today and nothing else does:

| | |
|---|---|
| `today_for(user)` | the day that person sees |
| `today_in(name)` | the same, for loops holding a zone without a person |
| `date_at(moment, name)` | that instant, read in that calendar |
| `today_utc()` | what belongs to nobody: purges, logs, the cron lock |

`_now()` is the single clock read in the project. Everything derives from it,
which makes a date freezable at one point and guarantees a single pass never
sees two instants.

`today_for` never raises. A tz database can drop a zone between versions, and a
dashboard answering 500 over that would be worse than the offset; it falls back
to UTC and says so in the log.

`CommitmentService` receives the zone at construction, once per request, rather
than as an eighth parameter to eight methods. **A test sweeps `services`, `api`
and `repositories` and refuses `today_utc()`, `date.today()` and
`datetime.today()`.** Two files are tolerated and named: `core/clock.py`, where
the definition lives, and `jobs/daily.py`, whose date serves the purge and the
report.

## Avatars and object storage

`POST /v1/users/me/avatar` accepts JPEG and PNG only, at most 9 MB, and the type
is read from the file's magic bytes rather than the declared content type. The
image is re-encoded through Pillow, which strips EXIF, and stored under a random
key in an S3-compatible bucket. Reads go through a signed URL that expires after
`AVATAR_URL_EXPIRE_SECONDS` (86400).

`avatar_url` and `avatar_key` are both on `users` for a reason: the first holds a
remote Google picture, the second an object we own. `avatar_url` is **not**
writable through `PATCH /v1/users/me`. Open to the client it would bypass the
size cap, the image cleaner and the storage, and leak every viewer's IP address
to a server of the writer's choosing on each render.

Without `API_S3` configured, avatar upload answers `STORAGE_UNAVAILABLE` and the
rest of the app is unaffected.

## Rate limiting

`core/rate_limit.py` keys on the JWT `sub` when present, else on the client IP
resolved through `TRUSTED_PROXY_COUNT` hops of `X-Forwarded-For`.

| Route group | Limit |
|---|---|
| Reads (`READ_LIMIT`) | 120/minute |
| Mail-sending routes (`EMAIL_LIMIT`, keyed by IP) | 5/minute and 20/hour |
| `POST /auth/login`, `/auth/refresh` (by IP) | 10/minute |
| Commitment writes | 60/minute |
| Batch operations | 30/minute |
| Whole-collection deletes | 6/minute |
| Push subscribe and unsubscribe | 30/hour |
| `POST /push/test` | 10/hour |
| Account deletion, password changes | 5/hour |

Limiting is disabled by an autouse fixture in tests; opt back in with the
`rate_limit_on` fixture.

## Observability

`app.py :: configure_logging` writes to **stderr, never stdout**. Outside a
terminal Python block-buffers stdout, so a 500 traceback sits in an 8 KB buffer
while the platform shows nothing.

`core/observability.py :: ContextFilter` puts the request id and the caller on
every record. Identity is bound **once per request** rather than passed to each
log call, which is why lines written by services that know nothing about it
still carry it. `SAFE_ID` filters a client-supplied `X-Request-ID`: without it a
newline in that header writes a fake log line.

`api/middlewares/request_context.py` is added **last** in `app.py`, so it wraps
everything and the context exists before anything logs. `QUIET_PATHS` silences
`/health`, hit every fifteen seconds. `caller_of` returns the JWT `sub` or
`ip:<addr>`, the prefix keeping an anonymous caller from reading as an account
id.

`QUIET_CODES` drops `INVALID_ACCESS_TOKEN` to DEBUG: the client refreshes on its
own, and logging every 401 buries real incidents. The validation handler logs
**field names only**, since request bodies carry passwords and one-time codes.

The unhandled-exception handler returns a fixed body. No exception text ever
reaches a client.

## API surface

Every route is under `/v1`. All of them require a bearer token except the auth
routes marked public.

### `/v1/auth`

| Method | Path | Public | |
|---|---|---|---|
| POST | `/register` | yes | creates the account, mails a code |
| POST | `/verify-email` | yes | verifies and opens the session |
| POST | `/resend-verification` | yes | new code, 5/hour |
| POST | `/login` | yes | |
| POST | `/google/start` | yes | authorization URL |
| POST | `/google` | yes | exchanges the code |
| POST | `/forgot-password` | yes | mails a reset code |
| POST | `/reset-password` | yes | |
| POST | `/refresh` | cookie | rotates the session |
| POST | `/logout` | | revokes and clears the cookie |
| GET | `/me` | | the account |

### `/v1/users`

| Method | Path | |
|---|---|---|
| GET | `/me` | the account |
| PATCH | `/me` | names, currency, locale, time zone, every reminder switch |
| POST | `/me/avatar` | upload |
| DELETE | `/me/avatar` | |
| POST | `/me/change-password` | needs the current one |
| POST | `/me/set-password` | for a Google-only account |
| POST | `/me/change-email` | mails a code to the new address |
| POST | `/me/confirm-email-change` | |
| POST | `/me/resend-email-change` | |
| POST | `/me/delete` | needs the password, cascades everywhere |

### `/v1/commitments`

| Method | Path | |
|---|---|---|
| GET | `/summary` | the dashboard: month total, by type, by status, by category, upcoming, late count |
| GET | `` | list, filterable by type and status |
| POST | `` | create |
| GET | `/{id}` | |
| PATCH | `/{id}` | |
| DELETE | `/{id}` | to the trash |
| DELETE | `` | whole collection to the trash, filterable |
| PATCH | `/batch-status` | |
| POST | `/batch-delete` | |
| POST | `/restore` | out of the trash |
| GET | `/trash` | |
| DELETE | `/trash` | permanent |
| GET | `/occurrences` | defaults to the current month, in the account's zone |
| GET | `/occurrences/late` | |
| PATCH | `/occurrences/{id}` | mark paid, skipped or back to pending |

### `/v1/push`

| Method | Path | |
|---|---|---|
| GET | `/key` | the public VAPID key, null when the server has no pair |
| POST | `/subscriptions` | register a browser |
| DELETE | `/subscriptions` | |
| POST | `/test` | one notification, proof the channel works |

`GET /health` returns `{"status": "ok"}` without touching the database.

## Tests

1,176 tests collected, across `tests/unit` (pure logic, no database),
`tests/integration` (end to end through the HTTP API against a real Postgres)
and `tests/e2e` (read-only checks against a live deployment, skipped unless
`E2E_API_URL` and `E2E_FRONTEND_URL` are set).

Tests need `DB_URL_TEST` in `.env`, pointing at a real Postgres database
separate from the development one. `tests/conftest.py` swaps
`core.database.AsyncSessionLocal` for a session bound to it before importing
`app`.

**Never run two suites at once against that database.** A session fixture drops
and rebuilds the schema, so two runs produce errors in unrelated files and look
like a regression that is not there.

Every guard added to this codebase is expected to prove it bites: reintroduce
the defect it prevents and watch a named test fail, before calling it a guard.

## Deployment

Two separate Railway services deploy from this repository: the API, and a second
one that runs `python -m jobs.daily` at `0 12 * * *`.

**Start command, healthcheck path, cron schedule and restart policy are set in
the Railway dashboard**, one set per service, and the dashboard is the single
source of truth for them. `railway.json` and `railway.cron.json` are no longer
tracked by git: Railway retires Config as Code on 2026-12-01, and the one
setting they carried that the dashboard did not had never taken effect anyway.
Both files stay in the working tree, git-ignored, as a record of the intended
values. Railway builds from the repository, so it never reads them.

Migrations run at startup, not before it. The `lifespan` in `app.py` calls
`core/migrations.py`, which runs `alembic upgrade head` through Alembic's API on
the engine's own connection: no subprocess, no second engine, no synchronous
driver (`psycopg2` is deliberately absent). A **blocking** advisory lock
serialises replicas, since an opportunistic one would let the loser serve
requests against a half-built schema. A failure stops the container instead of
letting the app answer 500 on every route that touches the database.

The cron service imports `core.config` exactly like the API, so it needs the same
three required variables plus `FRONTEND_URL`, the sender addresses and
`OPERATOR_EMAIL`. Without them it dies at startup and reminders silently stop.

`/health` returns `{"status": "ok"}` without touching the database, so a
deployment can be reported healthy while Postgres is unreachable.

## Traps that have already cost time

**A `preDeployCommand` declared in Config as Code was never executed.** Three
deployments served 500s on `relation "users" does not exist` while the build log
showed no Alembic output at all. That is why migrations now run from `lifespan`,
and why a green deployment is not evidence that a pre-deploy hook ran.

**Migrations are never exercised by the test suite.** `tests/conftest.py` builds
the schema with `Base.metadata.create_all` and replaces `app.run_migrations`
with a no-op, so a green run says nothing about Alembic. After adding a column,
run `alembic upgrade head` against the development database yourself, or the app
raises `UndefinedColumnError` at runtime while every test passes.

**`requirements.txt` is curated by hand.** It was a global `pip freeze` until the
first deployment: 316 lines including Django, Flask, Streamlit, spaCy, PyQt6 and
`psycopg2`, whose source build needs `pg_config` and fails on a stock build
image. It now lists the 25 packages the import graph actually needs. Add an
entry only when something imports it, pin it, keep the file UTF-8, and check
that a clean environment can install it and import the app:

```bash
python -m venv /tmp/check && /tmp/check/bin/pip install -r requirements.txt
```

The rule is *no compilation required*, not *a wheel exists*: `http-ece` ships
only an sdist but is pure Python and builds anywhere. What must never enter the
list is a package needing a compiler or a system library, which is how
`psycopg2` broke the first deployment. `tzdata` is in there because `zoneinfo`
finds no zones at all on Windows without it, and no dated test can be written.

**`api/responses.py :: user_response` is written field by field.** A preference
added to the table, the migration and both schemas but not there returns the
default forever: the write succeeds, the read lies, and the switch falls back
under the user's finger with no error anywhere. That happened with
`reminder_weekly_enabled`.
`tests/integration/test_profile.py :: TestEveryPreferenceComesBack` now walks
every field of `UpdateProfile`, not just the ones prefixed `reminder_`, and will
fail on the next omission.

**Domain constants live in `models/db/commitments_db.py`** and are imported by
schemas, services and CHECK constraints alike. Add new ones there so the three
cannot drift apart.

**Comments are rare on purpose.** Write one only when it explains *why* a
non-obvious choice was made; the ones in `occurrence_generator.py` and
`commitment_repository.py` are the model to follow. `CLAUDE.md` carries the map
of the lines that look removable and are not.
