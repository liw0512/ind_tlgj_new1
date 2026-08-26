import unittest

from system.model.map_control.mfac_model.context_resolver import (
    MFACContextResolver,
)


class MFACContextResolverTest(unittest.TestCase):
    def test_default_consumes_final_condition_label(self):
        resolver = MFACContextResolver("v012")
        result = resolver.resolve(
            {
                "condition_snapshot_version": "v012",
                "condition_label": "8",
                "base_condition_id": "12",
                "grid_id": "P3-S2",
                "policy_region_id": "R_0008",
            }
        )

        self.assertEqual(result.mfac_context_id, "MFAC-COND-8")
        self.assertEqual(result.resolution_source, "CONDITION_DEFAULT")
        self.assertEqual(result.base_condition_id, "12")

    def test_base_override_keeps_mfac_contexts_split_inside_one_condition(self):
        resolver = MFACContextResolver(
            "v012",
            base_condition_overrides={
                "12": "MFAC-BASE-12",
                "13": "MFAC-BASE-13",
            },
        )

        first = resolver.resolve(
            {
                "condition_snapshot_version": "v012",
                "condition_label": "8",
                "base_condition_id": "12",
            }
        )
        second = resolver.resolve(
            {
                "condition_snapshot_version": "v012",
                "condition_label": "8",
                "base_condition_id": "13",
            }
        )

        self.assertEqual(first.mfac_context_id, "MFAC-BASE-12")
        self.assertEqual(second.mfac_context_id, "MFAC-BASE-13")
        self.assertEqual(first.resolution_source, "BASE_CONDITION_OVERRIDE")

    def test_rejects_snapshot_version_mismatch(self):
        resolver = MFACContextResolver("v012")
        with self.assertRaises(ValueError):
            resolver.resolve(
                {
                    "condition_snapshot_version": "v013",
                    "condition_label": "8",
                    "base_condition_id": "12",
                }
            )

    def test_artifact_round_trip(self):
        original = MFACContextResolver(
            "v012",
            condition_contexts={"8": "MFAC-MERGED-8"},
            base_condition_overrides={"12": "MFAC-BASE-12"},
        )
        restored = MFACContextResolver.from_artifact(original.to_artifact())

        self.assertEqual(restored.to_artifact(), original.to_artifact())


if __name__ == "__main__":
    unittest.main()
