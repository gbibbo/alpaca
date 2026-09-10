import os, time, requests, pytest

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8001").rstrip("/")


@pytest.fixture
def real_redis_bus():
    """Force a real-Redis Streams bus for tests that genuinely need it, and undo it afterwards.

    Several unit-test modules do ``os.environ.setdefault("USE_FAKE_REDIS", "1")`` at import time
    (a cheap guard so importing them never touches a real broker). That assignment persists for the
    whole process AND freezes ``lib.settings.settings.use_fake_redis`` to True the first time the
    settings singleton is constructed. As a result, later tests that need a real Redis Streams
    backend get a fakeredis client instead, silently fall back to Pub/Sub, and fail
    (``'PubSubBus' object has no attribute 'streams'``). The failure only shows up when a real
    Redis is running AND the poisoning modules were collected first, i.e. in the full suite.

    Opt in from a streams/system-event module with, at module top level::

        pytestmark = pytest.mark.usefixtures("real_redis_bus")

    The fixture reconfigures env + settings singleton + global bus to real Redis for the duration
    of the test, skips cleanly if no real Redis is reachable, and restores the previous state after
    (so it never leaks back onto other tests).
    """
    import importlib

    bus = importlib.import_module("lib.bus")
    settings_mod = importlib.import_module("lib.settings")

    # Is a real Redis actually reachable? If not, these tests can't run meaningfully -> skip.
    url = os.environ.get("REDIS_URL", getattr(settings_mod.get_settings(), "redis_url",
                                              "redis://localhost:6379/0"))
    try:
        import redis
        redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=1).ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"real Redis not reachable at {url} ({exc})")

    saved_env = {k: os.environ.get(k) for k in ("USE_FAKE_REDIS", "BUS_BACKEND")}
    saved_flag = settings_mod.settings.use_fake_redis
    saved_bus = getattr(bus, "_message_bus", None)

    os.environ["USE_FAKE_REDIS"] = "0"
    os.environ.setdefault("BUS_BACKEND", "streams")
    settings_mod.settings.use_fake_redis = False   # thaw the frozen singleton for this test
    bus._message_bus = None                        # drop any cached (possibly fake) global bus
    try:
        yield
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        settings_mod.settings.use_fake_redis = saved_flag
        bus._message_bus = saved_bus

@pytest.fixture(scope="session")
def base_url():
    return BASE_URL

@pytest.fixture(scope="session")
def _wait_api_ready(base_url):
    """Espera que la API responda /health; si no, salta los tests que la usan.
    NO es autouse: antes saltaba la suite entera cuando la API no estaba levantada.
    Los modulos que necesitan la API declaran pytestmark = pytest.mark.usefixtures("_wait_api_ready")."""
    deadline = time.time() + 20
    last_err = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/health", timeout=2)
            if r.status_code == 200:
                return
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    pytest.skip(f"API no disponible en {base_url}/health ({last_err})")

@pytest.fixture(scope="module")
def job_id(base_url):
    """Crea un backtest y devuelve su id para tests que lo pidan."""
    try:
        r = requests.post(f"{base_url}/backtest/jobs",
                          json={"symbols": ["AAPL", "GOOGL"]},
                          timeout=10)
        r.raise_for_status()
        if "application/json" in (r.headers.get("content-type") or ""):
            data = r.json()
        else:
            data = {}
    except Exception as e:
        pytest.skip(f"No se pudo crear job: {e}")

    jid = (data.get("id") or data.get("job_id") or data.get("jobId")
           or data.get("job"))
    if not jid:
        # Fallback simple: toma la primera cadena tipo UUID que aparezca
        for v in data.values():
            if isinstance(v, str) and "-" in v and len(v) >= 8:
                jid = v; break
    if not jid:
        pytest.skip(f"No pude extraer job_id del response: {data}")
    return jid
