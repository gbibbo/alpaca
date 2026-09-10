# Quick Start Guide - Algorithmic Trading Platform

This guide will help you get started with the trading platform in under 10 minutes.

## Prerequisites Check

Before starting, ensure you have:
- Python 3.9+ installed
- Redis 6.0+ installed (or use Docker)
- Git installed
- 8GB RAM minimum

```bash
# Check Python version
python --version

# Check Redis version
redis-cli --version

# Check if Redis is running
redis-cli ping  # Should return PONG
```

## 1. Installation (5 minutes)

### Step 1: Clone and Setup Virtual Environment
```bash
# Clone repository (replace with your repo URL)
git clone <repository-url>
cd alpaca

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or
venv\Scripts\activate     # Windows
```

### Step 2: Install Dependencies
```bash
# Install all dependencies
make install
# or manually
pip install -r requirements.txt
```

### Step 3: Start Redis (if not running)
```bash
# Option A: Using Make
make redis-start

# Option B: Using Docker manually
docker run -d --name trading-redis -p 6379:6379 redis:7-alpine

# Option C: Local Redis
redis-server
```

## 2. Quick Validation (2 minutes)

Run a quick health check to ensure everything is working:

```bash
# Run quick validation tests (9 tests, ~10 seconds)
make test-quick
```

Expected output:
```
✅ 1/9 Bus
✅ 2/9 Settings
✅ 3/9 Models
✅ 4/9 Market Hours
✅ 5/9 Persistence
✅ 6/9 Deduplication
✅ 7/9 Risk Manager
✅ 8/9 Simulator
✅ 9/9 Backend Streams
```

## 3. First Backtest (3 minutes)

Backtests run in the **isolated research engine** (`lib/backtest.py`): no message bus, no
broker, no orders sent. `TRADING_MODE=backtest` is the default. See `docs/STRATEGIES.md` for
the execution model and how to add your own strategy.

### Command line (CSV input)

```bash
# data/sample/TEST.csv is a synthetic fixture shipped for validation
python apps/simulator/main.py --symbols TEST --start 2024-01-01 --end 2026-01-01 \
  --csv data/sample --timeframe 1Day --strategies daily_trend,buy_and_hold \
  --initial-cash 100000 --output out/run.json
```

`--strategies` accepts any registered strategy (see `apps/strategies/library.py`) plus the
`cash` and `buy_and_hold` benchmarks. Use `--risk-params '{"stop_loss_pct":0.03}'` to override
sizing/risk. One base timeframe per run; supply bars of that timeframe.

### Research API (auth-gated)

```bash
export AUTH_ADMIN_PASSWORD='choose-a-password'
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
# then: POST /api/auth/token -> POST /backtest/jobs -> /start -> poll -> /results -> /download
```

## 4. Understanding the Results

The result JSON has one entry per account under `accounts` (your strategies plus `cash` and
`buy_and_hold`). Each carries `metrics`, `equity_curve`, `fills`, `signals`, `rejections`,
`first_fill_timestamp`, and `final_portfolio`. Top level includes `data_sha256` /
`result_sha256` for reproducibility and an `assumptions` list stating the engine's limits.

```json
{
  "return_pct": 10.75,
  "max_drawdown_pct": 6.23,
  "max_drawdown_duration_days": 84.0,
  "sharpe": 1.2,
  "avg_exposure_pct": 99.6,
  "trades": {"total": 42, "win_rate": 65.0, "profit_factor": 1.8, "avg_win": 120.0, "avg_loss": -70.0}
}
```

Compare accounts over the same window: benchmarks act from the first bar while a strategy waits
for its lookback, so check each account's `first_fill_timestamp`. A single in-sample run is not
predictive evidence.

## 5. Testing Comprehensive Features

### Test Epic 6 (Market Hours)
```bash
make test-epic6
```
This validates:
- NYSE/NASDAQ calendar with 10+ holidays
- Early close detection (Black Friday, Christmas Eve)
- Market hours validation (9:30 AM - 4:00 PM ET)
- Timezone-aware datetime handling

### Test Epic 7 (Persistence)
```bash
make test-epic7
```
This validates:
- SQLite persistence
- CSV/Parquet export
- SHA256 reproducibility verification
- Complete data capture

### Run Full Regression Tests
```bash
make test-regression
```
This runs:
- 27 regression tests from Epic 6 & 7
- 13 system health tests
- Complete validation suite

## 6. Running the Full System

### Start Infrastructure
```bash
# Start Redis
make redis-start

# Check Redis is running
redis-cli ping  # Should return PONG
```

### Set Environment Variables
```bash
# Export required environment variables
export BUS_BACKEND=streams
export REDIS_URL=redis://127.0.0.1:6379/15
```

### Run Individual Services

**Terminal 1 - Risk Manager:**
```bash
export BUS_BACKEND=streams
export REDIS_URL=redis://127.0.0.1:6379/15
make run-risk
```

**Terminal 2 - Simulator:**
```bash
export BUS_BACKEND=streams
export REDIS_URL=redis://127.0.0.1:6379/15
make run-simulator
```

## 7. Monitoring and Verification

### Check Service Health
```bash
# View Redis stream statistics
redis-cli XINFO GROUPS signals

# Check message bus health
python -c "from lib.bus import get_bus; bus = get_bus(); print(bus.health_check())"
```

### View Logs
```bash
# Monitor Risk Manager logs
tail -f logs/risk_manager.log

# Monitor Simulator logs
tail -f logs/simulator.log
```

## Common Commands Cheat Sheet

### Installation & Setup
```bash
make install              # Install dependencies
make redis-start          # Start Redis container
make redis-stop           # Stop Redis container
make clean               # Clean temporary files
```

### Testing
```bash
make test                # Run all tests
make test-quick          # Quick validation (9 tests)
make test-regression     # Regression tests
make test-epic6          # Market hours tests
make test-epic7          # Persistence tests
```

### Backtesting
```bash
make backtest-googl      # GOOGL backtest (default)
make backtest-persist    # GOOGL with persistence
make backtest-custom SYMBOL=AAPL  # Custom symbol
```

### Services
```bash
make run-risk            # Start risk manager
make run-executor        # Start order executor
make run-simulator       # Start market simulator
make run-all             # Start all services
make stop-all            # Stop all services
```

### Monitoring
```bash
make metrics             # Show metrics URLs
make health              # Check service health
make redis-logs          # View Redis logs
```

## Troubleshooting

### Redis Connection Issues
```bash
# Check Redis is running
redis-cli ping

# If not running, start it
make redis-start

# Check Redis version
redis-cli INFO server | grep redis_version
```

### Import Errors
```bash
# Reinstall dependencies
pip install --force-reinstall -r requirements.txt

# Check Python version
python --version  # Should be 3.9+
```

### Port Already in Use
```bash
# Stop all services
make stop-all

# Or manually kill processes
pkill -f "apps/"
```

### Clean Start
```bash
# Stop everything
make stop-all
make redis-stop

# Clean temporary files
make clean

# Restart Redis
make redis-start

# Run quick test
make test-quick
```

## Next Steps

1. **Explore Epic 6 & 7 Features**: Read `EPIC6_7_README.md` for detailed documentation
2. **Review Test Results**: Check `TEST_SUMMARY.md` for comprehensive testing guide
3. **Configure Trading**: Edit `.env` file with your Alpaca credentials
4. **Create Custom Strategies**: Modify `apps/strategies/` to implement your own logic
5. **Monitor Performance**: Set up Prometheus and Grafana dashboards

## Getting Help

- **Quick Tests**: `make test-quick`
- **Full Documentation**: See `README.md`
- **Epic 6 & 7 Details**: See `EPIC6_7_README.md`
- **Testing Guide**: See `README_TESTING.md`
- **Logs**: Check `logs/` directory
- **Redis Status**: `redis-cli XINFO GROUPS signals`

## Summary

You've completed the quick start! You should now have:
- ✅ Platform installed and dependencies ready
- ✅ Redis running
- ✅ Quick tests passing
- ✅ First backtest completed
- ✅ Understanding of core commands

**Recommended Next Action**: Run `make test-regression` to ensure all features work correctly.
