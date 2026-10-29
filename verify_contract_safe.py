#!/usr/bin/env python3
"""Read-only and expected-failure contract checks. Does not reset slots or create bookings."""
import getpass, json, os, urllib.request, urllib.error, uuid
BASE=os.getenv("KOYA_URL","https://koya-clinic-booking-xi.vercel.app").rstrip("/")
KEY=os.getenv("KOYA_API_KEY") or getpass.getpass("Koya API key: ")
def call(method,path,body=None,key=KEY):
    payload=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(BASE+path,data=payload,method=method,headers={"X-API-Key":key,"Content-Type":"application/json"})
    try:
        res=urllib.request.urlopen(req,timeout=30)
    except urllib.error.HTTPError as e:
        res=e
    with res:
        raw=res.read()
        try: data=json.loads(raw)
        except ValueError: raise AssertionError(f"{method} {path}: non-JSON HTTP {res.status}: {raw[:200]!r}")
        if res.status>=400:
            assert isinstance(data,dict) and isinstance(data.get("error"),str), (res.status,data)
        return res.status,data,res.headers
def check(ok,label):
    assert ok,label
    print("PASS",label)
if __name__=="__main__":
    check(call("GET","/slots",key="definitely-invalid")[0]==401,"wrong API key 401")
    check(call("GET","/slots")[0]==200,"slots readable")
    check(call("GET","/slots?provider=nonexistent-provider")[1]=={"slots":[]},"exact provider filter")
    for limit in ("0","51","abc","1.5"):
        check(call("GET","/bookings?email=test%40example.com&limit="+limit)[0]==400,"invalid limit "+limit+" -> 400")
    import base64
    invalid=base64.urlsafe_b64encode(json.dumps(["2026-12-10T09:00:00Z","NOT-A-UUID"]).encode()).decode().rstrip("=")
    check(call("GET","/bookings?email=test%40example.com&cursor="+invalid)[0]==400,"malformed cursor -> 400")
    check(call("GET","/bookings?email=no-such-person%40example.com")[1]=={"bookings":[],"next_cursor":None},"empty booking page")
    missing_email="invalid-"+uuid.uuid4().hex+"@example.com"
    check(call("POST","/bookings",{"slot_id":"missing","name":" " ,"email":missing_email})[0]==400,"blank name -> 400")
    check(call("POST","/bookings",{"slot_id":"missing","name":"Test","email":"not-an-email"})[0]==400,"invalid email -> 400")
    check(call("POST","/bookings",{"name":"Test","email":"another-"+uuid.uuid4().hex+"@example.com"})[0]==400,"missing slot_id -> 400")
    email="limit-"+uuid.uuid4().hex+"@example.com"
    codes=[call("POST","/bookings",{"slot_id":"missing","name":"Test","email":email}) for _ in range(6)]
    check([x[0] for x in codes]==[404,404,404,404,404,429],"failure attempts counted; sixth -> 429")
    wait=codes[5][2].get("Retry-After","")
    check(wait.isdecimal() and int(wait)>0,"Retry-After whole seconds")
    other="other-"+uuid.uuid4().hex+"@example.com"
    check(call("POST","/bookings",{"slot_id":"missing","name":"Test","email":other})[0]==404,"different email is not rate-limited")
    print("ALL NON-DESTRUCTIVE CONTRACT CHECKS PASSED")
