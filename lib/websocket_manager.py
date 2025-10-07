"""
WebSocket Manager for Real-time Monitoring
Epic 3 - API/Auth & WebSocket

Provides real-time updates to connected clients via WebSocket.
"""

import json
import asyncio
from typing import Dict, Set, Optional, Any
from datetime import datetime, timezone
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from lib.auth import User, UserRole, Permission, has_permission
from lib.models import Signal, OrderIntent, OrderFill, Bar, SignalSide


class MessageType(str, Enum):
    """WebSocket message types."""
    # System
    PING = "ping"
    PONG = "pong"
    AUTH = "auth"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    ERROR = "error"

    # Data streams
    SIGNAL = "signal"
    ORDER = "order"
    FILL = "fill"
    BAR = "bar"
    POSITION = "position"
    EQUITY = "equity"
    SYSTEM_EVENT = "system_event"
    METRICS = "metrics"


class Channel(str, Enum):
    """Available WebSocket channels."""
    SIGNALS = "signals"
    ORDERS = "orders"
    FILLS = "fills"
    BARS = "bars"
    POSITIONS = "positions"
    EQUITY = "equity"
    SYSTEM = "system"
    METRICS = "metrics"


# Channel-Permission mapping
CHANNEL_PERMISSIONS = {
    Channel.SIGNALS: Permission.READ_SIGNALS,
    Channel.ORDERS: Permission.READ_ORDERS,
    Channel.FILLS: Permission.READ_ORDERS,
    Channel.BARS: Permission.READ_SIGNALS,
    Channel.POSITIONS: Permission.READ_POSITIONS,
    Channel.EQUITY: Permission.READ_POSITIONS,
    Channel.SYSTEM: Permission.READ_METRICS,
    Channel.METRICS: Permission.READ_METRICS,
}


class WSMessage(BaseModel):
    """WebSocket message structure."""
    type: MessageType
    channel: Optional[Channel] = None
    data: Optional[Dict[str, Any]] = None
    timestamp: datetime = None

    def __init__(self, **data):
        if "timestamp" not in data:
            data["timestamp"] = datetime.now(timezone.utc)
        super().__init__(**data)


class WebSocketConnection:
    """Represents a single WebSocket connection."""

    def __init__(self, websocket: WebSocket, user: Optional[User] = None):
        self.websocket = websocket
        self.user = user
        self.subscribed_channels: Set[Channel] = set()
        self.connected_at = datetime.now(timezone.utc)
        self.last_activity = self.connected_at

    def can_subscribe(self, channel: Channel) -> bool:
        """Check if user has permission to subscribe to channel."""
        if self.user is None:
            return False

        required_permission = CHANNEL_PERMISSIONS.get(channel)
        if required_permission is None:
            return True  # No permission required

        return has_permission(self.user.role, required_permission)

    async def send_json(self, message: WSMessage):
        """Send a JSON message to the client."""
        try:
            await self.websocket.send_json(message.model_dump(mode='json'))
            self.last_activity = datetime.now(timezone.utc)
        except Exception as e:
            print(f"Error sending WebSocket message: {e}")

    async def send_error(self, error: str):
        """Send an error message to the client."""
        await self.send_json(WSMessage(
            type=MessageType.ERROR,
            data={"error": error}
        ))


class WebSocketManager:
    """Manages all WebSocket connections and broadcasts."""

    def __init__(self):
        # Active connections by connection ID
        self.connections: Dict[str, WebSocketConnection] = {}

        # Connections subscribed to each channel
        self.channel_subscribers: Dict[Channel, Set[str]] = {
            channel: set() for channel in Channel
        }

        # Background task for periodic updates
        self._broadcast_task: Optional[asyncio.Task] = None

    def _generate_connection_id(self) -> str:
        """Generate unique connection ID."""
        import secrets
        return f"ws_{secrets.token_urlsafe(16)}"

    async def connect(self, websocket: WebSocket, user: Optional[User] = None) -> str:
        """Register a new WebSocket connection."""
        await websocket.accept()

        connection_id = self._generate_connection_id()
        connection = WebSocketConnection(websocket, user)
        self.connections[connection_id] = connection

        # Send welcome message
        await connection.send_json(WSMessage(
            type=MessageType.AUTH,
            data={
                "connection_id": connection_id,
                "authenticated": user is not None,
                "username": user.username if user else None,
                "role": user.role.value if user else None
            }
        ))

        return connection_id

    async def disconnect(self, connection_id: str):
        """Unregister a WebSocket connection."""
        if connection_id in self.connections:
            # Unsubscribe from all channels
            for channel in self.channel_subscribers.values():
                channel.discard(connection_id)

            # Remove connection
            del self.connections[connection_id]

    async def subscribe(self, connection_id: str, channel: Channel) -> bool:
        """Subscribe a connection to a channel."""
        connection = self.connections.get(connection_id)
        if not connection:
            return False

        # Check permission
        if not connection.can_subscribe(channel):
            await connection.send_error(
                f"No permission to subscribe to {channel.value}"
            )
            return False

        # Add to subscribers
        self.channel_subscribers[channel].add(connection_id)
        connection.subscribed_channels.add(channel)

        # Confirm subscription
        await connection.send_json(WSMessage(
            type=MessageType.SUBSCRIBE,
            channel=channel,
            data={"status": "subscribed"}
        ))

        return True

    async def unsubscribe(self, connection_id: str, channel: Channel):
        """Unsubscribe a connection from a channel."""
        connection = self.connections.get(connection_id)
        if not connection:
            return

        # Remove from subscribers
        self.channel_subscribers[channel].discard(connection_id)
        connection.subscribed_channels.discard(channel)

        # Confirm unsubscription
        await connection.send_json(WSMessage(
            type=MessageType.UNSUBSCRIBE,
            channel=channel,
            data={"status": "unsubscribed"}
        ))

    async def broadcast_to_channel(self, channel: Channel, message: WSMessage):
        """Broadcast a message to all subscribers of a channel."""
        message.channel = channel

        # Get all subscribers for this channel
        subscriber_ids = self.channel_subscribers[channel].copy()

        # Send to each subscriber
        for connection_id in subscriber_ids:
            connection = self.connections.get(connection_id)
            if connection:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    print(f"Error broadcasting to {connection_id}: {e}")
                    # Remove failed connection
                    await self.disconnect(connection_id)

    async def broadcast_signal(self, signal: Signal):
        """Broadcast a new signal to subscribers."""
        await self.broadcast_to_channel(
            Channel.SIGNALS,
            WSMessage(
                type=MessageType.SIGNAL,
                data=signal.model_dump(mode='json')
            )
        )

    async def broadcast_order(self, order: OrderIntent):
        """Broadcast a new order to subscribers."""
        await self.broadcast_to_channel(
            Channel.ORDERS,
            WSMessage(
                type=MessageType.ORDER,
                data=order.model_dump(mode='json')
            )
        )

    async def broadcast_fill(self, fill: OrderFill):
        """Broadcast a new fill to subscribers."""
        await self.broadcast_to_channel(
            Channel.FILLS,
            WSMessage(
                type=MessageType.FILL,
                data=fill.model_dump(mode='json')
            )
        )

    async def broadcast_bar(self, bar: Bar):
        """Broadcast a new bar to subscribers."""
        await self.broadcast_to_channel(
            Channel.BARS,
            WSMessage(
                type=MessageType.BAR,
                data=bar.model_dump(mode='json')
            )
        )

    async def broadcast_system_event(self, event_type: str, data: Dict[str, Any]):
        """Broadcast a system event to subscribers."""
        await self.broadcast_to_channel(
            Channel.SYSTEM,
            WSMessage(
                type=MessageType.SYSTEM_EVENT,
                data={
                    "event_type": event_type,
                    **data
                }
            )
        )

    async def broadcast_metrics(self, metrics: Dict[str, Any]):
        """Broadcast metrics to subscribers."""
        await self.broadcast_to_channel(
            Channel.METRICS,
            WSMessage(
                type=MessageType.METRICS,
                data=metrics
            )
        )

    async def handle_ping(self, connection_id: str):
        """Handle ping message from client."""
        connection = self.connections.get(connection_id)
        if connection:
            await connection.send_json(WSMessage(type=MessageType.PONG))

    def get_connection_count(self) -> int:
        """Get total number of active connections."""
        return len(self.connections)

    def get_channel_subscriber_count(self, channel: Channel) -> int:
        """Get number of subscribers to a channel."""
        return len(self.channel_subscribers[channel])

    def get_stats(self) -> Dict[str, Any]:
        """Get WebSocket manager statistics."""
        return {
            "total_connections": self.get_connection_count(),
            "channel_subscribers": {
                channel.value: self.get_channel_subscriber_count(channel)
                for channel in Channel
            },
            "connections": [
                {
                    "id": conn_id[:16] + "...",
                    "user": conn.user.username if conn.user else "anonymous",
                    "role": conn.user.role.value if conn.user else None,
                    "subscribed_channels": [ch.value for ch in conn.subscribed_channels],
                    "connected_at": conn.connected_at.isoformat(),
                    "last_activity": conn.last_activity.isoformat()
                }
                for conn_id, conn in self.connections.items()
            ]
        }


# Global WebSocket manager instance
ws_manager = WebSocketManager()


async def handle_websocket_messages(
    websocket: WebSocket,
    connection_id: str,
    manager: WebSocketManager
):
    """Handle incoming WebSocket messages."""
    try:
        while True:
            # Receive message
            data = await websocket.receive_json()

            # Parse message
            try:
                message = WSMessage(**data)
            except Exception as e:
                connection = manager.connections.get(connection_id)
                if connection:
                    await connection.send_error(f"Invalid message format: {e}")
                continue

            # Handle message type
            if message.type == MessageType.PING:
                await manager.handle_ping(connection_id)

            elif message.type == MessageType.SUBSCRIBE:
                if message.channel:
                    await manager.subscribe(connection_id, message.channel)

            elif message.type == MessageType.UNSUBSCRIBE:
                if message.channel:
                    await manager.unsubscribe(connection_id, message.channel)

            else:
                connection = manager.connections.get(connection_id)
                if connection:
                    await connection.send_error(f"Unknown message type: {message.type}")

    except WebSocketDisconnect:
        await manager.disconnect(connection_id)
    except Exception as e:
        print(f"WebSocket error for {connection_id}: {e}")
        await manager.disconnect(connection_id)
