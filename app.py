"""Koya Cohort 4 clinic booking API."""
import base64
import hashlib
import hmac
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Koya Clinic Booking API", version="1.0")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
COLUMNS = "id,slot_id,name,email,status,created_at"

def err(msg, code, headers=None):
    return JSONResponse({"error":msg}, status_code=code, headers=headers)

def db():
    return psycopg.connect(os.environ["DATABASE_URL"])

# Tables are provisioned with schema.sql during deployment, not on each
# serverless cold start. Concurrent startup DDL can race in PostgreSQL.

@app.middleware("http")
async def auth(req: Request, next_call):
    if req.url.path == "/" or req.url.path.startswith("/static/"):
        return await next_call(req)
    api_key = os.getenv("API_KEY", "")
    if not api_key or not hmac.compare_digest(api_key,req.headers.get("X-API-Key","")):
        return err("invalid API key",401)
    return await next_call(req)

@app.get("/")
def homepage():
    return FileResponse(ROOT/"static"/"index.html")

def parse_dt(s):
    if not isinstance(s,str):
        raise ValueError("invalid date")
    dt=datetime.fromisoformat(s.replace("Z","+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)

def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")

def booking(row):
    return dict(zip(("id","slot_id","name","email","status","created_at"),(str(row[0]),row[1],row[2],row[3],row[4],iso(row[5]))))

@app.post("/reset")
async def reset(req: Request):
    try:
        body=await req.json()
        raw=body["slots"]
        if not isinstance(raw,list):
            raise ValueError()
        values=[]
        seen=set()
        for s in raw:
            if not isinstance(s,dict) or any(not isinstance(s.get(k),str) or not s[k] for k in ("id","provider","start","end")):
                raise ValueError()
            a,b=parse_dt(s["start"]),parse_dt(s["end"])
            if b<=a or s["id"] in seen:
                raise ValueError()
            seen.add(s["id"])
            values.append((s["id"],s["provider"],a,b))
    except (TypeError,ValueError,KeyError):
        return err("invalid slots",400)
    with db() as conn:
        with conn.cursor() as c:
            c.execute("SELECT pg_advisory_xact_lock(89011245)")
            c.execute("TRUNCATE bookings,slots,booking_attempts,idempotency RESTART IDENTITY CASCADE")
            if values:
                c.executemany("INSERT INTO slots(id,provider,start_at,end_at) VALUES(%s,%s,%s,%s)",values)
    return {"ok":True}

@app.get("/slots")
def slots(provider: str|None=None):
    with db() as conn:
        rows=conn.execute("""SELECT s.id,s.provider,s.start_at,s.end_at,NOT EXISTS
        (SELECT 1 FROM bookings b WHERE b.slot_id=s.id AND b.status='confirmed')
        FROM slots s WHERE (%s IS NULL OR s.provider=%s) ORDER BY s.start_at,s.id""",(provider,provider)).fetchall()
    return {"slots":[{"id":r[0],"provider":r[1],"start":iso(r[2]),"end":iso(r[3]),"available":r[4]} for r in rows]}

def normalized(body):
    if not isinstance(body,dict):
        raise ValueError("invalid booking details")
    for k in ("slot_id","name","email"):
        if not isinstance(body.get(k),str) or not body[k].strip():
            raise ValueError("missing or invalid "+k)
    email=body["email"].strip().lower()
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("invalid email")
    return {"slot_id":body["slot_id"].strip(),"name":body["name"].strip(),"email":email}

def rate_limit(cur,email):
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(email,))
    cur.execute("DELETE FROM booking_attempts WHERE created_at < now()-interval '1 minute'")
    cur.execute("""SELECT count(*),extract(epoch FROM(min(created_at)+interval '1 minute'-now()))
        FROM booking_attempts WHERE email=%s""",(email,))
    count, remaining=cur.fetchone()
    if count>=5:
        return max(1,math.ceil(float(remaining)))
    cur.execute("INSERT INTO booking_attempts(email) VALUES(%s)",(email,))
    return None

@app.post("/bookings")
async def make_booking(req: Request):
    try:
        raw=await req.json()
    except ValueError:
        return err("invalid JSON",400)
    try:
        body=normalized(raw)
    except ValueError as e:
        if isinstance(raw,dict) and isinstance(raw.get("email"),str) and raw["email"].strip():
            with db() as conn:
                with conn.cursor() as cur:
                    retry=rate_limit(cur,raw["email"].strip().lower())
            if retry:
                return err("too many booking attempts",429,{"Retry-After":str(retry)})
        return err(str(e),400)
    key=req.headers.get("Idempotency-Key")
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(89011245)")
            if key is not None:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,1))",(key,))
                cur.execute("SELECT digest,status_code,response FROM idempotency WHERE key=%s",(key,))
                old=cur.fetchone()
                if old:
                    if old[0]!=digest:
                        return err("idempotency key reused with different body",422)
                    return JSONResponse(old[2],status_code=old[1])
            retry=rate_limit(cur,body["email"])
            if retry:
                return err("too many booking attempts",429,{"Retry-After":str(retry)})
            row=cur.execute("SELECT start_at FROM slots WHERE id=%s FOR UPDATE",(body["slot_id"],)).fetchone()
            if row is None:
                result,code={"error":"slot not found"},404
            elif row[0]<=datetime.now(timezone.utc):
                result,code={"error":"slot already started"},400
            elif cur.execute("SELECT 1 FROM bookings WHERE slot_id=%s AND status='confirmed'",(body["slot_id"],)).fetchone():
                result,code={"error":"slot already booked"},409
            else:
                r=cur.execute("INSERT INTO bookings(id,slot_id,name,email) VALUES(%s,%s,%s,%s) RETURNING "+COLUMNS,(str(uuid4()),body["slot_id"],body["name"],body["email"])).fetchone()
                result,code=booking(r),201
            if key is not None:
                cur.execute("INSERT INTO idempotency(key,digest,status_code,response) VALUES(%s,%s,%s,%s)",(key,digest,code,json.dumps(result)))
            return JSONResponse(result,status_code=code)

@app.get("/bookings")
def list_bookings(email: str="",limit: str="10",cursor: str|None=None):
    try:
        n=int(limit)
        if not 1<=n<=50 or str(n)!=limit:
            raise ValueError()
    except ValueError:
        return err("invalid limit",400)
    if not email:
        return err("missing email",400)
    since=None
    ident=None
    if cursor:
        try:
            arr=json.loads(base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4)))
            since,ident=parse_dt(arr[0]),arr[1]
        except Exception:
            return err("invalid cursor",400)
    with db() as conn:
        rows=conn.execute("""SELECT """+COLUMNS+""" FROM bookings WHERE lower(email)=%s
         AND (%s::timestamptz IS NULL OR (created_at,id)>(%s::timestamptz,%s::uuid))
         ORDER BY created_at,id LIMIT %s""",(email.strip().lower(),since,since,ident,n+1)).fetchall()
    page=rows[:n]
    next_cursor=None
    if len(rows)>n and page:
        last=page[-1]
        payload=json.dumps([last[5].isoformat(timespec="microseconds"),str(last[0])],separators=(",",":"))
        next_cursor=base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return {"bookings":[booking(r) for r in page],"next_cursor":next_cursor}

@app.get("/bookings/{booking_id}")
def lookup(booking_id: str):
    with db() as conn:
        row=conn.execute("SELECT "+COLUMNS+" FROM bookings WHERE id::text=%s",(booking_id,)).fetchone()
    return booking(row) if row else err("booking not found",404)

@app.delete("/bookings/{booking_id}")
def cancel(booking_id: str):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(89011245)")
            row=cur.execute("SELECT "+COLUMNS+" FROM bookings WHERE id::text=%s FOR UPDATE",(booking_id,)).fetchone()
            if row is None:
                return err("booking not found",404)
            if row[4]=="cancelled":
                return err("booking already cancelled",409)
            changed=cur.execute("UPDATE bookings SET status='cancelled' WHERE id=%s RETURNING "+COLUMNS,(str(row[0]),)).fetchone()
            return booking(changed)
