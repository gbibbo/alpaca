"""
WebSocket API Routes
Epic 3 - API/Auth & WebSocket

Provides WebSocket endpoints for real-time monitoring.
"""

from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException, status

from lib.websocket_manager import (
    ws_manager,
    handle_websocket_messages,
    Channel,
)
from lib.auth import decode_token, get_user, authenticate_api_key
from lib.auth_dependencies import get_current_user_optional


router = APIRouter(prefix="/ws", tags=["WebSocket"])


@router.websocket("/live")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    api_key: Optional[str] = Query(None, alias="api_key")
):
    """
    WebSocket endpoint for real-time trading data.

    **Authentication:**
    - Provide `token` query parameter with JWT access token
    - OR provide `api_key` query parameter with API key

    **Message Format (Client -> Server):**
    ```json
    {
        "type": "subscribe" | "unsubscribe" | "ping",
        "channel": "signals" | "orders" | "fills" | "bars" | "positions" | "equity" | "system" | "metrics"
    }
    ```

    **Message Format (Server -> Client):**
    ```json
    {
        "type": "signal" | "order" | "fill" | "bar" | "system_event" | "metrics" | "error",
        "channel": "...",
        "data": {...},
        "timestamp": "2024-01-01T12:00:00Z"
    }
    ```

    **Available Channels:**
    - `signals`: Trading signals
    - `orders`: Order intents
    - `fills`: Order fills
    - `bars`: Market data bars
    - `positions`: Position updates
    - `equity`: Equity curve updates
    - `system`: System events
    - `metrics`: Performance metrics

    **Example Usage (JavaScript):**
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/ws/live?token=YOUR_JWT_TOKEN');

    ws.onopen = () => {
        // Subscribe to signals channel
        ws.send(JSON.stringify({
            type: 'subscribe',
            channel: 'signals'
        }));
    };

    ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        console.log('Received:', message);
    };
    ```
    """
    # Authenticate user
    user = None

    if token:
        token_data = decode_token(token)
        if token_data:
            user = get_user(token_data.username)

    if not user and api_key:
        api_key_obj = authenticate_api_key(api_key)
        if api_key_obj:
            from lib.auth import User, UserRole
            user = User(
                username=api_key_obj.key_id,
                email=f"{api_key_obj.name}@api.local",
                full_name=api_key_obj.name,
                role=api_key_obj.role
            )

    # Connect WebSocket
    connection_id = await ws_manager.connect(websocket, user)

    # Handle messages
    await handle_websocket_messages(websocket, connection_id, ws_manager)


@router.get("/stats")
async def get_websocket_stats():
    """
    Get WebSocket connection statistics.

    Returns information about active connections and channel subscriptions.
    """
    return ws_manager.get_stats()
