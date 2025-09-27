import os, sys, os.path, pytest
from datetime import timezone
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from lib.time_utils import TimeUtils

def test_parse_timestamp_z_suffix():
    ts = "2025-09-26T22:28:47.389531Z"
    dt = TimeUtils.parse_timestamp(ts)
    assert dt.tzinfo is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2025 and dt.month == 9 and dt.day == 26
