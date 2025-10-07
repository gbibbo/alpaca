#!/usr/bin/env python3
"""
tests/test_epic3_websocket.py
Epic 3: WebSocket Tests - Real-time communication testing
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch

from lib.websocket_manager import (
    WebSocketManager,
    WebSocketConnection,
    WSMessage,
    MessageType,
    Channel
)
from lib.auth import User, UserRole
from lib.models import Signal, SignalSide, OrderIntent, OrderFill, Bar


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    return User(
        username="testuser",
        email="test@example.com",
        role=UserRole.TRADER
    )


@pytest.fixture
def mock_admin():
    """Create a mock admin user for testing."""
    return User(
        username="admin",
        email="admin@example.com",
        role=UserRole.ADMIN
    )


@pytest.fixture
def mock_viewer():
    """Create a mock viewer user for testing."""
    return User(
        username="viewer",
        email="viewer@example.com",
        role=UserRole.VIEWER
    )


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.receive_json = AsyncMock()
    return ws


@pytest.fixture
def ws_manager():
    """Create a fresh WebSocketManager instance."""
    return WebSocketManager()


class TestWebSocketConnections:
    """Test WebSocket connection management"""

    @pytest.mark.asyncio
    async def test_connect_authenticated_user(self, ws_manager, mock_websocket, mock_user):
        """Test connecting an authenticated user"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)

        assert connection_id is not None
        assert connection_id.startswith("ws_")
        assert connection_id in ws_manager.connections
        assert ws_manager.get_connection_count() == 1

        # Verify welcome message was sent
        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["type"] == MessageType.AUTH
        assert call_args["data"]["authenticated"] is True
        assert call_args["data"]["username"] == "testuser"

    @pytest.mark.asyncio
    async def test_connect_anonymous_user(self, ws_manager, mock_websocket):
        """Test connecting an anonymous (unauthenticated) user"""
        connection_id = await ws_manager.connect(mock_websocket, user=None)

        assert connection_id is not None
        assert connection_id in ws_manager.connections

        # Verify welcome message indicates not authenticated
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["data"]["authenticated"] is False
        assert call_args["data"]["username"] is None

    @pytest.mark.asyncio
    async def test_disconnect(self, ws_manager, mock_websocket, mock_user):
        """Test disconnecting a user"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)
        assert ws_manager.get_connection_count() == 1

        await ws_manager.disconnect(connection_id)
        assert ws_manager.get_connection_count() == 0
        assert connection_id not in ws_manager.connections


class TestWebSocketSubscriptions:
    """Test channel subscription management"""

    @pytest.mark.asyncio
    async def test_subscribe_to_signals_channel(self, ws_manager, mock_websocket, mock_user):
        """Test subscribing to signals channel"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)

        # Subscribe to signals
        result = await ws_manager.subscribe(connection_id, Channel.SIGNALS)

        assert result is True
        assert ws_manager.get_channel_subscriber_count(Channel.SIGNALS) == 1

    @pytest.mark.asyncio
    async def test_subscribe_to_orders_channel(self, ws_manager, mock_websocket, mock_user):
        """Test subscribing to orders channel"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)

        result = await ws_manager.subscribe(connection_id, Channel.ORDERS)

        assert result is True
        assert ws_manager.get_channel_subscriber_count(Channel.ORDERS) == 1

    @pytest.mark.asyncio
    async def test_subscribe_multiple_channels(self, ws_manager, mock_websocket, mock_user):
        """Test subscribing to multiple channels"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)

        # Subscribe to multiple channels
        await ws_manager.subscribe(connection_id, Channel.SIGNALS)
        await ws_manager.subscribe(connection_id, Channel.ORDERS)
        await ws_manager.subscribe(connection_id, Channel.FILLS)

        connection = ws_manager.connections[connection_id]
        assert len(connection.subscribed_channels) == 3
        assert Channel.SIGNALS in connection.subscribed_channels
        assert Channel.ORDERS in connection.subscribed_channels
        assert Channel.FILLS in connection.subscribed_channels

    @pytest.mark.asyncio
    async def test_unsubscribe_from_channel(self, ws_manager, mock_websocket, mock_user):
        """Test unsubscribing from a channel"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)

        # Subscribe then unsubscribe
        await ws_manager.subscribe(connection_id, Channel.SIGNALS)
        assert ws_manager.get_channel_subscriber_count(Channel.SIGNALS) == 1

        await ws_manager.unsubscribe(connection_id, Channel.SIGNALS)
        assert ws_manager.get_channel_subscriber_count(Channel.SIGNALS) == 0


class TestWebSocketBroadcasting:
    """Test broadcasting messages to channels"""

    @pytest.mark.asyncio
    async def test_broadcast_signal(self, ws_manager, mock_websocket, mock_user):
        """Test broadcasting a signal to subscribers"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)
        await ws_manager.subscribe(connection_id, Channel.SIGNALS)

        # Create and broadcast a signal
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )

        await ws_manager.broadcast_signal(signal)

        # Verify signal was sent (at least 2 calls: welcome + signal)
        assert mock_websocket.send_json.call_count >= 2

    @pytest.mark.asyncio
    async def test_broadcast_order(self, ws_manager, mock_websocket, mock_user):
        """Test broadcasting an order to subscribers"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)
        await ws_manager.subscribe(connection_id, Channel.ORDERS)

        # Create and broadcast an order
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_order",
            signal_source="test"
        )

        await ws_manager.broadcast_order(order)

        # Verify order was sent
        assert mock_websocket.send_json.call_count >= 2

    @pytest.mark.asyncio
    async def test_broadcast_fill(self, ws_manager, mock_websocket, mock_user):
        """Test broadcasting a fill to subscribers"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)
        await ws_manager.subscribe(connection_id, Channel.FILLS)

        # Create and broadcast a fill
        from lib.models import OrderStatus
        fill = OrderFill(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            fill_price=Decimal("100.00"),
            fill_quantity=Decimal("10"),
            broker_order_id="broker_123",
            client_order_id="test_fill",
            status=OrderStatus.FILLED,
            total_value=Decimal("1000.00")
        )

        await ws_manager.broadcast_fill(fill)

        # Verify fill was sent
        assert mock_websocket.send_json.call_count >= 2

    @pytest.mark.asyncio
    async def test_broadcast_bar(self, ws_manager, mock_websocket, mock_user):
        """Test broadcasting a bar to subscribers"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)
        await ws_manager.subscribe(connection_id, Channel.BARS)

        # Create and broadcast a bar
        bar = Bar(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("102"),
            volume=1000
        )

        await ws_manager.broadcast_bar(bar)

        # Verify bar was sent
        assert mock_websocket.send_json.call_count >= 2

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_subscribers(self, ws_manager, mock_user):
        """Test broadcasting to multiple subscribers"""
        # Create multiple connections
        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws1.send_json = AsyncMock()

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()

        conn1 = await ws_manager.connect(ws1, mock_user)
        conn2 = await ws_manager.connect(ws2, mock_user)

        # Subscribe both to signals
        await ws_manager.subscribe(conn1, Channel.SIGNALS)
        await ws_manager.subscribe(conn2, Channel.SIGNALS)

        # Broadcast a signal
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )

        await ws_manager.broadcast_signal(signal)

        # Both should have received the signal
        assert ws1.send_json.call_count >= 2  # welcome + signal
        assert ws2.send_json.call_count >= 2  # welcome + signal


class TestWebSocketPermissions:
    """Test permission-based subscription"""

    @pytest.mark.asyncio
    async def test_viewer_can_read_signals(self, ws_manager, mock_websocket, mock_viewer):
        """Test that viewer role can subscribe to signals"""
        connection_id = await ws_manager.connect(mock_websocket, mock_viewer)

        result = await ws_manager.subscribe(connection_id, Channel.SIGNALS)

        assert result is True

    @pytest.mark.asyncio
    async def test_admin_can_subscribe_all_channels(self, ws_manager, mock_websocket, mock_admin):
        """Test that admin role can subscribe to all channels"""
        connection_id = await ws_manager.connect(mock_websocket, mock_admin)

        # Subscribe to all channels
        for channel in Channel:
            result = await ws_manager.subscribe(connection_id, channel)
            assert result is True


class TestWebSocketStats:
    """Test WebSocket statistics"""

    @pytest.mark.asyncio
    async def test_get_stats(self, ws_manager, mock_websocket, mock_user):
        """Test getting WebSocket manager statistics"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)
        await ws_manager.subscribe(connection_id, Channel.SIGNALS)
        await ws_manager.subscribe(connection_id, Channel.ORDERS)

        stats = ws_manager.get_stats()

        assert stats["total_connections"] == 1
        assert stats["channel_subscribers"]["signals"] == 1
        assert stats["channel_subscribers"]["orders"] == 1
        assert len(stats["connections"]) == 1
        assert stats["connections"][0]["user"] == "testuser"

    @pytest.mark.asyncio
    async def test_connection_count(self, ws_manager, mock_websocket, mock_user):
        """Test connection count tracking"""
        assert ws_manager.get_connection_count() == 0

        conn1 = await ws_manager.connect(mock_websocket, mock_user)
        assert ws_manager.get_connection_count() == 1

        ws2 = AsyncMock()
        ws2.accept = AsyncMock()
        ws2.send_json = AsyncMock()
        conn2 = await ws_manager.connect(ws2, mock_user)
        assert ws_manager.get_connection_count() == 2

        await ws_manager.disconnect(conn1)
        assert ws_manager.get_connection_count() == 1


class TestWebSocketMessages:
    """Test WebSocket message handling"""

    @pytest.mark.asyncio
    async def test_handle_ping(self, ws_manager, mock_websocket, mock_user):
        """Test handling ping message"""
        connection_id = await ws_manager.connect(mock_websocket, mock_user)

        await ws_manager.handle_ping(connection_id)

        # Should send pong response
        assert mock_websocket.send_json.call_count >= 2  # welcome + pong

    def test_ws_message_creation(self):
        """Test WSMessage creation"""
        message = WSMessage(
            type=MessageType.SIGNAL,
            channel=Channel.SIGNALS,
            data={"symbol": "GOOGL"}
        )

        assert message.type == MessageType.SIGNAL
        assert message.channel == Channel.SIGNALS
        assert message.data["symbol"] == "GOOGL"
        assert message.timestamp is not None
