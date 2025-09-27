#!/bin/bash

# Start trading system with full monitoring
echo "Starting trading system with monitoring..."

# Source environment variables
if [ -f .env ]; then
    source .env
fi

# Ensure monitoring stack is running
echo "Checking monitoring stack..."
if ! curl -s http://localhost:9090/-/healthy > /dev/null 2>&1; then
    echo "Starting monitoring stack first..."
    ./scripts/start-monitoring.sh
    sleep 10
fi

# Set environment for metrics collection
export ENABLE_METRICS=1
export BUS_BACKEND=streams

echo "Starting trading services with metrics enabled..."

# Start services with metrics
echo "Starting market data service (metrics on :8011)..."
python -m apps.market_data.main --enable-metrics &
MARKET_DATA_PID=$!

sleep 2

echo "Starting strategies service (metrics on :8012)..."
python -m apps.strategies.main --enable-metrics &
STRATEGIES_PID=$!

sleep 2

echo "Starting executor service (metrics on :8013)..."
python -m apps.executor.main --enable-metrics &
EXECUTOR_PID=$!

sleep 2

echo "Starting PnL aggregator service (metrics on :8014)..."
python -m apps.pnl_aggregator.main --enable-metrics &
PNL_PID=$!

sleep 2

echo "Starting risk manager service (metrics on :8015)..."
python -m apps.risk_manager.main --enable-metrics &
RISK_PID=$!

sleep 2

echo "Starting API service (metrics on :8016)..."
python -m apps.api.main --enable-metrics &
API_PID=$!

# Wait a bit for services to start
sleep 5

echo ""
echo "Trading system is starting with monitoring enabled..."
echo ""
echo "Service access points:"
echo "  - API Server:        http://localhost:8000"
echo "  - Market Data:       http://localhost:8001"
echo "  - Strategies:        http://localhost:8002"
echo "  - Executor:          http://localhost:8003"
echo "  - PnL Aggregator:    http://localhost:8004"
echo "  - Risk Manager:      http://localhost:8005"
echo ""
echo "Metrics endpoints:"
echo "  - Market Data:       http://localhost:8011/metrics"
echo "  - Strategies:        http://localhost:8012/metrics"
echo "  - Executor:          http://localhost:8013/metrics"
echo "  - PnL Aggregator:    http://localhost:8014/metrics"
echo "  - Risk Manager:      http://localhost:8015/metrics"
echo "  - API:               http://localhost:8016/metrics"
echo ""
echo "Monitoring dashboards:"
echo "  - Grafana:           http://localhost:3000 (admin/admin123)"
echo "  - Prometheus:        http://localhost:9090"
echo "  - Alertmanager:      http://localhost:9093"
echo ""

# Function to cleanup on exit
cleanup() {
    echo "Shutting down trading services..."
    kill $MARKET_DATA_PID $STRATEGIES_PID $EXECUTOR_PID $PNL_PID $RISK_PID $API_PID 2>/dev/null || true
    wait
    echo "All services stopped."
}

# Set trap to cleanup on exit
trap cleanup EXIT

# Wait for interrupt
echo "Press Ctrl+C to stop all services..."
wait