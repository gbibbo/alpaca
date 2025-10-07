#!/bin/bash
# Run load and performance tests for Epic 9
# Usage: bash scripts/run_load_tests.sh

set -e

echo "🔥 Epic 9 - Load and Performance Tests"
echo "======================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Ensure Redis is running
echo "Checking Redis connection..."
if redis-cli ping > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Redis is running${NC}"
else
    echo -e "${RED}❌ Redis is not running. Please start Redis first.${NC}"
    echo "   Run: redis-server"
    echo "   Or:  docker run -d -p 6379:6379 redis:7-alpine"
    exit 1
fi

echo ""
echo "Setting environment variables..."
export REDIS_URL=${REDIS_URL:-redis://127.0.0.1:6379/15}
export BUS_BACKEND=${BUS_BACKEND:-streams}
export USE_FAKE_REDIS=0

echo "REDIS_URL: $REDIS_URL"
echo "BUS_BACKEND: $BUS_BACKEND"
echo ""

# Run edge case tests
echo -e "${YELLOW}Running Edge Case Tests...${NC}"
echo "-------------------------------------------"
pytest tests/test_edge_cases.py -v
EDGE_EXIT=$?

echo ""
echo ""

# Run load/performance tests (slow tests)
echo -e "${YELLOW}Running Load/Performance Tests...${NC}"
echo "-------------------------------------------"
pytest tests/test_load_performance.py -v -m slow
LOAD_EXIT=$?

echo ""
echo ""

# Summary
echo "======================================"
echo "Test Summary:"
echo "======================================"

if [ $EDGE_EXIT -eq 0 ]; then
    echo -e "${GREEN}✅ Edge Case Tests: PASSED${NC}"
else
    echo -e "${RED}❌ Edge Case Tests: FAILED${NC}"
fi

if [ $LOAD_EXIT -eq 0 ]; then
    echo -e "${GREEN}✅ Load/Performance Tests: PASSED${NC}"
else
    echo -e "${RED}❌ Load/Performance Tests: FAILED${NC}"
fi

echo ""

# Exit with error if any test failed
if [ $EDGE_EXIT -ne 0 ] || [ $LOAD_EXIT -ne 0 ]; then
    echo -e "${RED}Some tests failed. Please review the output above.${NC}"
    exit 1
else
    echo -e "${GREEN}All Epic 9 tests passed successfully! 🎉${NC}"
    exit 0
fi
