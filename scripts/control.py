#!/usr/bin/env python3
"""
scripts/control.py
Complete Trading Platform Controller - Enhanced with ChatGPT fixes
Implements robust process management, port-based cleanup, and strict health checking
"""

import os
import sys
import subprocess
import signal
import time
import psutil
import requests
import threading
import queue
from pathlib import Path
from datetime import datetime

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent))

class TradingPlatformController:
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.redis_pid_file = self.base_dir / "redis.pid"
        self.api_pid_file = self.base_dir / "api.pid"
        self.grafana_pid_file = self.base_dir / "grafana.pid"
        self.prometheus_pid_file = self.base_dir / "prometheus.pid"
        self.observability_dir = Path.home() / "observability"
        self.prometheus_dir = self.observability_dir / "prometheus"
        self.grafana_dir = self.observability_dir / "grafana"
        
        # Process management
        self.processes = {}
        self.running = False
        self.verbose_logs = False
        
        # Trading services
        self.trading_services = {
            "data_ingestor": {
                "script": "apps/data_ingestor/main.py",
                "description": "Market data ingestion",
                "startup_delay": 0
            },
            "strategies": {
                "script": "apps/strategies/main.py",
                "description": "Trading strategies",
                "startup_delay": 5
            },
            "risk_manager": {
                "script": "apps/risk_manager/main.py",
                "description": "Risk management",
                "startup_delay": 10
            },
            "executor": {
                "script": "apps/executor/main.py",
                "description": "Order execution",
                "startup_delay": 15
            }
        }
    
    def find_grafana_binary(self):
        """Find Grafana binary with flexible detection"""
        if not self.grafana_dir.exists():
            return None
            
        for item in self.grafana_dir.iterdir():
            if item.is_dir() and "grafana" in item.name.lower():
                binary_path = item / "bin" / "grafana-server"
                if binary_path.exists():
                    return binary_path
        return None
    
    def setup_observability_dirs(self):
        """Create and setup observability directories"""
        if not self.verbose_logs:
            print("Setting up observability directories...")
        
        for dir_path in [self.observability_dir, self.prometheus_dir, self.grafana_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        prometheus_config = self.prometheus_dir / "prometheus.yml"
        if not prometheus_config.exists():
            self.create_prometheus_config(prometheus_config)
        
        grafana_ini = self.grafana_dir / "grafana.ini"
        if not grafana_ini.exists():
            self.create_grafana_config(grafana_ini)
        
        if not self.verbose_logs:
            print("✅ Observability directories ready")
    
    def create_prometheus_config(self, config_path):
        """Create Prometheus configuration file"""
        config_content = """global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'trading-platform'
    static_configs:
      - targets: ['127.0.0.1:8000']
    metrics_path: '/metrics'
    scrape_interval: 10s
    scrape_timeout: 5s
    
  - job_name: 'prometheus'
    static_configs:
      - targets: ['127.0.0.1:9090']
"""
        with open(config_path, 'w') as f:
            f.write(config_content)
        if self.verbose_logs:
            print(f"Created Prometheus config: {config_path}")
    
    def create_grafana_config(self, config_path):
        """Create Grafana configuration file"""
        config_content = f"""[default]
instance_name = trading_platform

[server]
http_port = 3000
domain = localhost

[security]
admin_user = admin
admin_password = trading123

[analytics]
reporting_enabled = false

[log]
mode = console
level = warn

[paths]
data = {self.grafana_dir}/data
logs = {self.grafana_dir}/logs
plugins = {self.grafana_dir}/plugins
"""
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        for subdir in ["data", "logs", "plugins"]:
            (self.grafana_dir / subdir).mkdir(exist_ok=True)
        
        if self.verbose_logs:
            print(f"Created Grafana config: {config_path}")
    
    def download_binaries(self):
        """Download missing binaries"""
        print("Checking observability binaries...")
        
        prometheus_binary = self.prometheus_dir / "prometheus-2.45.0.linux-amd64" / "prometheus"
        if not prometheus_binary.exists():
            print("Prometheus binary not found. Run setup to download:")
            print("  python scripts/setup_infrastructure.py")
            return False
        
        grafana_binary = self.find_grafana_binary()
        if not grafana_binary:
            print("Grafana binary not found. Run setup to download:")
            print("  python scripts/setup_infrastructure.py")
            return False
        
        print("✅ All binaries found")
        return True
    
    def kill_processes_by_port(self, port: int, service_name: str = "unknown"):
        """Kill all processes using a specific port - OPTIMIZED VERSION (no OOM killer)"""
        killed_pids = []
        
        try:
            # Method 1: Prefer lsof (fast and lightweight)
            try:
                result = subprocess.run(
                    ["lsof", "-ti", f":{port}", "-sTCP:LISTEN"], 
                    capture_output=True, 
                    text=True, 
                    timeout=3
                )
                
                if result.returncode == 0:
                    for pid_str in set(result.stdout.split()):
                        if pid_str and pid_str.isdigit():
                            pid = int(pid_str)
                            try:
                                process = psutil.Process(pid)
                                proc_name = process.name()
                                print(f"🔫 Killing process {pid} ({proc_name}) listening on port {port}")
                                
                                process.terminate()
                                time.sleep(1)
                                if process.is_running():
                                    process.kill()
                                killed_pids.append(pid)
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                    
                    if killed_pids:
                        print(f"✅ Killed {len(killed_pids)} processes using port {port}")
                        return True
                        
            except (subprocess.TimeoutExpired, FileNotFoundError):
                # lsof not available or timed out, use fallback
                pass
            
            # Method 2: Fallback with LIMITED process scanning (avoid OOM killer)
            # Only scan processes that could realistically be using our ports
            CANDIDATES = {"uvicorn", "python", "python3", "prometheus", "grafana-server", "redis-server"}
            
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if proc.info['name'] not in CANDIDATES:
                        continue
                        
                    process = psutil.Process(proc.info['pid'])
                    # Check connections only for candidate processes
                    for conn in process.connections(kind="inet"):
                        if (conn.status == psutil.CONN_LISTEN 
                            and conn.laddr and conn.laddr.port == port):
                            pid = proc.info['pid']
                            proc_name = proc.info['name']
                            print(f"🔫 Killing process {pid} ({proc_name}) listening on port {port}")
                            
                            process.terminate()
                            time.sleep(1)
                            if process.is_running():
                                process.kill()
                            killed_pids.append(pid)
                            break  # Found the process using this port
                            
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            
            if killed_pids:
                print(f"✅ Killed {len(killed_pids)} processes using port {port}")
            else:
                print(f"ℹ️ No processes found listening on port {port}")
                
            return len(killed_pids) > 0
            
        except Exception as e:
            print(f"⚠️ Error killing processes on port {port}: {e}")
            return False
    
    def start_redis(self):
        """Start Redis server or check if already running"""
        try:
            result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True, timeout=2)
            if result.stdout.strip() == "PONG":
                print("✅ Redis already running")
                return True
        except:
            pass
        
        print("Starting Redis server...")
        self.stop_redis(quiet=True)
        
        try:
            redis_data_dir = self.base_dir / "redis-data"
            redis_data_dir.mkdir(exist_ok=True)
            
            redis_cmd = [
                "redis-server", 
                "--daemonize", "yes",
                "--dir", str(redis_data_dir),
                "--logfile", str(redis_data_dir / "redis.log"),
                "--port", "6379",
                "--pidfile", str(self.redis_pid_file)
            ]
            
            result = subprocess.run(redis_cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                time.sleep(2)
                test_result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True)
                if test_result.stdout.strip() == "PONG":
                    print("✅ Redis started successfully")
                    return True
            
            print(f"❌ Failed to start Redis: {result.stderr}")
            return False
                
        except Exception as e:
            print(f"❌ Error starting Redis: {e}")
            return False
    
    def stop_redis(self, quiet=False):
        """Stop Redis server"""
        if not quiet:
            print("Stopping Redis server...")
        
        try:
            subprocess.run(["redis-cli", "shutdown"], capture_output=True)
            time.sleep(1)
            
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if 'redis-server' in proc.info['name']:
                        proc.kill()
                except:
                    pass
            
            if self.redis_pid_file.exists():
                self.redis_pid_file.unlink()
            
            if not quiet:
                print("✅ Redis stopped")
                
        except Exception as e:
            if not quiet:
                print(f"⚠️ Error stopping Redis: {e}")
    
    def start_service_quiet(self, service_name, cmd, cwd=None, env=None):
        """Start a service with minimal output but capture to log file"""
        try:
            print(f"Starting {service_name}...")
            
            logs_dir = self.base_dir / "logs"
            logs_dir.mkdir(exist_ok=True)
            
            log_file = logs_dir / f"{service_name}.log"
            with open(log_file, 'w') as f:
                process = subprocess.Popen(
                    cmd,
                    cwd=cwd or str(self.base_dir),
                    env=env or os.environ,
                    stdout=f,
                    stderr=subprocess.STDOUT
                )
            
            pid_file = getattr(self, f"{service_name}_pid_file", self.base_dir / f"{service_name}.pid")
            with open(pid_file, 'w') as f:
                f.write(str(process.pid))
            
            self.processes[service_name] = process
            
            print(f"✅ {service_name} started (PID: {process.pid})")
            return True
            
        except Exception as e:
            print(f"❌ Failed to start {service_name}: {e}")
            return False
    
    def start_api(self):
        """Start API server - ENHANCED WITH STRICT HEALTH CHECKING AND LOGS"""
        print("🚀 Starting API server with enhanced health checking...")
        
        # FIRST: Aggressively clean port 8000
        print("🧹 Cleaning port 8000...")
        self.kill_processes_by_port(8000, "api")
        
        # SECOND: Stop any existing API processes
        self.stop_api(quiet=True)
        
        # THIRD: Wait a moment for cleanup
        time.sleep(2)
        
        # FOURTH: Start API with NO RELOAD to avoid parent/child processes
        api_script = self.base_dir / "apps" / "api" / "main.py"
        
        # Use uvicorn directly instead of python main.py to avoid reloader issues
        cmd = [
            sys.executable, "-m", "uvicorn", 
            "apps.api.main:app",
            "--host", "127.0.0.1",  # Use 127.0.0.1 instead of localhost
            "--port", "8000",
            # No reload by default (don't add --reload option)
            "--log-level", "info"
        ]
        
        env = {**os.environ, "PYTHONPATH": str(self.base_dir)}
        
        if self.start_service_quiet("api", cmd, env=env):
            # FIFTH: Strict health check with BACKOFF and NO FALSE POSITIVES
            print("⏳ Performing strict health check with backoff...")
            health_passed = False
            
            # Backoff schedule: 1s, 1.5s, 2s, 2.5s, 3s... up to ~15s total
            backoff_delays = [1.0, 1.5, 2.0, 2.5, 3.0, 3.0, 3.0]  # 15.5s total
            
            for attempt, delay in enumerate(backoff_delays, 1):
                try:
                    print(f"  🩺 Health check attempt {attempt}/{len(backoff_delays)} (wait {delay}s)...")
                    
                    response = requests.get(
                        "http://127.0.0.1:8000/health",  # Use 127.0.0.1
                        timeout=3,
                        headers={"User-Agent": "TradingPlatform-HealthCheck"}
                    )
                    
                    if response.status_code == 200:
                        health_data = response.json()
                        print(f"  ✅ Health check PASSED: {health_data}")
                        health_passed = True
                        break
                    else:
                        print(f"  ⚠️ Health check returned {response.status_code}")
                        
                except requests.exceptions.RequestException as e:
                    print(f"  ❌ Health check failed: {type(e).__name__}")
                    
                # Backoff delay
                if attempt < len(backoff_delays):
                    time.sleep(delay)
            
            # SIXTH: STRICT FAILURE HANDLING WITH LOG INSPECTION
            if not health_passed:
                print("🚨 HEALTH CHECK FAILED - API is NOT healthy")
                print("📝 Last 20 lines of API log:")
                print("-" * 50)
                
                # Show recent API logs for diagnosis
                log_file = self.base_dir / "logs" / "api.log"
                if log_file.exists():
                    try:
                        with open(log_file, 'r') as f:
                            lines = f.readlines()
                            for line in lines[-20:]:
                                print(f"  {line.rstrip()}")
                    except Exception as e:
                        print(f"  ❌ Error reading log: {e}")
                else:
                    print("  ⚠️ No API log file found")
                
                print("-" * 50)
                print("🛑 Stopping failed API process...")
                self.stop_api(quiet=True)
                return False
            
            print("✅ API started successfully and health check PASSED")
            return True
        
        print("❌ Failed to start API process")
        return False
    
    def stop_api(self, quiet=False):
        """Stop API server - ENHANCED WITH PORT-BASED CLEANUP"""
        if not quiet:
            print("Stopping API server...")
        
        try:
            # Method 1: Kill by PID file
            if self.api_pid_file.exists():
                with open(self.api_pid_file, 'r') as f:
                    pid = int(f.read().strip())
                
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(2)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                
                self.api_pid_file.unlink()
            
            # Method 2: Kill by command line pattern
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info['cmdline']
                    if cmdline and any('apps/api/main.py' in arg or 'apps.api.main:app' in arg for arg in cmdline):
                        print(f"🔫 Killing API process {proc.info['pid']}")
                        proc.kill()
                except:
                    pass
            
            # Method 3: ENHANCED - Kill anything using port 8000
            self.kill_processes_by_port(8000, "api")
            
            # Wait for cleanup
            time.sleep(1)
            
            if not quiet:
                print("✅ API stopped")
                
        except Exception as e:
            if not quiet:
                print(f"⚠️ Error stopping API: {e}")
    
    def start_prometheus(self):
        """Start Prometheus server with port cleanup"""
        # Clean port 9090 first
        print("🧹 Cleaning port 9090...")
        self.kill_processes_by_port(9090, "prometheus")
        
        self.stop_prometheus(quiet=True)
        
        prometheus_binary = self.prometheus_dir / "prometheus-2.45.0.linux-amd64" / "prometheus"
        prometheus_config = self.prometheus_dir / "prometheus.yml"
        
        if not prometheus_binary.exists():
            print("❌ Prometheus binary not found")
            return False
        
        if not prometheus_config.exists():
            self.create_prometheus_config(prometheus_config)
        
        data_dir = self.prometheus_dir / "data"
        data_dir.mkdir(exist_ok=True)
        
        cmd = [
            str(prometheus_binary),
            f"--config.file={prometheus_config}",
            f"--storage.tsdb.path={data_dir}",
            "--web.enable-lifecycle",
            "--web.listen-address=127.0.0.1:9090",
            "--storage.tsdb.retention.time=3d",
            "--log.level=warn"
        ]
        
        if self.start_service_quiet("prometheus", cmd, cwd=str(self.prometheus_dir)):
            time.sleep(5)
            try:
                response = requests.get("http://127.0.0.1:9090/-/healthy", timeout=5)
                if response.status_code == 200:
                    print("✅ Prometheus health check passed")
                    return True
                else:
                    print("⚠️ Prometheus started but health check failed")
                    # Show recent logs for diagnosis  
                    print("📝 Check Prometheus logs:")
                    log_file = self.base_dir / "logs" / "prometheus.log"  # Correct path
                    if log_file.exists():
                        try:
                            with open(log_file, 'r') as f:
                                lines = f.readlines()
                                for line in lines[-10:]:
                                    print(f"  {line.rstrip()}")
                        except:
                            pass
                    else:
                        print(f"  💡 Check logs: tail -f {log_file}")
                    return True  # Continue anyway
            except Exception as e:
                print(f"⚠️ Prometheus started but not responding: {e}")
                return True  # Continue anyway
        return False
    
    def stop_prometheus(self, quiet=False):
        """Stop Prometheus server"""
        if not quiet:
            print("Stopping Prometheus server...")
        
        try:
            if self.prometheus_pid_file.exists():
                with open(self.prometheus_pid_file, 'r') as f:
                    pid = int(f.read().strip())
                
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(2)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                
                self.prometheus_pid_file.unlink()
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if 'prometheus' in str(proc.info.get('cmdline', [])):
                        proc.kill()
                except:
                    pass
            
            if not quiet:
                print("✅ Prometheus stopped")
                
        except Exception as e:
            if not quiet:
                print(f"⚠️ Error stopping Prometheus: {e}")
    
    def start_grafana(self):
        """Start Grafana server"""
        self.stop_grafana(quiet=True)
        
        grafana_binary = self.find_grafana_binary()
        
        if not grafana_binary:
            print("❌ Grafana binary not found")
            return False
        
        grafana_ini = self.grafana_dir / "grafana.ini"
        if not grafana_ini.exists():
            self.create_grafana_config(grafana_ini)
        
        install_root = grafana_binary.parent.parent
        cmd = [str(grafana_binary), "--config", str(grafana_ini), "--homepath", str(install_root)]
        
        if self.start_service_quiet("grafana", cmd, cwd=str(install_root)):
            print("⏳ Waiting for Grafana to be ready...")
            for i in range(20):
                try:
                    response = requests.get("http://localhost:3000/api/health", timeout=3)
                    if response.status_code == 200:
                        print("✅ Grafana health check passed")
                        return True
                except:
                    pass
                time.sleep(1)
            
            print("⚠️ Grafana started but health check timeout")
            return True
        return False
    
    def stop_grafana(self, quiet=False):
        """Stop Grafana server"""
        if not quiet:
            print("Stopping Grafana server...")
        
        try:
            if self.grafana_pid_file.exists():
                with open(self.grafana_pid_file, 'r') as f:
                    pid = int(f.read().strip())
                
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(2)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                
                self.grafana_pid_file.unlink()
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if 'grafana-server' in str(proc.info.get('cmdline', [])):
                        proc.kill()
                except:
                    pass
            
            if not quiet:
                print("✅ Grafana stopped")
                
        except Exception as e:
            if not quiet:
                print(f"⚠️ Error stopping Grafana: {e}")
    
    def start_trading_service(self, service_name):
        """Start a single trading service with enhanced logging"""
        if service_name not in self.trading_services:
            print(f"❌ Unknown service: {service_name}")
            return False
        
        service = self.trading_services[service_name]
        script_path = self.base_dir / service["script"]
        
        if not script_path.exists():
            print(f"❌ Service script not found: {script_path}")
            return False
        
        try:
            print(f"Starting {service_name}...")
            
            cmd = [sys.executable, str(script_path)]
            env = {**os.environ, "PYTHONPATH": str(self.base_dir)}
            
            # Enhanced logging: save to logs directory instead of /dev/null
            logs_dir = self.base_dir / "logs"
            logs_dir.mkdir(exist_ok=True)
            
            log_file = logs_dir / f"{service_name}.log"
            with open(log_file, 'w') as f:
                process = subprocess.Popen(
                    cmd,
                    env=env,
                    cwd=str(self.base_dir),
                    stdout=f,
                    stderr=subprocess.STDOUT
                )
            
            self.processes[service_name] = process
            
            # Save PID
            pid_file = self.base_dir / "pids" / f"{service_name}.pid"
            pid_file.parent.mkdir(exist_ok=True)
            with open(pid_file, 'w') as f:
                f.write(str(process.pid))
            
            print(f"✅ {service_name} started (PID: {process.pid})")
            print(f"   📝 Logs: tail -f {log_file}")
            return True
            
        except Exception as e:
            print(f"❌ Failed to start {service_name}: {e}")
            return False
    
    def stop_trading_service(self, service_name):
        """Stop a single trading service"""
        if service_name in self.processes:
            process = self.processes[service_name]
            try:
                process.terminate()
                process.wait(timeout=10)
                print(f"✅ {service_name} stopped")
            except subprocess.TimeoutExpired:
                process.kill()
                print(f"⚠️ {service_name} force killed")
            except Exception as e:
                print(f"❌ Error stopping {service_name}: {e}")
            
            del self.processes[service_name]
        
        pid_file = self.base_dir / "pids" / f"{service_name}.pid"
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
    
    def status(self):
        """Show comprehensive status"""
        print("📊 Trading Platform Status")
        print("=" * 50)
        
        # Redis status
        try:
            result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True, timeout=2)
            if result.stdout.strip() == "PONG":
                print("🟢 Redis: Running")
                try:
                    from lib.bus import MessageBus
                    bus = MessageBus()
                    if bus.supports_streams:
                        print("  📡 Redis Streams: Active")
                    else:
                        print("  📡 Redis Streams: Pub/Sub fallback")
                except:
                    print("  📡 Redis Streams: Unknown")
            else:
                print("🔴 Redis: Not responding")
        except:
            print("🔴 Redis: Stopped")
        
        # Infrastructure services
        services_status = [
            ("API", "http://127.0.0.1:8000/health"),
            ("Prometheus", "http://127.0.0.1:9090/-/healthy"),
            ("Grafana", "http://127.0.0.1:3000/api/health")
        ]
        
        for service, url in services_status:
            try:
                response = requests.get(url, timeout=3)
                if response.status_code == 200:
                    print(f"🟢 {service}: Running")
                else:
                    print(f"🔴 {service}: Not responding")
            except:
                print(f"🔴 {service}: Stopped")
        
        # Trading services
        print("\n📈 Trading Services:")
        for service_name, service_config in self.trading_services.items():
            pid_file = self.base_dir / "pids" / f"{service_name}.pid"
            if pid_file.exists():
                try:
                    with open(pid_file, 'r') as f:
                        pid = int(f.read().strip())
                    if psutil.pid_exists(pid):
                        print(f"🟢 {service_name:15} Running (PID: {pid})")
                    else:
                        print(f"🔴 {service_name:15} Stopped (stale PID)")
                        pid_file.unlink()
                except:
                    print(f"🔴 {service_name:15} Stopped")
            else:
                print(f"🔴 {service_name:15} Stopped")
        
        print("\n🌐 Access Points:")
        print("  • Trading Dashboard: http://127.0.0.1:8000/dashboard")
        print("  • API Documentation: http://127.0.0.1:8000/docs")
        print("  • Prometheus: http://127.0.0.1:9090")
        print("  • Grafana: http://127.0.0.1:3000 (admin/trading123)")
        print()
    
    def start_infrastructure(self):
        """Start infrastructure services"""
        print("🚀 Starting Infrastructure Services")
        print("=" * 40)
        
        self.setup_observability_dirs()
        
        success = True
        
        if not self.start_redis():
            success = False
        
        if success and not self.start_api():
            success = False
            
        if success and not self.start_prometheus():
            print("⚠️ Prometheus failed - continuing without it")
            
        if success and not self.start_grafana():
            print("⚠️ Grafana failed - continuing without it")
        
        return success
    
    def start_trading_services(self):
        """Start trading services with delays"""
        print("\n🚀 Starting Trading Services")
        print("=" * 40)
        
        print("⏳ Checking dependencies...")
        
        redis_ok = False
        for i in range(5):
            try:
                result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True, timeout=2)
                if result.stdout.strip() == "PONG":
                    redis_ok = True
                    break
            except:
                pass
            time.sleep(1)
        
        if not redis_ok:
            print("❌ Redis not available")
            return False
        
        api_ok = False
        for i in range(10):
            try:
                response = requests.get("http://localhost:8000/health", timeout=3)
                if response.status_code == 200:
                    api_ok = True
                    break
            except:
                pass
            time.sleep(1)
        
        if not api_ok:
            print("⚠️ API health check failed, but continuing anyway...")
            print("    (API might still be initializing)")
        else:
            print("✅ Dependencies check passed")
        
        success = True
        for service_name, service_config in self.trading_services.items():
            delay = service_config.get("startup_delay", 0)
            if delay > 0:
                print(f"⏱️ Waiting {delay}s before starting {service_name}")
                time.sleep(delay)
            
            if not self.start_trading_service(service_name):
                success = False
                break
        
        return success
    
    def start_all(self):
        """Start complete platform"""
        print("🚀 Starting Complete Trading Platform")
        print("=" * 50)
        
        if not self.start_infrastructure():
            print("❌ Infrastructure startup failed")
            return False
        
        print("\n⏳ Waiting 5s for infrastructure to stabilize...")
        time.sleep(5)
        
        if not self.start_trading_services():
            print("❌ Trading services startup failed")
            return False
        
        print("\n🎉 Platform started successfully!")
        self.status()
        return True
    
    def stop_all(self):
        """Stop all services"""
        print("🛑 Stopping all services...")
        
        for service_name in reversed(list(self.trading_services.keys())):
            self.stop_trading_service(service_name)
        
        self.stop_grafana()
        self.stop_prometheus()
        self.stop_api()
        self.stop_redis()
        
        print("✅ All services stopped")


def main():
    controller = TradingPlatformController()
    
    if len(sys.argv) < 2:
        print("Usage: python scripts/control.py <command>")
        print("Commands:")
        print("  start          - Start complete platform")
        print("  start-infra    - Start infrastructure only")
        print("  start-trading  - Start trading services only")
        print("  stop           - Stop all services")
        print("  status         - Show service status")
        print("  start-redis    - Start only Redis")
        print("  start-api      - Start only API")
        print("  start-prometheus - Start only Prometheus")
        print("  start-grafana  - Start only Grafana")
        print("  stop-redis     - Stop only Redis")
        print("  stop-api       - Stop only API")
        print("  stop-prometheus - Stop only Prometheus")
        print("  stop-grafana   - Stop only Grafana")
        return
    
    command = sys.argv[1].lower()
    
    def signal_handler(signum, frame):
        print(f"\n📡 Received signal {signum}")
        controller.stop_all()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        if command == "start":
            success = controller.start_all()
            sys.exit(0 if success else 1)
        elif command == "start-infra":
            success = controller.start_infrastructure()
            sys.exit(0 if success else 1)
        elif command == "start-trading":
            success = controller.start_trading_services()
            sys.exit(0 if success else 1)
        elif command == "stop":
            controller.stop_all()
        elif command == "status":
            controller.status()
        elif command == "start-redis":
            controller.start_redis()
        elif command == "start-api":
            controller.start_api()
        elif command == "start-prometheus":
            controller.start_prometheus()
        elif command == "start-grafana":
            controller.start_grafana()
        elif command == "stop-redis":
            controller.stop_redis()
        elif command == "stop-api":
            controller.stop_api()
        elif command == "stop-prometheus":
            controller.stop_prometheus()
        elif command == "stop-grafana":
            controller.stop_grafana()
        else:
            print(f"Unknown command: {command}")
    
    except KeyboardInterrupt:
        print("\n🛑 Operation interrupted")
        controller.stop_all()
    except Exception as e:
        print(f"❌ Error: {e}")
        controller.stop_all()
        sys.exit(1)


if __name__ == "__main__":
    main()