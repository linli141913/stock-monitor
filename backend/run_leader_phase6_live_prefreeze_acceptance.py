"""阶段6历史/行业先准备、最终行情后冻结的真实只读入口。"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Optional, Sequence, TextIO

from radar.leader_live_candidate_collection_batch import (
    LeaderLiveCandidateCollectionRequest,
    build_default_leader_live_candidate_collection_sources,
)
from radar.leader_evidence_candidate_plan import (
    build_leader_evidence_candidate_industry_scope,
)
from radar.leader_phase6_live_prefreeze import (
    build_leader_phase6_prepared_prefreeze_loader,
    prepare_leader_phase6_public_historical_inputs,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceStatus,
    build_default_leader_tradability_live_acceptance_sources,
    run_leader_tradability_live_acceptance,
)
from radar.sector_history_store import DEFAULT_SECTOR_HISTORY_STORE_DIR
from radar.sector_state_producer import (
    SECTOR_STATE_TRANSITION_POLICY_VERSION,
    SectorStateProductionStatus,
    build_initial_sector_state_snapshot,
    load_sector_state_snapshot,
    produce_sector_state_from_prefrozen,
    write_sector_state_snapshot,
)
from radar.sources.leader_tradability_public_live_poc import (
    SHANGHAI_TZ,
    _fetch_calendar_document,
)


CLASSIFICATION_PUBLICATION_PAGE_URL = (
    "https://www.capco.org.cn/xhgg/hyfl/hyfljg/202604/20260403/"
    "j_2026040315001700017751997384265508.html"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="先准备历史/行业，再执行同轮候选与可交易性冻结",
    )
    parser.add_argument(
        "--output-dir",
        default="/private/tmp",
    )
    parser.add_argument("--confirm-live-prefreeze", action="store_true")
    parser.add_argument("--initialize-sector-state", action="store_true")
    parser.add_argument("--previous-sector-state")
    return parser


def _print(payload: Any, stdout: TextIO) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    stdout.write("\n")


def _private_tmp_dir(value: str) -> Path:
    unresolved = Path(value).expanduser()
    if (
        not unresolved.is_absolute()
        or not str(unresolved).startswith("/private/tmp")
    ):
        raise ValueError(
            "leader_phase6_live_prefreeze_output_path_unverified"
        )
    root = unresolved.resolve()
    try:
        root.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError(
            "leader_phase6_live_prefreeze_output_path_unverified"
        ) from exc
    root.mkdir(parents=True, exist_ok=True)
    return root


def _private_tmp_state_path(value: Optional[str]) -> Optional[Path]:
    if value is None:
        return None
    unresolved = Path(value).expanduser()
    if (
        not unresolved.is_absolute()
        or not str(unresolved).startswith("/private/tmp/")
    ):
        raise ValueError(
            "leader_phase6_live_prefreeze_state_path_unverified"
        )
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError(
            "leader_phase6_live_prefreeze_state_path_unverified"
        ) from exc
    return resolved


def _write_new_json(path: Path, payload: Any) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _request(started_at: datetime) -> LeaderLiveCandidateCollectionRequest:
    prefix = f"stage6-prefreeze-{started_at:%Y%m%dT%H%M%S%f}"
    return LeaderLiveCandidateCollectionRequest(
        radar_run_id=prefix,
        security_master_batch_id=f"{prefix}-security-master",
        discovery_classification_batch_id=(
            f"{prefix}-classification-discovery"
        ),
        collection_classification_batch_id=(
            f"{prefix}-classification-collection"
        ),
        quote_batch_id=f"{prefix}-quotes",
        index_batch_id=f"{prefix}-indices",
    )


def _run_live(
    *,
    started_at: datetime,
    output_dir: Path,
):
    request = _request(started_at)
    candidate_sources = build_default_leader_live_candidate_collection_sources(
        classification_publication_page_url=(
            CLASSIFICATION_PUBLICATION_PAGE_URL
        ),
        verify_official_classification_archive=True,
    )
    security = candidate_sources.security_master_loader(
        request.radar_run_id,
        f"{request.radar_run_id}-prefreeze-security-master",
        started_at,
    )
    classification = candidate_sources.classification_loader(
        request.radar_run_id,
        f"{request.radar_run_id}-prefreeze-classification",
        started_at,
        tuple(security.items),
        first_observed_at=None,
        known_document_hashes=None,
    )
    share_as_of = datetime.now(SHANGHAI_TZ)
    share_quotes = candidate_sources.quote_loader(
        tuple(item.symbol for item in security.items),
        request.radar_run_id,
        f"{request.radar_run_id}-prefreeze-share-basis",
        share_as_of,
    )
    calendar_document = _fetch_calendar_document(as_of=started_at)
    prepared = prepare_leader_phase6_public_historical_inputs(
        radar_run_id=request.radar_run_id,
        security_master_batch=security,
        classification_snapshot=classification,
        share_quote_batch=share_quotes,
        calendar_document=calendar_document,
        artifact_dir=output_dir / request.radar_run_id,
        reuse_store_dir=DEFAULT_SECTOR_HISTORY_STORE_DIR,
        clock=lambda: datetime.now(SHANGHAI_TZ),
        history_checkpoint_dir=(
            output_dir / "leader-history-checkpoints"
        ),
        sector_checkpoint_dir=(
            output_dir / "sector-history-checkpoints"
        ),
    )
    evidence_sources = replace(
        build_default_leader_tradability_live_acceptance_sources(),
        calendar_loader=lambda _as_of: calendar_document,
    )
    acceptance = run_leader_tradability_live_acceptance(
        request,
        candidate_sources,
        evidence_sources=evidence_sources,
        phase6_prefreeze_loader=(
            build_leader_phase6_prepared_prefreeze_loader(
                prepared,
                clock=lambda: datetime.now(SHANGHAI_TZ),
            )
        ),
        clock=lambda: datetime.now(SHANGHAI_TZ),
    )
    return prepared, acceptance


def _finalize_sector_state(
    *,
    acceptance: Any,
    output_dir: Path,
    started_at: datetime,
    previous_state_path: Optional[Path],
) -> Any:
    runtime = getattr(acceptance, "runtime_inputs", None)
    context = getattr(runtime, "source_context", None)
    prefrozen = getattr(acceptance, "phase6_prefrozen_inputs", None)
    plan = getattr(context, "candidate_plan", None)
    sector_rule = getattr(prefrozen, "sector_rule", None)
    source_batch = getattr(sector_rule, "source_batch", None)
    feature_batch = getattr(source_batch, "feature_batch", None)
    if plan is None or feature_batch is None:
        return {
            "status": "blocked",
            "reasons": ["sector_state_prefrozen_input_unverified"],
        }
    industry_codes = tuple(sorted({
        item.industry_code for item in plan.items
    }))
    if previous_state_path is None:
        approval_record = prefrozen.threshold_approval_record
        previous = build_initial_sector_state_snapshot(
            industry_codes=industry_codes,
            classification_document_sha256=(
                feature_batch.classification_document_sha256
            ),
            rule_version=source_batch.rule_version,
            transition_policy_version=(
                SECTOR_STATE_TRANSITION_POLICY_VERSION
            ),
            threshold_set_id=approval_record.threshold_set_id,
            approval_id=approval_record.approval_id,
            observed_before=plan.as_of - timedelta(microseconds=1),
        )
    else:
        approval_record = prefrozen.threshold_approval_record
        previous = load_sector_state_snapshot(
            previous_state_path,
            expected_industry_codes=industry_codes,
            classification_document_sha256=(
                feature_batch.classification_document_sha256
            ),
            rule_version=source_batch.rule_version,
            transition_policy_version=(
                SECTOR_STATE_TRANSITION_POLICY_VERSION
            ),
            threshold_set_id=approval_record.threshold_set_id,
            approval_id=approval_record.approval_id,
            before_as_of=plan.as_of,
        )
    produced = produce_sector_state_from_prefrozen(
        context,
        prefrozen_inputs=prefrozen,
        previous_snapshot=previous,
    )
    if produced.status != SectorStateProductionStatus.READY:
        return {
            "status": produced.status.value,
            "reasons": list(produced.reasons),
            "evidence": produced.to_evidence(),
        }
    scope = build_leader_evidence_candidate_industry_scope(
        plan=plan,
        sector_rule_bridge=produced.sector_rule_bridge,
        state_production=produced,
    )
    state_path = output_dir / (
        f"sector-state-{started_at:%Y%m%dT%H%M%S%f}.json"
    )
    write_sector_state_snapshot(produced.next_snapshot, state_path)
    return {
        "status": "ready",
        "reasons": [],
        "stateSnapshotPath": str(state_path),
        "industryScopeSnapshotId": scope.state_snapshot_id,
        "activeIndustryCount": sum(
            item.state.value != "inactive" for item in scope.items
        ),
        "evidence": produced.to_evidence(),
    }


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO = sys.stdout,
    now_provider: Callable[[], datetime] = (
        lambda: datetime.now(SHANGHAI_TZ)
    ),
    live_runner: Callable[..., Any] = _run_live,
    sector_state_finalizer: Callable[..., Any] = _finalize_sector_state,
) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.confirm_live_prefreeze:
        _print({
            "status": "not_run",
            "reason": "leader_phase6_live_prefreeze_confirmation_missing",
            "phase6PrefrozenInputsReady": False,
        }, stdout)
        return 2
    try:
        output_dir = _private_tmp_dir(arguments.output_dir)
    except (OSError, ValueError):
        _print({
            "status": "error",
            "reason": "leader_phase6_live_prefreeze_output_path_unverified",
            "phase6PrefrozenInputsReady": False,
        }, stdout)
        return 3
    if bool(arguments.initialize_sector_state) == bool(
        arguments.previous_sector_state
    ):
        _print({
            "status": "error",
            "reason": "leader_phase6_sector_state_lineage_unverified",
            "phase6PrefrozenInputsReady": False,
            "sectorStateReady": False,
        }, stdout)
        return 3
    try:
        previous_state_path = _private_tmp_state_path(
            arguments.previous_sector_state
        )
    except ValueError:
        _print({
            "status": "error",
            "reason": (
                "leader_phase6_live_prefreeze_state_path_unverified"
            ),
            "phase6PrefrozenInputsReady": False,
            "sectorStateReady": False,
        }, stdout)
        return 3
    try:
        started_at = now_provider()
        if (
            not isinstance(started_at, datetime)
            or started_at.tzinfo is None
            or started_at.utcoffset() is None
        ):
            raise ValueError("leader_phase6_live_prefreeze_clock_unverified")
        prepared, acceptance = live_runner(
            started_at=started_at,
            output_dir=output_dir,
        )
        acceptance_evidence = acceptance.to_evidence()
        ready = bool(
            acceptance.status
            == LeaderTradabilityLiveAcceptanceStatus.COMPLETED
            or getattr(acceptance.status, "value", None) == "completed"
        ) and acceptance.phase6_prefrozen_inputs is not None
        sector_state = (
            sector_state_finalizer(
                acceptance=acceptance,
                output_dir=output_dir,
                started_at=started_at,
                previous_state_path=previous_state_path,
            )
            if ready else {
                "status": "not_run",
                "reasons": ["phase6_prefrozen_inputs_not_ready"],
            }
        )
        sector_state_ready = bool(
            isinstance(sector_state, dict)
            and sector_state.get("status") == "ready"
        )
        complete_ready = ready and sector_state_ready
        artifact = output_dir / (
            f"stage6-live-prefreeze-{started_at:%Y%m%dT%H%M%S%f}.json"
        )
        _write_new_json(artifact, {
            "prepared": prepared.to_evidence(),
            "acceptance": acceptance_evidence,
            "phase6PrefrozenInputsReady": ready,
            "sectorStateReady": sector_state_ready,
            "sectorState": sector_state,
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        })
    except Exception:
        _print({
            "status": "error",
            "reason": "leader_phase6_live_prefreeze_execution_failed",
            "phase6PrefrozenInputsReady": False,
        }, stdout)
        return 3
    _print({
        "status": "completed" if complete_ready else "not_ready",
        "artifactPath": str(artifact),
        "phase6PrefrozenInputsReady": ready,
        "sectorStateReady": sector_state_ready,
        "sectorStateSnapshotPath": sector_state.get(
            "stateSnapshotPath"
        ),
        "industryScopeSnapshotId": sector_state.get(
            "industryScopeSnapshotId"
        ),
        "prepared": prepared.to_evidence(),
        "acceptance": acceptance_evidence,
    }, stdout)
    return 0 if complete_ready else 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
