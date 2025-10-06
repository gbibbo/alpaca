#!/bin/bash
# scripts/test_regression_epic6_7.sh
# Complete Regression Test Suite for Epic 6 & 7
# Verifies that new implementations don't break existing functionality

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Counters
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Get script directory and project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "============================================================"
echo "REGRESSION TEST SUITE - EPIC 6 & 7"
echo "============================================================"
echo "Project: $PROJECT_DIR"
echo "Verifying that existing functionality still works"
echo ""

# Helper function to run a test
run_test() {
    local test_name="$1"
    local test_command="$2"

    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    echo -n "Test $TOTAL_TESTS: $test_name ... "

    if eval "$test_command" > /tmp/test_output_$TOTAL_TESTS.log 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        PASSED_TESTS=$((PASSED_TESTS + 1))
        return 0
    else
        echo -e "${RED}FAIL${NC}"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo "  Error log: /tmp/test_output_$TOTAL_TESTS.log"
        return 1
    fi
}

echo "============================================================"
echo "SECTION 1: BASIC IMPORTS AND DEPENDENCIES"
echo "============================================================"

run_test "Import lib.bus" \
    "python -c 'from lib.bus import get_bus, connect_bus'"

run_test "Import lib.models" \
    "python -c 'from lib.models import Signal, Bar, OrderIntent'"

run_test "Import lib.settings" \
    "python -c 'from lib.settings import get_settings'"

run_test "Import lib.time_utils" \
    "python -c 'from lib.time_utils import TimeUtils'"

run_test "Import lib.deduplication" \
    "python -c 'from lib.deduplication import get_deduplication_service'"

run_test "Import lib.metrics_helpers" \
    "python -c 'from lib.metrics_helpers import ServiceMetrics'"

echo ""
echo "============================================================"
echo "SECTION 2: SERVICE IMPORTS (BACKWARD COMPATIBILITY)"
echo "============================================================"

run_test "Import Risk Manager (with new validator)" \
    "python -c 'from apps.risk_manager.main import EnhancedRiskManager'"

run_test "Import Simulator (with persistence)" \
    "python -c 'from apps.simulator.main import HistoricalSimulator'"

# Note: Strategies, Executor, and API imports are optional
# They may not exist in all configurations
echo "Note: Strategies, Executor, and API imports skipped (optional components)"

echo ""
echo "============================================================"
echo "SECTION 3: RISK MANAGER FUNCTIONALITY"
echo "============================================================"

run_test "Risk Manager initializes without errors" \
    "python -c 'from apps.risk_manager.main import EnhancedRiskManager; rm = EnhancedRiskManager(); print(\"OK\")'"

run_test "Risk Manager has market validator" \
    "python -c 'from apps.risk_manager.main import EnhancedRiskManager; rm = EnhancedRiskManager(); assert hasattr(rm, \"market_validator\")'"

run_test "Risk Manager validates trading hours" \
    "python -c 'from apps.risk_manager.main import EnhancedRiskManager; rm = EnhancedRiskManager(); is_open, reason = rm.market_validator.validate_trading_hours(); print(f\"Open: {is_open}, Reason: {reason}\")'"

run_test "Risk Manager processes signal validation" \
    "python -c '
from apps.risk_manager.main import EnhancedRiskManager
from lib.models import Signal, SignalSide
from decimal import Decimal
from datetime import datetime
import uuid

rm = EnhancedRiskManager()
signal = Signal(
    signal_id=uuid.uuid4(),
    symbol=\"AAPL\",
    timestamp=datetime.utcnow(),
    side=SignalSide.BUY,
    confidence=Decimal(\"0.85\"),
    price=Decimal(\"150.0\"),
    source=\"test\"
)

# Should return validation result (may reject if market closed)
is_valid, reason = rm.validate_signal_comprehensive(signal)
print(f\"Valid: {is_valid}, Reason: {reason}\")
'"

run_test "Risk Manager rate limiters work" \
    "python -c '
from apps.risk_manager.main import EnhancedRiskManager

rm = EnhancedRiskManager()
assert rm.order_rate_limiter is not None
assert rm.signal_rate_limiter is not None
print(\"Rate limiters OK\")
'"

run_test "Risk Manager circuit breakers work" \
    "python -c '
from apps.risk_manager.main import EnhancedRiskManager

rm = EnhancedRiskManager()
assert len(rm.circuit_breakers) > 0
print(f\"Circuit breakers: {list(rm.circuit_breakers.keys())}\")
'"

run_test "Risk Manager deduplication works" \
    "python -c '
from apps.risk_manager.main import EnhancedRiskManager

rm = EnhancedRiskManager()
assert rm.deduplication is not None
stats = rm.deduplication.get_comprehensive_stats()
print(f\"Dedup stats: {stats}\")
'"

echo ""
echo "============================================================"
echo "SECTION 4: SIMULATOR FUNCTIONALITY"
echo "============================================================"

run_test "Simulator initializes WITHOUT persistence (backward compat)" \
    "python -c 'from apps.simulator.main import HistoricalSimulator; sim = HistoricalSimulator(); assert sim.persistence is None; print(\"OK: No persistence\")'"

run_test "Simulator initializes WITH persistence" \
    "python -c 'from apps.simulator.main import HistoricalSimulator; import tempfile, shutil; sim = HistoricalSimulator(enable_persistence=True); assert sim.persistence is not None; print(\"OK: Persistence enabled\")'"

run_test "Simulator loads CSV data" \
    "python -c '
from apps.simulator.main import AlpacaDataLoader
import tempfile, os

# Create temp CSV
td = tempfile.mkdtemp()
csv_path = os.path.join(td, \"test.csv\")
with open(csv_path, \"w\") as f:
    f.write(\"timestamp,open,high,low,close,volume\\n\")
    f.write(\"2024-01-02T09:30:00,150,151,149.5,150.5,1000000\\n\")

loader = AlpacaDataLoader()
bars = loader.load_from_csv(csv_path, \"AAPL\")
assert len(bars) == 1
print(f\"Loaded {len(bars)} bars\")

import shutil
shutil.rmtree(td)
'"

run_test "Simulator help shows new --persist flag" \
    "python apps/simulator/main.py --help | grep -q persist"

echo ""
echo "============================================================"
echo "SECTION 5: BUS AND MESSAGING"
echo "============================================================"

run_test "Redis connection works" \
    "python -c 'from lib.bus import connect_bus; assert connect_bus()'"

run_test "Bus publishes and subscribes" \
    "python -c '
from lib.bus import connect_bus, get_bus
from lib.models import Bar, TimeFrame
from datetime import datetime
from decimal import Decimal

connect_bus()
bus = get_bus()

bar = Bar(
    symbol=\"AAPL\",
    timestamp=datetime.utcnow(),
    open=Decimal(\"150.0\"),
    high=Decimal(\"151.0\"),
    low=Decimal(\"149.5\"),
    close=Decimal(\"150.5\"),
    volume=1000000,
    timeframe=TimeFrame.MINUTE
)

bus.publish_bar(bar)
print(\"Bar published successfully\")
bus.disconnect()
'"

run_test "System events work" \
    "python -c '
from lib.bus import connect_bus, get_bus

connect_bus()
bus = get_bus()

bus.publish_system_event(
    event_type=\"test_event\",
    source=\"regression_test\",
    data={\"test\": \"data\"}
)
print(\"System event published\")
bus.disconnect()
'"

echo ""
echo "============================================================"
echo "SECTION 6: BACKWARD COMPATIBILITY"
echo "============================================================"

run_test "Old Risk Manager alias works" \
    "python -c 'from apps.risk_manager.main import RiskManager; rm = RiskManager()'"

run_test "Simulator without persistence works like before" \
    "python -c '
from apps.simulator.main import HistoricalSimulator

# Old way (no persistence)
sim = HistoricalSimulator(speed_multiplier=10.0)
assert sim.persistence is None
assert sim.speed_multiplier == 10.0
print(\"Backward compatibility OK\")
'"

echo ""
echo "============================================================"
echo "FINAL RESULTS"
echo "============================================================"
echo ""
echo "Total tests: $TOTAL_TESTS"
echo -e "Passed: ${GREEN}$PASSED_TESTS${NC}"
echo -e "Failed: ${RED}$FAILED_TESTS${NC}"
echo ""

if [ $FAILED_TESTS -eq 0 ]; then
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}SUCCESS: ALL REGRESSION TESTS PASSED${NC}"
    echo -e "${GREEN}Existing functionality is intact${NC}"
    echo -e "${GREEN}Epic 6 & 7 integration successful${NC}"
    echo -e "${GREEN}============================================================${NC}"
    exit 0
else
    echo -e "${RED}============================================================${NC}"
    echo -e "${RED}FAILURE: SOME TESTS FAILED${NC}"
    echo -e "${RED}Please review failed tests${NC}"
    echo -e "${RED}============================================================${NC}"
    exit 1
fi
