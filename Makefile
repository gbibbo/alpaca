# Makefile para Trading Platform
# Implementa targets de ChatGPT para facilitar operaciones comunes

.PHONY: help install test clean backtest-googl run-risk run-executor run-all stop-all

# Configuración por defecto
PYTHON = python
PIP = pip
SYMBOL = GOOGL
START_DATE = 2021-01-01
INITIAL_CASH = 100000
POSITION_NOTIONAL = 10000
SLIPPAGE_BPS = 3

help:
	@echo "📊 Trading Platform - Available Commands"
	@echo "============================================="
	@echo ""
	@echo "🔧 Setup & Development:"
	@echo "  install          Install dependencies"
	@echo "  test            Run test suite"
	@echo "  clean           Clean temporary files"
	@echo ""
	@echo "📈 Backtesting:"
	@echo "  backtest-googl   Run GOOGL backtest with default settings"
	@echo "  backtest-custom  Run custom backtest (requires SYMBOL, START_DATE)"
	@echo ""
	@echo "🚀 Services:"
	@echo "  run-risk         Start risk manager (port 8011)"
	@echo "  run-executor     Start order executor"
	@echo "  run-strategy     Start random strategy"
	@echo "  run-simulator    Start market data simulator"
	@echo "  run-all          Start all services in parallel"
	@echo "  stop-all         Stop all services"
	@echo ""
	@echo "🔍 Monitoring:"
	@echo "  metrics          Open all metrics dashboards"
	@echo "  health           Check service health"
	@echo ""
	@echo "🐳 Infrastructure:"
	@echo "  redis-start      Start Redis container"
	@echo "  redis-stop       Stop Redis container"
	@echo "  redis-logs       Show Redis logs"

# Setup & Development
install:
	@echo "📦 Installing dependencies..."
	$(PIP) install -r requirements.txt
	@echo "✅ Dependencies installed"

test:
	@echo "🧪 Running tests..."
	$(PYTHON) -m pytest tests/ -v
	@echo "✅ Tests completed"

clean:
	@echo "🧹 Cleaning temporary files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf out/ 2>/dev/null || true
	@echo "✅ Cleanup completed"

# Backtesting targets
backtest-googl:
	@echo "📊 Running GOOGL backtest..."
	@mkdir -p out
	$(PYTHON) scripts/sim_random.py \
		--symbol GOOGL \
		--start 2021-01-01 \
		--initial-cash $(INITIAL_CASH) \
		--position-notional $(POSITION_NOTIONAL) \
		--slippage-bps $(SLIPPAGE_BPS) \
		--plot out/GOOGL.png \
		--output out/GOOGL.json \
		> out/GOOGL.txt
	@echo "✅ GOOGL backtest completed"
	@echo "📁 Results saved to out/GOOGL.*"

backtest-custom:
	@echo "📊 Running custom backtest for $(SYMBOL)..."
	@mkdir -p out
	$(PYTHON) scripts/sim_random.py \
		--symbol $(SYMBOL) \
		--start $(START_DATE) \
		--initial-cash $(INITIAL_CASH) \
		--position-notional $(POSITION_NOTIONAL) \
		--slippage-bps $(SLIPPAGE_BPS) \
		--plot out/$(SYMBOL).png \
		--output out/$(SYMBOL).json

# Service management
run-risk:
	@echo "🛡️  Starting Risk Manager..."
	@export BUS_BACKEND=streams && export RISK_METRICS_PORT=8011 && $(PYTHON) apps/risk_manager/main.py

run-executor:
	@echo "⚡ Starting Order Executor..."
	@export BUS_BACKEND=streams && $(PYTHON) apps/executor/main.py

run-strategy:
	@echo "🎯 Starting Random Strategy..."
	@export BUS_BACKEND=streams && $(PYTHON) apps/strategies/main.py

run-simulator:
	@echo "📡 Starting Market Simulator..."
	@export BUS_BACKEND=streams && $(PYTHON) apps/simulator/main.py

run-all:
	@echo "🚀 Starting all services with Redis Streams..."
	@echo "ℹ️  Use Ctrl+C to stop all services"
	@export BUS_BACKEND=streams && \
	($(PYTHON) apps/risk_manager/main.py &) && \
	($(PYTHON) apps/executor/main.py &) && \
	($(PYTHON) apps/strategies/main.py &) && \
	$(PYTHON) apps/simulator/main.py

stop-all:
	@echo "🛑 Stopping all services..."
	@pkill -f "apps/" 2>/dev/null || true
	@echo "✅ All services stopped"

# Monitoring
metrics:
	@echo "📊 Opening metrics dashboards..."
	@echo "Risk Manager: http://localhost:8011/metrics"
	@echo "Executor: http://localhost:8012/metrics"
	@echo "Strategy: http://localhost:8013/metrics"

health:
	@echo "🔍 Checking service health..."
	@curl -s http://localhost:8011/health 2>/dev/null | jq . || echo "❌ Risk Manager not responding"
	@curl -s http://localhost:8012/health 2>/dev/null | jq . || echo "❌ Executor not responding"

# Infrastructure
redis-start:
	@echo "🗄️  Starting Redis container..."
	@docker run -d --name trading-redis -p 6379:6379 redis:7-alpine
	@echo "✅ Redis started on port 6379"

redis-stop:
	@echo "🛑 Stopping Redis container..."
	@docker stop trading-redis 2>/dev/null || true
	@docker rm trading-redis 2>/dev/null || true
	@echo "✅ Redis stopped"

redis-logs:
	@echo "📋 Redis logs:"
	@docker logs trading-redis

# Advanced backtesting examples
backtest-high-freq:
	@echo "📊 Running high-frequency GOOGL backtest..."
	@mkdir -p out
	$(PYTHON) scripts/sim_random.py \
		--symbol GOOGL \
		--start 2023-01-01 \
		--timeframe 1Min \
		--initial-cash 50000 \
		--position-notional 5000 \
		--slippage-bps 5 \
		--signal-prob 0.02 \
		--plot out/GOOGL_hf.png

backtest-portfolio:
	@echo "📊 Running portfolio backtest..."
	@mkdir -p out
	@for symbol in AAPL GOOGL MSFT TSLA; do \
		echo "Testing $$symbol..."; \
		$(PYTHON) scripts/sim_random.py \
			--symbol $$symbol \
			--start 2021-01-01 \
			--initial-cash 25000 \
			--position-notional 2500 \
			--slippage-bps 3 \
			--plot out/$$symbol.png \
			--no-plot > out/$$symbol.txt; \
	done
	@echo "✅ Portfolio backtest completed"

# Testing specific backends
test-pubsub:
	@echo "🧪 Testing Pub/Sub backend..."
	@export BUS_BACKEND=pubsub && $(PYTHON) -c "from lib.bus import get_bus; bus = get_bus(); print('Backend:', bus.get_stats()['backend']); print('Health:', bus.health_check())"

test-streams:
	@echo "🧪 Testing Streams backend..."
	@export BUS_BACKEND=streams && $(PYTHON) -c "from lib.bus import get_bus; bus = get_bus(); print('Backend:', bus.get_stats()['backend']); print('Health:', bus.health_check())"

# Development helpers
format:
	@echo "🎨 Formatting code..."
	@black . --exclude=old_reference
	@echo "✅ Code formatted"

lint:
	@echo "🔍 Linting code..."
	@flake8 . --exclude=old_reference --max-line-length=120
	@echo "✅ Linting completed"

# Documentation
docs:
	@echo "📖 Generating documentation..."
	@echo "Available soon..."

# Emergency commands
emergency-stop:
	@echo "🚨 EMERGENCY STOP - Killing all trading processes..."
	@pkill -f "apps/" 2>/dev/null || true
	@pkill -f "scripts/" 2>/dev/null || true
	@echo "🛑 Emergency stop completed"

reset-redis:
	@echo "🔄 Resetting Redis data..."
	@docker exec trading-redis redis-cli FLUSHALL 2>/dev/null || echo "Redis not running"
	@echo "✅ Redis data cleared"