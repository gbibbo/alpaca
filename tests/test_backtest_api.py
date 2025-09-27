#!/usr/bin/env python3
"""
tests/test_backtest_api.py
Quick test script for the new backtest API endpoints
"""

import requests
import json
import time
from datetime import datetime, timedelta

# API base URL
BASE_URL = "http://localhost:8000"

def test_api_health():
    """Test basic API health"""
    print("🔍 Testing API health...")
    response = requests.get(f"{BASE_URL}/health")
    if response.status_code == 200:
        print("✅ API is healthy")
        return True
    else:
        print(f"❌ API health check failed: {response.status_code}")
        return False

def test_create_backtest():
    """Test creating a new backtest job"""
    print("\n🚀 Testing backtest job creation...")

    payload = {
        "symbols": ["AAPL", "GOOGL"],
        "start_date": "2022-01-01",
        "end_date": "2022-01-07",
        "timeframe": "1Day",
        "feed": "iex",
        "seed": 42,
        "speed_multiplier": 1.0,
        "strategies": ["random_50_50"],
        "initial_cash": 50000.0
    }

    response = requests.post(f"{BASE_URL}/backtest/jobs", json=payload)

    if response.status_code == 200:
        data = response.json()
        job_id = data.get("job_id")
        print(f"✅ Created backtest job: {job_id}")
        return job_id
    else:
        print(f"❌ Failed to create backtest: {response.status_code} - {response.text}")
        return None

def test_list_jobs():
    """Test listing backtest jobs"""
    print("\n📋 Testing job listing...")

    response = requests.get(f"{BASE_URL}/backtest/jobs")

    if response.status_code == 200:
        jobs = response.json()
        print(f"✅ Found {len(jobs)} backtest jobs")
        for job in jobs[:3]:  # Show first 3
            print(f"   - {job['job_id']}: {job['status']} ({job['created_at']})")
        return jobs
    else:
        print(f"❌ Failed to list jobs: {response.status_code}")
        return []

def test_job_status(job_id):
    """Test getting job status"""
    print(f"\n🔍 Testing job status for {job_id}...")

    response = requests.get(f"{BASE_URL}/backtest/jobs/{job_id}")

    if response.status_code == 200:
        job = response.json()
        print(f"✅ Job status: {job['status']}")
        print(f"   Created: {job['created_at']}")
        print(f"   Progress: {job['progress']}%")
        return job
    else:
        print(f"❌ Failed to get job status: {response.status_code}")
        return None

def test_start_job(job_id):
    """Test starting a job"""
    print(f"\n▶️  Testing job start for {job_id}...")

    response = requests.post(f"{BASE_URL}/backtest/jobs/{job_id}/start")

    if response.status_code == 200:
        print("✅ Job started successfully")
        return True
    else:
        print(f"❌ Failed to start job: {response.status_code} - {response.text}")
        return False

def test_quick_backtest():
    """Test the quick backtest endpoint"""
    print("\n⚡ Testing quick backtest...")

    params = {
        "symbols": "AAPL",
        "days": 7,
        "seed": 123
    }

    response = requests.post(f"{BASE_URL}/backtest/quick", params=params)

    if response.status_code == 200:
        data = response.json()
        print(f"✅ Quick backtest created: {data['job_id']}")
        print(f"   Status: {data['status']}")
        print(f"   Config: {data['config']}")
        return data['job_id']
    else:
        print(f"❌ Failed to create quick backtest: {response.status_code} - {response.text}")
        return None

def test_backtest_stats():
    """Test backtest statistics"""
    print("\n📊 Testing backtest stats...")

    response = requests.get(f"{BASE_URL}/backtest/stats")

    if response.status_code == 200:
        stats = response.json()
        print("✅ Backtest stats:")
        print(f"   Total jobs: {stats['total_jobs']}")
        print(f"   Running jobs: {stats['running_jobs']}")
        print(f"   Max concurrent: {stats['max_concurrent']}")
        print(f"   Status counts: {stats['status_counts']}")
        return stats
    else:
        print(f"❌ Failed to get stats: {response.status_code}")
        return None

def monitor_job(job_id, timeout=60):
    """Monitor a job until completion"""
    print(f"\n⏱️  Monitoring job {job_id} (timeout: {timeout}s)...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        response = requests.get(f"{BASE_URL}/backtest/jobs/{job_id}")

        if response.status_code == 200:
            job = response.json()
            status = job['status']
            progress = job['progress']

            print(f"   Status: {status} ({progress:.1f}%)")

            if status in ['completed', 'failed', 'cancelled']:
                if status == 'completed':
                    print("✅ Job completed successfully!")

                    # Try to get results
                    results_response = requests.get(f"{BASE_URL}/backtest/jobs/{job_id}/results")
                    if results_response.status_code == 200:
                        results = results_response.json()
                        print(f"   Results: {len(results.get('results', {}))} symbols processed")
                        if 'simulation_stats' in results:
                            stats = results['simulation_stats']
                            print(f"   Bars published: {stats.get('bars_published', 0)}")
                            print(f"   Duration: {stats.get('duration_seconds', 0):.1f}s")
                else:
                    print(f"❌ Job {status}")
                    if job.get('error_message'):
                        print(f"   Error: {job['error_message']}")

                return job

            time.sleep(5)  # Check every 5 seconds
        else:
            print(f"❌ Failed to check job status: {response.status_code}")
            break

    print("⏰ Monitoring timeout reached")
    return None

def main():
    """Run all API tests"""
    print("🧪 Testing Backtest API Endpoints")
    print("=" * 50)

    # Basic health check
    if not test_api_health():
        print("❌ API is not available. Make sure the API service is running on port 8000.")
        return

    # Test creating and managing jobs
    job_id = test_create_backtest()
    if job_id:
        test_job_status(job_id)
        test_start_job(job_id)

        # Monitor for a short time
        monitor_job(job_id, timeout=30)

    # Test listing jobs
    test_list_jobs()

    # Test quick backtest
    quick_job_id = test_quick_backtest()
    if quick_job_id:
        # Monitor quick job
        monitor_job(quick_job_id, timeout=45)

    # Test stats
    test_backtest_stats()

    print("\n🎉 API testing completed!")
    print("\nTo test manually:")
    print(f"  curl {BASE_URL}/backtest/stats")
    print(f"  curl -X POST {BASE_URL}/backtest/quick?symbols=AAPL&days=5&seed=42")

if __name__ == "__main__":
    main()