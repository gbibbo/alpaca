#!/usr/bin/env python3
"""
tests/test_regression.py
Regression tests - Basic sanity checks to ensure core functionality works
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from lib.models import Signal, SignalSide, OrderIntent, Bar


class TestBasicModels:
    """Test basic model creation"""

    def test_signal_creation(self):
        """Test creating a basic signal"""
        signal = Signal(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            side=SignalSide.BUY,
            price=Decimal("100.00"),
            confidence=0.8,
            source="test"
        )
        assert signal.symbol == "GOOGL"
        assert signal.side == SignalSide.BUY
        assert signal.price == Decimal("100.00")

    def test_order_intent_creation(self):
        """Test creating a basic order intent"""
        order = OrderIntent(
            symbol="GOOGL",
            side=SignalSide.BUY,
            quantity=Decimal("10"),
            client_order_id="test_001",
            signal_source="test"
        )
        assert order.symbol == "GOOGL"
        assert order.side == SignalSide.BUY
        assert order.quantity == Decimal("10")

    def test_bar_creation(self):
        """Test creating a basic bar"""
        bar = Bar(
            timestamp=datetime.now(timezone.utc),
            symbol="GOOGL",
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("102"),
            volume=1000
        )
        assert bar.symbol == "GOOGL"
        assert bar.high == Decimal("105")
        assert bar.low == Decimal("95")


class TestBasicImports:
    """Test that all modules can be imported"""

    def test_import_models(self):
        """Test importing models module"""
        from lib import models
        assert hasattr(models, 'Signal')
        assert hasattr(models, 'SignalSide')
        assert hasattr(models, 'OrderIntent')

    def test_import_auth(self):
        """Test importing auth module"""
        from lib import auth
        assert hasattr(auth, 'User')
        assert hasattr(auth, 'UserRole')
        assert hasattr(auth, 'authenticate_user')

    def test_import_websocket_manager(self):
        """Test importing websocket_manager module"""
        from lib import websocket_manager
        assert hasattr(websocket_manager, 'WebSocketManager')
        assert hasattr(websocket_manager, 'Channel')
