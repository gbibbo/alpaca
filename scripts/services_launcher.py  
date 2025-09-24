#!/usr/bin/env python3
"""
scripts/services_launcher.py  
Trading Services Launcher - Manages the actual trading microservices
Works alongside unified_launcher.py to provide complete platform management
"""

import os
import sys
import asyncio
import subprocess
import signal
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent))

class TradingServicesManager:
    """Manages the trading microservices (data_ingestor, strategies, risk_manager, executor)"""
    
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.logs_dir = self.base_dir / "logs"
        self.pids_dir = self.base_dir / "pids"
        
        # Create directories
        for dir_path in [self.logs_dir, self.pids_dir]:
            dir_path.mkdir(exist_ok=True)
        
        self.processes: Dict[str, subprocess.Popen] = {}
        self.running = False
        
        # Trading services definitions
        self.services = {
            "data_ingestor": {
                "script": "apps/data_ingestor/main.py",
                "description": "Market data ingestion from Alpaca",
                "startup_delay": 0,
                "required": True
            },
            "strategies": {
                "script": "apps/strategies/main.py", 
                "description": "Trading strategy engine",
                "startup_delay": 5,
                "required": True
            },
            "risk_manager": {
                "script": "apps/risk_manager/main.py",
                "description": "Risk management and validation",
                "startup_delay": 10,
                "required": True
            },
            "executor": {
                "script": "apps/executor/main.py",
                "description": "Order execution with Alpaca",
                "startup_delay": 15,
                "required": True
            }
        }
    
    def check_dependencies(self) -> bool:
        """Check if infrastructure services are running"""
        print("🔍 Checking infrastructure dependencies...")
        
        # Check Redis
        try:
            result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True, timeout=2)
            if result.stdout.strip() == "PONG":
                print("✅ Redis: Running")
            else:
                print("❌ Redis: Not responding")
                return False
        except:
            print("❌ Redis: Not available")
            print("💡 Start infrastructure first: python scripts/unified_launcher.py start")
            return False
        
        # Check API
        try:
            import requests
            response = requests.get("http://localhost:8000/health", timeout=3)
            if response.status_code == 200:
                print("✅ API: Running")
            else:
                print("❌ API: Not responding")
                return False
        except:
            print("❌ API: Not available")
            print("💡 Start infrastructure first: python scripts/unified_launcher.py start")
            return False
        
        # Check Redis Streams
        try:
            from lib.bus import MessageBus
            bus = MessageBus()
            if bus.supports_streams:
                print("✅ Redis Streams: Active")
            else:
                print("⚠️ Redis Streams: Fallback to Pub/Sub")
        except:
            print("⚠️ Redis Streams: Unknown status")
        
        return True
    
    def start_service(self, service_name: str) -> bool:
        """Start a trading service"""
        if service_name not in self.services:
            print(f"❌ Unknown service: {service_name}")
            return False
        
        if service_name in self.processes:
            print(f"⚠️ {service_name} already running")
            return True
        
        service = self.services[service_name]
        script_path = self.base_dir / service["script"]
        
        if not script_path.exists():
            print(f"❌ Service script not found: {script_path}")
            return False
        
        try:
            print(f"🚀 Starting {service_name}...")
            
            # Start service process
            cmd = [sys.executable, str(script_path)]
            env = os.environ.copy()
            env['PYTHONPATH'] = str(self.base_dir)
            
            process = subprocess.Popen(
                cmd,
                env=env,
                cwd=str(self.base_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            self.processes[service_name] = process
            
            # Save PID
            pid_file = self.pids_dir / f"{service_name}.pid"
            with open(pid_file, 'w') as f:
                f.write(str(process.pid))
            
            print(f"✅ {service_name} started (PID: {process.pid})")
            return True
            
        except Exception as e:
            print(f"❌ Failed to start {service_name}: {e}")
            return False
    
    def stop_service(self, service_name: str):
        """Stop a trading service"""
        if service_name in self.processes:
            process = self.processes[service_name]
            try:
                print(f"🛑 Stopping {service_name}...")
                process.terminate()
                process.wait(timeout=10)
                print(f"✅ {service_name} stopped")
            except subprocess.TimeoutExpired:
                process.kill()
                print(f"⚠️ {service_name} force killed")
            except Exception as e:
                print(f"❌ Error stopping {service_name}: {e}")
            
            del self.processes[service_name]
        
        # Also try to kill by PID file
        pid_file = self.pids_dir / f"{service_name}.pid"
        if pid_file.exists():
            try:
                with open(pid_file, 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                time.sleep(2)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                pid_file.unlink()
            except:
                pass
    
    def start_all(self, services: List[str] = None) -> bool:
        """Start all trading services with proper delays"""
        if not self.check_dependencies():
            return False
        
        if services is None:
            services = list(self.services.keys())
        
        print("\n🚀 Starting Trading Services...")
        print("=" * 40)
        
        self.running = True
        started_services = []
        
        # Start services with delays
        for service_name in services:
            if service_name not in self.services:
                print(f"❌ Unknown service: {service_name}")
                continue
            
            service = self.services[service_name]
            
            # Apply startup delay
            if service.get("startup_delay", 0) > 0:
                print(f"⏱️ Waiting {service['startup_delay']}s before starting {service_name}")
                time.sleep(service["startup_delay"])
            
            if self.start_service(service_name):
                started_services.append(service_name)
            elif service.get("required", False):
                print(f"❌ Required service {service_name} failed to start")
                self.stop_all()
                return False
        
        print(f"\n✅ Started {len(started_services)} trading services")
        self.show_status()
        return True
    
    def stop_all(self):
        """Stop all trading services"""
        print("\n🛑 Stopping trading services...")
        
        # Stop in reverse order
        service_names = list(reversed(list(self.processes.keys())))
        
        for service_name in service_names:
            self.stop_service(service_name)
        
        self.running = False
        print("✅ All trading services stopped")
    
    def show_status(self):
        """Show status of trading services"""
        print("\n📊 Trading Services Status")
        print("-" * 40)
        
        for service_name, service in self.services.items():
            if service_name in self.processes:
                process = self.processes[service_name]
                if process.poll() is None:
                    status = "🟢 RUNNING"
                    pid = process.pid
                else:
                    status = "🔴 DEAD"
                    pid = "N/A"
            else:
                status = "🔴 STOPPED"
                pid = "N/A"
            
            print(f"{service_name:15} {status:12} PID:{pid}")
            print(f"{'':15} {service['description']}")
        
        print("-" * 40)
    
    def monitor(self):
        """Monitor services and restart if needed"""
        print("\n👁️ Monitoring trading services (Ctrl+C to stop)...")
        
        try:
            while self.running:
                # Check each service
                for service_name in list(self.processes.keys()):
                    process = self.processes[service_name]
                    
                    if process.poll() is not None:
                        print(f"\n⚠️ {service_name} died, restarting...")
                        
                        # Remove dead process
                        del self.processes[service_name]
                        
                        # Restart with delay
                        service = self.services[service_name]
                        if service.get("startup_delay", 0) > 0:
                            time.sleep(service["startup_delay"])
                        
                        if not self.start_service(service_name):
                            print(f"❌ Failed to restart {service_name}")
                
                time.sleep(10)  # Check every 10 seconds
                
        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped")
        finally:
            self.stop_all()
    
    def restart_all(self):
        """Restart all trading services"""
        print("🔄 Restarting trading services...")
        self.stop_all()
        time.sleep(2)
        return self.start_all()


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Trading Services Launcher")
    parser.add_argument("command", choices=[
        "start", "stop", "restart", "status", "monitor"
    ], help="Command to execute")
    parser.add_argument("--services", nargs="+", 
                       choices=["data_ingestor", "strategies", "risk_manager", "executor"],
                       help="Specific services to manage")
    
    args = parser.parse_args()
    
    manager = TradingServicesManager()
    
    # Handle signals for graceful shutdown
    def signal_handler(signum, frame):
        print(f"\n📡 Received signal {signum}")
        manager.stop_all()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        if args.command == "start":
            success = manager.start_all(args.services)
            if success:
                print("\n✨ Trading services ready! Use 'monitor' to track them.")
            sys.exit(0 if success else 1)
            
        elif args.command == "stop":
            manager.stop_all()
            
        elif args.command == "restart":
            success = manager.restart_all()
            sys.exit(0 if success else 1)
            
        elif args.command == "status":
            manager.show_status()
            
        elif args.command == "monitor":
            if not manager.start_all(args.services):
                sys.exit(1)
            manager.monitor()
    
    except Exception as e:
        print(f"❌ Error: {e}")
        manager.stop_all()
        sys.exit(1)


if __name__ == "__main__":
    main()