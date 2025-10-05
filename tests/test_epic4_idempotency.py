#!/usr/bin/env python3
"""
tests/test_epic4_idempotency.py
Integration tests for Epic 4: Idempotency and 429/5xx retry handling
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import asyncio
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch
from lib.models import OrderIntent, SignalSide, OrderType
from apps.executor.main import EnhancedAlpacaExecutor


class TestClientOrderIdDeterministic:
    """Test deterministic client_order_id generation (Epic 4 T4.1)"""

    def test_generate_client_order_id_same_input(self):
        """Same input should generate same client_order_id"""
        order1 = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="temp",
            signal_source="smart_technical"
        )

        # Generate deterministic ID
        client_id1 = order1.generate_client_order_id()
        client_id2 = order1.generate_client_order_id()

        assert client_id1 == client_id2
        assert client_id1.startswith("risk_smart")
        assert "GOOGL" in client_id1

    def test_generate_client_order_id_different_symbol(self):
        """Different symbols should generate different client_order_ids"""
        order1 = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="temp1",
            signal_source="smart_technical"
        )

        order2 = OrderIntent(
            symbol="AAPL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="temp2",
            signal_source="smart_technical"
        )

        client_id1 = order1.generate_client_order_id()
        client_id2 = order2.generate_client_order_id()

        assert client_id1 != client_id2
        assert "GOOGL" in client_id1
        assert "AAPL" in client_id2

    def test_client_order_id_max_length(self):
        """client_order_id should not exceed 50 characters"""
        order = OrderIntent(
            symbol="VERYLONGSYMBOLNAME",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="temp",
            signal_source="very_long_strategy_source_name_that_exceeds_limits"
        )

        client_id = order.generate_client_order_id()
        assert len(client_id) <= 50


class TestDuplicateOrderDetection:
    """Test duplicate order detection by client_order_id (Epic 4 T4.1)"""

    @pytest.mark.asyncio
    async def test_duplicate_order_blocked_by_client_id(self):
        """Orders with same client_order_id should be detected as duplicates"""
        # Mock Alpaca client
        mock_trading_client = Mock()
        mock_trading_client.get_account = Mock(return_value=Mock(
            status="ACTIVE",
            buying_power=100000,
            cash=100000,
            portfolio_value=100000
        ))

        # First call: no existing order
        # Second call: order exists
        mock_existing_order = Mock(
            id="broker_order_123",
            status="new",
            filled_avg_price=None,
            filled_qty=0
        )

        mock_trading_client.get_order_by_client_order_id = Mock(
            side_effect=[
                Exception("Order not found"),  # First check: not found
                mock_existing_order  # Second check: found
            ]
        )

        mock_trading_client.submit_order = Mock(return_value=Mock(
            id="broker_order_123",
            status="new",
            symbol="GOOGL",
            qty=10,
            side="buy"
        ))

        # Create executor with mocked client
        with patch('apps.executor.main.TradingClient', return_value=mock_trading_client):
            executor = EnhancedAlpacaExecutor()
            executor.trading_client = mock_trading_client

            order_intent = OrderIntent(
                symbol="GOOGL",
                side=SignalSide.BUY,
                quantity=Decimal("10"),
                order_type=OrderType.MARKET,
                client_order_id="risk_smart_GOOGL_20250105_abc123",
                signal_source="smart_technical",
                price=Decimal("150")
            )

            # First execution: should submit order
            result1 = await executor.execute_order_with_validation(order_intent)

            # Second execution: should detect duplicate
            result2 = await executor.execute_order_with_validation(order_intent)

            # Verify submit_order called only once
            assert mock_trading_client.submit_order.call_count == 1

            # Verify duplicate metric recorded
            assert executor.metrics.DUPLICATE_ORDER_BLOCKED_BY_CLIENT_ID._metrics  # Metric exists


class TestRetryWith429:
    """Test 429 rate limit retry with same client_order_id (Epic 4 T4.1)"""

    @pytest.mark.asyncio
    async def test_429_retry_uses_same_client_order_id(self):
        """429 retry should use the same client_order_id"""
        mock_trading_client = Mock()
        mock_trading_client.get_account = Mock(return_value=Mock(
            status="ACTIVE",
            buying_power=100000,
            cash=100000,
            portfolio_value=100000
        ))

        mock_trading_client.get_order_by_client_order_id = Mock(
            side_effect=Exception("Order not found")
        )

        # First call: 429 error
        # Second call: Success
        mock_trading_client.submit_order = Mock(
            side_effect=[
                Exception("429 Too Many Requests"),
                Mock(
                    id="broker_order_456",
                    status="new",
                    symbol="AAPL",
                    qty=5,
                    side="buy"
                )
            ]
        )

        with patch('apps.executor.main.TradingClient', return_value=mock_trading_client):
            executor = EnhancedAlpacaExecutor()
            executor.trading_client = mock_trading_client

            order_intent = OrderIntent(
                symbol="AAPL",
                side=SignalSide.BUY,
                quantity=Decimal("5"),
                order_type=OrderType.MARKET,
                client_order_id="risk_test_AAPL_20250105_def456",
                signal_source="test_strategy",
                price=Decimal("180")
            )

            # Execute with retry
            result = await executor.execute_order_with_validation(order_intent)

            # Verify submit_order called twice (first 429, then success)
            assert mock_trading_client.submit_order.call_count == 2

            # Verify both calls used same client_order_id
            call1_client_id = mock_trading_client.submit_order.call_args_list[0][0][0].client_order_id
            call2_client_id = mock_trading_client.submit_order.call_args_list[1][0][0].client_order_id

            assert call1_client_id == call2_client_id == "risk_test_AAPL_20250105_def456"

            # Verify 429 retry metric recorded
            assert executor.metrics.BROKER_429_RETRIES._metrics  # Metric exists


class TestMetricsRecording:
    """Test Epic 4 metrics recording"""

    def test_duplicate_order_metric_increments(self):
        """duplicate_order_blocked_by_client_id_total should increment"""
        from lib.metrics_helpers import ExecutorMetrics

        metrics = ExecutorMetrics()

        # Record duplicate
        metrics.duplicate_order_blocked("GOOGL", "risk_smart_GOOGL_20250105_abc123")

        # Verify metric exists and has value
        metric_value = metrics.DUPLICATE_ORDER_BLOCKED_BY_CLIENT_ID.labels(
            symbol="GOOGL",
            client_order_id_prefix="risk_smart_GOOGL_202"
        )._value._value

        assert metric_value >= 1

    def test_429_retry_metric_increments(self):
        """broker_429_retries_total should increment on retry"""
        from lib.metrics_helpers import ExecutorMetrics

        metrics = ExecutorMetrics()

        # Record 429 retry
        metrics.broker_429_retry("submit_order", success=False)
        metrics.broker_429_retry("submit_order", success=True)

        # Verify metrics exist
        assert metrics.BROKER_429_RETRIES._metrics  # Both success and failure metrics


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
