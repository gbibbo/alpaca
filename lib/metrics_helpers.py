#!/usr/bin/env python3
"""
lib/metrics_helpers.py
Prometheus Metrics Helpers for Trading Platform
Implements ChatGPT's recommended observability patterns with standard metrics
"""

import os
import logging
import threading
from typing import Dict, Optional, Any
from prometheus_client import (
    Counter, Histogram, Gauge, Summary, Info,
    CollectorRegistry, MetricsHandler, start_http_server,
    generate_latest, CONTENT_TYPE_LATEST
)
from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)

# Global registry for metrics
TRADING_REGISTRY = CollectorRegistry()

# Track started metric servers to prevent port conflicts
_started_ports = set()
_port_lock = threading.Lock()

# =============================================================================
# STANDARD TRADING PLATFORM METRICS
# =============================================================================

# Message Bus Metrics
BUS_PUBLISHED = Counter(
    'trading_bus_messages_published_total',
    'Total number of messages published to the bus',
    ['stream', 'message_type', 'service'],
    registry=TRADING_REGISTRY
)

BUS_CONSUMED = Counter(
    'trading_bus_messages_consumed_total',
    'Total number of messages consumed from the bus',
    ['stream', 'message_type', 'service'],
    registry=TRADING_REGISTRY
)

BUS_ACKED = Counter(
    'trading_bus_messages_acked_total',
    'Total number of messages acknowledged',
    ['stream', 'service'],
    registry=TRADING_REGISTRY
)

BUS_ERRORS = Counter(
    'trading_bus_errors_total',
    'Total number of bus-related errors',
    ['stream', 'error_type', 'service'],
    registry=TRADING_REGISTRY
)

BUS_PENDING = Gauge(
    'trading_bus_pending_messages',
    'Number of pending messages in consumer group',
    ['stream', 'consumer_group'],
    registry=TRADING_REGISTRY
)

BUS_LATENCY = Histogram(
    'trading_bus_message_processing_seconds',
    'Time spent processing messages',
    ['stream', 'service'],
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
    registry=TRADING_REGISTRY
)

# Trading & Orders Metrics
SIGNALS_GENERATED = Counter(
    'trading_signals_generated_total',
    'Total number of trading signals generated',
    ['symbol', 'side', 'strategy', 'source'],
    registry=TRADING_REGISTRY
)

SIGNALS_PROCESSED = Counter(
    'trading_signals_processed_total',
    'Total number of signals processed by risk manager',
    ['symbol', 'status'],  # status: approved, rejected
    registry=TRADING_REGISTRY
)

ORDERS_SUBMITTED = Counter(
    'trading_orders_submitted_total',
    'Total number of orders submitted to broker',
    ['symbol', 'side', 'order_type'],
    registry=TRADING_REGISTRY
)

ORDERS_FILLED = Counter(
    'trading_orders_filled_total',
    'Total number of orders filled',
    ['symbol', 'side', 'fill_type'],  # fill_type: full, partial
    registry=TRADING_REGISTRY
)

ORDERS_FAILED = Counter(
    'trading_orders_failed_total',
    'Total number of failed orders',
    ['symbol', 'side', 'error_type'],
    registry=TRADING_REGISTRY
)

ORDER_VALUE = Summary(
    'trading_order_value_usd',
    'Value of orders in USD',
    ['symbol', 'side'],
    registry=TRADING_REGISTRY
)

ORDER_LATENCY = Histogram(
    'trading_order_execution_seconds',
    'Time from order submission to fill',
    ['symbol', 'order_type'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
    registry=TRADING_REGISTRY
)

# Rate Limiting & API Metrics
RATE_LIMIT_HITS = Counter(
    'trading_rate_limit_hits_total',
    'Number of times rate limits were hit',
    ['api', 'endpoint', 'limit_type'],
    registry=TRADING_REGISTRY
)

API_CALLS = Counter(
    'trading_api_calls_total',
    'Total number of API calls made',
    ['api', 'endpoint', 'method', 'status_code'],
    registry=TRADING_REGISTRY
)

API_LATENCY = Histogram(
    'trading_api_request_seconds',
    'API request latency',
    ['api', 'endpoint'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=TRADING_REGISTRY
)

ALPACA_429_TOTAL = Counter(
    'trading_alpaca_429_total',
    'Total number of 429 responses from Alpaca API',
    ['endpoint'],
    registry=TRADING_REGISTRY
)

ALPACA_RETRY_TOTAL = Counter(
    'trading_alpaca_retry_total',
    'Total number of Alpaca API retries',
    ['endpoint', 'attempt'],
    registry=TRADING_REGISTRY
)

# Risk Management Metrics
RISK_BREAKERS_OPEN = Gauge(
    'trading_circuit_breakers_open',
    'Number of circuit breakers currently open',
    ['breaker_name', 'service'],
    registry=TRADING_REGISTRY
)

RISK_CHECKS_FAILED = Counter(
    'trading_risk_checks_failed_total',
    'Total number of failed risk checks',
    ['check_type', 'reason'],
    registry=TRADING_REGISTRY
)

DUPLICATE_ORDERS_BLOCKED = Counter(
    'trading_duplicate_orders_blocked_total',
    'Total number of duplicate orders blocked',
    ['symbol', 'dedup_type'],
    registry=TRADING_REGISTRY
)

# Portfolio & PnL Metrics
PORTFOLIO_VALUE = Gauge(
    'trading_portfolio_value_usd',
    'Current portfolio value in USD',
    ['account_type'],
    registry=TRADING_REGISTRY
)

UNREALIZED_PNL = Gauge(
    'trading_unrealized_pnl_usd',
    'Current unrealized P&L in USD',
    ['symbol'],
    registry=TRADING_REGISTRY
)

REALIZED_PNL = Counter(
    'trading_realized_pnl_usd',
    'Realized P&L in USD',
    ['symbol', 'trade_type'],  # trade_type: long, short
    registry=TRADING_REGISTRY
)

POSITION_SIZE = Gauge(
    'trading_position_size',
    'Current position size in shares',
    ['symbol'],
    registry=TRADING_REGISTRY
)

# System Health Metrics
SERVICE_UPTIME = Gauge(
    'trading_service_uptime_seconds',
    'Service uptime in seconds',
    ['service'],
    registry=TRADING_REGISTRY
)

SERVICE_HEALTH = Gauge(
    'trading_service_health',
    'Service health status (1=healthy, 0=unhealthy)',
    ['service', 'component'],
    registry=TRADING_REGISTRY
)

MEMORY_USAGE = Gauge(
    'trading_memory_usage_bytes',
    'Memory usage in bytes',
    ['service'],
    registry=TRADING_REGISTRY
)

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def start_metrics_server(port: int = 8000) -> threading.Thread:
    """
    Start Prometheus metrics server for headless services
    Returns the server thread for control
    """
    with _port_lock:
        if port in _started_ports:
            logger.info(f"📊 Metrics server already running on port {port}, skipping")
            # Return a dummy thread to maintain API compatibility
            dummy_thread = threading.Thread(target=lambda: None)
            dummy_thread.start()
            return dummy_thread

        try:
            # Start HTTP server in background thread
            server_thread = threading.Thread(
                target=start_http_server,
                args=(port,),
                kwargs={'registry': TRADING_REGISTRY},
                daemon=True,
                name=f"metrics-server-{port}"
            )
            server_thread.start()
            _started_ports.add(port)

            logger.info(f"✅ Prometheus metrics server started on port {port}")
            logger.info(f"📊 Metrics available at: http://localhost:{port}/metrics")

            return server_thread

        except OSError as e:
            if e.errno == 98:  # Address already in use
                logger.warning(f"⚠️ Metrics port {port} already in use, skipping")
                _started_ports.add(port)  # Mark as used to prevent retries
                dummy_thread = threading.Thread(target=lambda: None)
                dummy_thread.start()
                return dummy_thread
            raise
        except Exception as e:
            logger.error(f"❌ Failed to start metrics server on port {port}: {e}")
            raise

def metrics_app() -> FastAPI:
    """
    Create FastAPI app with /metrics endpoint for web services
    Use this for services that already have FastAPI apps
    """
    app = FastAPI(title="Trading Metrics", version="1.0.0")

    @app.get("/metrics", response_class=PlainTextResponse)
    async def get_metrics():
        """Prometheus metrics endpoint"""
        try:
            metrics_data = generate_latest(TRADING_REGISTRY)
            return Response(
                content=metrics_data,
                media_type=CONTENT_TYPE_LATEST
            )
        except Exception as e:
            logger.error(f"Error generating metrics: {e}")
            return Response(
                content=f"# Error generating metrics: {e}\n",
                media_type=CONTENT_TYPE_LATEST,
                status_code=500
            )

    @app.get("/health")
    async def health_check():
        """Simple health check endpoint"""
        return {"status": "healthy", "metrics": "available"}

    logger.info("📊 FastAPI metrics app created with /metrics endpoint")
    return app

def add_metrics_to_fastapi(app: FastAPI) -> None:
    """
    Add metrics endpoint to existing FastAPI app
    """
    @app.get("/metrics", response_class=PlainTextResponse)
    async def get_metrics():
        """Prometheus metrics endpoint"""
        try:
            metrics_data = generate_latest(TRADING_REGISTRY)
            return Response(
                content=metrics_data,
                media_type=CONTENT_TYPE_LATEST
            )
        except Exception as e:
            logger.error(f"Error generating metrics: {e}")
            return Response(
                content=f"# Error generating metrics: {e}\n",
                media_type=CONTENT_TYPE_LATEST,
                status_code=500
            )

    logger.info("📊 Added /metrics endpoint to existing FastAPI app")

# =============================================================================
# CONTEXT MANAGERS FOR EASY METRICS
# =============================================================================

class MetricsTimer:
    """Context manager for timing operations"""

    def __init__(self, histogram: Histogram, labels: Dict[str, str] = None):
        self.histogram = histogram
        self.labels = labels or {}
        self.timer = None

    def __enter__(self):
        self.timer = self.histogram.labels(**self.labels).time()
        return self.timer.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.timer.__exit__(exc_type, exc_val, exc_tb)

def time_bus_processing(stream: str, service: str):
    """Timer for bus message processing"""
    return MetricsTimer(BUS_LATENCY, {'stream': stream, 'service': service})

def time_order_execution(symbol: str, order_type: str):
    """Timer for order execution"""
    return MetricsTimer(ORDER_LATENCY, {'symbol': symbol, 'order_type': order_type})

def time_api_request(api: str, endpoint: str):
    """Timer for API requests"""
    return MetricsTimer(API_LATENCY, {'api': api, 'endpoint': endpoint})

# =============================================================================
# SERVICE-SPECIFIC METRIC HELPERS
# =============================================================================

class ServiceMetrics:
    """Base class for service-specific metrics"""

    def __init__(self, service_name: str):
        self.service_name = service_name
        self.start_time = None

    def mark_service_start(self):
        """Mark service startup"""
        import time
        self.start_time = time.time()
        SERVICE_HEALTH.labels(service=self.service_name, component='main').set(1)
        logger.info(f"📊 Service {self.service_name} metrics initialized")

    def mark_service_stop(self):
        """Mark service shutdown"""
        SERVICE_HEALTH.labels(service=self.service_name, component='main').set(0)
        logger.info(f"📊 Service {self.service_name} metrics stopped")

    def update_uptime(self):
        """Update service uptime metric"""
        if self.start_time:
            import time
            uptime = time.time() - self.start_time
            SERVICE_UPTIME.labels(service=self.service_name).set(uptime)

    def update_memory_usage(self):
        """Update memory usage metric"""
        try:
            import psutil
            process = psutil.Process()
            memory_bytes = process.memory_info().rss
            MEMORY_USAGE.labels(service=self.service_name).set(memory_bytes)
        except ImportError:
            logger.debug("psutil not available for memory metrics")
        except Exception as e:
            logger.debug(f"Error updating memory usage: {e}")

class RiskManagerMetrics(ServiceMetrics):
    """Metrics helpers for Risk Manager"""

    def __init__(self):
        super().__init__('risk_manager')

    def signal_processed(self, symbol: str, status: str):
        """Record signal processing"""
        SIGNALS_PROCESSED.labels(symbol=symbol, status=status).inc()

    def risk_check_failed(self, check_type: str, reason: str):
        """Record failed risk check"""
        RISK_CHECKS_FAILED.labels(check_type=check_type, reason=reason).inc()

    def circuit_breaker_status(self, breaker_name: str, is_open: bool):
        """Update circuit breaker status"""
        RISK_BREAKERS_OPEN.labels(breaker_name=breaker_name, service=self.service_name).set(1 if is_open else 0)

class ExecutorMetrics(ServiceMetrics):
    """Metrics helpers for Order Executor"""

    def __init__(self):
        super().__init__('executor')

    def order_submitted(self, symbol: str, side: str, order_type: str):
        """Record order submission"""
        ORDERS_SUBMITTED.labels(symbol=symbol, side=side, order_type=order_type).inc()

    def order_filled(self, symbol: str, side: str, fill_type: str, value: float):
        """Record order fill"""
        ORDERS_FILLED.labels(symbol=symbol, side=side, fill_type=fill_type).inc()
        ORDER_VALUE.labels(symbol=symbol, side=side).observe(value)

    def order_failed(self, symbol: str, side: str, error_type: str):
        """Record order failure"""
        ORDERS_FAILED.labels(symbol=symbol, side=side, error_type=error_type).inc()

    def alpaca_429(self, endpoint: str):
        """Record Alpaca 429 response"""
        ALPACA_429_TOTAL.labels(endpoint=endpoint).inc()

    def alpaca_retry(self, endpoint: str, attempt: int):
        """Record Alpaca retry"""
        ALPACA_RETRY_TOTAL.labels(endpoint=endpoint, attempt=str(attempt)).inc()

class StrategyMetrics(ServiceMetrics):
    """Metrics helpers for Trading Strategies"""

    def __init__(self, strategy_name: str):
        super().__init__(f'strategy_{strategy_name}')
        self.strategy_name = strategy_name

    def signal_generated(self, symbol: str, side: str, source: str):
        """Record signal generation"""
        SIGNALS_GENERATED.labels(
            symbol=symbol,
            side=side,
            strategy=self.strategy_name,
            source=source
        ).inc()

class BusMetrics:
    """Metrics helpers for Message Bus"""

    @staticmethod
    def message_published(stream: str, message_type: str, service: str):
        """Record message publication"""
        BUS_PUBLISHED.labels(stream=stream, message_type=message_type, service=service).inc()

    @staticmethod
    def message_consumed(stream: str, message_type: str, service: str):
        """Record message consumption"""
        BUS_CONSUMED.labels(stream=stream, message_type=message_type, service=service).inc()

    @staticmethod
    def message_acked(stream: str, service: str):
        """Record message acknowledgment"""
        BUS_ACKED.labels(stream=stream, service=service).inc()

    @staticmethod
    def bus_error(stream: str, error_type: str, service: str):
        """Record bus error"""
        BUS_ERRORS.labels(stream=stream, error_type=error_type, service=service).inc()

    @staticmethod
    def update_pending_messages(stream: str, consumer_group: str, count: int):
        """Update pending messages gauge"""
        BUS_PENDING.labels(stream=stream, consumer_group=consumer_group).set(count)

# =============================================================================
# REGISTRY UTILITIES
# =============================================================================

def get_metrics_summary() -> Dict[str, Any]:
    """Get summary of all metrics for debugging"""
    try:
        from prometheus_client.parser import text_string_to_metric_families

        metrics_text = generate_latest(TRADING_REGISTRY).decode('utf-8')
        metrics_families = text_string_to_metric_families(metrics_text)

        summary = {}
        for family in metrics_families:
            summary[family.name] = {
                'type': family.type,
                'help': family.documentation,
                'samples': len(list(family.samples))
            }

        return summary

    except Exception as e:
        logger.error(f"Error generating metrics summary: {e}")
        return {"error": str(e)}

def reset_metrics():
    """Reset all metrics (useful for testing)"""
    logger.warning("🔄 Resetting all trading metrics")
    TRADING_REGISTRY._collector_to_names.clear()
    TRADING_REGISTRY._names_to_collectors.clear()

# =============================================================================
# AUTO-DISCOVERY FOR PORT ALLOCATION
# =============================================================================

def find_available_port(start_port: int = 8000, max_attempts: int = 100) -> int:
    """Find an available port for metrics server"""
    import socket

    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                logger.debug(f"Port {port} is available for metrics server")
                return port
        except OSError:
            continue

    raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_attempts}")

# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    # Example usage for testing
    import time

    # Start metrics server
    port = find_available_port(8000)
    server = start_metrics_server(port)

    # Create service metrics
    risk_metrics = RiskManagerMetrics()
    risk_metrics.mark_service_start()

    # Simulate some activity
    risk_metrics.signal_processed("AAPL", "approved")
    risk_metrics.signal_processed("GOOGL", "rejected")

    BusMetrics.message_published("signals", "signal", "strategy")
    BusMetrics.message_consumed("signals", "signal", "risk_manager")

    print(f"Metrics server running on port {port}")
    print("Check metrics at: http://localhost:{}/metrics".format(port))
    print("Press Ctrl+C to stop")

    try:
        while True:
            risk_metrics.update_uptime()
            risk_metrics.update_memory_usage()
            time.sleep(5)
    except KeyboardInterrupt:
        print("Stopping metrics server")
        risk_metrics.mark_service_stop()