#!/usr/bin/env python3
"""
lib/deduplication.py
Signal and Order Deduplication Service
Implements idempotency and TTL to prevent duplicate processing
"""

import time
import logging
from typing import Set, Dict, Optional
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from uuid import UUID
from lib.models import Signal, OrderIntent, OrderFill

logger = logging.getLogger(__name__)

class DeduplicationService:
    """
    Service to prevent duplicate processing of signals and orders
    Implements TTL-based cleanup and configurable retention periods
    """
    
    def __init__(self, 
                 signal_ttl_seconds: int = 3600,  # 1 hour default
                 order_ttl_seconds: int = 86400,  # 24 hours default
                 cleanup_interval_seconds: int = 300):  # 5 minutes cleanup
        
        self.signal_ttl = signal_ttl_seconds
        self.order_ttl = order_ttl_seconds
        self.cleanup_interval = cleanup_interval_seconds
        
        # Storage for processed items with timestamps
        self._processed_signals: Dict[str, float] = {}
        self._processed_orders: Dict[str, float] = {}
        self._processed_fills: Dict[str, float] = {}
        
        # Track by symbol for performance
        self._signals_by_symbol: Dict[str, Set[str]] = defaultdict(set)
        self._orders_by_symbol: Dict[str, Set[str]] = defaultdict(set)
        
        # Last cleanup timestamp
        self._last_cleanup = time.time()
        
        logger.info(f"Deduplication service initialized with signal_ttl={signal_ttl_seconds}s, order_ttl={order_ttl_seconds}s")
    
    def _generate_signal_key(self, signal: Signal) -> str:
        """Generate unique key for signal deduplication"""
        # Use signal_id if available, otherwise create composite key
        if hasattr(signal, 'signal_id') and signal.signal_id:
            return f"signal:{signal.signal_id}"
        
        # Fallback to composite key for backward compatibility
        timestamp_str = signal.timestamp.isoformat() if signal.timestamp else "unknown"
        return f"signal:{signal.symbol}:{signal.side}:{signal.source}:{timestamp_str}"
    
    def _generate_order_key(self, order: OrderIntent) -> str:
        """Generate unique key for order deduplication"""
        # Use intent_id if available, otherwise use client_order_id
        if hasattr(order, 'intent_id') and order.intent_id:
            return f"order:{order.intent_id}"
        
        return f"order:{order.client_order_id}"
    
    def _generate_fill_key(self, fill: OrderFill) -> str:
        """Generate unique key for fill deduplication"""
        # Use fill_id if available, otherwise use broker_order_id
        if hasattr(fill, 'fill_id') and fill.fill_id:
            return f"fill:{fill.fill_id}"
        
        return f"fill:{fill.broker_order_id}"
    
    def _cleanup_expired(self) -> None:
        """Clean up expired entries based on TTL"""
        current_time = time.time()
        
        # Only cleanup if enough time has passed
        if current_time - self._last_cleanup < self.cleanup_interval:
            return
        
        logger.debug("Starting deduplication cleanup")
        
        # Clean expired signals
        expired_signals = []
        for key, timestamp in self._processed_signals.items():
            if current_time - timestamp > self.signal_ttl:
                expired_signals.append(key)
        
        for key in expired_signals:
            del self._processed_signals[key]
            # Also remove from symbol tracking
            for symbol_set in self._signals_by_symbol.values():
                symbol_set.discard(key)
        
        # Clean expired orders
        expired_orders = []
        for key, timestamp in self._processed_orders.items():
            if current_time - timestamp > self.order_ttl:
                expired_orders.append(key)
        
        for key in expired_orders:
            del self._processed_orders[key]
            # Also remove from symbol tracking
            for symbol_set in self._orders_by_symbol.values():
                symbol_set.discard(key)
        
        # Clean expired fills (same TTL as orders)
        expired_fills = []
        for key, timestamp in self._processed_fills.items():
            if current_time - timestamp > self.order_ttl:
                expired_fills.append(key)
        
        for key in expired_fills:
            del self._processed_fills[key]
        
        self._last_cleanup = current_time
        
        if expired_signals or expired_orders or expired_fills:
            logger.info(f"Cleaned up {len(expired_signals)} signals, {len(expired_orders)} orders, {len(expired_fills)} fills")
    
    def is_signal_processed(self, signal: Signal) -> bool:
        """Check if signal has already been processed"""
        self._cleanup_expired()
        
        key = self._generate_signal_key(signal)
        return key in self._processed_signals
    
    def mark_signal_processed(self, signal: Signal) -> bool:
        """Mark signal as processed. Returns True if newly marked, False if already existed"""
        self._cleanup_expired()
        
        key = self._generate_signal_key(signal)
        
        # Check if already exists
        if key in self._processed_signals:
            logger.debug(f"Signal already processed: {key}")
            return False
        
        # Mark as processed
        current_time = time.time()
        self._processed_signals[key] = current_time
        self._signals_by_symbol[signal.symbol].add(key)
        
        logger.debug(f"Marked signal as processed: {key}")
        return True
    
    def is_order_processed(self, order: OrderIntent) -> bool:
        """Check if order has already been processed"""
        self._cleanup_expired()
        
        key = self._generate_order_key(order)
        return key in self._processed_orders
    
    def mark_order_processed(self, order: OrderIntent) -> bool:
        """Mark order as processed. Returns True if newly marked, False if already existed"""
        self._cleanup_expired()
        
        key = self._generate_order_key(order)
        
        # Check if already exists
        if key in self._processed_orders:
            logger.debug(f"Order already processed: {key}")
            return False
        
        # Mark as processed
        current_time = time.time()
        self._processed_orders[key] = current_time
        self._orders_by_symbol[order.symbol].add(key)
        
        logger.debug(f"Marked order as processed: {key}")
        return True
    
    def is_fill_processed(self, fill: OrderFill) -> bool:
        """Check if fill has already been processed"""
        self._cleanup_expired()
        
        key = self._generate_fill_key(fill)
        return key in self._processed_fills
    
    def mark_fill_processed(self, fill: OrderFill) -> bool:
        """Mark fill as processed. Returns True if newly marked, False if already existed"""
        self._cleanup_expired()
        
        key = self._generate_fill_key(fill)
        
        # Check if already exists
        if key in self._processed_fills:
            logger.debug(f"Fill already processed: {key}")
            return False
        
        # Mark as processed
        current_time = time.time()
        self._processed_fills[key] = current_time
        
        logger.debug(f"Marked fill as processed: {key}")
        return True
    
    def get_stats(self) -> Dict[str, int]:
        """Get deduplication statistics"""
        self._cleanup_expired()
        
        return {
            "processed_signals": len(self._processed_signals),
            "processed_orders": len(self._processed_orders),
            "processed_fills": len(self._processed_fills),
            "tracked_symbols": len(self._signals_by_symbol),
            "signal_ttl_seconds": self.signal_ttl,
            "order_ttl_seconds": self.order_ttl
        }
    
    def get_processed_signals_for_symbol(self, symbol: str) -> Set[str]:
        """Get all processed signal keys for a specific symbol"""
        self._cleanup_expired()
        return self._signals_by_symbol.get(symbol, set()).copy()
    
    def get_processed_orders_for_symbol(self, symbol: str) -> Set[str]:
        """Get all processed order keys for a specific symbol"""
        self._cleanup_expired()
        return self._orders_by_symbol.get(symbol, set()).copy()
    
    def force_cleanup(self) -> Dict[str, int]:
        """Force immediate cleanup and return stats"""
        old_stats = self.get_stats()
        
        # Force cleanup by setting last cleanup to 0
        self._last_cleanup = 0
        self._cleanup_expired()
        
        new_stats = self.get_stats()
        
        return {
            "signals_removed": old_stats["processed_signals"] - new_stats["processed_signals"],
            "orders_removed": old_stats["processed_orders"] - new_stats["processed_orders"],
            "fills_removed": old_stats["processed_fills"] - new_stats["processed_fills"]
        }
    
    def clear_all(self) -> None:
        """Clear all stored data (for testing)"""
        self._processed_signals.clear()
        self._processed_orders.clear()
        self._processed_fills.clear()
        self._signals_by_symbol.clear()
        self._orders_by_symbol.clear()
        self._last_cleanup = time.time()
        
        logger.info("Deduplication service cleared")

# Global deduplication service instance
_dedup_service: Optional[DeduplicationService] = None

def get_deduplication_service(
    signal_ttl_seconds: int = 3600,
    order_ttl_seconds: int = 86400,
    cleanup_interval_seconds: int = 300
) -> DeduplicationService:
    """Get or create global deduplication service"""
    global _dedup_service
    
    if _dedup_service is None:
        _dedup_service = DeduplicationService(
            signal_ttl_seconds=signal_ttl_seconds,
            order_ttl_seconds=order_ttl_seconds,
            cleanup_interval_seconds=cleanup_interval_seconds
        )
    
    return _dedup_service

def reset_deduplication_service() -> None:
    """Reset global deduplication service (for testing)"""
    global _dedup_service
    _dedup_service = None