# OubliePas API

Subscription and bill tracker. This repository is the FastAPI backend; the React
single-page app lives in the sibling repository `../frontend`.

A **commitment** is the recurring thing you track, a subscription or an invoice.
An **occurrence** is one dated instalment of it. The service stores both, emails
you before a due date, and tells you when a free trial or a cancellation notice
is about to close.

## Requirements

- Python 3.13, the version the suite runs on. Nothing pins it, so a deployment
  platform will pick its own default unless you tell it otherwise
- PostgreSQL, two databases: one for development, one for tests
- A [Resend](https://resend.com) API key for outgoing mail
- Optional: Redis for rate limiting, S3-compatible object storage for avatars

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
The schema describes the whole surface of an API that has a single client which
already knows it, so nothing is gained by publishing it in production.

## Commands

```bash
.venv/Scripts/python.exe -m pytest                        # the whole suite
.venv/Scripts/python.exe -m pytest tests/unit -q          # unit only
.venv/Scripts/python.exe -m pytest tests/integration/test_commitments_api.py -k TestSummary

.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic revision --autogenerate -m "message"
.venv/Scripts/python.exe -m alembic -x db_url=<url> upgrade head   # another database

.venv/Scripts/python.exe -m jobs.daily                    # the nightly job
```

`jobs.daily` prints a JSON report and exits 1 if any send failed:

```json
{"date": "2026-08-28", "occurrences_generated": 12, "purged": 0,
 "reminders_purged": 3, "users": 5, "emails_sent": 7, "occurrences": 4,
 "overdue": 2, "actions": 1, "skipped": 0, "failed": 0}
```

`emails_sent` is a floor, not the bill: the Resend quota also counts
transactional mail (verification, password reset, address change), which does
not pass through this job. The Resend dashboard is the authority.

There is no linter or formatter configured.

## Configuration

Every variable is documented in `.env.example`. Three make the app refuse to
start when missing: `JWT_SECRET_KEY`, `RESEND_API_KEY`, and `DATABASE_URL` (or
`DB_URL`, which wins if both are set). A `postgresql://` URL is rewritten to
`postgresql+asyncpg://` at import.

Two boot-time checks in `core/config.py` exist to fail loudly rather than have
you debug a silent browser behaviour later:

- `check_cookie_policy` rejects `COOKIE_SAMESITE=none` without `COOKIE_SECURE`.
  Browsers drop such a cookie without a word.
- `check_cors_policy` rejects `CORS_ORIGINS=*`, which browsers refuse whenever
  credentials are allowed.

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

Routers stay thin: they resolve dependencies, call one service method, and
return a Pydantic response model. Every service and repository is wired in
`api/dependencies.py` as an `Annotated[..., Depends(...)]` alias, so new
collaborators are added there rather than instantiated inside a route.

Route order matters. Literal paths such as `/trash`, `/restore` and
`/occurrences/late` are registered before `/{commitment_id}`, or FastAPI matches
the parameterised route first.

### Errors

Forty-two typed errors live in `core/exceptions.py`, each an `AppException`
carrying `status_code`, `code` and `message`. The handler in `app.py` renders
them as `{"detail": {"code", "message"}}`; validation failures use the same
envelope with an extra `errors` list. The front end switches on `code`, so a
bare `HTTPException` is never raised.

`get_session` commits even when an `AppException` propagates. That is
deliberate: a failed one-time-code attempt still has to persist its counter.
Only unexpected exceptions roll back.

## The commitments domain

Occurrences are materialised rows, not computed on read. `OccurrenceGenerator`
writes them ahead of time, and two rules govern how far:

- **Horizon.** `horizon_days(frequency)` returns at least 90 days, but never
  less than one full period plus 30: yearly gets 396, quarterly 122. A flat
  90-day window left annual commitments with no row for most of the year, so
  their next due date rendered as null everywhere.
- **Floor.** Recurring frequencies never generate rows before today. A
  subscription backdated two years must not fabricate two years of history.
  `oneoff` is the exception: a bill dated yesterday really existed.

Editing a commitment triggers different work depending on the field.
`RESCHEDULING_FIELDS` (amount, frequency, starts_on, ends_on, status) call
`generator.resync`, which deletes pending occurrences from today onward and
regenerates them. `ACTION_FIELDS` (trial_ends_on, cancellation_notice_days) only
clear the `action_required` reminder log so it can fire again.

`paid_at` is the timestamp of the click; `paid_on` is the date the user says
they paid, defaulting to today and refused if in the future. The schedule does
not shift when a payment is late, which is a product decision rather than an
oversight.

An account tracks at most `MAX_COMMITMENTS_PER_TYPE` (25) subscriptions and 25
invoices. Only `active` and `paused` count, so archiving frees a slot and the
archive keeps its full history.

### Soft delete

Deleting sets `deleted_at`; the daily job purges after `PURGE_AFTER_DAYS` (30).
Every read in `CommitmentRepository` goes through `_live()` or
`_live_occurrences()`, which apply the `deleted_at IS NULL` guard. Fourteen
queries route through those two helpers precisely so the guard cannot be
forgotten in one of them and leak a deleted amount back into a total.

## Reminders

Three families, keyed by `OccurrenceReminder(occurrence_id, kind)` with a unique
constraint so each reminder is sent once:

| kind | trigger |
|---|---|
| `notice` | due date approaching, `reminder_days_before` ahead |
| `overdue` | past due and still unpaid |
| `action_required` | a trial or cancellation-notice deadline is open |

`ReminderService._dispatch` skips a user unless they are active, verified, have
`reminder_email_enabled`, **and** have the switch for that family on
(`reminder_notice_enabled`, `reminder_overdue_enabled`,
`reminder_action_enabled`). It commits after each user so a mid-run failure
does not resend to those already emailed. A failed send is logged and left
unmarked, so the next run retries it.

`jobs/daily.py` runs purge, then generate, then send, guarded by a Postgres
advisory lock so two workers cannot both fire. When a run sends more than
`RESEND_DAILY_ALERT_THRESHOLD` (80, that is 80% of the free Resend tier) it
emails `OPERATOR_EMAIL` once. That alert is never counted in `emails_sent` and
its failure never fails the job: the alert is the thermometer, not the patient.

## Authentication

Access tokens are 15-minute bearer JWTs. The refresh token lives in an httpOnly
cookie and is stored hashed in `refresh_tokens`. Google sign-in shares the same
repositories and keeps the disposable-email filter.

One-time codes for email verification, password reset and email change all go
through `services/emailing/otp_service.py`. Attempts are counted per user *and
per kind* in `verification_attempts`, so a failed reset cannot lock out a
verification. The counter is incremented before the code is compared, which is
the security property: an attempt that crashes mid-check still costs the caller
one try.

## Tests

Over 900 tests across `tests/unit` (pure logic, no database) and
`tests/integration` (end to end through the HTTP API, against a real Postgres).
`tests/e2e` is a reserved placeholder and is empty.

Tests need `DB_URL_TEST` in `.env`, pointing at a real Postgres database
separate from the development one. `tests/conftest.py` swaps
`core.database.AsyncSessionLocal` for a session bound to it before importing
`app`. Rate limiting is disabled by an autouse fixture; opt back in with the
`rate_limit_on` fixture.

Every guard added to this codebase is expected to prove it bites: reintroduce
the defect it prevents and watch the test fail, before calling it a guard.

## Deployment

`railway.json` describes the API service and runs `alembic upgrade head` as a
pre-deploy command, so migrations are applied automatically before traffic
switches over. `railway.cron.json` describes a **second, separate service** that
runs `python -m jobs.daily` at `0 12 * * *`. The send time is fixed at 12:00 UTC
by design.

The cron service imports `core.config` exactly like the API, so it needs the
same three required variables plus `FRONTEND_URL`, the sender addresses and
`OPERATOR_EMAIL`. Without them it dies at startup and reminders silently stop.

`/health` returns `{"status": "ok"}` without touching the database, so a
deployment can be reported healthy while Postgres is unreachable.

## Traps that have already cost time

**Migrations are never exercised by the test suite.** `tests/conftest.py` builds
the schema with `Base.metadata.create_all`, so a green run says nothing about
Alembic. After adding a column, run `alembic upgrade head` against the
development database yourself, or the app raises `UndefinedColumnError` at
runtime while every test passes.

**`requirements.txt` is curated by hand.** It was a global `pip freeze` encoded
in UTF-16 until the first deployment, 316 lines including Django, Flask,
Streamlit, spaCy and PyQt6, and pip could not even read the file. It now lists
the 21 packages the import graph actually needs. Add an entry only when
something imports it, and keep the file UTF-8.

**Domain constants live in `models/db/commitments_db.py`** and are imported by
schemas, services and CHECK constraints alike. Add new ones there so the three
cannot drift apart.
