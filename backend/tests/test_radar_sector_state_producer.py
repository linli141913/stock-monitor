import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import tempfile

from radar.sector_history_backfill import (
    HistoricalValue,
    SectorHistoricalAnalysis,
)
from radar.sector_state_producer import (
    SECTOR_STATE_TRANSITION_POLICY_VERSION,
    SectorLifecycleState,
    SectorStateObservation,
    SectorStatePreviousSnapshot,
    SectorStateProductionStatus,
    build_initial_sector_state_snapshot,
    build_sector_state_observation_from_runtime,
    is_sector_evidence_candidate_state,
    load_sector_state_snapshot,
    produce_sector_state_from_prefrozen,
    produce_sector_state_snapshot,
    sector_threshold_record_sha256,
    write_sector_state_snapshot,
)
from radar.sector_threshold_review import SectorThresholdApprovalRecord


UTC = timezone.utc
AS_OF = datetime(2026, 8, 25, 15, 1, tzinfo=UTC)


def condition(metric, operator, value):
    return {"metric": metric, "operator": operator, "value": value}


def policy(entry, hold, exit_, *, consecutive=1, minimum_hold=0,
           cooldown=0, failure="stop_evaluation"):
    return {
        "entry": entry,
        "hold": hold,
        "exit": exit_,
        "consecutive_observations": consecutive,
        "minimum_hold_time": minimum_hold,
        "cooldown": cooldown,
        "data_failure_behavior": failure,
    }


def approval():
    policies = {
        "observe": policy(
            condition("relativeReturn", "gte", -0.001),
            condition("relativeReturn", "gte", -0.01),
            condition("relativeReturn", "lt", -0.01),
        ),
        "startup": policy(
            condition("turnoverRatio20d", "gte", 1.2),
            condition("turnoverRatio20d", "gte", 1.0),
            condition("turnoverRatio20d", "lt", 0.8),
            consecutive=2,
            minimum_hold=300,
            cooldown=300,
        ),
        "confirmed": policy(
            condition("persistencePositiveRatio5d", "gte", 0.6),
            condition("persistencePositiveRatio5d", "gte", 0.4),
            condition("persistencePositiveRatio5d", "lt", 0.4),
            consecutive=2,
            minimum_hold=300,
            cooldown=600,
            failure="degrade",
        ),
        "accelerating": policy(
            condition("relativeReturn", "gte", 0.006),
            condition("relativeReturn", "gte", -0.001),
            condition("relativeReturn", "lt", -0.009),
            consecutive=2,
            minimum_hold=300,
            cooldown=600,
            failure="degrade",
        ),
        "divergence": policy(
            condition("relativeReturn", "lt", -0.009),
            condition("relativeReturn", "lt", -0.001),
            condition("relativeReturn", "gte", -0.001),
            consecutive=2,
            minimum_hold=300,
            cooldown=300,
            failure="hold",
        ),
        "reflow": policy(
            condition("relativeReturn", "gte", 0.006),
            condition("relativeReturn", "gte", -0.001),
            condition("relativeReturn", "lt", -0.001),
            consecutive=2,
            minimum_hold=300,
            cooldown=600,
            failure="degrade",
        ),
        "retreat": policy(
            condition("turnoverRatio20d", "lt", 0.8),
            condition("turnoverRatio20d", "lt", 1.0),
            condition("turnoverRatio20d", "gte", 1.0),
            consecutive=2,
            minimum_hold=300,
            cooldown=900,
            failure="degrade",
        ),
        "invalid": policy(
            condition("turnoverRatio20d", "lt", 0.8),
            condition("turnoverRatio20d", "lt", 1.0),
            condition("turnoverRatio20d", "gte", 1.0),
            consecutive=2,
            cooldown=900,
            failure="invalidate",
        ),
    }
    base = SectorThresholdApprovalRecord(
        calibration_identity="a" * 64,
        source_evidence_sha256="b" * 64,
        rule_version="radar-sector-v0-shadow",
        threshold_set_id="threshold-set-1",
        approval_id="approval-1",
        approved_by="project-owner",
        approved_at=AS_OF - timedelta(days=1),
        state_policies=policies,
        record_sha256="placeholder",
    )
    # 生产者必须绑定 record_sha256；测试使用模块的同一规范化实现重建。
    from radar.sector_state_producer import sector_threshold_record_sha256
    return replace(
        base,
        record_sha256=sector_threshold_record_sha256(base),
    )


def history(code="01", dates=None):
    dates = dates or tuple(
        date(2026, 7, 27) + timedelta(days=index)
        for index in range(20)
    )
    return SectorHistoricalAnalysis(
        division_code=code,
        member_count=10,
        same_minute_turnover_samples=tuple(
            HistoricalValue(trade_date=value, value=100.0)
            for value in dates
        ),
        relative_return_samples=tuple(
            HistoricalValue(trade_date=value, value=0.002)
            for value in dates
        ),
        persistence_samples=(),
        turnover_ratio_samples=(),
        persistence_ratio_samples=(),
        latest_turnover_ratio_20d=None,
        latest_persistence_positive_ratio_5d=1.0,
        comparable_time=time(15, 0),
    )


def observation(*, turnover=130.0, sector_return_percent=1.0,
                market_return_percent=0.2, quote_batch_id="quotes-1",
                as_of=AS_OF):
    return SectorStateObservation(
        candidate_plan_id="plan-1",
        radar_run_id="run-1",
        quote_batch_id=quote_batch_id,
        classification_document_sha256="c" * 64,
        as_of=as_of,
        comparable_time=time(15, 0),
        sector_returns_percent_points={"01": sector_return_percent},
        sector_turnover_amount_cny={"01": turnover},
        market_equal_return_percent_points=market_return_percent,
        historical_analyses=(history(),),
        approval_record=approval(),
    )


class SectorStateProducerTests(unittest.TestCase):
    def initial(self):
        approved = approval()
        return build_initial_sector_state_snapshot(
            industry_codes=("01",),
            classification_document_sha256="c" * 64,
            rule_version="radar-sector-v0-shadow",
            transition_policy_version=(
                SECTOR_STATE_TRANSITION_POLICY_VERSION
            ),
            threshold_set_id=approved.threshold_set_id,
            approval_id=approved.approval_id,
            observed_before=AS_OF - timedelta(seconds=1),
        )

    def test_current_metrics_use_same_minute_history_and_percent_conversion(self):
        result = produce_sector_state_snapshot(
            observation(),
            previous_snapshot=self.initial(),
        )

        self.assertEqual(result.status, SectorStateProductionStatus.READY)
        item = result.items[0]
        self.assertEqual(item.metrics.turnover_ratio_20d, 1.3)
        self.assertAlmostEqual(item.metrics.relative_return, 0.008)
        self.assertEqual(item.metrics.persistence_positive_ratio_5d, 1.0)
        self.assertEqual(item.state, SectorLifecycleState.OBSERVE)
        self.assertTrue(item.evidence_candidate_eligible)

    def test_risk_transition_has_priority_and_only_one_step_per_observation(self):
        previous = self.initial()
        for index, minute in enumerate((0, 5, 10, 15, 20, 25, 30)):
            result = produce_sector_state_snapshot(
                observation(
                    quote_batch_id=f"quotes-up-{index}",
                    as_of=AS_OF + timedelta(minutes=minute),
                ),
                previous_snapshot=previous,
            )
            previous = result.next_snapshot
        self.assertEqual(
            previous.records[0].state,
            SectorLifecycleState.ACCELERATING,
        )
        for index, minute in enumerate((35, 40)):
            result = produce_sector_state_snapshot(
                observation(
                    turnover=70.0,
                    sector_return_percent=-1.0,
                    market_return_percent=0.0,
                    quote_batch_id=f"quotes-risk-{index}",
                    as_of=AS_OF + timedelta(minutes=minute),
                ),
                previous_snapshot=previous,
            )
            previous = result.next_snapshot

        item = result.items[0]
        self.assertEqual(item.state, SectorLifecycleState.RETREAT)
        self.assertNotEqual(item.state, SectorLifecycleState.INVALID)
        self.assertFalse(item.evidence_candidate_eligible)

    def test_duplicate_or_older_observation_is_rejected(self):
        first = produce_sector_state_snapshot(
            observation(),
            previous_snapshot=self.initial(),
        )
        duplicate = produce_sector_state_snapshot(
            observation(),
            previous_snapshot=first.next_snapshot,
        )

        self.assertEqual(
            duplicate.status,
            SectorStateProductionStatus.BLOCKED,
        )
        self.assertIn("sector_state_observation_not_increasing", duplicate.reasons)

    def test_invalid_state_cannot_reenter_observe_before_approved_cooldown(self):
        previous = self.initial()
        sequence = (
            (0, 130.0, 1.0),
            (5, 70.0, -1.0),
            (10, 70.0, -1.0),
            (15, 70.0, -1.0),
            (20, 70.0, -1.0),
        )
        for index, (minute, turnover, sector_return) in enumerate(sequence):
            produced = produce_sector_state_snapshot(
                observation(
                    turnover=turnover,
                    sector_return_percent=sector_return,
                    market_return_percent=0.0,
                    quote_batch_id=f"quotes-invalid-{index}",
                    as_of=AS_OF + timedelta(minutes=minute),
                ),
                previous_snapshot=previous,
            )
            previous = produced.next_snapshot
        self.assertEqual(
            previous.records[0].state,
            SectorLifecycleState.INVALID,
        )

        before_cooldown = produce_sector_state_snapshot(
            observation(
                quote_batch_id="quotes-reenter-early",
                as_of=AS_OF + timedelta(minutes=25),
            ),
            previous_snapshot=previous,
        )
        self.assertEqual(
            before_cooldown.items[0].state,
            SectorLifecycleState.INVALID,
        )
        after_cooldown = produce_sector_state_snapshot(
            observation(
                quote_batch_id="quotes-reenter-after-cooldown",
                as_of=AS_OF + timedelta(minutes=35),
            ),
            previous_snapshot=before_cooldown.next_snapshot,
        )
        self.assertEqual(
            after_cooldown.items[0].state,
            SectorLifecycleState.OBSERVE,
        )

    def test_malformed_approval_and_numeric_inputs_fail_closed(self):
        malformed_approval = replace(
            approval(),
            state_policies={"observe": {}},
        )
        malformed_approval = replace(
            malformed_approval,
            record_sha256=sector_threshold_record_sha256(
                malformed_approval
            ),
        )
        malformed_policy = produce_sector_state_snapshot(
            replace(
                observation(),
                approval_record=malformed_approval,
            ),
            previous_snapshot=self.initial(),
        )
        malformed_number = produce_sector_state_snapshot(
            replace(
                observation(),
                market_equal_return_percent_points="not-a-number",
            ),
            previous_snapshot=self.initial(),
        )
        naive_approval = replace(
            approval(),
            approved_at=approval().approved_at.replace(tzinfo=None),
        )
        naive_approval = replace(
            naive_approval,
            record_sha256=sector_threshold_record_sha256(naive_approval),
        )
        naive_approval_result = produce_sector_state_snapshot(
            replace(
                observation(),
                approval_record=naive_approval,
            ),
            previous_snapshot=self.initial(),
        )

        self.assertEqual(
            malformed_policy.status,
            SectorStateProductionStatus.BLOCKED,
        )
        self.assertEqual(
            malformed_number.status,
            SectorStateProductionStatus.BLOCKED,
        )
        self.assertEqual(
            naive_approval_result.status,
            SectorStateProductionStatus.BLOCKED,
        )

    def test_only_observe_startup_confirmed_are_new_candidate_eligible(self):
        eligible = {
            SectorLifecycleState.OBSERVE,
            SectorLifecycleState.STARTUP,
            SectorLifecycleState.CONFIRMED,
        }
        for state in SectorLifecycleState:
            with self.subTest(state=state):
                self.assertEqual(
                    is_sector_evidence_candidate_state(state),
                    state in eligible,
                )

    def test_forged_previous_snapshot_fails_closed(self):
        forged = SectorStatePreviousSnapshot(
            industry_codes=("01",),
            classification_document_sha256="c" * 64,
            rule_version="radar-sector-v0-shadow",
            transition_policy_version=(
                SECTOR_STATE_TRANSITION_POLICY_VERSION
            ),
            threshold_set_id=approval().threshold_set_id,
            approval_id=approval().approval_id,
            observed_at=AS_OF - timedelta(seconds=1),
            last_quote_batch_id=None,
            records=self.initial().records,
            snapshot_sha256="0" * 64,
        )
        result = produce_sector_state_snapshot(
            observation(),
            previous_snapshot=forged,
        )

        self.assertEqual(result.status, SectorStateProductionStatus.BLOCKED)
        self.assertIn("sector_state_previous_snapshot_unverified", result.reasons)

    def test_previous_state_from_another_threshold_approval_is_rejected(self):
        changed = replace(
            approval(),
            threshold_set_id="threshold-set-2",
            approval_id="approval-2",
        )
        changed = replace(
            changed,
            record_sha256=sector_threshold_record_sha256(changed),
        )
        result = produce_sector_state_snapshot(
            replace(observation(), approval_record=changed),
            previous_snapshot=self.initial(),
        )

        self.assertEqual(result.status, SectorStateProductionStatus.BLOCKED)
        self.assertIn("sector_state_previous_snapshot_unverified", result.reasons)

    def test_runtime_adapter_replays_ready_sector_bridge_and_exact_approval(self):
        from tests import test_radar_sector_rule_runtime_bridge as helpers

        helper = helpers.SectorRuleRuntimeBridgeTests(
            methodName="test_complete_versioned_evidence_returns_ready_contract"
        )
        helper.setUp()
        source_batch = helper.source_batch()
        from radar.leader_phase6_live_prefreeze import (
            resolve_sector_comparable_time,
        )
        runtime_comparable_time = resolve_sector_comparable_time(
            source_batch.feature_batch.source_time
        )
        code = helper.plan.items[0].industry_code
        coverage = next(
            item for item in source_batch.history_evidence.rows
            if item.division_code == code
        )
        approval_record = replace(
            approval(),
            threshold_set_id=(
                source_batch.threshold_approval_evidence.threshold_set_id
            ),
            approval_id=(
                source_batch.threshold_approval_evidence.approval_id
            ),
            approved_at=(
                source_batch.threshold_approval_evidence.approved_at
            ),
        )
        approval_record = replace(
            approval_record,
            record_sha256=sector_threshold_record_sha256(approval_record),
        )
        analysis = replace(history(
            code,
            dates=coverage.same_minute_trading_dates,
        ), comparable_time=runtime_comparable_time)

        observation_value, bridge = (
            build_sector_state_observation_from_runtime(
                helper.context,
                source_batch=source_batch,
                historical_analyses=(analysis,),
                approval_record=approval_record,
                comparable_time=runtime_comparable_time,
            )

        )

        self.assertEqual(
            observation_value.candidate_plan_id,
            helper.plan.candidate_set_id,
        )
        self.assertEqual(observation_value.quote_batch_id, helper.plan.quote_batch_id)
        self.assertEqual(
            tuple(observation_value.sector_returns_percent_points),
            (code,),
        )
        self.assertEqual(bridge.status.value, "ready")

        from radar.leader_formal_research_production_provider import (
            LeaderFormalResearchProductionSourceStatus,
        )
        from radar.leader_phase6_live_prefreeze import (
            LeaderPhase6PrefrozenInputs,
        )
        from radar.sector_rule_production_collector import (
            SectorRuleProductionFrozenBatch,
        )

        prefrozen = LeaderPhase6PrefrozenInputs(
            history=object(),
            sector_rule=SectorRuleProductionFrozenBatch(
                source_batch=source_batch,
                fetched_at=helper.plan.as_of,
                source_status=(
                    LeaderFormalResearchProductionSourceStatus.COMPLETED
                ),
            ),
            sector_historical_analyses=(analysis,),
            sector_comparable_time=runtime_comparable_time,
            threshold_approval_record=approval_record,
        )
        initial = build_initial_sector_state_snapshot(
            industry_codes=(code,),
            classification_document_sha256=(
                source_batch.feature_batch.classification_document_sha256
            ),
            rule_version=source_batch.rule_version,
            transition_policy_version=(
                SECTOR_STATE_TRANSITION_POLICY_VERSION
            ),
            threshold_set_id=approval_record.threshold_set_id,
            approval_id=approval_record.approval_id,
            observed_before=helper.plan.as_of - timedelta(seconds=1),
        )
        produced = produce_sector_state_from_prefrozen(
            helper.context,
            prefrozen_inputs=prefrozen,
            previous_snapshot=initial,
        )
        self.assertEqual(produced.status, SectorStateProductionStatus.READY)
        self.assertEqual(produced.sector_rule_bridge, bridge)

        with self.assertRaisesRegex(
            ValueError,
            "sector_state_runtime_input_unverified",
        ):
            build_sector_state_observation_from_runtime(
                helper.context,
                source_batch=source_batch,
                historical_analyses=(replace(
                    analysis,
                    comparable_time=time(14, 55),
                ),),
                approval_record=approval_record,
                comparable_time=runtime_comparable_time,
            )
        self.assertNotEqual(runtime_comparable_time, time(15, 0))
        with self.assertRaisesRegex(
            ValueError,
            "sector_state_runtime_input_unverified",
        ):
            build_sector_state_observation_from_runtime(
                helper.context,
                source_batch=source_batch,
                historical_analyses=(replace(
                    analysis,
                    comparable_time=time(15, 0),
                ),),
                approval_record=approval_record,
                comparable_time=time(15, 0),
            )

    def test_private_tmp_snapshot_round_trip_rejects_tamper_and_future(self):
        produced = produce_sector_state_snapshot(
            observation(),
            previous_snapshot=self.initial(),
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "sector-state.json"
            write_sector_state_snapshot(produced.next_snapshot, path)
            with self.assertRaisesRegex(
                FileExistsError,
                "sector-state.json",
            ):
                write_sector_state_snapshot(produced.next_snapshot, path)
            loaded = load_sector_state_snapshot(
                path,
                expected_industry_codes=("01",),
                classification_document_sha256="c" * 64,
                rule_version="radar-sector-v0-shadow",
                transition_policy_version=(
                    SECTOR_STATE_TRANSITION_POLICY_VERSION
                ),
                threshold_set_id=approval().threshold_set_id,
                approval_id=approval().approval_id,
                before_as_of=AS_OF + timedelta(seconds=1),
            )
            self.assertEqual(
                loaded.snapshot_sha256,
                produced.next_snapshot.snapshot_sha256,
            )
            with self.assertRaisesRegex(
                ValueError,
                "sector_state_snapshot_unverified",
            ):
                load_sector_state_snapshot(
                    path,
                    expected_industry_codes=("01",),
                    classification_document_sha256="c" * 64,
                    rule_version="radar-sector-v0-shadow",
                    transition_policy_version=(
                        SECTOR_STATE_TRANSITION_POLICY_VERSION
                    ),
                    threshold_set_id=approval().threshold_set_id,
                    approval_id=approval().approval_id,
                    before_as_of=AS_OF,
                )

            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["records"][0]["state"] = "confirmed"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "sector_state_snapshot_unverified",
            ):
                load_sector_state_snapshot(
                    path,
                    expected_industry_codes=("01",),
                    classification_document_sha256="c" * 64,
                    rule_version="radar-sector-v0-shadow",
                    transition_policy_version=(
                        SECTOR_STATE_TRANSITION_POLICY_VERSION
                    ),
                    threshold_set_id=approval().threshold_set_id,
                    approval_id=approval().approval_id,
                    before_as_of=AS_OF + timedelta(seconds=1),
                )

        with self.assertRaisesRegex(
            ValueError,
            "sector_state_snapshot_path_unverified",
        ):
            write_sector_state_snapshot(
                produced.next_snapshot,
                Path("/tmp/not-private-tmp-sector-state.json"),
            )


if __name__ == "__main__":
    unittest.main()
