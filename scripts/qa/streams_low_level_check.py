# scripts/qa/streams_low_level_check.py
#!/usr/bin/env python3
import os, sys
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM      = os.getenv("QA_STREAM", "__qa_streams__")
GROUP       = os.getenv("QA_GROUP", "qa")
CONSUMER_A  = "qa-consumer-a"
CONSUMER_B  = "qa-consumer-b"
N = int(os.getenv("QA_N", "200"))

r = redis.from_url(REDIS_URL, decode_responses=True)

def ensure_group():
    try:
        r.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
        print(f"[init] group {GROUP} created on stream {STREAM}")
    except Exception as e:
        msg = str(e).lower()
        if "busygroup" in msg or "exists" in msg:
            print(f"[init] group {GROUP} already exists")
        else:
            raise

def publish(n:int):
    for i in range(n):
        r.xadd(STREAM, {"type":"qa","data":str(i)})
    ln = r.xlen(STREAM)
    print(f"[pub] published={n}, stream_len={ln}")

def consume_no_ack(consumer:str, m:int):
    read = 0
    while read < m:
        resp = r.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=min(100, m-read), block=500)
        if not resp:
            break
        for _s, entries in resp:
            read += len(entries)
    print(f"[cA] read_without_ack={read}")
    return read

def pending_info():
    try:
        info = r.xpending(STREAM, GROUP)
        if isinstance(info, dict):
            pend = info.get("pending", 0)
            consumers = {c['name']: c['pending'] for c in info.get("consumers", [])}
        else:
            pend = info[0] if info else 0
            consumers = {}
        return pend, consumers
    except Exception as e:
        print("[warn] XPENDING failed:", e)
        return -1, {}

def reclaim_all(consumer:str, min_idle_ms:int=0, batch:int=500):
    total = 0
    start_id = "0-0"
    while True:
        next_id, items, deleted_ids = r.xautoclaim(
            STREAM, GROUP, consumer, min_idle_ms, start_id, count=batch
        )
        if not items and not deleted_ids:
            break
        for mid, _fields in items:
            r.xack(STREAM, GROUP, mid)
            total += 1
        start_id = next_id or "0-0"
    print(f"[cB] reclaimed+acked={total}")
    return total

def main():
    try:
        assert r.ping()
    except Exception as e:
        print("❌ Redis no responde:", e); sys.exit(2)

    ensure_group()
    publish(N)
    read_a = consume_no_ack(CONSUMER_A, N//2)
    pend, cons = pending_info()
    print(f"[xp1] pending={pend}, by_consumer={cons}")

    _ = reclaim_all(CONSUMER_B, min_idle_ms=0, batch=500)
    pend2, cons2 = pending_info()
    print(f"[xp2] pending={pend2}, by_consumer={cons2}")

    ok = (read_a >= N//3) and (pend2 == 0)
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
