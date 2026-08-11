# -*- coding: utf-8 -*-
"""The bridge must fail closed without deleting first-module output."""

from system.model.slurry_control.condition_model.online_condition_policy_bridge import (
    SlurryPolicyOnlineBridge,
)


def broken_factory(config_spec):
    del config_spec
    raise RuntimeError("active model unavailable")


def main():
    bridge = SlurryPolicyOnlineBridge(
        {
            "enabled": True,
            "initialize_on_start": True,
            "failure_mode": "BLOCKED_OUTPUT",
            "output_prefix": "slurry_policy_",
        },
        policy_factory=broken_factory,
    )
    first_output = {
        "date": "2026-08-03 15:00:00",
        "jzfh": 350.0,
        "jyq_SO2": 20.0,
        "condition_snapshot_version": "v001",
        "condition_label": "10",
        "condition_valid": True,
        "condition_stable": True,
        "raw_grid_id": "P1-S1",
        "original_field": "KEEP",
    }
    final = bridge.process(first_output, target=20.0)
    assert final["original_field"] == "KEEP"
    assert final["condition_label"] == "10"
    assert final["slurry_policy_decision_status"] == "BLOCKED"
    assert final["slurry_policy_action_family"] == "HOLD"
    assert final["slurry_policy_integration_valid"] is False
    assert "active model unavailable" in final["slurry_policy_integration_error"]
    print("POLICY_BRIDGE_FAILURE_SAFE_TEST_PASSED")


if __name__ == "__main__":
    main()
