#!/usr/bin/env python3
"""
scripts/unified_launcher.py
Unified Trading Platform Launcher - Single command to rule them all
Starts all services in parallel, manages processes, provides unified logging
"""

import os
import sys
import asyncio
import subprocess
import signal
import time
import psutil
import requests
import json
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import queue

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent))

class ProcessManager:
    """Manages background processes with logging and health monitoring"""
    
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.observability_dir = Path.home() / "observability"
        self.logs_dir = self.base_dir / "logs"
        self.pids_dir = self.base_dir / "pids"
        
        # Create directories
        for dir_path in [self.logs_dir, self.pids_dir]:
            dir_path.mkdir(exist_ok=True)
        
        self.processes: Dict[str, subprocess.Popen] = {}
        self.log_threads: Dict[str, threading.Thread] = {}
        self.running = False
        self.log_queue = queue.Queue()
        
        # Service definitions
        self.services = {
            "redis": {
                "cmd": ["redis-server", "--daemonize", "no", "--port", "6379"],
                "cwd": str(self.base_dir),
                "health_url": None,
                "health_check": self.check_redis_health,
                "required": True,
                "startup_delay": 0
            },
            "api": {
                "cmd": [sys.executable, "apps/api/main.py"],
                "cwd": str(self.base_dir),
                "health_url": "http://localhost:8000/health",
                "health_check": None,
                "required": True,
                "startup_delay": 3,
                "env": {**os.environ, "PYTHONPATH": str(self.base_dir)}
            },
            "prometheus": {
                "cmd": self.get_prometheus_cmd(),
                "cwd": str(self.observability_dir / "prometheus"),
                "health_url": "http://localhost:9090/-/healthy",
                "health_check": None,
                "required": False,
                "startup_delay": 1
            },
            "grafana": {
                "cmd": self.get_grafana_cmd(),
                "cwd": str(self.observability_dir / "grafana"),
                "health_url": "http://localhost:3000/api/health",
                "health_check": None,
                "required": False,
                "startup_delay": 2
            }
        }
    
    def get_prometheus_cmd(self) -> List[str]:
        """Get Prometheus command if binary exists"""
        prometheus_dir = self.observability_dir / "prometheus"
        binary = prometheus_dir / "prometheus-2.45.0.linux-amd64" / "prometheus"
        config = prometheus_dir / "prometheus.yml"
        data_dir = prometheus_dir / "data"
        
        if not binary.exists() or not config.exists():
            return []
        
        data_dir.mkdir(exist_ok=True)
        
        return [
            str(binary),
            f"--config.file={config}",
            f"--storage.tsdb.path={data_dir}",
            "--web.enable-lifecycle",
            "--storage.tsdb.retention.time=7d"
        ]
    
    def get_grafana_cmd(self) -> List[str]:
        """Get Grafana command if binary exists"""
        grafana_dir = self.observability_dir / "grafana"
        binary = grafana_dir / "grafana-v10.2.4" / "bin" / "grafana-server"
        config = grafana_dir / "grafana.ini"
        
        if not binary.exists() or not config.exists():
            return []
        
        # Create required directories
        for subdir in ["data", "logs", "plugins"]:
            (grafana_dir / subdir).mkdir(exist_ok=True)
        
        return [
            str(binary),
            "--config", str(config),
            "--homepath", str(binary.parent.parent)
        ]
    
    def check_redis_health(self) -> bool:
        """Check Redis health"""
        try:
            result = subprocess.run(
                ["redis-cli", "ping"], 
                capture_output=True, 
                text=True, 
                timeout=2
            )
            return result.stdout.strip() == "PONG"
        except:
            return False
    
    def check_url_health(self, url: str, timeout: int = 5) -> bool:
        """Check URL health"""
        try:
            response = requests.get(url, timeout=timeout)
            return response.status_code == 200
        except:
            return False
    
    def start_log_collector(self):
        """Start unified log collector thread"""
        def log_collector():
            while self.running:
                try:
                    # Get log message from queue
                    service, line = self.log_queue.get(timeout=1)
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    
                    # Color coding for services
                    colors = {
                        "redis": "\033[91m",      # Red
                        "api": "\033[92m",        # Green  
                        "prometheus": "\033[93m", # Yellow
                        "grafana": "\033[94m",    # Blue
                    }
                    reset_color = "\033[0m"
                    
                    color = colors.get(service, "")
                    print(f"{color}[{timestamp}] {service.upper():10}{reset_color} | {line.strip()}")
                    
                except queue.Empty:
                    continue
                except:
                    break
        
        self.log_thread = threading.Thread(target=log_collector, daemon=True)
        self.log_thread.start()
    
    def start_process_logger(self, service_name: str, process: subprocess.Popen):
        """Start logger thread for a process"""
        def log_reader():
            try:
                for line in iter(process.stdout.readline, b''):
                    if line and self.running:
                        self.log_queue.put((service_name, line.decode('utf-8', errors='ignore')))
                    if not self.running:
                        break
            except:
                pass
        
        thread = threading.Thread(target=log_reader, daemon=True)
        thread.start()
        self.log_threads[service_name] = thread
    
    def start_service(self, service_name: str) -> bool:
        """Start a single service"""
        if service_name in self.processes:
            print(f"⚠️ {service_name} already running")
            return True
        
        service = self.services.get(service_name)
        if not service:
            print(f"❌ Unknown service: {service_name}")
            return False
        
        # Check if command is available
        if not service["cmd"]:
            print(f"⚠️ {service_name} binary not found, skipping")
            return not service["required"]
        
        try:
            print(f"🚀 Starting {service_name}...")
            
            # Start process
            process = subprocess.Popen(
                service["cmd"],
                cwd=service.get("cwd", "."),
                env=service.get("env", os.environ),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=False
            )
            
            self.processes[service_name] = process
            
            # Start log capture
            self.start_process_logger(service_name, process)
            
            # Save PID
            pid_file = self.pids_dir / f"{service_name}.pid"
            with open(pid_file, 'w') as f:
                f.write(str(process.pid))
            
            print(f"✅ {service_name} started (PID: {process.pid})")
            return True
            
        except Exception as e:
            print(f"❌ Failed to start {service_name}: {e}")
            return False
    
    def stop_service(self, service_name: str, timeout: int = 10):
        """Stop a single service"""
        if service_name not in self.processes:
            # Try to kill by PID file
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
                    print(f"✅ {service_name} stopped (by PID file)")
                except:
                    pass
            return
        
        process = self.processes[service_name]
        print(f"🛑 Stopping {service_name}...")
        
        try:
            # Graceful shutdown
            process.terminate()
            
            # Wait for graceful shutdown
            try:
                process.wait(timeout=timeout)
                print(f"✅ {service_name} stopped gracefully")
            except subprocess.TimeoutExpired:
                # Force kill
                process.kill()
                process.wait()
                print(f"⚠️ {service_name} force killed")
            
            # Clean up
            del self.processes[service_name]
            
            # Remove PID file
            pid_file = self.pids_dir / f"{service_name}.pid"
            if pid_file.exists():
                pid_file.unlink()
                
        except Exception as e:
            print(f"❌ Error stopping {service_name}: {e}")
    
    def wait_for_health(self, service_name: str, max_wait: int = 30) -> bool:
        """Wait for service to become healthy"""
        service = self.services.get(service_name)
        if not service:
            return False
        
        print(f"⏳ Waiting for {service_name} to be healthy...")
        
        for i in range(max_wait):
            # Check if process is still running
            if service_name in self.processes:
                process = self.processes[service_name]
                if process.poll() is not None:
                    print(f"❌ {service_name} process died")
                    return False
            
            # Check health
            is_healthy = False
            
            if service["health_check"]:
                is_healthy = service["health_check"]()
            elif service["health_url"]:
                is_healthy = self.check_url_health(service["health_url"])
            else:
                # No health check, assume healthy if process is running
                is_healthy = True
            
            if is_healthy:
                print(f"✅ {service_name} is healthy")
                return True
            
            time.sleep(1)
        
        print(f"❌ {service_name} health check timeout")
        return False
    
    def start_all(self, services: List[str] = None) -> bool:
        """Start all services in parallel"""
        if services is None:
            services = list(self.services.keys())
        
        print("🚀 Starting Trading Platform (Unified Mode)")
        print("=" * 50)
        
        self.running = True
        self.start_log_collector()
        
        # Start services with delays
        started_services = []
        for service_name in services:
            service = self.services.get(service_name)
            if not service:
                continue
            
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
        
        # Wait for all services to be healthy
        print("\n🏥 Checking service health...")
        all_healthy = True
        for service_name in started_services:
            if not self.wait_for_health(service_name, max_wait=20):
                if self.services[service_name].get("required", False):
                    all_healthy = False
                    break
        
        if all_healthy:
            print("\n🎉 All services started successfully!")
            self.show_status()
            return True
        else:
            print("\n❌ Some required services failed health checks")
            self.stop_all()
            return False
    
    def stop_all(self):
        """Stop all services"""
        print("\n🛑 Stopping all services...")
        self.running = False
        
        # Stop in reverse order
        services_to_stop = list(reversed(list(self.processes.keys())))
        
        for service_name in services_to_stop:
            self.stop_service(service_name)
        
        print("✅ All services stopped")
    
    def show_status(self):
        """Show status of all services"""
        print("\n📊 Service Status")
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
            
            print(f"{service_name:12} {status:12} PID:{pid}")
        
        print("-" * 40)
        
        # Show URLs
        urls = [
            ("Trading Dashboard", "http://localhost:8000/dashboard"),
            ("API Documentation", "http://localhost:8000/docs"),
            ("Prometheus", "http://localhost:9090"),
            ("Grafana", "http://localhost:3000 (admin/trading123)")
        ]
        
        print("\n🌐 Access Points:")
        for name, url in urls:
            print(f"  • {name}: {url}")
    
    def monitor(self):
        """Monitor services and restart if needed"""
        print("\n👁️ Starting service monitoring (Ctrl+C to stop)...")
        
        try:
            while self.running:
                # Check each service
                for service_name in list(self.processes.keys()):
                    process = self.processes[service_name]
                    
                    if process.poll() is not None:
                        print(f"\n⚠️ {service_name} died, restarting...")
                        
                        # Remove dead process
                        del self.processes[service_name]
                        
                        # Restart
                        if self.start_service(service_name):
                            if not self.wait_for_health(service_name, max_wait=10):
                                print(f"❌ {service_name} restart failed health check")
                        else:
                            print(f"❌ Failed to restart {service_name}")
                
                time.sleep(5)  # Check every 5 seconds
                
        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped")
        finally:
            self.stop_all()
    
    def restart_all(self):
        """Restart all services"""
        print("🔄 Restarting all services...")
        self.stop_all()
        time.sleep(3)
        return self.start_all()


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Unified Trading Platform Launcher")
    parser.add_argument("command", choices=[
        "start", "stop", "restart", "status", "monitor", "logs"
    ], help="Command to execute")
    parser.add_argument("--services", nargs="+", 
                       choices=["redis", "api", "prometheus", "grafana"],
                       help="Specific services to manage")
    parser.add_argument("--skip-health", action="store_true",
                       help="Skip health checks")
    
    args = parser.parse_args()
    
    manager = ProcessManager()
    
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
                print("\n✨ Platform ready! Press Ctrl+C to stop or use 'monitor' command")
                # Keep running to show logs
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    print("\n🛑 Shutting down...")
                    manager.stop_all()
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
            
        elif args.command == "logs":
            # Show recent logs
            print("📄 Recent logs from all services:")
            for log_file in manager.logs_dir.glob("*.log"):
                print(f"\n--- {log_file.stem} ---")
                try:
                    with open(log_file, 'r') as f:
                        lines = f.readlines()[-20:]  # Last 20 lines
                        for line in lines:
                            print(line.strip())
                except:
                    print("Unable to read log file")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        manager.stop_all()
        sys.exit(1)


if __name__ == "__main__":
    main()