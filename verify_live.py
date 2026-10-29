#!/usr/bin/env python3
"""Run against the *assessment-only* deployment. WARNING: POST /reset clears all bookings."""
import concurrent.futures
import getpass
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from uuid import uuid4

BASE = os.getenv("KOYA_URL", "https://koya-clinic-booking-xi.vercel.app").rstrip("/")
KEY = os.getenv("KOYA_API_KEY") or getpass.getpass("Koya API key: ")

def parse_response(response):
    raw = response.read()
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        preview = raw[:600].decode("utf-8", errors="replace")
        return {"error": "Non-JSON response", "status": response.status, "content_type": response.headers.get("Content-Type", ""), "body_preview": preview}

def call(method, path, payload=None, key=KEY, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={"X-API-Key":key,"Content-Type":"application/json",**(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = parse_response(r)
            if result.get("error") == "Non-JSON response":
                print("HTTP response from", method, path, ":", result)
            return r.status,result,dict(r.headers)
    except urllib.error.HTTPError as exc:
        result = parse_response(exc)
        print("HTTP error from", method, path, ":", exc.code, result)
        return exc.code,result,dict(exc.headers)

def expect(cond, label):
    if not cond:
        raise AssertionError(label)
    print("PASS",label)

if __name__ == "__main__":
    stamp=datetime.now(timezone.utc)+timedelta(days=30)
    start=stamp.replace(microsecond=0)
    slots=[{"id":f"assessment-{i}","provider":"Dr Ade","start":(start+timedelta(hours=i)).isoformat().replace("+00:00","Z"),"end":(start+timedelta(hours=i,minutes=30)).isoformat().replace("+00:00","Z")} for i in range(12)]
    expect(call("GET","/slots",key="wrong")[0]==401,"invalid key rejected")
    expect(call("POST","/reset",{"slots":slots})[0]==200,"reset accepts slots")
    st, result,_=call("GET","/slots")
    expect(st==200 and len(result["slots"])==12,"slot list")
    data={"slot_id":slots[0]["id"],"name":"  Ada Obi  ","email":"  ADA@example.com "}
    st,first,_=call("POST","/bookings",data,headers={"Idempotency-Key":"demo-1-"+uuid4().hex})
    expect(st==201 and first["email"]=="ada@example.com" and first["name"]=="Ada Obi","booking and normalization")
    st,looked,_=call("GET","/bookings/"+first["id"])
    expect(st==200 and looked["id"]==first["id"],"lookup")
    expect(call("POST","/bookings",data)[0]==409,"duplicate slot 409")
    st,cancelled,_=call("DELETE","/bookings/"+first["id"])
    expect(st==200 and cancelled["status"]=="cancelled","cancellation")
    expect(call("DELETE","/bookings/"+first["id"])[0]==409,"double cancellation")
    idem="demo-idempotency-"+uuid4().hex
    data={"slot_id":slots[1]["id"],"name":"Ada Obi","email":"ada@example.com"}
    st,a,_=call("POST","/bookings",data,headers={"Idempotency-Key":idem})
    st2,b,_=call("POST","/bookings",data,headers={"Idempotency-Key":idem})
    expect(st==st2==201 and a["id"]==b["id"],"idempotent retry")
    expect(call("POST","/bookings",{**data,"name":"Different"},headers={"Idempotency-Key":idem})[0]==422,"idempotency mismatch")
    st,pg,_=call("GET","/bookings?email=ADA%40example.com&limit=1")
    expect(st==200 and len(pg["bookings"])==1 and pg["next_cursor"],"cursor pagination")
    concurrent_data=[{"slot_id":slots[2]["id"],"name":"Concurrent","email":f"parallel{i}@example.com"} for i in range(8)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses=list(pool.map(lambda d:call("POST","/bookings",d)[0],concurrent_data))
    expect(responses.count(201)==1 and responses.count(409)==7,"concurrent booking exclusion")
    rate_email="ratelimit-"+uuid4().hex+"@example.com"
    rate_codes=[call("POST","/bookings",{"slot_id":"nonexistent","name":"Demo","email":rate_email})[0] for _ in range(6)]
    expect(rate_codes[:5]==[404]*5 and rate_codes[5]==429,"per-email 5 attempts/min rate limit")
    print("ALL CHECKS PASSED")
