#!/usr/bin/env python3
"""
Trading Platform Launcher
Orchestrates multiple microservices according to ChatGPT architecture
"""

import os
import sys
import asyncio
import subprocess
import signal
import time
import logging
from pathlib import Path
from typing import Dict, List
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ServiceManager:
    """Manages multiple trading microservices"""
    
    def __init__(self, config_path: str = "configs/base.yaml"):
        self.config_path = config_path
        self.config = self.load_config()
        self.processes: Dict[str, subprocess.Popen] = {}
        self.running = False
        
        # Service definitions
        self.services = {
            "data_ingestor": {
                "script": "apps/data_ingestor/main.py",
                "description": "Market data ingestion from Alpaca",
                "required": True,
                "startup_delay": 0
            },
            "strategies": {
                "script": "apps/strategies/main.py", 
                "description": "Trading strategy engine",
                "required": True,
                "startup_delay": 5
            },
            "risk_manager": {
                "script": "apps/risk_manager/main.py",
                "description": "Risk management and validation",
                "required": True,
                "startup_delay": 10
            },
            "executor": {
                "script": "apps/executor/main.py",
                "description": "Order execution with Alpaca",
                "required": True,
                "startup_delay": 15
            },
            "api": {
                "script": "apps/api/main.py",
                "description": "REST API for monitoring",
                "required": False,
                "startup_delay": 20
            }
        }
        
    def load_config(self) -> dict:
        """Load system configuration"""
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return {}
    
    def check_dependencies(self) -> bool:
        """Check if Redis and other dependencies are available"""
        logger.info("Checking dependencies...")
        
        # Check Redis
        try:
            import redis
            r = redis.Redis(host='localhost', port=6379, socket_timeout=5)
            r.ping()
            logger.info("✅ Redis connection OK")
        except Exception as e:
            logger.error(f"❌ Redis not available: {e}")
            logger.error("Please start Redis with: docker-compose up redis")
            return False
        
        # Check Python packages
        required_packages = ['pandas', 'alpaca', 'fastapi', 'pydantic']
        for package in required_packages:
            try:
                __import__(package.replace('-', '_'))
                logger.info(f"✅ {package} package OK")
            except ImportError:
                logger.error(f"❌ Missing package: {package}")
                logger.error("Please install with: pip install -r requirements.txt")
                return False
        
        # Check .env file
        if not os.path.exists('.env'):
            logger.error("❌ Missing .env file with Alpaca credentials")
            logger.error("Please create .env with your Alpaca API keys")
            return False
        else:
            logger.info("✅ .env file found")
        
        return True
    
    def start_service(self, service_name: str) -> bool:
        """Start a single service"""
        if service_name not in self.services:
            logger.error(f"Unknown service: {service_name}")
            return False
        
        service = self.services[service_name]
        script_path = service["script"]
        
        if not os.path.exists(script_path):
            logger.error(f"Service script not found: {script_path}")
            return False
        
        try:
            # Start service process
            cmd = [sys.executable, script_path]
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path.cwd())
            
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            self.processes[service_name] = process
            logger.info(f"✅ Started {service_name} (PID: {process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start {service_name}: {e}")
            return False
    
    def stop_service(self, service_name: str):
        """Stop a single service"""
        if service_name in self.processes:
            process = self.processes[service_name]
            try:
                process.terminate()
                process.wait(timeout=10)
                logger.info(f"✅ Stopped {service_name}")
            except subprocess.TimeoutExpired:
                process.kill()
                logger.warning(f"⚠️ Force killed {service_name}")
            except Exception as e:
                logger.error(f"❌ Error stopping {service_name}: {e}")
            
            del self.processes[service_name]
    
    def check_service_health(self, service_name: str) -> bool:
        """Check if service is running"""
        if service_name not in self.processes:
            return False
        
        process = self.processes[service_name]
        return process.poll() is None
    
    def monitor_services(self):
        """Monitor service health and restart if needed"""
        while self.running:
            for service_name in list(self.processes.keys()):
                if not self.check_service_health(service_name):
                    logger.warning(f"⚠️ Service {service_name} died, restarting...")
                    self.stop_service(service_name)
                    time.sleep(2)
                    self.start_service(service_name)
            
            time.sleep(10)  # Check every 10 seconds
    
    def start_all(self, services: List[str] = None):
        """Start all or specified services"""
        if not self.check_dependencies():
            logger.error("❌ Dependency check failed")
            return False
        
        logger.info("🚀 Starting Trading Platform...")
        
        # Determine which services to start
        if services is None:
            services_to_start = list(self.services.keys())
        else:
            services_to_start = services
        
        # Start services with delays
        for service_name in services_to_start:
            service = self.services[service_name]
            
            if service.get("startup_delay", 0) > 0:
                logger.info(f"⏳ Waiting {service['startup_delay']}s before starting {service_name}")
                time.sleep(service["startup_delay"])
            
            success = self.start_service(service_name)
            if not success and service.get("required", False):
                logger.error(f"❌ Failed to start required service: {service_name}")
                self.stop_all()
                return False
        
        self.running = True
        
        # Show status
        self.show_status()
        
        # Start monitoring
        logger.info("🔍 Starting service monitoring...")
        try:
            self.monitor_services()
        except KeyboardInterrupt:
            logger.info("🛑 Received shutdown signal")
        finally:
            self.stop_all()
    
    def stop_all(self):
        """Stop all services"""
        logger.info("🛑 Stopping all services...")
        self.running = False
        
        # Stop in reverse order
        service_names = list(self.processes.keys())
        service_names.reverse()
        
        for service_name in service_names:
            self.stop_service(service_name)
        
        logger.info("✅ All services stopped")
    
    def show_status(self):
        """Show status of all services"""
        logger.info("📊 Service Status:")
        logger.info("-" * 50)
        
        for service_name, service in self.services.items():
            is_running = self.check_service_health(service_name)
            status = "🟢 RUNNING" if is_running else "🔴 STOPPED"
            pid = self.processes.get(service_name, {}).pid if is_running else "N/A"
            
            logger.info(f"{service_name:15} {status:10} PID:{pid}")
            logger.info(f"{'':15} {service['description']}")
        
        logger.info("-" * 50)
        
        if self.check_service_health("api"):
            logger.info("🌐 API available at: http://localhost:8000")
            logger.info("📊 API docs at: http://localhost:8000/docs")
        
        logger.info("🔍 Redis GUI at: http://localhost:8081 (if running)")

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Trading Platform Launcher")
    parser.add_argument(
        "--services", 
        nargs="+", 
        help="Specific services to start",
        choices=["data_ingestor", "strategies", "risk_manager", "executor", "api"]
    )
    parser.add_argument(
        "--config", 
        default="configs/base.yaml",
        help="Configuration file path"
    )
    parser.add_argument(
        "--check-deps", 
        action="store_true",
        help="Only check dependencies"
    )
    
    args = parser.parse_args()
    
    # Initialize service manager
    manager = ServiceManager(args.config)
    
    # Handle signals for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        manager.stop_all()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check dependencies only
    if args.check_deps:
        if manager.check_dependencies():
            logger.info("✅ All dependencies OK")
            sys.exit(0)
        else:
            logger.error("❌ Dependency check failed")
            sys.exit(1)
    
    # Start services
    try:
        manager.start_all(args.services)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        manager.stop_all()
        sys.exit(1)

if __name__ == "__main__":
    main()