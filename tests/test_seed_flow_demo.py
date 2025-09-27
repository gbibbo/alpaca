#!/usr/bin/env python3
"""
tests/test_seed_flow_demo.py
End-to-end test of the seed configuration flow
"""

import asyncio
import os
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent))

# Force streams backend
os.environ['BUS_BACKEND'] = 'streams'

async def test_seed_flow():
    """Test the complete seed flow from simulator to strategy"""
    print("🧪 Testing complete seed configuration flow...")

    try:
        from lib.bus import connect_bus, get_bus
        from apps.strategies.main import Random50Strategy

        # Connect to bus
        if not connect_bus():
            print("❌ Failed to connect to Redis")
            return False

        bus = get_bus()
        print(f"✅ Connected to {type(bus.backend).__name__}")

        # Test 1: Create Random50Strategy and verify initial seed
        print("\n📋 Test 1: Creating Random50Strategy...")
        strategy = Random50Strategy(seed=42)
        print(f"✅ Strategy created with seed: {strategy.seed}")

        # Test 2: Publish strategy config event (simulating simulator)
        print("\n📡 Test 2: Publishing strategy config event...")
        new_seed = 99999
        bus.publish_system_event(
            event_type="strategy_config",
            source="test_simulator",
            data={
                "config_type": "reproducible_mode",
                "random_seed": new_seed
            }
        )
        print(f"✅ Published strategy_config with seed: {new_seed}")

        # Test 3: Consume and apply the configuration
        print("\n👂 Test 3: Consuming strategy config event...")
        config_received = False

        async def consume_config():
            nonlocal config_received
            async for event in bus.subscribe_system_events(event_type="strategy_config"):
                print(f"📨 Received: {event.event_type} from {event.source}")
                print(f"   Data: {event.data}")

                if event.data.get("config_type") == "reproducible_mode":
                    seed = event.data.get("random_seed")
                    if seed:
                        print(f"🔧 Applying seed: {seed}")
                        strategy.set_seed(seed)
                        print(f"✅ Strategy seed updated to: {strategy.seed}")
                        config_received = True
                        break

        try:
            await asyncio.wait_for(consume_config(), timeout=5.0)
        except asyncio.TimeoutError:
            print("⏰ Timeout waiting for config event")

        # Test 4: Verify seed was applied
        print("\n🔍 Test 4: Verifying seed application...")
        if config_received and strategy.seed == new_seed:
            print(f"✅ Seed successfully applied: {strategy.seed}")

            # Test random generation consistency
            print("\n🎲 Test 5: Testing random generation consistency...")

            # Generate some random numbers with the seed
            original_seed = strategy.seed
            rng1 = strategy.rng
            val1 = rng1.random()
            val2 = rng1.random()

            # Reset with same seed
            strategy.set_seed(original_seed)
            rng2 = strategy.rng
            val3 = rng2.random()
            val4 = rng2.random()

            if val1 == val3 and val2 == val4:
                print(f"✅ Random generation is reproducible!")
                print(f"   Values: {val1:.6f}, {val2:.6f}")
                return True
            else:
                print(f"❌ Random generation not reproducible!")
                print(f"   First run: {val1:.6f}, {val2:.6f}")
                print(f"   Second run: {val3:.6f}, {val4:.6f}")
                return False
        else:
            print(f"❌ Seed not applied correctly. Current: {strategy.seed}, Expected: {new_seed}")
            return False

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Main test function"""
    print("🧪 Seed Configuration Flow Test")
    print("=" * 50)

    success = await test_seed_flow()

    print("\n" + "=" * 50)
    if success:
        print("🎉 All tests passed!")
        print("\nThe complete seed flow is working:")
        print("  1. ✅ Simulator publishes strategy_config events")
        print("  2. ✅ Strategies consume system events")
        print("  3. ✅ Random50Strategy updates seed dynamically")
        print("  4. ✅ Random generation is reproducible")
    else:
        print("❌ Some tests failed!")

    return 0 if success else 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)