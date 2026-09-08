import unittest
from datetime import timedelta

from tests.test_radar_etf_formal_admission import (
    AS_OF,
    constituents,
    exposure,
    lifecycle,
    methodology,
    product,
    ranking_input,
    relation,
    scope,
)


class EtfFormalAdmissionCollectorTests(unittest.TestCase):
    def test_nav_provider_support_is_explicitly_limited_by_manager(self):
        from radar.etf_formal_admission_collector import _manager_nav_supported

        self.assertTrue(_manager_nav_supported(
            product().model_copy(update={"manager": "华夏基金管理有限公司"})
        ))
        self.assertTrue(_manager_nav_supported(
            product().model_copy(update={"manager": "华泰柏瑞基金管理有限公司"})
        ))
        self.assertFalse(_manager_nav_supported(
            product().model_copy(update={"manager": "未知基金管理有限公司"})
        ))

    def test_finalizer_rebinds_collected_current_evidence_to_forward_sample(self):
        from radar.etf_formal_admission_collector import (
            CollectedEtfFormalAdmissionMaterial,
            EtfFormalAdmissionMaterialCollection,
            finalize_etf_formal_admission_materials,
        )

        materials = EtfFormalAdmissionMaterialCollection(
            material_as_of=AS_OF,
            items=(CollectedEtfFormalAdmissionMaterial(
                symbol="159915",
                product=product(),
                lifecycle_evidence=lifecycle(),
                industry_scope_evidence=scope(),
                index_relation_evidence=relation(),
                methodology_evidence=methodology(),
                constituent_evidence=constituents(),
                industry_exposure_evidence=exposure(),
                ranking_input_evidence=ranking_input(),
            ),),
        )
        sample_as_of = AS_OF + timedelta(minutes=1)

        bundle = finalize_etf_formal_admission_materials(
            materials=materials,
            sample_id="forward-development-1",
            radar_run_id="run-1",
            as_of=sample_as_of,
        )

        admission = bundle.admissions[0]
        self.assertEqual(bundle.as_of, sample_as_of)
        self.assertEqual(admission.as_of, sample_as_of)
        self.assertEqual(admission.monitoring_status.value, "ready")
        self.assertEqual(admission.ranking_status.value, "missing")
        evidence = admission.to_evidence()
        self.assertEqual(
            [
                reason
                for item in evidence["items"][:-1]
                for reason in item["reasons"]
            ],
            [],
        )
        self.assertEqual(
            evidence["items"][-1]["reasons"],
            ["etf_rule_not_frozen", "ranking_calibration_sample_missing"],
        )

    def test_finalizer_rejects_sample_before_material_collection_completed(self):
        from radar.etf_formal_admission_collector import (
            CollectedEtfFormalAdmissionMaterial,
            EtfFormalAdmissionMaterialCollection,
            finalize_etf_formal_admission_materials,
        )

        materials = EtfFormalAdmissionMaterialCollection(
            material_as_of=AS_OF,
            items=(CollectedEtfFormalAdmissionMaterial(
                symbol="159915",
                product=product(),
            ),),
        )

        with self.assertRaisesRegex(
            ValueError,
            "etf_formal_material_from_future",
        ):
            finalize_etf_formal_admission_materials(
                materials=materials,
                sample_id="forward-development-1",
                radar_run_id="run-1",
                as_of=AS_OF - timedelta(seconds=1),
            )


if __name__ == "__main__":
    unittest.main()
