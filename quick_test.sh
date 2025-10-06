#!/bin/bash
# quick_test.sh - Quick verification that everything works

echo "=============================================="
echo "QUICK VERIFICATION - EPIC 6 & 7"
echo "=============================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

test_cmd() {
    local name="$1"
    local cmd="$2"

    echo -n "Testing: $name ... "
    if eval "$cmd" > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAIL${NC}"
        FAILED=$((FAILED + 1))
    fi
}

# Core imports
test_cmd "Bus" "python -c 'from lib.bus import get_bus'"
test_cmd "Models" "python -c 'from lib.models import Signal, Bar, OrderIntent'"
test_cmd "Settings" "python -c 'from lib.settings import get_settings'"
test_cmd "Risk Manager" "python -c 'from apps.risk_manager.main import EnhancedRiskManager'"
test_cmd "Simulator" "python -c 'from apps.simulator.main import HistoricalSimulator'"

# Epic 6
test_cmd "Market Hours" "python -c 'from apps.risk_manager.market_hours import MarketHoursValidator'"

# Epic 7
test_cmd "Persistence" "python -c 'from apps.simulator.persist import BacktestPersistence'"

# Integration
test_cmd "RM+Market Hours" "python -c 'from apps.risk_manager.main import EnhancedRiskManager; rm = EnhancedRiskManager(); assert hasattr(rm, \"market_validator\")'"

test_cmd "Simulator+Persistence" "python -c 'from apps.simulator.main import HistoricalSimulator; s = HistoricalSimulator(enable_persistence=True); assert s.persistence is not None'"

echo ""
echo "=============================================="
echo "RESULTS"
echo "=============================================="
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ ALL TESTS PASSED${NC}"
    echo "System is working correctly!"
    exit 0
else
    echo -e "${RED}✗ SOME TESTS FAILED${NC}"
    echo "Please review the failures above"
    exit 1
fi
