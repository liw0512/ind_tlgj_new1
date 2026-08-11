from __future__ import annotations

import copy
import unittest

import pandas as pd

from slurry_policy_config import ONLINE_POLICY_CONFIG
from slurry_policy_online.disturbance_monitor import DisturbanceMonitor
from slurry_policy_online.target_control import TargetManager


class OnlineStabilityTest(unittest.TestCase):
    def test_fast_mode_hold_and_recovery(self):
        online = copy.deepcopy(ONLINE_POLICY_CONFIG)
        online["fast_mode"]["minimum_hold_minutes"] = 0.0
        online["fast_mode"]["exit_stable_cycles"] = 2
        online["fast_mode"]["recovery_hold_minutes"] = 1.0
        effective = {
            "trend_window_minutes": 1.0,
            "load_slow_rate": 1.0,
            "load_fast_rate": 3.0,
            "inlet_so2_slow_rate": 20.0,
            "inlet_so2_fast_rate": 60.0,
        }
        runtime = {}
        monitor = DisturbanceMonitor(effective, online, runtime)
        t0 = pd.Timestamp("2026-01-01 00:00:00")
        first = monitor.update(t0, 300.0, 1000.0, 20.0)
        self.assertEqual(first["control_mode"], "REGULAR")
        fast = monitor.update(t0 + pd.Timedelta(minutes=1), 300.0, 1200.0, 20.0)
        self.assertEqual(fast["control_mode"], "FAST_CHANGE")
        self.assertIn("FAST", fast["disturbance_mode"])

        # 新建平稳窗口，连续两次退出确认后进入恢复期。
        monitor.history.clear()
        a = monitor.update(t0 + pd.Timedelta(minutes=2), 300.0, 1200.0, 20.0)
        b = monitor.update(t0 + pd.Timedelta(minutes=3), 300.0, 1200.0, 20.0)
        self.assertEqual(a["control_mode"], "FAST_CHANGE")
        self.assertEqual(b["control_mode"], "FAST_RECOVERY")
        c = monitor.update(t0 + pd.Timedelta(minutes=4, seconds=1), 300.0, 1200.0, 20.0)
        self.assertEqual(c["control_mode"], "REGULAR")

    def test_target_ramp(self):
        online = copy.deepcopy(ONLINE_POLICY_CONFIG)
        online["so2_control"]["maximum_effective_target_change_per_minute"] = 1.0
        runtime = {}
        manager = TargetManager(online, runtime)
        t0 = pd.Timestamp("2026-01-01 00:00:00")
        commanded, effective, changed, hold = manager.resolve(20.0, t0)
        self.assertEqual((commanded, effective, changed, hold), (20.0, 20.0, False, False))
        commanded, effective, changed, hold = manager.resolve(15.0, t0 + pd.Timedelta(minutes=1))
        self.assertEqual(commanded, 15.0)
        self.assertAlmostEqual(effective, 19.0)
        self.assertTrue(changed)
        self.assertTrue(hold)
        manager.consume_hold_cycle()
        _commanded, effective2, changed2, _hold2 = manager.resolve(15.0, t0 + pd.Timedelta(minutes=2))
        self.assertAlmostEqual(effective2, 18.0)
        self.assertFalse(changed2)


if __name__ == "__main__":
    unittest.main()
