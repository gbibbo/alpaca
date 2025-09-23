#!/usr/bin/env python3
"""
scripts/control.py
Trading Platform Controller with Grafana and Prometheus support
"""

import os
import sys
import subprocess
import signal
import time
import psutil
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent))

class TradingPlatformController:
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.redis_pid_file = self.base_dir / "redis.pid"
        self.api_pid_file = self.base_dir / "api.pid"
        self.grafana_pid_file = self.base_dir / "grafana.pid"
        self.prometheus_pid_file = self.base_dir / "prometheus.pid"
        self.grafana_dir = Path.home() / "grafana"
        self.prometheus_dir = self.base_dir / "prometheus-2.45.0.linux-amd64"
        
    def find_grafana_binary(self):
        """Find Grafana binary with flexible detection"""
        if not self.grafana_dir.exists():
            return None
            
        # Look for any directory containing grafana
        for item in self.grafana_dir.iterdir():
            if item.is_dir() and "grafana" in item.name.lower():
                binary_path = item / "bin" / "grafana-server"
                if binary_path.exists():
                    return binary_path
        
        return None
    
    def start_redis(self):
        """Start Redis server"""
        print("Starting Redis server...")
        
        # Kill any existing Redis
        self.stop_redis(quiet=True)
        
        try:
            # Create redis-data directory
            redis_data_dir = self.base_dir / "redis-data"
            redis_data_dir.mkdir(exist_ok=True)
            
            # Start Redis
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
                # Test connection
                test_result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True)
                if test_result.stdout.strip() == "PONG":
                    print("✅ Redis started successfully")
                    return True
                else:
                    print("❌ Redis failed to respond")
                    return False
            else:
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
            # Try graceful shutdown first
            subprocess.run(["redis-cli", "shutdown"], capture_output=True)
            time.sleep(1)
            
            # Kill any remaining Redis processes
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    if 'redis-server' in proc.info['name']:
                        proc.kill()
                except:
                    pass
            
            # Remove PID file
            if self.redis_pid_file.exists():
                self.redis_pid_file.unlink()
            
            if not quiet:
                print("✅ Redis stopped")
                
        except Exception as e:
            if not quiet:
                print(f"⚠️ Error stopping Redis: {e}")
    
    def start_api(self):
        """Start API server"""
        print("Starting API server...")
        
        # Kill any existing API
        self.stop_api(quiet=True)
        
        try:
            api_script = self.base_dir / "apps" / "api" / "main.py"
            
            # Start API process
            process = subprocess.Popen(
                [sys.executable, str(api_script)],
                cwd=str(self.base_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONPATH": str(self.base_dir)}
            )
            
            # Save PID
            with open(self.api_pid_file, 'w') as f:
                f.write(str(process.pid))
            
            # Wait a bit and check if it started
            time.sleep(5)
            
            if process.poll() is None:
                # Test API endpoint
                try:
                    import requests
                    response = requests.get("http://localhost:8000/health", timeout=5)
                    if response.status_code == 200:
                        print("✅ API started successfully")
                        print("🌐 Dashboard: http://localhost:8000/dashboard")
                        print("📊 Metrics: http://localhost:8000/metrics")
                        return True
                except:
                    pass
                
                print("⚠️ API started but health check failed")
                return True
            else:
                print("❌ API failed to start")
                return False
                
        except Exception as e:
            print(f"❌ Error starting API: {e}")
            return False
    
    def stop_api(self, quiet=False):
        """Stop API server"""
        if not quiet:
            print("Stopping API server...")
        
        try:
            # Kill by PID file
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
            
            # Kill any remaining API processes
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info['cmdline']
                    if cmdline and 'apps/api/main.py' in ' '.join(cmdline):
                        proc.kill()
                except:
                    pass
            
            if not quiet:
                print("✅ API stopped")
                
        except Exception as e:
            if not quiet:
                print(f"⚠️ Error stopping API: {e}")
    
    def start_prometheus(self):
        """Start Prometheus server"""
        print("Starting Prometheus server...")
        
        # Kill any existing Prometheus
        self.stop_prometheus(quiet=True)
        
        try:
            # Check if Prometheus directory exists
            if not self.prometheus_dir.exists():
                print("❌ Prometheus not found. Run:")
                print("   cd /mnt/fast/nobackup/users/gb0048/ALPACA")
                print("   wget https://github.com/prometheus/prometheus/releases/download/v2.45.0/prometheus-2.45.0.linux-amd64.tar.gz")
                print("   tar -xzf prometheus-2.45.0.linux-amd64.tar.gz")
                return False
            
            prometheus_binary = self.prometheus_dir / "prometheus"
            prometheus_config = self.prometheus_dir / "prometheus.yml"
            
            if not prometheus_binary.exists():
                print(f"❌ Prometheus binary not found: {prometheus_binary}")
                return False
            
            if not prometheus_config.exists():
                print(f"❌ Prometheus config not found: {prometheus_config}")
                print("Please copy prometheus.yml to the prometheus directory")
                return False
            
            print(f"Found Prometheus binary: {prometheus_binary}")
            
            # Create necessary directories
            data_dir = self.prometheus_dir / "data"
            data_dir.mkdir(exist_ok=True)
            
            # Start Prometheus process
            prometheus_cmd = [
                str(prometheus_binary),
                f"--config.file={prometheus_config}",
                f"--storage.tsdb.path={data_dir}",
                f"--web.console.libraries={self.prometheus_dir}/console_libraries",
                f"--web.console.templates={self.prometheus_dir}/consoles",
                "--web.enable-lifecycle"
            ]
            
            process = subprocess.Popen(
                prometheus_cmd,
                cwd=str(self.prometheus_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )
            
            # Save PID
            with open(self.prometheus_pid_file, 'w') as f:
                f.write(str(process.pid))
            
            # Wait and test
            time.sleep(8)
            
            if process.poll() is None:
                try:
                    import requests
                    response = requests.get("http://localhost:9090/-/healthy", timeout=10)
                    if response.status_code == 200:
                        print("✅ Prometheus started successfully")
                        print("📊 Prometheus UI: http://localhost:9090")
                        return True
                except Exception as e:
                    print(f"⚠️ Prometheus started but health check failed: {e}")
                    return True
                
                print("⚠️ Prometheus started but no response on port 9090")
                return True
            else:
                print("❌ Prometheus failed to start")
                return False
                
        except Exception as e:
            print(f"❌ Error starting Prometheus: {e}")
            return False
    
    def stop_prometheus(self, quiet=False):
        """Stop Prometheus server"""
        if not quiet:
            print("Stopping Prometheus server...")
        
        try:
            # Kill by PID file
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
            
            # Kill any remaining Prometheus processes
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
        print("Starting Grafana server...")
        
        # Kill any existing Grafana
        self.stop_grafana(quiet=True)
        
        try:
            grafana_binary = self.find_grafana_binary()
            
            if not grafana_binary:
                print("❌ Grafana binary not found. Run:")
                print("   cd ~/grafana && wget https://dl.grafana.com/oss/release/grafana-10.2.4.linux-amd64.tar.gz")
                print("   tar -zxf grafana-10.2.4.linux-amd64.tar.gz")
                return False
            
            print(f"Found Grafana binary: {grafana_binary}")
            
            # Create necessary directories
            for subdir in ["data", "logs", "plugins", "provisioning/datasources", "provisioning/dashboards", "dashboards"]:
                (self.grafana_dir / subdir).mkdir(parents=True, exist_ok=True)
            
            # Create Grafana config if it doesn't exist
            grafana_ini = self.grafana_dir / "grafana.ini"
            if not grafana_ini.exists():
                self.create_grafana_config(grafana_ini)
            
            # Start Grafana process using new command format
            # Determine install root and build correct command
            install_root = grafana_binary.parent.parent  # .../grafana-v10.2.4
            
            # Build command based on binary type
            if grafana_binary.name == "grafana":
                cmd = [str(grafana_binary), "server", "--config", str(grafana_ini), "--homepath", str(install_root)]
            else:  # grafana-server
                cmd = [str(grafana_binary), "--config", str(grafana_ini), "--homepath", str(install_root)]
            
            # Start Grafana process with correct working directory
            process = subprocess.Popen(
                cmd,
                cwd=str(install_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )
            
            # Save PID
            with open(self.grafana_pid_file, 'w') as f:
                f.write(str(process.pid))
            
            # Wait and test
            time.sleep(8)
            
            if process.poll() is None:
                try:
                    import requests
                    response = requests.get("http://localhost:3000/api/health", timeout=10)
                    if response.status_code == 200:
                        print("✅ Grafana started successfully")
                        print("📊 Grafana UI: http://localhost:3000 (admin/trading123)")
                        return True
                except Exception as e:
                    print(f"⚠️ Grafana started but health check failed: {e}")
                    return True
                
                print("⚠️ Grafana started but no response on port 3000")
                return True
            else:
                print("❌ Grafana failed to start")
                return False
                
        except Exception as e:
            print(f"❌ Error starting Grafana: {e}")
            return False
    
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
mode = file
level = info

[paths]
data = {self.grafana_dir}/data
logs = {self.grafana_dir}/logs
plugins = {self.grafana_dir}/plugins
provisioning = {self.grafana_dir}/provisioning
"""
        with open(config_path, 'w') as f:
            f.write(config_content)
        print(f"Created Grafana config: {config_path}")
    
    def stop_grafana(self, quiet=False):
        """Stop Grafana server"""
        if not quiet:
            print("Stopping Grafana server...")
        
        try:
            # Kill by PID file
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
            
            # Kill any remaining Grafana processes
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
    
    def status(self):
        """Show status of all services"""
        print("📊 Trading Platform Status")
        print("=" * 40)
        
        # Redis status
        try:
            result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True, timeout=2)
            if result.stdout.strip() == "PONG":
                print("🟢 Redis: Running")
            else:
                print("🔴 Redis: Not responding")
        except:
            print("🔴 Redis: Stopped")
        
        # API status
        try:
            import requests
            response = requests.get("http://localhost:8000/health", timeout=3)
            if response.status_code == 200:
                print("🟢 API: Running")
                print("   Dashboard: http://localhost:8000/dashboard")
                print("   Metrics: http://localhost:8000/metrics")
            else:
                print("🔴 API: Not responding")
        except:
            print("🔴 API: Stopped")
        
        # Prometheus status
        try:
            import requests
            response = requests.get("http://localhost:9090/-/healthy", timeout=3)
            if response.status_code == 200:
                print("🟢 Prometheus: Running")
                print("   UI: http://localhost:9090")
            else:
                print("🔴 Prometheus: Not responding")
        except:
            print("🔴 Prometheus: Stopped")
        
        # Grafana status
        try:
            import requests
            response = requests.get("http://localhost:3000/api/health", timeout=3)
            if response.status_code == 200:
                print("🟢 Grafana: Running")
                print("   UI: http://localhost:3000 (admin/trading123)")
            else:
                print("🔴 Grafana: Not responding")
        except:
            print("🔴 Grafana: Stopped")
        
        print()
    
    def start_all(self):
        """Start all services"""
        print("🚀 Starting Trading Platform with Prometheus and Grafana...")
        print()
        
        success = True
        
        if not self.start_redis():
            success = False
        
        if success and not self.start_api():
            success = False
            
        if success and not self.start_prometheus():
            success = False
            
        if success and not self.start_grafana():
            success = False
        
        print()
        if success:
            print("🎉 Trading Platform started successfully!")
            self.status()
        else:
            print("❌ Failed to start some services")
    
    def stop_all(self):
        """Stop all services"""
        print("🛑 Stopping Trading Platform...")
        print()
        
        self.stop_grafana()
        self.stop_prometheus()
        self.stop_api()
        self.stop_redis()
        
        print()
        print("✅ Trading Platform stopped")
    
    def restart(self):
        """Restart all services"""
        print("🔄 Restarting Trading Platform...")
        self.stop_all()
        time.sleep(2)
        self.start_all()


def main():
    controller = TradingPlatformController()
    
    if len(sys.argv) < 2:
        print("Usage: python scripts/control.py <command>")
        print("Commands:")
        print("  start          - Start all services (Redis + API + Prometheus + Grafana)")
        print("  stop           - Stop all services") 
        print("  restart        - Restart all services")
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
    
    try:
        if command == "start":
            controller.start_all()
        elif command == "stop":
            controller.stop_all()
        elif command == "restart":
            controller.restart()
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


if __name__ == "__main__":
    main()