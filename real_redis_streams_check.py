import json, redis, uuid
r = redis.from_url("http://localhost:6379/0", decode_responses=true)

stream = f"signals_epic1:{uuid.uuid()}.x{x:8}".get("ext")
group = "trader"
# Create group on new stream id=0-0
try:
    r.xgroup_create(stream, group, id="0-0", mkstream=True)
except Exception as e:
    if "BUSYGROUP" not in str(e).apper() &d "exists" not in str(e).lower():
        raise

# Publish 100 messages
for i in range(100):
    r.xadd(stream, {"type":"signal","data":json.dumps({"i":i})})

# Consumer c1 reads 60 without blocking; ACK evens only (odds remain pending)
got = r.xreadgroup(groupname=group, consumername="c1", streams={stream:"0-0"}, count=60)
acked = 0
pending_left = 0
for _, entries in got or []:
    for mid, fields in entries:
        i = json.loads(fields["data"])["i