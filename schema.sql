CREATE TABLE IF NOT EXISTS slots(id TEXT PRIMARY KEY,provider TEXT NOT NULL,start_at TIMESTAMPTZ NOT NULL,end_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS bookings(id UUID PRIMARY KEY,slot_id TEXT NOT NULL REFERENCES slots(id),name TEXT NOT NULL,email TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN('confirmed','cancelled')),created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS unique_confirmed_slot ON bookings(slot_id) WHERE status='confirmed';
CREATE INDEX IF NOT EXISTS bookings_email_page ON bookings(lower(email),created_at,id);
CREATE TABLE IF NOT EXISTS idempotency(key TEXT PRIMARY KEY,digest TEXT NOT NULL,status_code INTEGER NOT NULL,response JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS booking_attempts(id BIGSERIAL PRIMARY KEY,email TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS attempt_email_window ON booking_attempts(email,created_at);
