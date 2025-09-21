#!/bin/bash
# scripts/setup.sh
# Trading Platform Setup Script
# Sets up the complete modular trading system according to ChatGPT architecture

set -e

echo "🚀 Setting up Algorithmic Trading Platform (Modular Architecture)"
echo "================================================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Check if running in correct directory
if [ ! -f "requirements.txt" ]; then
    log_error "Please run this script from the project root directory"
    exit 1
fi

log_info "Checking system requirements..."

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.8"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    log_error "Python 3.8+ required. Found: $PYTHON_VERSION"
    exit 1
fi
log_success "Python version OK: $PYTHON_VERSION"

# Check if conda or venv is available
if command -v conda &> /dev/null; then
    log_info "Found conda, will use conda environment"
    USE_CONDA=true
elif command -v python3 -m venv &> /dev/null; then
    log_info "Found venv, will use virtual environment"
    USE_CONDA=false
else
    log_error "Neither conda nor venv available"
    exit 1
fi

# Create virtual environment
log_info "Creating virtual environment..."

if [ "$USE_CONDA" = true ]; then
    ENV_NAME="trading_platform"
    conda create -n $ENV_NAME python=3.11 -y
    log_success "Conda environment '$ENV_NAME' created"
    log_info "Activate with: conda activate $ENV_NAME"
else
    ENV_DIR="venv"
    python3 -m venv $ENV_DIR
    log_success "Virtual environment created in '$ENV_DIR'"
    log_info "Activate with: source $ENV_DIR/bin/activate"
fi

# Install dependencies
log_info "Installing Python dependencies..."

if [ "$USE_CONDA" = true ]; then
    source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null || source ~/anaconda3/etc/profile.d/conda.sh 2>/dev/null || true
    conda activate $ENV_NAME
    pip install -r requirements.txt
else
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
fi

log_success "Dependencies installed"

# Check for Docker
log_info "Checking Docker availability..."
if command -v docker &> /dev/null && command -v docker-compose &> /dev/null; then
    log_success "Docker and docker-compose found"
    
    # Start Redis
    log_info "Starting Redis with Docker..."
    docker-compose up -d redis
    
    # Wait for Redis to be ready
    log_info "Waiting for Redis to be ready..."
    for i in {1..30}; do
        if docker-compose exec redis redis-cli ping &> /dev/null; then
            log_success "Redis is ready"
            break
        fi
        if [ $i -eq 30 ]; then
            log_error "Redis failed to start"
            exit 1
        fi
        sleep 1
    done
else
    log_warning "Docker not found. Please install Redis manually:"
    log_info "  brew install redis (macOS)"
    log_info "  sudo apt install redis-server (Ubuntu)"
    log_info "  Or use Docker: docker run -d -p 6379:6379 redis:alpine"
fi

# Create directories
log_info "Creating necessary directories..."
mkdir -p logs data backups
log_success "Directories created"

# Check .env file
if [ ! -f ".env" ]; then
    log_info "Creating .env template..."
    cat > .env << EOF
# Alpaca API Configuration (Paper Trading)
APCA_API_BASE_URL=https://paper-api.alpaca.markets
APCA_API_KEY_ID=your_api_key_here
APCA_API_SECRET_KEY=your_secret_key_here

# Trading Configuration
SYMBOLS=AAPL,MSFT,GOOGL,TSLA,NVDA
HISTORICAL_DAYS=7
RISK_PCT=0.02

# System Configuration  
API_HOST=0.0.0.0
API_PORT=8000

# Redis
REDIS_URL=redis://localhost:6379
EOF
    log_warning ".env file created with template"
    log_warning "Please edit .env with your Alpaca API credentials"
else
    log_success ".env file already exists"
fi

# Make scripts executable
log_info "Making scripts executable..."
chmod +x scripts/*.sh scripts/*.py
log_success "Scripts are executable"

# Test the system
log_info "Testing system components..."

# Test Redis connection
if [ "$USE_CONDA" = true ]; then
    conda activate $ENV_NAME
else
    source venv/bin/activate
fi

python3 -c "
import redis
try:
    r = redis.Redis(host='localhost', port=6379, socket_timeout=5)
    r.ping()
    print('✅ Redis connection OK')
except Exception as e:
    print(f'❌ Redis connection failed: {e}')
    exit(1)
"

# Test imports
python3 -c "
try:
    import pandas, numpy, alpaca, fastapi, pydantic, redis
    print('✅ All Python packages imported successfully')
except ImportError as e:
    print(f'❌ Import error: {e}')
    exit(1)
"

log_success "System test passed"

# Final instructions
echo ""
echo "🎉 Setup completed successfully!"
echo "================================"
echo ""

if [ "$USE_CONDA" = true ]; then
    echo "To activate the environment:"
    echo "  conda activate $ENV_NAME"
else
    echo "To activate the environment:"
    echo "  source venv/bin/activate"
fi

echo ""
echo "Next steps:"
echo "1. Edit .env with your Alpaca API credentials"
echo "2. Start the trading platform:"
echo "   python scripts/launcher.py"
echo ""
echo "3. Or start individual services:"
echo "   python scripts/launcher.py --services data_ingestor strategies"
echo ""
echo "4. Monitor the system:"
echo "   - API: http://localhost:8000"
echo "   - Redis GUI: http://localhost:8081"
echo ""
echo "5. Check dependencies anytime:"
echo "   python scripts/launcher.py --check-deps"

log_success "Ready to trade! 📈"