"""Economic invariants and real isolated CLI/API execution; no external orders."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from lib.backtest import ResearchConfig, load_csv, run_backtest
from lib.models import Bar, TimeFrame, SignalSide, OrderIntent
from lib.portfolio import Portfolio
from lib.strategy_base import Strategy, register


@register
class AuditBuy(Strategy):
    name = "audit_buy"
    timeframe = TimeFrame.MINUTE
    lookback_bars = 1
    cooldown_seconds = 60
    signal_expiry_seconds = 3600

    def analyze(self, symbol, bars):
        return self.make_signal(symbol, SignalSide.BUY, 1, bars)


def bars(n=5, prices=None):
    start = datetime(2024, 1, 2, 15, tzinfo=timezone.utc)
    return [Bar(symbol="TEST", timestamp=start + timedelta(minutes=i), open=p, high=p + 1,
                low=p - 1, close=p, volume=10000) for i, p in enumerate(prices or [100] * n)]


def test_ledger_fees_marks_and_idempotency():
    ledger = Portfolio(1000)
    ledger.fill('1', 'TEST', 'BUY', 2, 100, 1)
    ledger.mark('TEST', 110)
    assert ledger.equity == 1019
    ledger.fill('2', 'TEST', 'SELL', 2, 110, 1)
    assert ledger.equity == 1018 and ledger.total_realized == 18
    assert not ledger.fill('2', 'TEST', 'SELL', 2, 110, 1)
    assert ledger.equity == 1018


def test_next_bar_no_lookahead_and_determinism():
    config = ResearchConfig(strategies=['audit_buy'], initial_cash=1000,
                            max_position_size=.8, max_portfolio_risk=.8)
    result = run_backtest(bars(), config)
    account = result['accounts']['audit_buy']
    assert account['fills'] and account['fills'][0]['timestamp'] == bars()[1].timestamp.isoformat()
    assert all(fill['price'] >= 100 for fill in account['fills'] if fill['side'] == 'BUY')
    assert result == run_backtest(bars(), config)
    changed = bars(prices=[100, 100, 100, 100, 150])
    other = run_backtest(changed, config)['accounts']['audit_buy']
    cutoff = changed[-1].timestamp.isoformat()
    assert [f for f in account['fills'] if f['timestamp'] < cutoff] == [f for f in other['fills'] if f['timestamp'] < cutoff]
    assert account['final_portfolio']['cash'] >= 0


def test_zero_volume_cannot_fill_and_negative_return():
    data = bars(prices=[100, 100, 90, 80])
    result = run_backtest(data, ResearchConfig(strategies=['buy_and_hold'], initial_cash=1000))
    assert result['accounts']['buy_and_hold']['metrics']['return_pct'] < 0
    for bar in data:
        bar.volume = 0
    assert not run_backtest(data, ResearchConfig(strategies=['buy_and_hold']))['accounts']['buy_and_hold']['fills']


def test_reject_duplicate_and_timeframe():
    with pytest.raises(ValueError, match='Duplicate bar'):
        run_backtest(bars() * 2, ResearchConfig())
    with pytest.raises(ValueError, match='requires'):
        run_backtest(bars(), ResearchConfig(strategies=['daily_trend']))


def test_tracker_restart_and_outbox(tmp_path):
    from apps.executor.main import OrderTracker
    from unittest.mock import Mock
    path = str(tmp_path / 'orders.sqlite')
    tracker = OrderTracker(path)
    intent = OrderIntent(symbol='TEST', side='BUY', quantity=10, client_order_id='test', signal_source='audit')
    tracker.add_pending_order(intent, 'broker')
    first = tracker.update_order_status('broker', 'partially_filled', Decimal(5), Decimal(100))
    tracker.db.close()
    tracker = OrderTracker(path)
    second = tracker.update_order_status('broker', 'filled', Decimal(10), Decimal(110))
    assert second.fill_price == 120
    bus = Mock()
    tracker.drain_outbox(bus)
    assert bus.publish_order_fill.call_count == 2
    assert {c.args[0].fill_id for c in bus.publish_order_fill.call_args_list} == {first.fill_id, second.fill_id}
    tracker.drain_outbox(bus)
    assert bus.publish_order_fill.call_count == 2
    tracker.db.close()


def test_execution_guard_no_broker_calls_in_backtest():
    from lib.execution_safety import check_order
    from lib.settings import Settings
    from unittest.mock import Mock
    client = Mock()
    with pytest.raises(RuntimeError, match='backtest'):
        check_order(client, Mock(), Settings(_env_file=None, trading_mode='backtest'), Mock())
    assert not client.mock_calls


@pytest.mark.asyncio
async def test_api_job_real_subprocess(monkeypatch, tmp_path):
    from httpx import ASGITransport, AsyncClient
    from apps.api import main as api
    from lib.auth import create_user, UserRole, USERS_DB
    monkeypatch.setattr(api.get_settings(), 'trading_mode', 'backtest')
    manager = api.JobManager()
    manager.results_dir = tmp_path
    monkeypatch.setattr(api, 'job_manager', manager)
    username = 'e2e-audit'
    USERS_DB.pop(username, None)
    create_user(username, 'e2e@example.com', 'test-only-password', UserRole.ADMIN)
    try:
        async with AsyncClient(transport=ASGITransport(app=api.app), base_url='http://test') as client:
            assert (await client.post('/backtest/jobs', json={})).status_code == 401
            login = await client.post('/api/auth/token', data={'username': username, 'password': 'test-only-password'})
            assert login.status_code == 200, login.text
            headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
            request = {'symbols': ['TEST'], 'start_date': '2024-01-01', 'end_date': '2026-01-01',
                       'timeframe': '1Day', 'strategies': ['buy_and_hold'], 'csv_dir': 'data/sample', 'initial_cash': 12345}
            response = await client.post('/backtest/jobs', json=request, headers=headers)
            assert response.status_code == 200, response.text
            job_id = response.json()['job_id']
            response = await client.post(f'/backtest/jobs/{job_id}/start', headers=headers)
            assert response.status_code == 200, response.text
            for _ in range(200):
                job = (await client.get(f'/backtest/jobs/{job_id}', headers=headers)).json()
                if job['status'] in ('completed', 'failed'):
                    break
                await asyncio.sleep(.1)
            assert job['status'] == 'completed', job
            result = (await client.get(f'/backtest/jobs/{job_id}/results', headers=headers)).json()
            assert result['config']['initial_cash'] == 12345
            assert result['accounts']['buy_and_hold']['fills']
            assert (await client.get('/portfolio', headers=headers)).status_code == 503
    finally:
        USERS_DB.pop(username, None)
