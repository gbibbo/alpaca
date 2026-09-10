#!/usr/bin/env python3
"""
tests/test_durable_bus_guard.py
paper/live trading must not run on a fire-and-forget Pub/Sub bus or fakeredis; backtest is free
to use either. The guard runs before connecting, so no real Redis is needed to test the refusals.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def _reset_bus():
    import lib.bus as bus
    bus._message_bus = None


def test_backtest_allows_pubsub_fakeredis(monkeypatch):
    _reset_bus()
    monkeypatch.setenv("TRADING_MODE", "backtest")
    monkeypatch.setenv("USE_FAKE_REDIS", "1")
    monkeypatch.setenv("BUS_BACKEND", "pubsub")
    from lib.settings import Settings
    import lib.bus as bus
    monkeypatch.setattr(bus, "get_settings", lambda: Settings(_env_file=None, trading_mode="backtest", use_fake_redis=True))
    b = bus.MessageBus()                       # no raise
    assert b.backend_type == "pubsub"


def test_paper_rejects_fakeredis(monkeypatch):
    _reset_bus()
    from lib.settings import Settings
    import lib.bus as bus
    monkeypatch.setenv("USE_FAKE_REDIS", "1")
    monkeypatch.setenv("BUS_BACKEND", "streams")
    monkeypatch.setattr(bus, "get_settings", lambda: Settings(_env_file=None, trading_mode="paper", use_fake_redis=True))
    with pytest.raises(RuntimeError, match="real Redis"):
        bus.MessageBus()


def test_paper_rejects_pubsub(monkeypatch):
    _reset_bus()
    from lib.settings import Settings
    import lib.bus as bus
    monkeypatch.setenv("USE_FAKE_REDIS", "0")
    monkeypatch.setenv("BUS_BACKEND", "pubsub")
    monkeypatch.setattr(bus, "get_settings", lambda: Settings(_env_file=None, trading_mode="paper", use_fake_redis=False))
    with pytest.raises(RuntimeError, match="streams"):
        bus.MessageBus()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
