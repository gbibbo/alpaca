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

Run your first backtest to see the system in action:

### Option A: Simple Backtest
```bash
# Run GOOGL backtest with default settings
make backtest-googl
```

Results will be saved to:
- `out/GOOGL.png` - Performance chart
- `out/GOOGL.json` - Detailed metrics
- `out/GOOGL.txt` - Console output

### Option B: Backtest with Persistence
```bash
# Run backtest with full data persistence
make backtest-persist
```

Results will be saved to:
- `out/run_<timestamp>_<uuid>/backtest.db` - SQLite database
- `out/run_<timestamp>_<uuid>/summary.json` - Summary metrics
- `out/run_<timestamp>_<uuid>/data/*.csv` - CSV exports

### Option C: Custom Symbol
```bash
# Backtest a different symbol
make backtest-custom SYMBOL=AAPL START_DATE=2023-01-01
```

## 4. Understanding the Results

After running a backtest, check the output files:

### Performance Chart (`out/GOOGL.png`)
The chart shows:
- Portfolio equity curve over time
- Entry and exit points
- Drawdown periods
- Final performance

### Metrics (`out/GOOGL.json`)
Key metrics include:
```json
{
  "total_return": 15.5,
  "sharpe_ratio": 1.2,
  "max_drawdown": -5.3,
  "win_rate": 0.65,
  "total_trades": 42
}
```

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
