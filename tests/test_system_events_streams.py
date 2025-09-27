import os, sys, os.path, asyncio, json, pytest
# Asegura que 'lib/' sea importable en pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ['BUS_BACKEND'] = 'streams'

from lib.bus import connect_bus, get_bus

@pytest.mark.asyncio
async def test_backend_has_subscribe_system_events():
    connect_bus()
    bus = get_bus()
    backend = bus.backend
    assert hasattr(backend, "subscribe_system_events"), (
        f"{type(backend).__name__} no implementa subscribe_system_events(). "
        "Esto explica por qué Strategies no consume 'strategy_config'."
    )

@pytest.mark.asyncio
async def test_publish_and_consume_system_via_raw_redis():
    """
    Prueba de vida del stream 'system' sin usar backend.subscribe_system_events:
      1) publish_system_event publica en 'system'
      2) XREADGROUP (ID '>') recupera SOLO mensajes nuevos
    """
    connect_bus()
    bus = get_bus()

    # Conexión redis asyncio directa (misma instancia que usa el backend)
    try:
        r = bus.backend.redis  # preferido si existe
    except Exception:
        # fallback genérico por REDIS_URL
        from urllib.parse import urlparse
        import redis.asyncio as redis
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        u = urlparse(url)
        db = int((u.path or "/0").lstrip("/"))
        r = redis.Redis(host=u.hostname or "localhost", port=u.port or 6379, db=db)

    stream = "system"
    group = getattr(bus.backend, "consumer_groups", {}).get("system", "system_processors")

    # Crea grupo si no existe y mueve offset a '$' para solo mensajes nuevos
    try:    await r.xgroup_create(stream, group, id="$", mkstream=True)
    except Exception: pass
    try:    await r.xgroup_setid(stream, group, id="$")
    except Exception: pass

    # Publica un evento nuevo
    seed = 55555
    bus.publish_system_event(
        event_type="strategy_config",
        source="pytest",
        data={"config_type": "reproducible_mode", "random_seed": seed}
    )

    # Lee UN mensaje NUEVO del grupo
    consumer = "pytest_consumer"
    rep = await r.xreadgroup(groupname=group, consumername=consumer,
                             streams={stream: ">"}, count=1, block=3000)
    assert rep, "No llegó ningún evento nuevo con XREADGROUP (>)"

    # Flatten + validar contenido
    entries = rep[0][1]
    fields = entries[0][1]
    kv = {k.decode(): v.decode() for k, v in fields.items()}
    assert kv.get("type") == "system_event"
    assert kv.get("event_type") == "strategy_config"

    payload = json.loads(kv["data"])
    assert payload["data"]["random_seed"] == seed

    # ACK para limpiar
    msg_id = entries[0][0].decode()
    await r.xack(stream, group, msg_id)
