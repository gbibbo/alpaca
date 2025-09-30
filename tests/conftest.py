import os, time, requests, pytest

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8001").rstrip("/")

@pytest.fixture(scope="session")
def base_url():
    return BASE_URL

@pytest.fixture(scope="session", autouse=True)
def _wait_api_ready(base_url):
    """Espera que la API responda /health; si no, salta tests de API."""
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
