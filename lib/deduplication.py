#!/usr/bin/env python3
"""
lib/deduplication.py
Enhanced Deduplication Service with Redis Persistence
Implements ChatGPT's recommendations for robust idempotency and TTL management
"""

import json
import logging
from typing import Set, Dict, Optional, Any
from datetime import datetime, timedelta
from uuid import UUID
from lib.models import Signal, OrderIntent, OrderFill
from lib.time_utils import TimeUtils, MonotonicTimer
from lib.settings import get_settings

logger = logging.getLogger(__name__)

class DeduplicationService:
    """
    Enhanced service to prevent duplicate processing with Redis persistence
    Implements TTL-based cleanup, monitoring, and cross-restart persistence
    """
    
    def __init__(self, redis_client=None):
        self.settings = get_settings()
        
        # Redis client for persistence
        if redis_client:
            self.redis = redis_client
        else:
            from lib.bus import get_bus
            bus = get_bus()
            self.redis = bus.redis_client
        
        # TTL configurations (seconds)
        self.signal_ttl = 3600  # 1 hour
        self.order_ttl = 86400   # 24 hours  
        self.fill_ttl = 604800   # 1 week (for audit)
        
        # Redis key prefixes
        self.signal_prefix = "dedup:signal:"
        self.order_prefix = "dedup:order:"
        self.fill_prefix = "dedup:fill:"
        self.stats_key = "dedup:stats"
        
        # In-memory cache for performance (with Redis as source of truth)
        self._signal_cache: Dict[str, float] = {}
        self._order_cache: Dict[str, float] = {}
        self._fill_cache: Dict[str, float] = {}
        
        # Performance tracking
        self.cache_hits = 0
        self.cache_misses = 0
        self.redis_hits = 0
        self.redis_misses = 0
        
        logger.info(f"Enhanced deduplication service initialized with Redis persistence")
        logger.info(f"TTLs: signals={self.signal_ttl}s, orders={self.order_ttl}s, fills={self.fill_ttl}s")
    
    def _generate_signal_key(self, signal: Signal) -> str:
        """Generate unique, stable key for signal deduplication"""
        # Use signal_id if available for true idempotency
        if hasattr(signal, 'signal_id') and signal.signal_id:
            return f"{self.signal_prefix}{signal.signal_id}"
        
        # Fallback: create deterministic key from signal properties
        # This ensures same signal content = same key
        key_parts = [
            signal.symbol,
            signal.side,
            signal.source,
            f"{float(signal.confidence):.3f}",  # Normalize confidence
            signal.timestamp.isoformat() if signal.timestamp else "no_timestamp"
        ]
        
        key_hash = hash(tuple(key_parts))
        return f"{self.signal_prefix}{signal.symbol}:{signal.source}:{abs(key_hash)}"
    
    def _generate_order_key(self, order: OrderIntent) -> str:
        """Generate unique, stable key for order deduplication"""
        # Use intent_id if available
        if hasattr(order, 'intent_id') and order.intent_id:
            return f"{self.order_prefix}{order.intent_id}"
        
        # Use client_order_id (should be unique per order attempt)
        return f"{self.order_prefix}{order.client_order_id}"
    
    def _generate_fill_key(self, fill: OrderFill) -> str:
        """Generate unique, stable key for fill deduplication"""
        # Use fill_id if available
        if hasattr(fill, 'fill_id') and fill.fill_id:
            return f"{self.fill_prefix}{fill.fill_id}"
        
        # Use broker_order_id + timestamp for uniqueness
        timestamp_part = fill.timestamp.isoformat() if fill.timestamp else "no_timestamp"
        return f"{self.fill_prefix}{fill.broker_order_id}:{timestamp_part}"
    
    def _check_cache_first(self, key: str, cache: Dict[str, float], ttl: int) -> Optional[bool]:
        """Check in-memory cache first for performance"""
        if key in cache:
            # Check if cache entry is still valid
            cached_time = cache[key]
            current_time = MonotonicTimer.current()
            
            if current_time - cached_time < ttl:
                self.cache_hits += 1
                return True  # Found in cache and still valid
            else:
                # Cache entry expired, remove it
                del cache[key]
                self.cache_misses += 1
                return None  # Cache miss due to expiration
        
        self.cache_misses += 1
        return None  # Not in cache
    
    def _check_redis(self, key: str) -> bool:
        """Check Redis for key existence"""
        try:
            exists = self.redis.exists(key)
            if exists:
                self.redis_hits += 1
                return True
            else:
                self.redis_misses += 1
                return False
        except Exception as e:
            logger.error(f"Redis check failed for key {key}: {e}")
            return False
    
    def _set_redis_with_ttl(self, key: str, value: Any, ttl: int) -> bool:
        """Set key in Redis with TTL"""
        try:
            # Store as JSON for complex objects, or simple string for basic values
            if isinstance(value, (dict, list)):
                stored_value = json.dumps(value)
            else:
                stored_value = str(value)
            
            self.redis.setex(key, ttl, stored_value)
            return True
        except Exception as e:
            logger.error(f"Redis set failed for key {key}: {e}")
            return False
    
    def is_signal_processed(self, signal: Signal) -> bool:
        """Check if signal has already been processed (cache + Redis)"""
        key = self._generate_signal_key(signal)
        
        # Check cache first
        cache_result = self._check_cache_first(key, self._signal_cache, self.signal_ttl)
        if cache_result is not None:
            return cache_result
        
        # Check Redis
        redis_result = self._check_redis(key)
        
        # Update cache if found in Redis
        if redis_result:
            current_time = MonotonicTimer.current()
            self._signal_cache[key] = current_time
        
        return redis_result
    
    def mark_signal_processed(self, signal: Signal) -> bool:
        """
        Mark signal as processed in both cache and Redis
        Returns True if newly marked, False if already existed
        """
        key = self._generate_signal_key(signal)
        
        # Check if already processed first
        if self.is_signal_processed(signal):
            logger.debug(f"Signal already processed: {key}")
            return False
        
        # Mark in both cache and Redis
        current_time = MonotonicTimer.current()
        self._signal_cache[key] = current_time
        
        # Store signal metadata in Redis for audit
        signal_data = {
            "symbol": signal.symbol,
            "side": signal.side,
            "confidence": float(signal.confidence),
            "source": signal.source,
            "processed_at": TimeUtils.utc_now().isoformat(),
            "market_time": TimeUtils.market_now().isoformat()
        }
        
        success = self._set_redis_with_ttl(key, signal_data, self.signal_ttl)
        
        if success:
            logger.debug(f"Marked signal as processed: {key}")
            self._update_stats("signals_processed")
            return True
        else:
            # Remove from cache if Redis failed
            self._signal_cache.pop(key, None)
            logger.error(f"Failed to mark signal in Redis: {key}")
            return False
    
    def is_order_processed(self, order: OrderIntent) -> bool:
        """Check if order has already been processed"""
        key = self._generate_order_key(order)
        
        # Check cache first
        cache_result = self._check_cache_first(key, self._order_cache, self.order_ttl)
        if cache_result is not None:
            return cache_result
        
        # Check Redis
        redis_result = self._check_redis(key)
        
        # Update cache if found in Redis
        if redis_result:
            current_time = MonotonicTimer.current()
            self._order_cache[key] = current_time
        
        return redis_result
    
    def mark_order_processed(self, order: OrderIntent) -> bool:
        """Mark order as processed. Returns True if newly marked, False if already existed"""
        key = self._generate_order_key(order)
        
        # Check if already processed
        if self.is_order_processed(order):
            logger.debug(f"Order already processed: {key}")
            return False
        
        # Mark in both cache and Redis
        current_time = MonotonicTimer.current()
        self._order_cache[key] = current_time
        
        # Store order metadata in Redis
        order_data = {
            "symbol": order.symbol,
            "side": order.side,
            "quantity": float(order.quantity),
            "order_type": order.order_type,
            "client_order_id": order.client_order_id,
            "signal_source": order.signal_source,
            "processed_at": TimeUtils.utc_now().isoformat(),
            "market_time": TimeUtils.market_now().isoformat()
        }
        
        success = self._set_redis_with_ttl(key, order_data, self.order_ttl)
        
        if success:
            logger.debug(f"Marked order as processed: {key}")
            self._update_stats("orders_processed")
            return True
        else:
            self._order_cache.pop(key, None)
            logger.error(f"Failed to mark order in Redis: {key}")
            return False
    
    def is_fill_processed(self, fill: OrderFill) -> bool:
        """Check if fill has already been processed"""
        key = self._generate_fill_key(fill)
        
        # Check cache first
        cache_result = self._check_cache_first(key, self._fill_cache, self.fill_ttl)
        if cache_result is not None:
            return cache_result
        
        # Check Redis
        redis_result = self._check_redis(key)
        
        # Update cache if found in Redis
        if redis_result:
            current_time = MonotonicTimer.current()
            self._fill_cache[key] = current_time
        
        return redis_result
    
    def mark_fill_processed(self, fill: OrderFill) -> bool:
        """Mark fill as processed. Returns True if newly marked, False if already existed"""
        key = self._generate_fill_key(fill)
        
        # Check if already processed
        if self.is_fill_processed(fill):
            logger.debug(f"Fill already processed: {key}")
            return False
        
        # Mark in both cache and Redis
        current_time = MonotonicTimer.current()
        self._fill_cache[key] = current_time
        
        # Store fill metadata in Redis (important for audit trail)
        fill_data = {
            "symbol": fill.symbol,
            "side": fill.side,
            "quantity": float(fill.quantity),
            "fill_price": float(fill.fill_price),
            "fill_quantity": float(fill.fill_quantity),
            "total_value": float(fill.total_value),
            "broker_order_id": fill.broker_order_id,
            "client_order_id": fill.client_order_id,
            "status": fill.status,
            "processed_at": TimeUtils.utc_now().isoformat(),
            "market_time": TimeUtils.market_now().isoformat()
        }
        
        success = self._set_redis_with_ttl(key, fill_data, self.fill_ttl)
        
        if success:
            logger.debug(f"Marked fill as processed: {key}")
            self._update_stats("fills_processed")
            return True
        else:
            self._fill_cache.pop(key, None)
            logger.error(f"Failed to mark fill in Redis: {key}")
            return False
    
    def _update_stats(self, stat_name: str):
        """Update statistics in Redis"""
        try:
            self.redis.hincrby(self.stats_key, stat_name, 1)
            self.redis.hset(self.stats_key, "last_updated", TimeUtils.utc_now().isoformat())
            self.redis.expire(self.stats_key, 86400 * 7)  # Keep stats for 1 week
        except Exception as e:
            logger.error(f"Failed to update stats: {e}")
    
    def get_stats(self) -> Dict[str, int]:
        """Get deduplication statistics (backward compatible)"""
        stats = self.get_comprehensive_stats()
        return {
            "processed_signals": len(self._signal_cache),
            "processed_orders": len(self._order_cache),
            "processed_fills": len(self._fill_cache),
            "tracked_symbols": len(set(k.split(':')[2] for k in self._signal_cache.keys() if ':' in k)),
            "signal_ttl_seconds": self.signal_ttl,
            "order_ttl_seconds": self.order_ttl
        }
    
    def get_comprehensive_stats(self) -> Dict[str, Any]:
        """Get comprehensive deduplication statistics"""
        try:
            redis_stats = self.redis.hgetall(self.stats_key)
            
            # Convert Redis hash values to appropriate types
            processed_stats = {}
            for key, value in redis_stats.items():
                if key in ['signals_processed', 'orders_processed', 'fills_processed']:
                    processed_stats[key] = int(value) if value else 0
                else:
                    processed_stats[key] = value
            
            return {
                # Processed counts from Redis
                "processed_counts": processed_stats,
                
                # Cache statistics  
                "cache_stats": {
                    "signal_cache_size": len(self._signal_cache),
                    "order_cache_size": len(self._order_cache),
                    "fill_cache_size": len(self._fill_cache),
                    "cache_hits": self.cache_hits,
                    "cache_misses": self.cache_misses,
                    "cache_hit_rate": self.cache_hits / max(1, self.cache_hits + self.cache_misses)
                },
                
                # Redis statistics
                "redis_stats": {
                    "redis_hits": self.redis_hits,
                    "redis_misses": self.redis_misses,
                    "redis_hit_rate": self.redis_hits / max(1, self.redis_hits + self.redis_misses)
                },
                
                # Configuration
                "config": {
                    "signal_ttl_seconds": self.signal_ttl,
                    "order_ttl_seconds": self.order_ttl,
                    "fill_ttl_seconds": self.fill_ttl
                },
                
                "last_updated": TimeUtils.utc_now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to get comprehensive stats: {e}")
            return {"error": str(e)}
    
    def get_processed_signals_for_symbol(self, symbol: str) -> Set[str]:
        """Get all processed signal keys for a specific symbol"""
        matching_keys = set()
        for key in self._signal_cache.keys():
            if symbol in key:
                matching_keys.add(key)
        return matching_keys
    
    def get_processed_orders_for_symbol(self, symbol: str) -> Set[str]:
        """Get all processed order keys for a specific symbol"""
        matching_keys = set()
        for key in self._order_cache.keys():
            if symbol in key:
                matching_keys.add(key)
        return matching_keys
    
    def cleanup_expired_cache(self) -> Dict[str, int]:
        """Force cleanup of expired cache entries"""
        current_time = MonotonicTimer.current()
        
        # Clean signal cache
        expired_signals = []
        for key, cached_time in self._signal_cache.items():
            if current_time - cached_time > self.signal_ttl:
                expired_signals.append(key)
        
        for key in expired_signals:
            del self._signal_cache[key]
        
        # Clean order cache
        expired_orders = []
        for key, cached_time in self._order_cache.items():
            if current_time - cached_time > self.order_ttl:
                expired_orders.append(key)
                
        for key in expired_orders:
            del self._order_cache[key]
        
        # Clean fill cache
        expired_fills = []
        for key, cached_time in self._fill_cache.items():
            if current_time - cached_time > self.fill_ttl:
                expired_fills.append(key)
                
        for key in expired_fills:
            del self._fill_cache[key]
        
        cleanup_stats = {
            "signals_removed": len(expired_signals),
            "orders_removed": len(expired_orders), 
            "fills_removed": len(expired_fills)
        }
        
        if sum(cleanup_stats.values()) > 0:
            logger.info(f"Cache cleanup completed: {cleanup_stats}")
        
        return cleanup_stats
    
    def force_cleanup(self) -> Dict[str, int]:
        """Force immediate cleanup and return stats (backward compatible)"""
        return self.cleanup_expired_cache()
    
    def clear_all(self, confirm: bool = False) -> bool:
        """Clear all deduplication data (dangerous - use with caution)"""
        if not confirm:
            logger.warning("clear_all() called without confirmation - skipping")
            return False
        
        try:
            # Clear Redis keys
            pattern_keys = [
                f"{self.signal_prefix}*",
                f"{self.order_prefix}*", 
                f"{self.fill_prefix}*",
                self.stats_key
            ]
            
            for pattern in pattern_keys:
                keys = self.redis.keys(pattern)
                if keys:
                    self.redis.delete(*keys)
            
            # Clear cache
            self._signal_cache.clear()
            self._order_cache.clear()
            self._fill_cache.clear()
            
            # Reset stats
            self.cache_hits = 0
            self.cache_misses = 0
            self.redis_hits = 0
            self.redis_misses = 0
            
            logger.warning("All deduplication data cleared")
            return True
            
        except Exception as e:
            logger.error(f"Failed to clear deduplication data: {e}")
            return False


# Global enhanced deduplication service instance
_dedup_service: Optional[DeduplicationService] = None

def get_deduplication_service(
    signal_ttl_seconds: int = 3600,
    order_ttl_seconds: int = 86400,
    cleanup_interval_seconds: int = 300,
    redis_client=None
) -> DeduplicationService:
    """Get or create global deduplication service (backward compatible)"""
    global _dedup_service
    
    if _dedup_service is None:
        _dedup_service = DeduplicationService(redis_client=redis_client)
    
    return _dedup_service

def get_enhanced_deduplication_service(redis_client=None) -> DeduplicationService:
    """Get or create global enhanced deduplication service"""
    return get_deduplication_service(redis_client=redis_client)

def reset_deduplication_service() -> None:
    """Reset global deduplication service (for testing)"""
    global _dedup_service
    _dedup_service = None

def reset_enhanced_deduplication_service() -> None:
    """Reset global enhanced deduplication service (for testing, backward compatible)"""
    reset_deduplication_service()