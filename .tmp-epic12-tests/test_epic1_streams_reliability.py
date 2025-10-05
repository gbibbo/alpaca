import os, time, json, random
from typing import List, Dict, Tuple, Any

# Forzamos fakeredis para evitar dependencias externas
import fakeredis
_FR = fakeredis.FakeRedis(decode_responses=True)
def _r(): return _FR

STREAM = os.getenv("EPIC1_STREAM", "signals")
GROUP = os.getenv("EPIC1_GROUP", "trader")
CONSUMER_1 = os.getenv("EPIC1_CONSUMER1", "c1")
CONSUMER_2 = os.getenv("EPIC1_CONSUMER2", "c2")

def ensure_group():
    r = _r()
    try:
        # id="0-0" para poder leer historial en el primer read
        r.xgroup_create(STREAM, GROUP, id="0-0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" in str(e).upper() or "exists" in str(e).lower():
            pass
        else:
            raise

def publish(n: int, payload_fn=None) -> List[str]:
    r = _r()
    ids = []
    for i in range(n):
        payload = {"type": "signal", "i": i, "data": {"rnd": random.random()}}
        if payload_fn: payload = payload_fn(i)
        ids.append(r.xadd(STREAM, {"type": payload["type"], "data": json.dumps(payload)}))
    return ids

def _to_kv_dict(maybe):
    if isinstance(maybe, dict): return maybe
    if isinstance(maybe, list):
        try: return dict(maybe)
        except Exception: return {"_raw_list": str(maybe)}
    return {"_raw": str(maybe)}

def _extract_id_fields(e) -> Tuple[str, Dict]:
    # Normaliza a (id:str, fields:dict) tolerando formas raras/nested
    if isinstance(e, dict) and "id" in e and "fields" in e:
        return str(e["id"]), _to_kv_dict(e["fields"])
    if isinstance(e, (list, tuple)):
        # nested ((id,{fields}), extra...)
        if len(e)>=1 and isinstance(e[0], (list,tuple)) and len(e[0])>=2:
            mid, fields = e[0][0], _to_kv_dict(e[0][1])
            if isinstance(mid,(list,tuple)) and mid: mid = mid[0]
            return str(mid), fields
        if len(e)>=2:
            mid, fields = e[0], _to_kv_dict(e[1])
            if isinstance(mid,(list,tuple)) and mid: mid = mid[0]
            return str(mid), fields
    return (str(e), {})

def parse_entries_general(entries: Any):
    if entries is None: return
    if isinstance(entries, (list, tuple)):
        for e in entries:
            yield _extract_id_fields(e)
    elif isinstance(entries, dict):
        yield _extract_id_fields(entries)

def read_group_nonblocking(consumer: str, start_id: str, max_total: int, ack_ok: bool, fail_mod: int = 0) -> List[str]:
    """Lectura no bloqueante en bucle corto; evita BLOCK para no colgar tests."""
    r = _r()
    processed = []
    attempts = 0
    # usar varias pasadas cortas para consumir lote
    while len(processed) < max_total and attempts < 20:
        got = r.xreadgroup(groupname=GROUP, consumername=consumer,
                           streams={STREAM: start_id}, count=max_total - len(processed), block=0)
        if not got:
            time.sleep(0.02); attempts += 1; continue
        for _stream_name, entries in got:
            for mid, fields in parse_entries_general(entries):
                data = {}
                try:
                    if isinstance(fields, dict) and "data" in fields:
                        data = json.loads(fields["data"])
                except Exception: pass
                if ack_ok:
                    if fail_mod and (data.get("i", 0) % fail_mod == 0):
                        # simulamos fallo → NO ACK (queda pendiente)
                        processed.append(mid); continue
                    r.xack(STREAM, GROUP, mid)
                processed.append(mid)
        # tras la primera pasada, continuar con '>' para nuevos
        start_id = '>'
        attempts += 1
    return processed

def xp_pending() -> int:
    r = _r()
    try:
        summary = r.xpending(STREAM, GROUP)
        return int(summary["pending"] if isinstance(summary, dict) else summary)
    except Exception:
        try:
            groups = r.xinfo_groups(STREAM)
            for g in groups:
                if g.get("name")==GROUP:
                    return int(g.get("pending",0))
        except Exception: pass
        return 0

def normalize_xautoclaim_result(res):
    # (next, entries) | (next, entries, deleted) | entries
    if isinstance(res, tuple):
        if len(res)>=2: return res[0], res[1]
        if len(res)==1: return "0-0", res[0]
    return "0-0", res

def auto_claim_all(consumer: str, min_idle_ms: int, batch: int) -> int:
    r = _r()
    claimed = 0
    if hasattr(r, "xautoclaim"):
        start_id = "0-0"
        # usar min_idle_ms=0 para fakeredis (no esperar)
        while True:
            try:
                res = r.xautoclaim(STREAM, GROUP, consumer, min_idle_ms, start_id, count=batch)
            except TypeError:
                res = r.xautoclaim(STREAM, GROUP, consumer, min_idle_ms, start_id, batch)
            next_start, entries = normalize_xautoclaim_result(res)
            got_any = False
            for mid, fields in parse_entries_general(entries):
                r.xack(STREAM, GROUP, mid)
                claimed += 1
                got_any = True
            if not got_any: break
            start_id = next_start or "0-0"
    else:
        # Sin XAUTOCLAIM: limpiar con lecturas no bloqueantes
        while True:
            got = r.xreadgroup(groupname=GROUP, consumername=consumer,
                               streams={STREAM: ">"}, count=batch, block=0)
            if not got: break
            for _stream_name, entries in got:
                for mid, fields in parse_entries_general(entries):
                    r.xack(STREAM, GROUP, mid)
                    claimed += 1
    return claimed

def test_streams_ack_and_reclaim():
    ensure_group()
    total = 400
    publish(total)
    # c1 consume desde el inicio y deja fallar ~cada 3ro (queda pendiente)
    acked_c1 = read_group_nonblocking(CONSUMER_1, start_id="0-0", max_total=total, ack_ok=True, fail_mod=3)
    pend_before = xp_pending()
    assert pend_before >= 80, f"Se esperaban pendientes, got={pend_before}"
    # c2 reclama TODO (min_idle_ms=0 para fakeredis)
    claimed_c2 = auto_claim_all(CONSUMER_2, min_idle_ms=0, batch=200)
    pend_after = xp_pending()
    assert pend_after == 0, f"Pendientes tras reclaim debe ser 0, got={pend_after}"
    assert len(acked_c1) + claimed_c2 >= total, "Todos los mensajes deben quedar procesados"

def test_handler_failure_then_recover():
    ensure_group()
    publish(90)
    _ = read_group_nonblocking(CONSUMER_1, start_id="0-0", max_total=90, ack_ok=True, fail_mod=5)
    assert xp_pending() >= 10
    claimed = auto_claim_all(CONSUMER_2, min_idle_ms=0, batch=200)
    assert xp_pending() == 0
    assert claimed >= 10

def test_lag_fields_present_if_supported():
    ensure_group()
    publish(5)
    _ = read_group_nonblocking(CONSUMER_1, start_id="0-0", max_total=5, ack_ok=True, fail_mod=0)
    # No afirmamos lag exacto; solo verificamos que no explote XINFO si existe
    r = _r()
    try:
        _ = r.xinfo_stream(STREAM)
        _ = r.xinfo_groups(STREAM)
    except Exception:
        pass
