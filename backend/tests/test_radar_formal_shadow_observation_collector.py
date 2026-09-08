import copy
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pydantic import ValidationError
from radar.contracts import MarketFeatureSnapshot
from radar.market_research_state import (
    MarketResearchStatePolicy,
    produce_market_research_state,
)


UTC = timezone.utc
AS_OF = datetime(2026, 9, 4, 3, 9, 5, tzinfo=UTC)
FETCHED_AT = AS_OF + timedelta(seconds=2)
OBSERVED_AT = AS_OF + timedelta(seconds=3)


def json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def closed_gate():
    return {
        "formalScoreReady": False,
        "formalGateReady": False,
        "formalUsable": False,
        "stateTransitionAllowed": False,
    }


def sector_snapshot(run_id="run-1", *, industry_codes=("01", "02")):
    payload = {
        "contractId": "radar-sector-state-snapshot-v1",
        "industryCodes": list(industry_codes),
        "classificationDocumentSha256": "a" * 64,
        "ruleVersion": "radar-sector-v0-shadow",
        "transitionPolicyVersion": (
            "radar-sector-state-transition-conservative-v1"
        ),
        "thresholdSetId": "threshold-1",
        "approvalId": "approval-1",
        "observedAt": AS_OF.isoformat(),
        "lastQuoteBatchId": f"{run_id}-quotes",
        "records": [
            {
                "industryCode": code,
                "state": "observe",
                "stateSince": None,
                "pendingState": None,
                "pendingCount": 0,
                "cooldowns": {},
            }
            for code in industry_codes
        ],
    }
    payload["snapshotSha256"] = sha256(json_bytes(payload))
    return payload


def market_feature_snapshot(
    run_id="run-1",
    *,
    changes=(1.0, 0.5, 0.8, -0.1),
    advancers=70,
    decliners=25,
    flat=5,
):
    identities = (
        ("sse_composite", "000001", "sse", "sh000001", "上证指数"),
        ("szse_component", "399001", "szse", "sz399001", "深证成指"),
        ("chinext", "399006", "szse", "sz399006", "创业板指"),
        ("star50", "000688", "sse", "sh000688", "科创50"),
    )
    breadth_count = advancers + decliners + flat
    completeness = {
        "expectedCount": breadth_count,
        "returnedCount": breadth_count,
        "validCount": breadth_count,
        "rowCoverage": 1.0,
        "requiredFieldCoverage": {"change_percent": 1.0},
        "isComplete": True,
        "reasons": [],
    }
    return MarketFeatureSnapshot.model_validate({
        "radarRunId": run_id,
        "indexBatchId": f"{run_id}-indices",
        "quoteBatchId": f"{run_id}-quotes",
        "asOf": AS_OF.isoformat(),
        "sourceTime": AS_OF.isoformat(),
        "fetchedAt": AS_OF.isoformat(),
        "indices": [{
            "indexKey": key,
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "sourceSymbol": source_symbol,
            "sourceTime": AS_OF.isoformat(),
            "fetchedAt": AS_OF.isoformat(),
            "price": 1000 + index,
            "changePercent": changes[index],
            "source": "tencent_finance",
        } for index, (key, symbol, exchange, source_symbol, name)
            in enumerate(identities)],
        "indexCompleteness": {
            **completeness,
            "expectedCount": 4,
            "returnedCount": 4,
            "validCount": 4,
        },
        "breadth": {
            "advancers": advancers,
            "decliners": decliners,
            "flat": flat,
            "unavailable": 0,
            "completeness": completeness,
        },
        "turnover": {
            "rawValue": 12345.0,
            "contributingCount": breadth_count,
            "unitStatus": "verified",
            "formalUsable": True,
            "completeness": completeness,
            "reasons": [],
        },
        "excludedEtfCount": 10,
        "duplicateSymbols": [],
        "unknownSymbols": [],
    })


def trend_artifact(
    run_id="run-1",
    *,
    snapshot=None,
    research_policy=None,
):
    snapshot = snapshot or market_feature_snapshot(run_id)
    state = produce_market_research_state(
        snapshot,
        **({"policy": research_policy} if research_policy else {}),
    ).to_evidence()
    snapshot_payload = snapshot.model_dump(mode="json", by_alias=True)
    return {
        "prepared": {
            "contractId": (
                "radar-leader-phase6-prepared-historical-inputs-v1"
            ),
            "radarRunId": run_id,
            "classificationDocumentSha256": "a" * 64,
        },
        "tradability": {
            "contractId": "radar-leader-tradability-live-acceptance-v1",
            "status": "completed",
            "radarRunId": run_id,
            "asOf": AS_OF.isoformat(),
            "gate": closed_gate(),
        },
        "marketResearchState": state,
        "marketFeatureSnapshotEvidence": {
            "contractId": "radar-market-feature-snapshot-evidence-v1",
            "snapshotSha256": sha256(
                canonical_json_bytes(snapshot_payload)
            ),
            "snapshot": snapshot_payload,
        },
        "sectorStateSnapshotPath": "/private/tmp/sector-state.json",
        "gate": closed_gate(),
    }


def leader_artifact(run_id="run-1"):
    parent_plan_id = "parent-plan"
    qualified_plan_id = "qualified-plan"
    preliminary_plan_id = "preliminary-plan"
    industry_scope_id = "industry-scope-snapshot"
    qualification_id = "c" * 64
    parent_items = [
        {
            "index": 0,
            "symbol": "000001",
            "industryCode": "01",
            "parentIndex": 0,
            "selectionKind": "new_candidate",
            "researchPartialScore": 1.0,
            "previousState": None,
        },
        {
            "index": 1,
            "symbol": "000002",
            "industryCode": "02",
            "parentIndex": 1,
            "selectionKind": "new_candidate",
            "researchPartialScore": 0.5,
            "previousState": None,
        },
    ]
    review_items = [
        {
            "index": 0,
            "symbol": "000001",
            "industryCode": "01",
            "qualificationStatus": "qualified",
            "qualifiedCandidateIndex": 0,
            "reviewEligible": True,
            "firstRejectionReason": "formal_rule_not_ready",
            "reasons": ["formal_rule_not_ready"],
        },
        {
            "index": 1,
            "symbol": "000002",
            "industryCode": "02",
            "qualificationStatus": "excluded",
            "qualifiedCandidateIndex": None,
            "reviewEligible": False,
            "firstRejectionReason": "business_missing",
            "reasons": ["business_missing"],
        },
    ]
    qualified_business = {
        "contractId": "radar-leader-business-automatic-evidence-v1",
        "status": "ready",
        "candidatePlanId": qualified_plan_id,
        "candidateCount": 1,
        "readyCount": 1,
        "missingCount": 0,
        "sourceFailedCount": 0,
        "sourceUnverifiedCount": 0,
        "reusedCount": 0,
        "reasons": [],
        "packetPath": None,
        "deliveryPacketPath": None,
        "productionFrozenInputsReady": True,
        "gate": closed_gate(),
    }
    verification_business = {
        "contractId": "radar-leader-business-automatic-evidence-v1",
        "status": "missing",
        "candidatePlanId": parent_plan_id,
        "candidateCount": 2,
        "readyCount": 1,
        "missingCount": 1,
        "sourceFailedCount": 0,
        "sourceUnverifiedCount": 0,
        "reusedCount": 0,
        "reasons": ["business_missing"],
        "packetPath": "/private/tmp/verification-business.json",
        "deliveryPacketPath": None,
        "productionFrozenInputsReady": False,
        "gate": closed_gate(),
    }
    material_acceptance = {
        "contractId": "radar-leader-business-material-live-acceptance-v1",
        "status": "completed",
        "radarRunId": run_id,
        "asOf": AS_OF.isoformat(),
        "candidatePlanId": qualified_plan_id,
        "candidateCount": 1,
        "issuerStatus": "ready",
        "queueStatus": "pending_review",
        "reviewSummary": {
            "pendingReview": 1,
            "missing": 0,
            "sourceFailed": 0,
            "sourceUnverified": 0,
        },
        "reasons": [],
        "gate": closed_gate(),
    }
    verification_material = copy.deepcopy(material_acceptance)
    verification_material.update({
        "candidatePlanId": parent_plan_id,
        "candidateCount": 2,
        "reviewSummary": {
            "pendingReview": 2,
            "missing": 0,
            "sourceFailed": 0,
            "sourceUnverified": 0,
        },
    })
    readiness = {
        "contractId": "radar-leader-phase6-live-source-readiness-v1",
        "status": "ready_for_review",
        "radarRunId": run_id,
        "candidatePlanId": qualified_plan_id,
        "asOf": AS_OF.isoformat(),
        "candidateCount": 1,
        "validEmptyResult": False,
        "reasons": [],
        "readiness": {
            "contractId": "radar-leader-phase6-production-readiness-v1",
            "assembly": {
                "contractId": (
                    "radar-leader-formal-research-runtime-assembly-v1"
                ),
                "status": "ready",
                "radarRunId": run_id,
                "candidatePlanId": qualified_plan_id,
                "candidateCount": 1,
                "reasons": [],
                "healthReasons": [],
                "components": [
                    {
                        "name": name,
                        "status": "ready",
                        "candidateCount": 1,
                        "readyCount": 1,
                        "reasons": [],
                    }
                    for name in (
                        "sector_rule", "history", "business_catalyst",
                        "tradability", "risk",
                    )
                ],
                "gate": closed_gate(),
            },
            "acceptance": {
                "contractId": (
                    "radar-leader-formal-research-production-acceptance-v1"
                ),
                "status": "ready_for_review",
                "candidateCount": 1,
                "missingComponents": [],
                "reasons": [],
                "componentStatuses": [
                    {"name": name, "status": "ready"}
                    for name in (
                        "sector_rule", "history", "business_catalyst",
                        "tradability", "risk",
                    )
                ],
                "gate": closed_gate(),
            },
            "gate": closed_gate(),
        },
        "gate": closed_gate(),
    }
    industry_gate = {
        "contractId": "radar-leader-formal-industry-gate-evidence-v1",
        "status": "ready",
        "radarRunId": run_id,
        "asOf": AS_OF.isoformat(),
        "parentCandidatePlanId": parent_plan_id,
        "candidatePlanId": qualified_plan_id,
        "candidateCount": 1,
        "industryScopeSnapshotId": industry_scope_id,
        "evidenceReadyCount": 1,
        "policyAudit": {
            "contractId": (
                "radar-leader-formal-industry-gate-policy-audit-v1"
            ),
            "status": "unapproved",
            "qualitativeRequirements": ["industry_state_active"],
            "requirementSourceReferences": ["approved-plan-reference"],
            "requiredPolicyFields": ["approval_identity_and_time"],
            "observedApprovalContractId": None,
            "observedApprovalId": None,
            "reasons": ["leader_formal_industry_gate_policy_unapproved"],
            "approved": False,
        },
        "items": [{
            "index": 0,
            "symbol": "000001",
            "industryCode": "01",
            "industryState": "observe",
            "sectorRuleEvidenceReady": True,
            "crossSectionEvidenceReady": True,
            "evidenceReady": True,
            "reasons": ["leader_formal_industry_gate_policy_unapproved"],
            "formalGatePassed": False,
        }],
        "reasons": ["leader_formal_industry_gate_policy_unapproved"],
        "gate": closed_gate(),
    }
    risk_delivery = {
        "contractId": "radar-leader-risk-lifecycle-delivery-v1",
        "status": "missing",
        "realPocStatus": "partial",
        "candidatePlanId": qualified_plan_id,
        "radarRunId": run_id,
        "quoteBatchId": f"{run_id}-quotes",
        "asOf": AS_OF.isoformat(),
        "windowFrom": "2024-01-01",
        "windowUntil": "2026-09-04",
        "requestCount": 7,
        "fetchedPageCount": 7,
        "categoryCount": 7,
        "candidateScopeCount": 1,
        "shardCount": 1,
        "reasons": ["risk_lifecycle_batch_no_ready_items"],
        "lifecycleStatus": "missing",
        "projectionStatus": "missing",
        "readyProjectionCount": 0,
        "gate": closed_gate(),
    }
    return {
        "evidenceCandidateSelection": {
            "contractId": "radar-leader-evidence-candidate-acceptance-v1",
            "status": "ready",
            "reasons": [],
            "preliminaryCandidatePlanId": preliminary_plan_id,
            "preliminaryCandidateCount": 2,
            "candidatePlanId": parent_plan_id,
            "candidateCount": 2,
            "industryScopeSnapshotId": industry_scope_id,
            "previousStateSnapshotId": "previous-state-snapshot",
            "evidencePlan": {
                "contractId": "radar-leader-evidence-candidate-plan-v1",
                "status": "ready",
                "preliminaryCandidatePlanId": preliminary_plan_id,
                "preliminaryCandidateCount": 2,
                "candidatePlanId": parent_plan_id,
                "candidateCount": 2,
                "selectionPolicyId": "selection-policy-v1",
                "items": parent_items,
                "reasons": [],
                "gate": closed_gate(),
            },
            "gate": closed_gate(),
        },
        "collection": {
            "contractId": "radar-leader-phase6-live-source-collection-v1",
            "status": "ready_for_review",
            "validEmptyResult": False,
            "candidateSourcePacketSha256": "d" * 64,
            "reasons": [],
            "qualification": {
                "contractId": "radar-leader-evidence-qualification-v1",
                "status": "ready",
                "qualificationId": qualification_id,
                "parentCandidatePlanId": parent_plan_id,
                "parentCandidateCount": 2,
                "qualifiedCandidatePlanId": qualified_plan_id,
                "qualifiedCandidateCount": 1,
                "excludedCandidateCount": 1,
                "excludedItems": [{
                    "index": 1,
                    "symbol": "000002",
                    "status": "missing",
                    "reasons": ["business_missing"],
                }],
                "reasons": [],
                "businessAutomatic": copy.deepcopy(qualified_business),
                "gate": closed_gate(),
            },
            "verificationCandidateSourcePacketSha256": "e" * 64,
            "stateDecisionReview": {
                "contractId": (
                    "radar-leader-phase6-state-decision-review-v1"
                ),
                "status": "ready_for_review",
                "radarRunId": run_id,
                "asOf": AS_OF.isoformat(),
                "parentCandidatePlanId": parent_plan_id,
                "parentCandidateCount": 2,
                "qualifiedCandidatePlanId": qualified_plan_id,
                "qualifiedCandidateCount": 1,
                "excludedCandidateCount": 1,
                "qualificationId": qualification_id,
                "industryGateEvidence": industry_gate,
                "reasons": [],
                "items": review_items,
                "gate": closed_gate(),
            },
            "verificationMaterialAcceptance": verification_material,
            "verificationBusinessAutomatic": verification_business,
            "materialAcceptance": material_acceptance,
            "businessAutomatic": qualified_business,
            "riskDelivery": risk_delivery,
            "readiness": readiness,
            "gate": closed_gate(),
        },
        "gate": closed_gate(),
    }


def etf_artifact(run_id="run-1", *, status="ready", symbol="515050"):
    records = [{
        "symbol": symbol,
        "sourceTime": AS_OF.isoformat(),
        "fetchedAt": FETCHED_AT.isoformat(),
        "lastPrice": 1.25,
        "changePercent": 0.0,
        "turnoverAmountCny": 0.0,
        "quoteSourceContractId": "tencent-quote-snapshot-v1",
        "status": "ready",
    }]
    return {
        "contractId": "radar-etf-live-shadow-observation-v1",
        "status": status,
        "runId": run_id,
        "asOf": FETCHED_AT.isoformat(),
        "sourceTime": AS_OF.isoformat(),
        "fetchedAt": FETCHED_AT.isoformat(),
        "requestedCount": 1,
        "returnedCount": 1,
        "missingCount": 0,
        "failedCount": 0,
        "staleCount": 0,
        "lockState": "acquired",
        "durationMs": 0,
        "sourceHealthStatus": "healthy",
        "sourceHealthReasons": [],
        "requestedSymbols": [symbol],
        "missingSymbols": [],
        "failedSymbols": [],
        "staleSymbols": [],
        "records": records,
    }


def etf_admission_artifact(
    run_id="run-1",
    *,
    symbol="515050",
    item_reasons=None,
):
    keys = (
        "product_identity",
        "product_lifecycle",
        "industry_scope",
        "index_relation",
        "index_methodology",
        "index_constituents",
        "industry_exposure",
        "ranking_inputs",
        "rule_policy",
    )
    item_reasons = item_reasons or {}
    items = [
        {
            "key": key,
            "status": "missing" if item_reasons.get(key) else "ready",
            "reasons": list(item_reasons.get(key, ())),
        }
        for key in keys
    ]
    reasons = list(dict.fromkeys(
        reason
        for item in items
        for reason in item["reasons"]
    ))
    admission = {
        "contractId": "radar-etf-formal-admission-evidence-v1",
        "symbol": symbol,
        "asOf": FETCHED_AT.isoformat(),
        "ruleVersion": "radar-etf-rule-v1",
        "status": "missing" if reasons else "ready",
        "monitoringStatus": (
            "missing" if any(item["reasons"] for item in items[:-1])
            else "ready"
        ),
        "rankingStatus": items[-1]["status"],
        "reasons": reasons,
        "items": items,
    }
    semantic = {
        "contractId": "radar-etf-formal-admission-bundle-v1",
        "sampleId": "sample-1",
        "radarRunId": run_id,
        "asOf": FETCHED_AT.isoformat(),
        "admissions": [admission],
    }
    return {**semantic, "snapshotSha256": sha256(json_bytes(semantic))}


def refresh_admission_sha(bundle):
    semantic = {
        key: value for key, value in bundle.items()
        if key != "snapshotSha256"
    }
    bundle["snapshotSha256"] = sha256(json_bytes(semantic))
    return bundle


def receipt(module, source_raw, **changes):
    source_contracts = {
        "trendRotation": "radar-market-research-state-v1",
        "leaderObservation": (
            "radar-leader-phase6-live-source-collection-v1"
        ),
        "etfObservation": "radar-etf-live-shadow-observation-v1",
    }
    scopes = {
        "trendRotation": "stage6_market_and_sector_state",
        "leaderObservation": "stage6_parent_candidate_partition",
        "etfObservation": "live_requested_etf_quotes",
    }
    as_of = FETCHED_AT if module == "etfObservation" else AS_OF
    fetched_at = FETCHED_AT if module == "etfObservation" else AS_OF
    expected = 2 if module != "etfObservation" else 1
    payload = {
        "contractId": "radar-formal-shadow-run-receipt-v1",
        "module": module,
        "runId": "run-1",
        "asOf": as_of.isoformat(),
        "sourceTime": AS_OF.isoformat(),
        "fetchedAt": fetched_at.isoformat(),
        "observedAt": OBSERVED_AT.isoformat(),
        "sourceContractId": source_contracts[module],
        "sourceArtifactSha256": sha256(source_raw),
        "coverageScope": scopes[module],
        "expectedCount": expected,
        "observedCount": expected,
        "missingCount": 0,
        "failedCount": 0,
        "staleCount": 0,
        "coverage": 1.0,
        "lockState": "acquired",
        "durationMs": 0,
        "sourceReady": True,
    }
    if module == "etfObservation":
        payload.update({
            "sourceHealthStatus": "healthy",
            "sourceHealthReasons": [],
        })
    payload.update(changes)
    return payload


def collection_policy(**changes):
    payload = {
        "contractId": "radar-formal-shadow-collection-policy-v1",
        "policyId": "stage10-shadow-collection-policy-v1",
        "policySha256": (
            "d8e02f3ec075ed8c82a195ccd7f93ec80760e0a83cb98a3b709f9eaf182640a4"
        ),
        "receiptContractId": "radar-formal-shadow-run-receipt-v1",
        "maximumSourceAgeSecondsByModule": {
            "trendRotation": 90,
            "etfObservation": 90,
            "leaderObservation": 90,
        },
        "maximumCollectionDelaySecondsByModule": {
            "trendRotation": 300,
            "etfObservation": 300,
            "leaderObservation": 300,
        },
    }
    payload.update(changes)
    return payload


def policy_bytes(**changes):
    return json_bytes(collection_policy(**changes))


class FormalShadowRunReceiptTests(unittest.TestCase):
    def test_receipt_is_frozen_extra_forbid_and_strict(self):
        from radar.formal_shadow_observation_collector import (
            FormalShadowRunReceipt,
        )

        raw = json_bytes(trend_artifact())
        parsed = FormalShadowRunReceipt.model_validate(
            receipt("trendRotation", raw)
        )
        with self.assertRaises(ValidationError):
            parsed.source_ready = False
        for field, value in (
            ("sourceReady", "true"),
            ("expectedCount", True),
            ("durationMs", False),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                FormalShadowRunReceipt.model_validate(
                    receipt("trendRotation", raw, **{field: value})
                )
        with self.assertRaises(ValidationError):
            FormalShadowRunReceipt.model_validate({
                **receipt("trendRotation", raw),
                "unexpected": 1,
            })

    def test_receipt_rejects_wrong_contract_scope_coverage_and_time(self):
        from radar.formal_shadow_observation_collector import (
            FormalShadowRunReceipt,
        )

        raw = json_bytes(trend_artifact())
        invalid = (
            {"contractId": "radar-formal-shadow-run-receipt-v0"},
            {"sourceContractId": "wrong-contract"},
            {"coverageScope": "all"},
            {"expectedCount": 0, "observedCount": 0, "coverage": 0.0},
            {"observedCount": 1, "missingCount": 1, "coverage": 1.0},
            {"fetchedAt": (AS_OF - timedelta(seconds=1)).isoformat()},
            {"fetchedAt": (AS_OF + timedelta(seconds=1)).isoformat()},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                FormalShadowRunReceipt.model_validate(
                    receipt("trendRotation", raw, **changes)
                )

    def test_zero_coverage_is_valid_missing_receipt_but_never_ready(self):
        from radar.formal_shadow_observation_collector import (
            FormalShadowRunReceipt,
        )

        raw = json_bytes(etf_artifact())
        parsed = FormalShadowRunReceipt.model_validate(receipt(
            "etfObservation",
            raw,
            observedCount=0,
            missingCount=1,
            coverage=0.0,
            sourceReady=False,
        ))
        self.assertEqual(parsed.coverage, 0.0)
        self.assertFalse(parsed.source_ready)

    def test_receipt_accepts_exact_runtime_ratio_for_nonterminating_fraction(self):
        from radar.formal_shadow_observation_collector import (
            FormalShadowRunReceipt,
        )

        raw = json_bytes(etf_artifact())
        parsed = FormalShadowRunReceipt.model_validate(receipt(
            "etfObservation",
            raw,
            expectedCount=3,
            observedCount=1,
            missingCount=2,
            coverage=1 / 3,
            sourceReady=False,
        ))

        self.assertEqual(parsed.coverage, 1 / 3)

    def test_receipt_rejects_ordered_times_crossing_shanghai_natural_day(self):
        from radar.formal_shadow_observation_collector import (
            FormalShadowRunReceipt,
        )

        raw = json_bytes(trend_artifact())
        payload = receipt(
            "trendRotation",
            raw,
            sourceTime="2026-09-03T15:59:59+00:00",
            fetchedAt="2026-09-03T16:00:00+00:00",
            asOf="2026-09-03T16:00:01+00:00",
            observedAt="2026-09-03T16:00:02+00:00",
        )
        with self.assertRaisesRegex(ValidationError, "same_shanghai_day"):
            FormalShadowRunReceipt.model_validate(payload)

    def test_collection_policy_is_external_frozen_extra_forbid_and_strict(self):
        from radar.formal_shadow_observation_collector import (
            FormalShadowCollectionPolicy,
        )

        parsed = FormalShadowCollectionPolicy.model_validate(
            collection_policy()
        )
        with self.assertRaises(TypeError):
            parsed.maximum_source_age_seconds_by_module[
                "trendRotation"
            ] = 999
        for changed in (
            {"unexpected": True},
            {"contractId": "radar-formal-shadow-collection-policy-v0"},
            {"maximumSourceAgeSecondsByModule": {
                "trendRotation": True,
                "etfObservation": 90,
                "leaderObservation": 90,
            }},
            {"maximumCollectionDelaySecondsByModule": {
                "trendRotation": 30,
                "etfObservation": 30,
            }},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValidationError):
                FormalShadowCollectionPolicy.model_validate(
                    collection_policy(**changed)
                )

    def test_collection_policy_cannot_be_copied_or_serialized_to_relax_limits(self):
        from radar.formal_shadow_observation_collector import (
            FormalShadowCollectionPolicy,
            adapt_trend_rotation_observation,
        )

        source_raw = json_bytes(trend_artifact())
        sector_raw = json_bytes(sector_snapshot())
        permissive = collection_policy(
            maximumSourceAgeSecondsByModule={
                key: 999999 for key in (
                    "trendRotation", "leaderObservation", "etfObservation"
                )
            },
            maximumCollectionDelaySecondsByModule={
                key: 999999 for key in (
                    "trendRotation", "leaderObservation", "etfObservation"
                )
            },
        )
        old_receipt = receipt(
            "trendRotation",
            source_raw,
            sourceTime=(AS_OF - timedelta(hours=3)).isoformat(),
        )
        cases = (
            (old_receipt, collection_policy(), OBSERVED_AT,
             "formal_shadow_source_expired"),
            (receipt("trendRotation", source_raw), collection_policy(),
             OBSERVED_AT + timedelta(hours=10),
             "formal_shadow_receipt_expired"),
            (receipt("trendRotation", source_raw), permissive, OBSERVED_AT,
             "formal_shadow_collection_policy_invalid"),
            (receipt("trendRotation", source_raw), collection_policy(
                policyId="unapproved-policy",
            ), OBSERVED_AT, "formal_shadow_collection_policy_invalid"),
            (receipt("trendRotation", source_raw), collection_policy(
                policySha256="0" * 64,
            ), OBSERVED_AT, "formal_shadow_collection_policy_invalid"),
        )
        for receipt_value, policy_value, evaluated_at, reason in cases:
            with self.subTest(policy=policy_value), self.assertRaisesRegex(
                ValueError,
                reason,
            ):
                adapt_trend_rotation_observation(
                    json_bytes(receipt_value),
                    source_raw,
                    sector_raw,
                    evaluated_at=evaluated_at,
                    collection_policy_json_bytes=json_bytes(policy_value),
                )

        parsed = FormalShadowCollectionPolicy.model_validate(
            collection_policy()
        )
        with self.assertRaises(ValidationError):
            parsed.model_copy(update={
                "maximum_source_age_seconds_by_module": {
                    key: 999999 for key in (
                        "trendRotation", "leaderObservation", "etfObservation"
                    )
                },
            })


class FormalShadowObservationAdapterTests(unittest.TestCase):
    def test_external_policy_is_required_and_receipt_cannot_relax_freshness(self):
        from radar.formal_shadow_observation_collector import (
            FormalShadowRunReceipt,
            adapt_trend_rotation_observation,
        )

        stage6_raw = json_bytes(trend_artifact())
        sector_raw = json_bytes(sector_snapshot())
        normal_receipt = receipt("trendRotation", stage6_raw)
        with self.assertRaises(ValidationError):
            FormalShadowRunReceipt.model_validate({
                **normal_receipt,
                "maxSourceAgeSeconds": 999999,
            })
        with self.assertRaisesRegex(
            ValueError,
            "formal_shadow_collection_policy_required",
        ):
            adapt_trend_rotation_observation(
                json_bytes(normal_receipt),
                stage6_raw,
                sector_raw,
                evaluated_at=OBSERVED_AT,
            )
        old_receipt = receipt(
            "trendRotation",
            stage6_raw,
            sourceTime=(AS_OF - timedelta(seconds=91)).isoformat(),
        )
        with self.assertRaisesRegex(
            ValueError,
            "formal_shadow_source_expired",
        ):
            adapt_trend_rotation_observation(
                json_bytes(old_receipt),
                stage6_raw,
                sector_raw,
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=policy_bytes(),
            )

    def test_trend_binds_same_run_stage6_market_and_sector_snapshot(self):
        from radar.formal_shadow_observation_collector import (
            adapt_trend_rotation_observation,
        )

        stage6_raw = json_bytes(trend_artifact())
        observation = adapt_trend_rotation_observation(
            json_bytes(receipt("trendRotation", stage6_raw)),
            stage6_raw,
            json_bytes(sector_snapshot()),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )

        self.assertEqual(observation.module, "trendRotation")
        self.assertEqual(observation.observation_status, "ready")
        self.assertEqual(observation.coverage, 1.0)
        self.assertEqual(observation.duration_ms, 0)
        self.assertRegex(observation.evidence_sha256, r"^[0-9a-f]{64}$")

    def test_trend_consumes_real_stage6_generator_snapshot_output(self):
        import run_leader_phase6_live_five_source_acceptance as stage6
        from radar.formal_shadow_observation_collector import (
            adapt_trend_rotation_observation,
        )

        snapshot = market_feature_snapshot()
        state, snapshot_evidence = stage6._market_research_evidence_bundle(
            SimpleNamespace(
                radar_run_id="run-1",
                as_of=AS_OF,
                runtime_inputs=SimpleNamespace(
                    market_snapshot=snapshot.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                ),
            )
        )
        source = trend_artifact()
        source["marketResearchState"] = state
        source["marketFeatureSnapshotEvidence"] = snapshot_evidence
        source_raw = json_bytes(source)

        observation = adapt_trend_rotation_observation(
            json_bytes(receipt("trendRotation", source_raw)),
            source_raw,
            json_bytes(sector_snapshot()),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )

        self.assertEqual(observation.observation_status, "ready")
        self.assertEqual(observation.coverage, 1.0)

    def test_trend_rejects_raw_tampering_and_identity_rule_or_snapshot_drift(self):
        from radar.formal_shadow_observation_collector import (
            adapt_trend_rotation_observation,
        )

        stage6 = trend_artifact()
        stage6_raw = json_bytes(stage6)
        base_receipt = json_bytes(receipt("trendRotation", stage6_raw))
        cases = []
        wrong_run = copy.deepcopy(stage6)
        wrong_run["marketResearchState"]["radarRunId"] = "wrong"
        wrong_run_raw = json_bytes(wrong_run)
        cases.append((
            json_bytes(receipt("trendRotation", wrong_run_raw)),
            wrong_run_raw,
            json_bytes(sector_snapshot()),
        ))
        wrong_bool = copy.deepcopy(stage6)
        wrong_bool["marketResearchState"]["researchUsable"] = 1
        wrong_bool_raw = json_bytes(wrong_bool)
        cases.append((
            json_bytes(receipt("trendRotation", wrong_bool_raw)),
            wrong_bool_raw,
            json_bytes(sector_snapshot()),
        ))
        wrong_wrapper = copy.deepcopy(stage6)
        wrong_wrapper["prepared"]["contractId"] = "wrong-contract"
        wrong_wrapper_raw = json_bytes(wrong_wrapper)
        cases.append((
            json_bytes(receipt("trendRotation", wrong_wrapper_raw)),
            wrong_wrapper_raw,
            json_bytes(sector_snapshot()),
        ))
        wrong_sector = sector_snapshot()
        wrong_sector["ruleVersion"] = "radar-sector-v0-other"
        wrong_sector["snapshotSha256"] = sha256(json_bytes({
            key: value for key, value in wrong_sector.items()
            if key != "snapshotSha256"
        }))
        cases.append((base_receipt, stage6_raw, json_bytes(wrong_sector)))
        bad_sha = sector_snapshot()
        bad_sha["records"][0]["pendingCount"] = 1
        cases.append((base_receipt, stage6_raw, json_bytes(bad_sha)))
        malformed_sector = sector_snapshot()
        malformed_sector["industryCodes"][0] = {}
        malformed_semantic = {
            key: value for key, value in malformed_sector.items()
            if key != "snapshotSha256"
        }
        malformed_sector["snapshotSha256"] = sha256(
            json_bytes(malformed_semantic)
        )
        cases.append((base_receipt, stage6_raw, json_bytes(malformed_sector)))
        for receipt_raw, source_raw, sector_raw in cases:
            with self.subTest(), self.assertRaises(ValueError):
                adapt_trend_rotation_observation(
                    receipt_raw,
                    source_raw,
                    sector_raw,
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_trend_rejects_classification_drift_and_invalid_market_metrics(self):
        from radar.formal_shadow_observation_collector import (
            adapt_trend_rotation_observation,
        )

        base = trend_artifact()
        cases = []
        wrong_classification = copy.deepcopy(base)
        wrong_classification["prepared"]["classificationDocumentSha256"] = (
            "c" * 64
        )
        cases.append((wrong_classification, sector_snapshot()))
        for invalid_metrics in (
            {},
            {
                **base["marketResearchState"]["metrics"],
                "advancerRatio": 1.1,
            },
            {
                **base["marketResearchState"]["metrics"],
                "positiveIndexCount": True,
            },
            {
                **base["marketResearchState"]["metrics"],
                "flatIndexCount": 1,
            },
        ):
            changed = copy.deepcopy(base)
            changed["marketResearchState"]["metrics"] = invalid_metrics
            cases.append((changed, sector_snapshot()))

        for source, sector in cases:
            source_raw = json_bytes(source)
            with self.subTest(source=source), self.assertRaises(ValueError):
                adapt_trend_rotation_observation(
                    json_bytes(receipt("trendRotation", source_raw)),
                    source_raw,
                    json_bytes(sector),
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_trend_replays_state_snapshot_sha_and_frozen_default_policy(self):
        from radar.formal_shadow_observation_collector import (
            adapt_trend_rotation_observation,
        )

        forged_state = trend_artifact()
        forged_state["marketResearchState"]["state"] = "oscillation"
        forged_sha = trend_artifact()
        forged_sha["marketResearchState"]["snapshotSha256"] = "c" * 64
        policy_drift = trend_artifact(
            research_policy=MarketResearchStatePolicy(
                strong_min_advancer_ratio=0.9,
            )
        )
        reason_drift = trend_artifact()
        reason_drift["marketResearchState"]["reasons"] = [
            "market_features_incomplete"
        ]

        for source in (
            forged_state,
            forged_sha,
            policy_drift,
            reason_drift,
        ):
            source_raw = json_bytes(source)
            with self.subTest(source=source), self.assertRaisesRegex(
                ValueError,
                "trend_market_state_unverified",
            ):
                adapt_trend_rotation_observation(
                    json_bytes(receipt("trendRotation", source_raw)),
                    source_raw,
                    json_bytes(sector_snapshot()),
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_trend_requires_complete_hashed_market_feature_snapshot(self):
        from radar.formal_shadow_observation_collector import (
            adapt_trend_rotation_observation,
        )

        missing = trend_artifact()
        missing.pop("marketFeatureSnapshotEvidence")
        wrong_sha = trend_artifact()
        wrong_sha["marketFeatureSnapshotEvidence"]["snapshotSha256"] = (
            "d" * 64
        )
        wrong_index = trend_artifact()
        wrong_index["marketFeatureSnapshotEvidence"]["snapshot"][
            "indexBatchId"
        ] = (
            "unproven-index-batch"
        )
        wrong_index["marketFeatureSnapshotEvidence"]["snapshotSha256"] = (
            sha256(canonical_json_bytes(
                wrong_index["marketFeatureSnapshotEvidence"]["snapshot"]
            ))
        )
        wrong_quote = trend_artifact()
        wrong_quote["marketFeatureSnapshotEvidence"]["snapshot"][
            "quoteBatchId"
        ] = (
            "unproven-quote-batch"
        )
        wrong_quote["marketFeatureSnapshotEvidence"]["snapshotSha256"] = (
            sha256(canonical_json_bytes(
                wrong_quote["marketFeatureSnapshotEvidence"]["snapshot"]
            ))
        )
        missing_field = trend_artifact()
        missing_field["marketFeatureSnapshotEvidence"]["snapshot"].pop(
            "turnover"
        )
        missing_field["marketFeatureSnapshotEvidence"]["snapshotSha256"] = (
            sha256(canonical_json_bytes(
                missing_field["marketFeatureSnapshotEvidence"]["snapshot"]
            ))
        )
        incomplete = trend_artifact()
        incomplete_snapshot = incomplete["marketFeatureSnapshotEvidence"][
            "snapshot"
        ]
        incomplete_snapshot["breadth"]["completeness"]["isComplete"] = (
            False
        )
        incomplete_snapshot["breadth"]["completeness"]["reasons"] = [
            "source_time_stale"
        ]
        incomplete["marketFeatureSnapshotEvidence"]["snapshotSha256"] = (
            sha256(canonical_json_bytes(incomplete_snapshot))
        )
        unit_unverified = trend_artifact()
        unit_snapshot = unit_unverified["marketFeatureSnapshotEvidence"][
            "snapshot"
        ]
        unit_snapshot["turnover"]["unitStatus"] = "unverified"
        unit_snapshot["turnover"]["formalUsable"] = False
        unit_unverified["marketFeatureSnapshotEvidence"][
            "snapshotSha256"
        ] = sha256(canonical_json_bytes(unit_snapshot))

        for source in (
            missing,
            wrong_sha,
            wrong_index,
            wrong_quote,
            missing_field,
            incomplete,
            unit_unverified,
        ):
            source_raw = json_bytes(source)
            with self.subTest(source=source), self.assertRaisesRegex(
                ValueError,
                "trend_market_(snapshot|state)_unverified",
            ):
                adapt_trend_rotation_observation(
                    json_bytes(receipt("trendRotation", source_raw)),
                    source_raw,
                    json_bytes(sector_snapshot()),
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_trend_accepts_true_zero_and_frozen_policy_boundaries(self):
        from radar.formal_shadow_observation_collector import (
            adapt_trend_rotation_observation,
        )

        cases = (
            trend_artifact(
                snapshot=market_feature_snapshot(
                    changes=(1.0, 0.5, 0.5, 0.0),
                    advancers=60,
                    decliners=30,
                    flat=10,
                )
            ),
            trend_artifact(
                snapshot=market_feature_snapshot(
                    changes=(0.0, 0.0, 0.0, 0.0),
                    advancers=0,
                    decliners=0,
                    flat=100,
                )
            ),
            trend_artifact(
                snapshot=market_feature_snapshot(
                    changes=(-1.5, -1.5, -1.0, 0.0),
                    advancers=25,
                    decliners=75,
                    flat=0,
                )
            ),
        )
        for source in cases:
            source_raw = json_bytes(source)
            with self.subTest(state=source["marketResearchState"]["state"]):
                observation = adapt_trend_rotation_observation(
                    json_bytes(receipt("trendRotation", source_raw)),
                    source_raw,
                    json_bytes(sector_snapshot()),
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )
                self.assertEqual(observation.observation_status, "ready")

    def test_leader_exclusions_are_not_missing_when_full_parent_is_reviewed(self):
        from radar.formal_shadow_observation_collector import (
            adapt_leader_observation,
        )

        source_raw = json_bytes(leader_artifact())
        observation = adapt_leader_observation(
            json_bytes(receipt("leaderObservation", source_raw)),
            source_raw,
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )

        self.assertEqual(observation.observation_status, "ready")
        self.assertEqual(observation.missing_count, 0)
        self.assertEqual(observation.coverage, 1.0)

    def test_leader_source_failed_exclusion_cannot_be_ready(self):
        from radar.formal_shadow_observation_collector import (
            adapt_leader_observation,
        )

        source = leader_artifact()
        source["collection"]["qualification"]["excludedItems"][0][
            "status"
        ] = "source_failed"
        source_raw = json_bytes(source)
        with self.assertRaisesRegex(ValueError, "leader_source_failed"):
            adapt_leader_observation(
                json_bytes(receipt("leaderObservation", source_raw)),
                source_raw,
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=policy_bytes(),
            )

    def test_leader_wrapper_reasons_must_be_strictly_empty(self):
        from radar.formal_shadow_observation_collector import (
            adapt_leader_observation,
        )

        cases = (
            (("evidenceCandidateSelection", "reasons"), ["source_failed"]),
            (("evidenceCandidateSelection", "evidencePlan", "reasons"),
             "source_failed"),
            (("collection", "reasons"), ["unexpected_reason"]),
        )
        for path, invalid_reasons in cases:
            source = leader_artifact()
            target = source
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = invalid_reasons
            source_raw = json_bytes(source)
            with self.subTest(path=path), self.assertRaisesRegex(
                ValueError,
                "leader_stage6_reasons_unverified",
            ):
                adapt_leader_observation(
                    json_bytes(receipt("leaderObservation", source_raw)),
                    source_raw,
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_leader_replays_nested_source_statuses_counts_and_summaries(self):
        from radar.formal_shadow_observation_collector import (
            adapt_leader_observation,
        )

        cases = []
        qualification_business = leader_artifact()
        qualification_business["collection"]["qualification"][
            "businessAutomatic"
        ]["sourceFailedCount"] = 1
        cases.append(qualification_business)

        collection_business = leader_artifact()
        collection_business["collection"]["businessAutomatic"].update({
            "status": "source_failed",
            "readyCount": 0,
            "sourceFailedCount": 1,
            "reasons": ["business_source_failed"],
            "productionFrozenInputsReady": False,
        })
        cases.append(collection_business)

        verification_business = leader_artifact()
        verification_business["collection"][
            "verificationBusinessAutomatic"
        ].update({
            "status": "source_failed",
            "missingCount": 0,
            "sourceFailedCount": 1,
            "reasons": ["business_source_failed"],
        })
        cases.append(verification_business)

        material_summary = leader_artifact()
        material_summary["collection"]["materialAcceptance"][
            "reviewSummary"
        ].update({"pendingReview": 0, "sourceFailed": 1})
        cases.append(material_summary)

        verification_material_summary = leader_artifact()
        verification_material_summary["collection"][
            "verificationMaterialAcceptance"
        ]["reviewSummary"].update({"pendingReview": 1, "sourceFailed": 1})
        cases.append(verification_material_summary)

        readiness_component = leader_artifact()
        readiness_component["collection"]["readiness"]["readiness"][
            "assembly"
        ]["components"][2].update({
            "status": "source_failed",
            "readyCount": 0,
            "reasons": ["business_source_failed"],
        })
        cases.append(readiness_component)

        readiness_extra_failure_count = leader_artifact()
        readiness_extra_failure_count["collection"]["readiness"][
            "readiness"
        ]["acceptance"]["sourceFailedCount"] = 1
        cases.append(readiness_extra_failure_count)

        qualification_extra_failure_count = leader_artifact()
        qualification_extra_failure_count["collection"]["qualification"][
            "sourceFailedCount"
        ] = 1
        cases.append(qualification_extra_failure_count)

        risk_source_failed = leader_artifact()
        risk_source_failed["collection"]["riskDelivery"].update({
            "status": "source_failed",
            "reasons": ["risk_lifecycle_source_failed"],
        })
        cases.append(risk_source_failed)

        industry_source_failed = leader_artifact()
        industry_gate = industry_source_failed["collection"][
            "stateDecisionReview"
        ]["industryGateEvidence"]
        industry_gate["reasons"] = ["industry_source_failed"]
        industry_gate["policyAudit"]["reasons"] = [
            "industry_source_failed"
        ]
        industry_gate["items"][0]["reasons"] = [
            "industry_source_failed"
        ]
        cases.append(industry_source_failed)

        nested_reason = leader_artifact()
        nested_reason["collection"]["qualification"]["excludedItems"][0][
            "reasons"
        ] = ["business_source_failed"]
        nested_reason["collection"]["stateDecisionReview"]["items"][1].update({
            "firstRejectionReason": "business_source_failed",
            "reasons": ["business_source_failed"],
        })
        nested_reason["collection"]["verificationBusinessAutomatic"][
            "reasons"
        ] = ["business_source_failed"]
        cases.append(nested_reason)

        for source in cases:
            source_raw = json_bytes(source)
            with self.subTest(source=source), self.assertRaisesRegex(
                ValueError,
                "leader_(source_failed|stage6_(nested|artifact)_unverified)",
            ):
                adapt_leader_observation(
                    json_bytes(receipt("leaderObservation", source_raw)),
                    source_raw,
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_leader_rejects_incomplete_parent_partition_open_gate_and_empty(self):
        from radar.formal_shadow_observation_collector import (
            adapt_leader_observation,
        )

        source = leader_artifact()
        source_raw = json_bytes(source)
        incomplete = copy.deepcopy(source)
        incomplete["collection"]["stateDecisionReview"]["items"].pop()
        open_gate = copy.deepcopy(source)
        open_gate["collection"]["gate"]["formalUsable"] = True
        empty = copy.deepcopy(source)
        empty["collection"]["status"] = "empty"
        empty["collection"]["validEmptyResult"] = True
        boolean_index = copy.deepcopy(source)
        boolean_index["evidenceCandidateSelection"]["evidencePlan"][
            "items"
        ][0]["index"] = False
        malformed_symbol = copy.deepcopy(source)
        malformed_symbol["evidenceCandidateSelection"]["evidencePlan"][
            "items"
        ][0]["symbol"] = []
        for changed in (
            incomplete,
            open_gate,
            empty,
            boolean_index,
            malformed_symbol,
        ):
            changed_raw = json_bytes(changed)
            with self.subTest(), self.assertRaises(ValueError):
                adapt_leader_observation(
                    json_bytes(receipt("leaderObservation", changed_raw)),
                    changed_raw,
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_etf_live_artifact_preserves_real_zero_and_becomes_ready(self):
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        source_raw = json_bytes(etf_artifact())
        observation = adapt_etf_observation(
            json_bytes(receipt("etfObservation", source_raw)),
            source_raw,
            json_bytes(etf_admission_artifact()),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )

        self.assertEqual(observation.observation_status, "ready")
        self.assertEqual(observation.duration_ms, 0)
        self.assertEqual(observation.failed_count, 0)

    def test_etf_product_admission_cannot_substitute_for_live_artifact(self):
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        admission = json_bytes({
            "contractId": "radar-etf-formal-admission-bundle-v1",
            "sampleId": "sample-1",
            "radarRunId": "run-1",
            "asOf": FETCHED_AT.isoformat(),
            "admissions": [],
            "snapshotSha256": "d" * 64,
        })
        with self.assertRaises(ValueError):
            adapt_etf_observation(
                json_bytes(receipt("etfObservation", admission)),
                admission,
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=policy_bytes(),
            )

    def test_etf_requires_admission_identity_and_rejects_untrusted_quote_contract(self):
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        source_raw = json_bytes(etf_artifact())
        receipt_raw = json_bytes(receipt("etfObservation", source_raw))
        with self.assertRaisesRegex(ValueError, "etf_admission_required"):
            adapt_etf_observation(
                receipt_raw,
                source_raw,
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=policy_bytes(),
            )
        observation = adapt_etf_observation(
            receipt_raw,
            source_raw,
            json_bytes(etf_admission_artifact()),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )

        self.assertEqual(observation.observation_status, "ready")

        untrusted = etf_artifact()
        untrusted["records"][0]["quoteSourceContractId"] = "untrusted-v1"
        untrusted_raw = json_bytes(untrusted)
        with self.assertRaises(ValueError):
            adapt_etf_observation(
                json_bytes(receipt("etfObservation", untrusted_raw)),
                untrusted_raw,
                json_bytes(etf_admission_artifact()),
                evaluated_at=OBSERVED_AT,
                collection_policy_json_bytes=policy_bytes(),
            )

    def test_etf_requires_unique_ready_product_identity_and_lifecycle(self):
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        cases = []
        ordinary_stock = etf_admission_artifact(
            symbol="000001",
            item_reasons={
                "product_identity": ["etf_product_type_not_etf"],
            },
        )
        cases.append((etf_artifact(symbol="000001"), ordinary_stock))
        lifecycle_missing = etf_admission_artifact(item_reasons={
            "product_lifecycle": [
                "etf_product_lifecycle_evidence_missing"
            ],
        })
        cases.append((etf_artifact(), lifecycle_missing))

        missing_identity = etf_admission_artifact()
        missing_identity["admissions"][0]["items"].pop(0)
        cases.append((etf_artifact(), refresh_admission_sha(missing_identity)))
        duplicate_identity = etf_admission_artifact()
        duplicate_identity["admissions"][0]["items"].insert(
            1,
            copy.deepcopy(duplicate_identity["admissions"][0]["items"][0]),
        )
        cases.append((etf_artifact(), refresh_admission_sha(duplicate_identity)))
        failed_identity = etf_admission_artifact()
        failed_identity["admissions"][0]["items"][0]["status"] = "failed"
        cases.append((etf_artifact(), refresh_admission_sha(failed_identity)))
        reason_with_ready = etf_admission_artifact()
        reason_with_ready["admissions"][0]["items"][0]["reasons"] = [
            "etf_product_type_not_etf"
        ]
        reason_with_ready["admissions"][0]["status"] = "missing"
        reason_with_ready["admissions"][0]["monitoringStatus"] = "missing"
        reason_with_ready["admissions"][0]["reasons"] = [
            "etf_product_type_not_etf"
        ]
        cases.append((etf_artifact(), refresh_admission_sha(reason_with_ready)))
        malicious_reasons = etf_admission_artifact()
        malicious_reasons["admissions"][0]["items"][0]["reasons"] = {
            "source_failed": True,
        }
        cases.append((etf_artifact(), refresh_admission_sha(malicious_reasons)))

        for source, admission in cases:
            source_raw = json_bytes(source)
            with self.subTest(admission=admission), self.assertRaisesRegex(
                ValueError,
                "etf_admission_(artifact|product_identity|product_lifecycle)",
            ):
                adapt_etf_observation(
                    json_bytes(receipt("etfObservation", source_raw)),
                    source_raw,
                    json_bytes(admission),
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_etf_ranking_gap_does_not_become_live_observation_gate(self):
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        source_raw = json_bytes(etf_artifact())
        admission_raw = json_bytes(etf_admission_artifact(item_reasons={
            "ranking_inputs": ["etf_ranking_input_not_verified"],
        }))
        observation = adapt_etf_observation(
            json_bytes(receipt("etfObservation", source_raw)),
            source_raw,
            admission_raw,
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )

        self.assertEqual(observation.observation_status, "ready")

    def test_etf_missing_failed_stale_and_lock_contention_never_ready(self):
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        cases = (
            ("missing", {"returnedCount": 0, "missingCount": 1,
                         "records": [], "missingSymbols": ["515050"]},
             {"observedCount": 0, "missingCount": 1, "coverage": 0.0}),
            ("failed", {"returnedCount": 0, "failedCount": 1,
                        "records": [], "failedSymbols": ["515050"]},
             {"observedCount": 0, "failedCount": 1, "coverage": 0.0}),
            ("stale", {"staleCount": 1, "staleSymbols": ["515050"]},
             {"staleCount": 1}),
            ("failed", {"lockState": "contended"},
             {"lockState": "contended"}),
        )
        for status, artifact_changes, receipt_changes in cases:
            artifact = etf_artifact(status=status)
            artifact.update(artifact_changes)
            if status == "stale":
                stale_source_time = AS_OF - timedelta(seconds=180)
                artifact["sourceTime"] = stale_source_time.isoformat()
                artifact["records"][0]["sourceTime"] = (
                    stale_source_time.isoformat()
                )
                artifact["records"][0]["status"] = "stale"
                receipt_changes["sourceTime"] = stale_source_time.isoformat()
            raw = json_bytes(artifact)
            receipt_payload = receipt(
                "etfObservation",
                raw,
                sourceReady=False,
                **receipt_changes,
            )
            with self.subTest(status=status, changes=artifact_changes):
                observation = adapt_etf_observation(
                    json_bytes(receipt_payload),
                    raw,
                    json_bytes(etf_admission_artifact()),
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )
                self.assertNotEqual(observation.observation_status, "ready")
                self.assertEqual(observation.observation_status, status)

    def test_etf_rejects_future_stale_source_and_count_or_symbol_tampering(self):
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        source = etf_artifact()
        source_raw = json_bytes(source)
        future = copy.deepcopy(source)
        future["records"][0]["sourceTime"] = (
            OBSERVED_AT + timedelta(seconds=1)
        ).isoformat()
        stale = copy.deepcopy(source)
        stale["sourceTime"] = (AS_OF - timedelta(seconds=301)).isoformat()
        wrong_count = copy.deepcopy(source)
        wrong_count["returnedCount"] = 0
        wrong_symbol = copy.deepcopy(source)
        wrong_symbol["records"][0]["symbol"] = "515790"
        for changed in (future, stale, wrong_count, wrong_symbol):
            changed_raw = json_bytes(changed)
            with self.subTest(), self.assertRaises(ValueError):
                adapt_etf_observation(
                    json_bytes(receipt("etfObservation", changed_raw)),
                    changed_raw,
                    json_bytes(etf_admission_artifact()),
                    evaluated_at=OBSERVED_AT,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_adapter_rejects_future_or_expired_receipt_at_evaluation_time(self):
        from radar.formal_shadow_observation_collector import (
            adapt_etf_observation,
        )

        source_raw = json_bytes(etf_artifact())
        receipt_raw = json_bytes(receipt("etfObservation", source_raw))
        for evaluated_at in (
            OBSERVED_AT - timedelta(microseconds=1),
            OBSERVED_AT + timedelta(seconds=301),
        ):
            with self.subTest(evaluated_at=evaluated_at), self.assertRaisesRegex(
                ValueError,
                "formal_shadow_receipt_(from_future|expired)",
            ):
                adapt_etf_observation(
                    receipt_raw,
                    source_raw,
                    json_bytes(etf_admission_artifact()),
                    evaluated_at=evaluated_at,
                    collection_policy_json_bytes=policy_bytes(),
                )

    def test_public_entry_rejects_non_bytes_and_invalid_evaluation_time_stably(self):
        from radar.formal_shadow_observation_collector import (
            adapt_formal_shadow_observation,
        )

        trend_raw = json_bytes(trend_artifact())
        trend_receipt = json_bytes(receipt("trendRotation", trend_raw))
        trend_arguments = {
            "receipt_json_bytes": trend_receipt,
            "source_artifact_json_bytes": trend_raw,
            "supporting_artifact_json_bytes": json_bytes(sector_snapshot()),
            "evaluated_at": OBSERVED_AT,
            "collection_policy_json_bytes": policy_bytes(),
        }
        etf_raw = json_bytes(etf_artifact())
        etf_arguments = {
            "receipt_json_bytes": json_bytes(
                receipt("etfObservation", etf_raw)
            ),
            "source_artifact_json_bytes": etf_raw,
            "formal_admission_json_bytes": json_bytes(
                etf_admission_artifact()
            ),
            "evaluated_at": OBSERVED_AT,
            "collection_policy_json_bytes": policy_bytes(),
        }
        cases = (
            (trend_arguments, "source_artifact_json_bytes", "not-bytes",
             "formal_shadow_source_json_bytes_required"),
            (trend_arguments, "supporting_artifact_json_bytes", "not-bytes",
             "formal_shadow_supporting_json_bytes_required"),
            (etf_arguments, "formal_admission_json_bytes", "not-bytes",
             "formal_shadow_admission_json_bytes_required"),
            (trend_arguments, "evaluated_at", "not-a-datetime",
             "formal_shadow_evaluated_at_invalid"),
            (trend_arguments, "evaluated_at", datetime(2026, 9, 4, 3, 9, 8),
             "formal_shadow_evaluated_at_invalid"),
        )
        for base, key, value, reason in cases:
            arguments = {**base, key: value}
            with self.subTest(key=key, value=value), self.assertRaisesRegex(
                ValueError,
                reason,
            ):
                adapt_formal_shadow_observation(**arguments)

    def test_evidence_sha_binds_receipt_and_every_authoritative_artifact(self):
        from radar.formal_shadow_observation_collector import (
            adapt_trend_rotation_observation,
        )

        stage6_raw = json_bytes(trend_artifact())
        sector_one = sector_snapshot()
        first = adapt_trend_rotation_observation(
            json_bytes(receipt("trendRotation", stage6_raw)),
            stage6_raw,
            json_bytes(sector_one),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )
        sector_two = copy.deepcopy(sector_one)
        sector_two["records"][0]["state"] = "startup"
        semantic = {
            key: value for key, value in sector_two.items()
            if key != "snapshotSha256"
        }
        sector_two["snapshotSha256"] = sha256(json_bytes(semantic))
        second = adapt_trend_rotation_observation(
            json_bytes(receipt("trendRotation", stage6_raw)),
            stage6_raw,
            json_bytes(sector_two),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )
        delayed_receipt = receipt(
            "trendRotation", stage6_raw, durationMs=1
        )
        third = adapt_trend_rotation_observation(
            json_bytes(delayed_receipt),
            stage6_raw,
            json_bytes(sector_one),
            evaluated_at=OBSERVED_AT,
            collection_policy_json_bytes=policy_bytes(),
        )

        self.assertNotEqual(first.evidence_sha256, second.evidence_sha256)
        self.assertNotEqual(first.evidence_sha256, third.evidence_sha256)


if __name__ == "__main__":
    unittest.main()
