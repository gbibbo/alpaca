#!/bin/bash
# tests/test_all_fixes.sh - Re-prueba compacta con todos los fixes aplicados

set -euo pipefail

echo "🔧 Starting comprehensive test with all fixes applied..."

# 0) Limpiar puertos de métricas y procesos previos
echo "🧹 Cleaning up existing processes and ports..."
for p in 8011 8012 8013 8014; do
    (lsof -ti:$p 2>/dev/null | xargs -r kill -9) || true
done

pkill -f "apps/strategies/main.py" 2>/dev/null || true
pkill -f "apps/risk_manager/main.py" 2>/dev/null || true
pkill -f "apps/executor/main.py" 2>/dev/null || true
pkill -f "apps/simulator/main.py" 2>/dev/null || true

# 1) Configurar entorno
echo "🔧 Setting up environment..."
export REDIS_URL="redis://localhost:6379/0"
export USE_FAKE_REDIS=0
export BUS_BACKEND=streams

# 2) Verificar que Redis está corriendo
echo "🔴 Checking Redis connection..."
python - <<'PY'
import os
os.environ['BUS_BACKEND']="streams"
from lib.bus import connect_bus, get_bus
ok = connect_bus()
print("bus_connected:", ok)
if ok:
    stats = get_bus().get_stats()
    print("bus_backend:", stats.get('backend'))
    print("supports_streams:", stats.get('supports_streams'))
else:
    print("❌ Redis connection failed!")
    exit(1)
PY

# 3) Test básico de Streams
echo "🧪 Running basic Streams test..."
python tests/test_streams_integration.py

# 4) Iniciar servicios (background)
echo "🚀 Starting services..."
( python apps/strategies/main.py    > logs.strategies 2>&1 & echo $! > .pid_strat )
echo "   - Strategies started"

( python apps/risk_manager/main.py  > logs.risk       2>&1 & echo $! > .pid_risk  )
echo "   - Risk Manager started"

( python apps/executor/main.py      > logs.exec       2>&1 & echo $! > .pid_exec  )
echo "   - Executor started"

sleep 3

( python apps/simulator/main.py --symbols GOOGL --start 2022-01-01 --timeframe 1Day --feed iex > logs.sim 2>&1 & echo $! > .pid_sim )
echo "   - Simulator started"

sleep 5

# 5) Verificar métricas (deben responder las 4)
echo "📊 Checking metrics endpoints..."
all_ok=true
for u in 8011 8012 8013 8014; do
    echo "   Testing port :$u..."
    if curl -sS --max-time 5 "http://localhost:$u/metrics" >/dev/null 2>&1; then
        echo "   ✅ Port $u responding"
    else
        echo "   ❌ Port $u not responding"
        all_ok=false
    fi
done

if [ "$all_ok" = true ]; then
    echo "✅ All metrics endpoints are responding!"
else
    echo "⚠️  Some metrics endpoints are not responding"
fi

# 6) Verificar Streams (pendings ~0)
echo "🔄 Checking Redis Streams status..."
echo "--- XPENDING ---"
redis-cli XPENDING bars bars_processors || true
redis-cli XPENDING signals signal_processors || true
redis-cli XPENDING orders.intent order_processors || true
redis-cli XPENDING orders.fill fill_processors || true

# 7) Mostrar logs rápidos
echo "📋 Service logs summary:"
for f in logs.strategies logs.risk logs.exec logs.sim; do
    if [ -f "$f" ]; then
        echo "--- $f (last 3 lines) ---"
        tail -n 3 "$f" 2>/dev/null || echo "   (empty or error reading log)"
    fi
done

# 8) Test de métricas con actividad
echo "📈 Testing metrics with data..."
sleep 3  # Let some activity happen

echo "Sample metrics from executor (port 8012):"
curl -sS "http://localhost:8012/metrics" 2>/dev/null | grep -E "trading_|order|signal" | head -n 10 || echo "   (no metrics found)"

# 9) Cleanup
echo "🧹 Cleaning up..."
kill $(cat .pid_sim .pid_strat .pid_risk .pid_exec 2>/dev/null || true) 2>/dev/null || true
rm -f .pid_* 2>/dev/null || true

echo "🎉 Test completed! Check the output above for any issues."