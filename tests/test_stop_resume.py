#!/usr/bin/env python3
"""
tests/test_stop_resume.py
Emergency stop uses a fresh per-request id that the executor acks specifically, so a stale ack
cannot be read as confirmation, and /system/resume lifts the stop. Uses the ASGI app with an
injected fake operational bus.
"""
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

pytestmark = pytest.mark.asyncio


async def _admin_client(monkeypatch):
    import fakeredis
    from httpx import ASGITransport, AsyncClient
    from apps.api import main as api
    from lib.auth import create_user, UserRole, USERS_DB, reset_login_throttle
    reset_login_throttle()
    # Inject a fake operational bus so the operational endpoints are live (not 503).
    monkeypatch.setattr(api, "bus", types.SimpleNamespace(redis_client=fakeredis.FakeRedis(decode_responses=True)))
    USERS_DB.pop("stopper", None)
    create_user("stopper", "s@x.com", "pw-stop", UserRole.ADMIN)
    client = AsyncClient(transport=ASGITransport(app=api.app), base_url="http://t")
    login = await client.post("/api/auth/token", data={"username": "stopper", "password": "pw-stop"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    return api, client, headers


async def test_stop_fresh_ack_and_resume(monkeypatch):
    api, client, headers = await _admin_client(monkeypatch)
    try:
        r = await client.post("/system/emergency_stop", headers=headers)
        assert r.status_code == 200, r.text
        stop_id = r.json()["stop_id"]
        assert r.json()["executor_ack"] is False

        s = (await client.get("/system/stop_status", headers=headers)).json()
        assert s["stopped"] is True and s["executor_ack"] is False

        # A stale ack (different id) must NOT count as confirmation of this stop.
        api.bus.redis_client.set("trading:stop_ack", "some-old-stop-id")
        s = (await client.get("/system/stop_status", headers=headers)).json()
        assert s["executor_ack"] is False

        # The executor acking THIS stop id confirms it.
        api.bus.redis_client.set("trading:stop_ack", stop_id)
        s = (await client.get("/system/stop_status", headers=headers)).json()
        assert s["executor_ack"] is True

        # Resume lifts the stop and clears the ack.
        r = await client.post("/system/resume", headers=headers)
        assert r.status_code == 200 and r.json()["was_stopped"] is True
        s = (await client.get("/system/stop_status", headers=headers)).json()
        assert s["stopped"] is False and s["executor_ack"] is False
        assert api.bus.redis_client.get("trading:emergency_stop") is None
    finally:
        from lib.auth import USERS_DB
        USERS_DB.pop("stopper", None)
        await client.aclose()


async def test_new_stop_clears_previous_ack(monkeypatch):
    api, client, headers = await _admin_client(monkeypatch)
    try:
        first = (await client.post("/system/emergency_stop", headers=headers)).json()["stop_id"]
        api.bus.redis_client.set("trading:stop_ack", first)
        assert (await client.get("/system/stop_status", headers=headers)).json()["executor_ack"] is True
        # A second stop gets a new id and clears the old ack -> not acked until re-confirmed.
        second = (await client.post("/system/emergency_stop", headers=headers)).json()["stop_id"]
        assert second != first
        s = (await client.get("/system/stop_status", headers=headers)).json()
        assert s["stop_id"] == second and s["executor_ack"] is False
    finally:
        from lib.auth import USERS_DB
        USERS_DB.pop("stopper", None)
        await client.aclose()
