import os, socket, requests

PORTS = [int(p) for p in os.getenv("EPIC2_PORTS", "8000,8011,8012,8013,8014,8015,8016").split(",")]

def is_up(port:int)->bool:
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); s.settimeout(0.2)
    try: s.connect(("127.0.0.1",port)); s.close(); return True
    except Exception: return False

def test_metrics_endpoints_if_running():
    for port in PORTS:
        if not is_up(port): continue
        r=requests.get(f"http://127.0.0.1:{port}/metrics",timeout=2)
        assert r.status_code==200
        assert ("# HELP" in r.text) or ("# TYPE" in r.text)
    assert True
