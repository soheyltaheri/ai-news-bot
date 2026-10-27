# tests/test_smart_queue.py
import pytest
import math
from main import get_smart_delay

def test_get_smart_delay_normal():
    # When queue is below cap, delay should be exactly default_interval_minutes
    delay = get_smart_delay(2, 10, queue_cap=45, resume_threshold=20)
    assert delay == 2

def test_get_smart_delay_capped():
    # When queue is at or above cap, delay should be scaled down using logarithmic formula
    default_interval = 10
    delay = get_smart_delay(default_interval, 45, queue_cap=45, resume_threshold=20)
    
    # Calculate expected limits
    scale = min(1.0, math.log2(max(2, default_interval) / 2) / math.log2(15))
    min_mult = 0.70 - 0.35 * scale
    max_mult = 0.90 - 0.30 * scale
    
    expected_min = default_interval * min_mult
    expected_max = default_interval * max_mult
    
    assert expected_min <= delay <= expected_max
    assert delay >= 1.0
