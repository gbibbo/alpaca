#!/bin/bash

# Start monitoring stack for trading system
echo "Starting trading system monitoring stack..."

# Create network if it doesn't exist
docker network create trading-network 2>/dev/null || true

# Start Redis first (required by services)
echo "Starting Redis..."
docker-compose up -d redis

# Wait for Redis to be ready
echo "Waiting for Redis to be ready..."
sleep 5

# Start monitoring stack
echo "Starting monitoring services..."
docker-compose -f docker-compose.monitoring.yml up -d

# Wait for services to start
echo "Waiting for monitoring services to start..."
sleep 15

# Health check
echo "Checking service health..."

# Check Prometheus
if curl -s http://localhost:9090/-/healthy > /dev/null 2>&1; then
    echo "✓ Prometheus is healthy"
else
    echo "✗ Prometheus is not responding"
fi

# Check Grafana
if curl -s http://localhost:3000/api/health > /dev/null 2>&1; then
    echo "✓ Grafana is healthy"
else
    echo "✗ Grafana is not responding"
fi

# Check Alertmanager
if curl -s http://localhost:9093/-/healthy > /dev/null 2>&1; then
    echo "✓ Alertmanager is healthy"
else
    echo "✗ Alertmanager is not responding"
fi

echo ""
echo "Monitoring services are starting up..."
echo "Access points:"
echo "  - Grafana:       http://localhost:3000 (admin/admin123)"
echo "  - Prometheus:    http://localhost:9090"
echo "  - Alertmanager:  http://localhost:9093"
echo ""
echo "To start trading services with metrics:"
echo "  ./scripts/start-with-metrics.sh"
echo ""
echo "To view logs:"
echo "  docker-compose -f docker-compose.monitoring.yml logs -f"