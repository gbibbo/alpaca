#!/usr/bin/env python3
# scripts/epic1_streams_check.py
"""
Prueba robusta de Épica 1 (Redis Streams confiable) con defensas anti-bucle:
- Publica N mensajes en STREAM.
- c1 lee NUEVOS ('>') y ACKea solo los pares (deja impares pendientes).
- c2 reclama los pendientes con XAUTOCLAIM preferentemente con JUSTID (solo IDs) y los ACKea en lotes.
- Si XAUTOCLAIM no está disponible, usa XPENDING RANGE + XCLAIM, y como último recurso lee '>' y ACKea.
- Protecciones: corte por iteraciones, detección de progreso (start_id) y de repetición de IDs.

Variables de entorno:
  USE_FAKE_REDIS=1|0     (default 0 -> Redis real)
  REDIS_URL              (default redis://localhost:6379/0)
  STREAM_NAME            (default signals_epic1)
  GROUP_NAME             (default trader)
  CONSUMER_1, CONSUMER_2 (default c1, c2)
  NUM_MSG, READ_COUNT    (default 100, 60)
  RECLAIM_BATCH          (default 500)
  MIN_IDLE_MS            (default 0)  # 0 => reclamables de inmediato
  DEBUG_STREAMS=1        (logs adicionales)
"""

import os
import re
import json
import uuid
from typing import Any, Dict, Iterable, List, Tuple

# ---------- Config ----------
USE_FAKE = os.getenv("USE_FAKE_REDIS", "0") == "1"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DEBUG = os.getenv("DEBUG_STREAMS", "0") == "1"

STREAM = os.getenv("STREAM_NAME", "signals_epic1")
GROUP = os.getenv("GROUP_NAME", "trader")
CONSUMER_1 = os.getenv("CONSUMER_1", "c1")
CONSUMER_2 = os.getenv("CONSUMER_2", "c2")

NUM_MSG = int(os.getenv("NUM_MSG", "100"))
READ_COUNT = int(os.getenv("READ_COUNT", "60"))
RECLAIM_BATCH = int(os.getenv("RECLAIM_BATCH", "500"))
MIN_IDLE_MS = int(os.getenv("MIN_IDLE_MS", "0"))

MSGID_RX = re.compile(r"^\d+-\d+$")

# ---------- Client factory ----------
def get_client():
    if USE_FAKE:
        import fakeredis  # type: ignore
        return fakeredis.FakeRedis(decode_responses=True)
    else:
        import redis  # type: ignore
        return redis.from_url(REDIS_URL, decode_responses=True)

# ---------- Helpers ----------
def delete_stream_if_exists(r, stream: str) -> None:
    try:
        r.delete(stream)
    except Exception:
        pass

def ensure_group(r, stream: str, group: str, start_id: str = "0-0") -> None:
    try:
        r.xgroup_create(stream, group, id=start_id, mkstream=True)
    except Exception as e:
        s = str(e).lower()
        if "busygroup" in s or "exists" in s:
            return
        raise

def publish_messages(r, stream: str, n: int) -> List[str]:
    ids = []
    for i in range(n):
        payload = {"i": i, "rnd": uuid.uuid4().hex[:8]}
        ids.append(r.xadd(stream, {"type": "signal", "data": json.dumps(payload)}))
    return ids

def pending_summary_count(r, stream: str, group: str) -> int:
    try:
        summary = r.xpending(stream, group)
        if isinstance(summary, dict):
            return int(summary.get("pending", 0))
        return int(summary)
    except Exception:
        try:
            groups = r.xinfo_groups(stream)
            for g in groups:
                if g.get("name") == group:
                    return int(g.get("pending", 0))
        except Exception:
            pass
        return 0

def normalize_entry(item: Any) -> Tuple[str, Dict[str, Any]]:
    """
    Normaliza 'item' a (message_id:str, fields:dict) y valida ID.
    """
    mid = None
    fields: Dict[str, Any] = {}

    if isinstance(item, dict):
        if "id" in item and "fields" in item:
            mid = item["id"]; fields = item["fields"] if isinstance(item["fields"], dict) else {}
        elif "message_id" in item:
            mid = item["message_id"]; fields = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
        elif "id" in item:
            mid = item["id"]; fields = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
    elif isinstance(item, (list, tuple)):
        if len(item) >= 1 and isinstance(item[0], (list, tuple)) and len(item[0]) >= 2:
            mid = item[0][0]; f = item[0][1]; fields = f if isinstance(f, dict) else {}
        elif len(item) >= 2:
            mid = item[0]; f = item[1]; fields = f if isinstance(f, dict) else {}
        elif len(item) == 1:
            mid = item[0]

    if isinstance(mid, (list, tuple)) and len(mid) >= 1:
        mid = mid[0]
    mid = str(mid) if mid is not None else str(item)

    if not MSGID_RX.match(mid):
        return "", fields
    return mid, fields

def ack_even_only(r, entry_list: Iterable[Any]) -> Tuple[int, int]:
    acked = 0
    left_pending = 0
    for item in entry_list:
        mid, fields = normalize_entry(item)
        if not mid:
            left_pending += 1
            continue
        try:
            data = json.loads(fields.get("data", "{}")) if isinstance(fields, dict) else {}
            i = int(data.get("i", -1))
        except Exception:
            i = -1
        if i != -1 and i % 2 == 0:
            r.xack(STREAM, GROUP, mid)
            acked += 1
        else:
            left_pending += 1
    return acked, left_pending

def parse_xautoclaim_result(res: Any) -> Tuple[str, List[Any]]:
    if isinstance(res, tuple):
        if len(res) >= 2:  # (next_start, entries[, deleted])
            return str(res[0] or "0-0"), list(res[1] or [])
        if len(res) == 1:  # (entries,)
            return "0-0", list(res[0] or [])
    return "0-0", list(res or [])

def batched(iterable: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(iterable), n):
        yield iterable[i:i+n]

def reclaim_all_with_xautoclaim(r, consumer: str, min_idle_ms: int, batch: int) -> int:
    """
    Preferir XAUTOCLAIM con JUSTID=True (si la firma lo permite) para recibir solo IDs.
    Si no hay JUSTID, normaliza entradas. Protección contra bucles: límite de iteraciones y
    verificación de progreso (next_start cambia) y de repetición de IDs.
    """
    claimed_total = 0
    max_loops = 1000
    last_start = None
    seen_ids: set[str] = set()
    loops = 0

    # Ruta XAUTOCLAIM
    if hasattr(r, "xautoclaim"):
        start = "0-0"
        while loops < max_loops:
            loops += 1
            # 1) Intento con JUSTID=True (firma keyword)
            try:
                res = r.xautoclaim(STREAM, GROUP, consumer, min_idle_ms, start, count=batch, justid=True)
                # res: (next_start, [ids]) en redis>=7 (redis-py lo normaliza)
                if isinstance(res, tuple) and len(res) >= 2 and isinstance(res[1], list) and all(isinstance(x, str) for x in res[1]):
                    next_start = str(res[0] or "0-0")
                    ids = [mid for mid in res[1] if MSGID_RX.match(str(mid))]
                    # ACK en lotes
                    for chunk in batched(ids, 200):
                        if chunk:
                            r.xack(STREAM, GROUP, *chunk)
                            claimed_total += len(chunk)
                            seen_ids.update(chunk)
                    if DEBUG:
                        print(f"[XAUTOCLAIM justid] loop={loops} got={len(ids)} next_start={next_start}")
                    if not ids and (next_start == start or next_start == last_start):
                        break
                    last_start, start = start, next_start
                    if not ids:
                        break
                    continue
            except TypeError:
                # la firma no acepta justid= -> seguimos con entrada completa
                pass
            except Exception as e:
                if DEBUG:
                    print(f"[XAUTOCLAIM justid error] {e}")

            # 2) Sin JUSTID: parsear entradas y extraer IDs válidos
            try:
                try:
                    res = r.xautoclaim(STREAM, GROUP, consumer, min_idle_ms, start, count=batch)
                except TypeError:
                    res = r.xautoclaim(STREAM, GROUP, consumer, min_idle_ms, start, batch)
                next_start, entries = parse_xautoclaim_result(res)
                ids_to_ack: List[str] = []
                for entry in entries:
                    mid, _ = normalize_entry(entry)
                    if mid:
                        ids_to_ack.append(mid)
                # Evitar ACKs repetidos y detecta ciclos
                new_ids = [mid for mid in ids_to_ack if mid not in seen_ids]
                for chunk in batched(new_ids, 200):
                    if chunk:
                        r.xack(STREAM, GROUP, *chunk)
                        claimed_total += len(chunk)
                        seen_ids.update(chunk)
                if DEBUG:
                    print(f"[XAUTOCLAIM entries] loop={loops} new={len(new_ids)} total_seen={len(seen_ids)} next_start={next_start}")
                if (not new_ids) and (next_start == start or next_start == last_start):
                    break
                last_start, start = start, (next_start or "0-0")
                if not entries:
                    break
            except Exception as e:
                if DEBUG:
                    print(f"[XAUTOCLAIM entries error] {e}")
                break
        if claimed_total > 0:
            return claimed_total

    # Fallback: XPENDING RANGE + XCLAIM (por lotes)
    pending_ids: List[str] = []
    try:
        pend = r.xpending_range(STREAM, GROUP, '-', '+', 10000)  # type: ignore[attr-defined]
        for p in pend:
            mid, _ = normalize_entry(p)
            if mid and mid not in seen_ids:
                pending_ids.append(mid)
        if DEBUG:
            print(f"[XPENDING RANGE] pendientes={len(pending_ids)}")
    except Exception as e:
        if DEBUG:
            print(f"[XPENDING RANGE error] {e}")
        pending_ids = []

    while pending_ids:
        chunk, pending_ids = pending_ids[:100], pending_ids[100:]
        try:
            claimed = r.xclaim(STREAM, GROUP, consumer, min_idle_time=min_idle_ms or 0, message_ids=chunk)
        except TypeError:
            claimed = r.xclaim(STREAM, GROUP, consumer, min_idle_ms or 0, chunk)
        ids_to_ack: List[str] = []
        for entry in claimed or []:
            mid, _ = normalize_entry(entry)
            if mid and mid not in seen_ids:
                ids_to_ack.append(mid)
        for sub in batched(ids_to_ack, 200):
            if sub:
                r.xack(STREAM, GROUP, *sub)
                claimed_total += len(sub)
                seen_ids.update(sub)
        if DEBUG:
            print(f"[XCLAIM] claimed={len(ids_to_ack)} total_claimed={claimed_total}")

    if claimed_total > 0:
        return claimed_total

    # Último recurso: leer '>' y ACK (por si quedaron liberados)
    got2 = r.xreadgroup(groupname=GROUP, consumername=consumer, streams={STREAM: ">"}, count=batch)
    ids_to_ack = []
    for _s, part in got2 or []:
        for entry in part or []:
            mid, _ = normalize_entry(entry)
            if mid and mid not in seen_ids:
                ids_to_ack.append(mid)
    for sub in batched(ids_to_ack, 200):
        if sub:
            r.xack(STREAM, GROUP, *sub)
            claimed_total += len(sub)
            seen_ids.update(sub)
    if DEBUG:
        print(f"[READ '>'] read={len(ids_to_ack)}")
    return claimed_total

# ---------- Main ----------
def main() -> None:
    r = get_client()
    delete_stream_if_exists(r, STREAM)
    ensure_group(r, STREAM, GROUP, start_id="0-0")

    # Publicar
    ids = publish_messages(r, STREAM, NUM_MSG)
    print(f"Publicado: {len(ids)} mensajes en stream '{STREAM}'")

    # c1: lee NUEVOS ('>') SIN bloqueo y ACKea solo pares
    got = r.xreadgroup(groupname=GROUP, consumername=CONSUMER_1, streams={STREAM: ">"}, count=READ_COUNT)
    entries: List[Any] = []
    for _stream_name, part in got or []:
        if isinstance(part, list):
            entries.extend(part)

    acked_c1, left_pending = ack_even_only(r, entries)
    pend_after_c1 = pending_summary_count(r, STREAM, GROUP)
    print(
        f"c1 ACK (pares): {acked_c1} | "
        f"Dejó pendientes (impares/en lote): {left_pending} | "
        f"Pendientes reportados: {pend_after_c1}"
    )

    # c2: reclaim + ACK con defensas anti-bucle
    claimed_c2 = reclaim_all_with_xautoclaim(r, CONSUMER_2, MIN_IDLE_MS, RECLAIM_BATCH)

    pend_final = pending_summary_count(r, STREAM, GROUP)
    print(f"c2 reclamó+ACK: {claimed_c2} | Pendientes finales: {pend_final}")
    assert pend_final == 0, f"Quedaron mensajes pendientes ({pend_final})"
    print("✅ Épica 1 (Streams confiable) – OK")

if __name__ == "__main__":
    main()
