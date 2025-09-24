#!/usr/bin/env python3
"""
scripts/setup_infrastructure.py
Automated Infrastructure Setup - Downloads and configures everything automatically
"""

import os
import sys
import subprocess
import requests
import tarfile
import zipfile
import shutil
from pathlib import Path
import time

class InfrastructureSetup:
    def __init__(self):
        self.base_dir = Path(__file__).parent.parent
        self.observability_dir = Path.home() / "observability"
        self.downloads = {
            "prometheus": {
                "url": "https://github.com/prometheus/prometheus/releases/download/v2.45.0/prometheus-2.45.0.linux-amd64.tar.gz",
                "extract_dir": "prometheus-2.45.0.linux-amd64",
                "binary": "prometheus",
                "target_dir": self.observability_dir / "prometheus"
            },
            "grafana": {
                "url": "https://dl.grafana.com/oss/release/grafana-10.2.4.linux-amd64.tar.gz", 
                "extract_dir": "grafana-v10.2.4",
                "binary": "bin/grafana-server",
                "target_dir": self.observability_dir / "grafana"
            },
            "loki": {
                "url": "https://github.com/grafana/loki/releases/download/v2.9.0/loki-linux-amd64.zip",
                "extract_dir": "loki-linux-amd64",
                "binary": "loki-linux-amd64",
                "target_dir": self.observability_dir / "loki"
            }
        }
    
    def download_file(self, url: str, filepath: Path) -> bool:
        """Download file with progress"""
        try:
            print(f"⬇️ Downloading {filepath.name}...")
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            print(f"\r  Progress: {percent:.1f}%", end='', flush=True)
            
            print(f"\n✅ Downloaded {filepath.name}")
            return True
            
        except Exception as e:
            print(f"\n❌ Download failed: {e}")
            return False
    
    def extract_file(self, filepath: Path, extract_to: Path) -> bool:
        """Extract archive file"""
        try:
            print(f"📦 Extracting {filepath.name}...")
            
            if filepath.suffix == '.gz' and filepath.stem.endswith('.tar'):
                with tarfile.open(filepath, 'r:gz') as tar:
                    tar.extractall(extract_to)
            elif filepath.suffix == '.zip':
                with zipfile.ZipFile(filepath, 'r') as zip_ref:
                    zip_ref.extractall(extract_to)
            else:
                print(f"❌ Unknown archive format: {filepath}")
                return False
            
            print(f"✅ Extracted to {extract_to}")
            return True
            
        except Exception as e:
            print(f"❌ Extraction failed: {e}")
            return False
    
    def setup_binary(self, name: str, config: dict) -> bool:
        """Download and setup a binary"""
        print(f"\n🔧 Setting up {name}...")
        
        target_dir = config["target_dir"]
        extract_dir = target_dir / config["extract_dir"]
        binary_path = extract_dir / config["binary"]
        
        # Check if already installed
        if binary_path.exists():
            print(f"✅ {name} already installed: {binary_path}")
            return True
        
        # Create directories
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Download
        filename = config["url"].split('/')[-1]
        download_path = target_dir / filename
        
        if not download_path.exists():
            if not self.download_file(config["url"], download_path):
                return False
        
        # Extract
        if not self.extract_file(download_path, target_dir):
            return False
        
        # Verify binary exists
        if not binary_path.exists():
            print(f"❌ Binary not found after extraction: {binary_path}")
            return False
        
        # Make executable
        binary_path.chmod(0o755)
        print(f"✅ {name} setup complete: {binary_path}")
        
        # Clean up download file
        download_path.unlink()
        print(f"🗑️ Cleaned up {filename}")
        
        return True
    
    def create_configs(self):
        """Create all configuration files"""
        print("\n📝 Creating configuration files...")
        
        # Create directories
        for service_dir in ["prometheus", "grafana", "loki"]:
            (self.observability_dir / service_dir).mkdir(parents=True, exist_ok=True)
        
        # Prometheus config
        prometheus_config = self.observability_dir / "prometheus" / "prometheus.yml"
        if not prometheus_config.exists():
            prometheus_content = """
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    monitor: 'trading-platform'

rule_files:
  - "alerts.yml"

scrape_configs:
  - job_name: 'trading-platform'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 10s
    scrape_timeout: 5s
    
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
"""
            with open(prometheus_config, 'w') as f:
                f.write(prometheus_content.strip())
            print(f"✅ Created: {prometheus_config}")
        
        # Prometheus alerts
        alerts_config = self.observability_dir / "prometheus" / "alerts.yml"
        if not alerts_config.exists():
            alerts_content = """
groups:
  - name: trading.rules
    rules:
      - alert: TradingSystemDown
        expr: up{job="trading-platform"} == 0
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "Trading platform is down"
          
      - alert: HighErrorRate
        expr: rate(trading_custom_errors_total[5m]) > 0.1
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
"""
            with open(alerts_config, 'w') as f:
                f.write(alerts_content.strip())
            print(f"✅ Created: {alerts_config}")
        
        # Grafana config  
        grafana_config = self.observability_dir / "grafana" / "grafana.ini"
        if not grafana_config.exists():
            grafana_content = f"""
[default]
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
level = info

[paths]
data = {self.observability_dir}/grafana/data
logs = {self.observability_dir}/grafana/logs
plugins = {self.observability_dir}/grafana/plugins
provisioning = {self.observability_dir}/grafana/provisioning
"""
            with open(grafana_config, 'w') as f:
                f.write(grafana_content.strip())
            print(f"✅ Created: {grafana_config}")
        
        # Grafana provisioning
        self.setup_grafana_provisioning()
        
        print("✅ All configuration files created")
    
    def setup_grafana_provisioning(self):
        """Setup Grafana auto-provisioning"""
        provisioning_dir = self.observability_dir / "grafana" / "provisioning"
        datasources_dir = provisioning_dir / "datasources"
        dashboards_dir = provisioning_dir / "dashboards"
        dashboard_files_dir = self.observability_dir / "grafana" / "dashboards"
        
        for dir_path in [provisioning_dir, datasources_dir, dashboards_dir, dashboard_files_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Datasources config
        datasources_config = datasources_dir / "datasources.yml"
        datasources_content = """
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: true
"""
        with open(datasources_config, 'w') as f:
            f.write(datasources_content.strip())
        
        # Dashboards config
        dashboards_config = dashboards_dir / "dashboards.yml"
        dashboards_content = f"""
apiVersion: 1
providers:
  - name: 'default'
    orgId: 1
    folder: 'Trading Platform'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 10
    allowUiUpdates: true
    options:
      path: {dashboard_files_dir}
"""
        with open(dashboards_config, 'w') as f:
            f.write(dashboards_content.strip())
        
        print("✅ Grafana provisioning configured")
    
    def install_dependencies(self):
        """Install required Python packages"""
        print("\n📦 Installing Python dependencies...")
        
        packages = [
            "requests",
            "psutil", 
            "pydantic",
            "pydantic-settings",
            "fastapi",
            "uvicorn",
            "redis",
            "fakeredis",
            "pandas",
            "numpy",
            "alpaca-py",
            "prometheus-client",
            "prometheus-fastapi-instrumentator"
        ]
        
        for package in packages:
            try:
                __import__(package.replace('-', '_'))
                print(f"✅ {package} already installed")
            except ImportError:
                print(f"📦 Installing {package}...")
                result = subprocess.run([
                    sys.executable, "-m", "pip", "install", package
                ], capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"✅ Installed {package}")
                else:
                    print(f"❌ Failed to install {package}: {result.stderr}")
                    return False
        
        return True
    
    def setup_redis(self):
        """Ensure Redis is available"""
        print("\n🔧 Setting up Redis...")
        
        # Check if Redis is already running
        try:
            result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True, timeout=2)
            if result.stdout.strip() == "PONG":
                print("✅ Redis already running")
                return True
        except:
            pass
        
        # Try to start Redis
        try:
            subprocess.run(["redis-server", "--daemonize", "yes"], check=True)
            time.sleep(2)
            
            # Test again
            result = subprocess.run(["redis-cli", "ping"], capture_output=True, text=True, timeout=2)
            if result.stdout.strip() == "PONG":
                print("✅ Redis started successfully")
                return True
            else:
                print("❌ Redis not responding")
                return False
        except subprocess.CalledProcessError:
            print("❌ Failed to start Redis")
            print("Please install Redis manually:")
            print("  Ubuntu: sudo apt install redis-server")
            print("  macOS: brew install redis") 
            print("  Or use Docker: docker run -d -p 6379:6379 redis:alpine")
            return False
    
    def run_setup(self):
        """Run complete infrastructure setup"""
        print("🚀 Setting up Trading Platform Infrastructure")
        print("=" * 50)
        
        # Install Python dependencies
        if not self.install_dependencies():
            print("❌ Failed to install Python dependencies")
            return False
        
        # Setup Redis
        if not self.setup_redis():
            print("❌ Redis setup failed")
            return False
        
        # Create config files
        self.create_configs()
        
        # Setup binaries
        all_success = True
        for name, config in self.downloads.items():
            if not self.setup_binary(name, config):
                print(f"❌ Failed to setup {name}")
                all_success = False
        
        if all_success:
            print("\n🎉 Infrastructure setup completed successfully!")
            print("\n📊 Access Points (after starting services):")
            print("  • Trading Dashboard: http://localhost:8000/dashboard")
            print("  • API Documentation: http://localhost:8000/docs")
            print("  • Prometheus: http://localhost:9090")
            print("  • Grafana: http://localhost:3000 (admin/trading123)")
            print("\n🚀 Next step: python scripts/unified_launcher.py start")
            return True
        else:
            print("\n❌ Some components failed to setup")
            return False


def main():
    """Main entry point"""
    setup = InfrastructureSetup()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        # Quick check mode
        print("🔍 Checking infrastructure status...")
        all_good = True
        
        for name, config in setup.downloads.items():
            binary_path = config["target_dir"] / config["extract_dir"] / config["binary"]
            if binary_path.exists():
                print(f"✅ {name}: {binary_path}")
            else:
                print(f"❌ {name}: Not found")
                all_good = False
        
        if all_good:
            print("✅ All infrastructure components ready")
        else:
            print("❌ Some components missing - run without --check to install")
        
        return all_good
    
    # Full setup
    return setup.run_setup()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)