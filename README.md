# Koya Clinic Booking API

FastAPI + PostgreSQL + responsive same-origin web interface. Implements the [Koya assessment API contract](https://koyaacademy.com/assessment-api).

## Setup

Set `DATABASE_URL` (persistent PostgreSQL) and `API_KEY` (private secret) in your host's environment. Do not place secrets in the public repository.

Install: `pip install -r requirements.txt`

Run: `uvicorn app:app --host 0.0.0.0 --port 8000`

Production start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`

Tables initialize on startup. The patient UI is served at `/`. Routes: POST /reset, GET /slots, POST /bookings, GET /bookings, GET and DELETE /bookings/{id}. All API requests require `X-API-Key`. Reset replaces slots even if they are in the past.

Database constraints protect a slot against simultaneous confirmed bookings; idempotency keys are persisted; booking attempts are counted per email.

## Deployment status

Requires a provisioned PostgreSQL database and an HTTPS Python app host (such as Render). Run integration tests and verify the URL before submitting.
