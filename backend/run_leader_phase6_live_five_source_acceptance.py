"""阶段6预冻结后同进程五源真实只读验收入口。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from typing import Any, Callable, Mapping, Optional, Sequence, TextIO

from radar.contracts import MarketFeatureSnapshot
from radar.leader_evidence_candidate_plan import (
    LeaderEvidenceCandidateAcceptanceResult,
    LeaderEvidenceCandidateAcceptanceStatus,
    LeaderEvidenceCandidateSelectionPolicy,
    build_leader_evidence_candidate_industry_scope,
    build_leader_phase6_evidence_candidate_acceptance,
    load_leader_evidence_previous_state_snapshot,
)
from radar.leader_repository import LeaderRepository
from radar.migrations import STAGE6_RADAR_MIGRATIONS, apply_pending_migrations
from radar.leader_phase6_live_source_readiness import (
    LeaderPhase6LiveSourceCollectionResult,
    LeaderPhase6LiveSourceReadinessStatus,
    run_leader_phase6_live_source_collection,
)
from radar.sector_state_producer import (
    SECTOR_STATE_TRANSITION_POLICY_VERSION,
    SectorStateProductionStatus,
    build_initial_sector_state_snapshot,
    load_sector_state_snapshot,
    produce_sector_state_from_prefrozen,
    write_sector_state_snapshot,
)
from radar.sector_threshold_review import (
    bind_latest_sector_threshold_approval,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceResult,
    LeaderTradabilityLiveAcceptanceStatus,
)
from radar.market_research_state import produce_market_research_state
from radar.formal_shadow_input_bundle import (
    PrivateTmpNoFollowFileLock,
    Stage6FormalShadowInputBundleInputs,
    Stage6FormalShadowInputBundlePublication,
    build_stage6_formal_shadow_input_context,
    publish_stage6_formal_shadow_input_bundles,
)
from radar.sources.leader_tradability_public_live_poc import SHANGHAI_TZ
from run_leader_phase6_live_prefreeze_acceptance import (
    _run_live as run_live_prefreeze,
)


MARKET_FEATURE_SNAPSHOT_EVIDENCE_CONTRACT_ID = (
    "radar-market-feature-snapshot-evidence-v1"
)


class _NoManualRiskRepository:
    @staticmethod
    def list_review_version_chains(symbols, as_of):
        del symbols, as_of
        return ()


_SAFE_PREFREEZE_FAILURE_REASONS = frozenset({
    "leader_phase6_public_prepare_history_unverified",
    "leader_phase6_public_prepare_share_basis_unverified",
    "leader_phase6_public_prepare_sector_unverified",
    "leader_phase6_public_prepare_security_master_unverified",
    "leader_phase6_public_prepare_approval_unverified",
    "leader_phase6_public_prepare_memberships_unverified",
    "leader_phase6_formal_shadow_lock_contended",
    "leader_phase6_formal_shadow_context_unverified",
    "leader_phase6_formal_shadow_clock_unverified",
})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="同进程完成预冻结、自动主营、官方风险和五源验收",
    )
    parser.add_argument("--confirm-live-five-source", action="store_true")
    parser.add_argument("--output-dir", default="/private/tmp")
    parser.add_argument("--initialize-sector-state", action="store_true")
    parser.add_argument("--previous-sector-state")
    parser.add_argument("--formal-shadow-input-root")
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
            "leader_phase6_live_five_source_output_path_unverified"
        )
    root = unresolved.resolve()
    try:
        root.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError(
            "leader_phase6_live_five_source_output_path_unverified"
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
            "leader_phase6_evidence_sector_state_path_unverified"
        )
    resolved = unresolved.resolve(strict=True)
    try:
        resolved.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError(
            "leader_phase6_evidence_sector_state_path_unverified"
        ) from exc
    if not resolved.is_file():
        raise ValueError(
            "leader_phase6_evidence_sector_state_path_unverified"
        )
    return resolved


@dataclass(frozen=True)
class _PrivateTmpFileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


def _file_identity(metadata: os.stat_result) -> _PrivateTmpFileIdentity:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("leader_phase6_formal_shadow_context_unverified")
    return _PrivateTmpFileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _open_private_tmp_parent(path: Path) -> tuple[int, str]:
    """逐段用 ``O_NOFOLLOW`` 打开父目录，拒绝任何祖先链接或替换。"""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not isinstance(nofollow, int)
        or not isinstance(directory_flag, int)
    ):
        raise ValueError("leader_phase6_formal_shadow_context_unverified")
    try:
        relative = path.relative_to(Path("/private/tmp"))
    except ValueError as exc:
        raise ValueError(
            "leader_phase6_formal_shadow_context_unverified"
        ) from exc
    parts = relative.parts
    if (
        not parts
        or parts[-1] in {"", ".", ".."}
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("leader_phase6_formal_shadow_context_unverified")

    flags = os.O_RDONLY | directory_flag | nofollow
    current = None
    try:
        current = os.open("/private/tmp", flags)
        for component in parts[:-1]:
            child = os.open(component, flags, dir_fd=current)
            try:
                opened = os.fstat(child)
                entry = os.stat(
                    component,
                    dir_fd=current,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or not stat.S_ISDIR(entry.st_mode)
                    or (opened.st_dev, opened.st_ino)
                    != (entry.st_dev, entry.st_ino)
                ):
                    raise ValueError(
                        "leader_phase6_formal_shadow_context_unverified"
                    )
            except BaseException:
                os.close(child)
                raise
            os.close(current)
            current = child
        return current, parts[-1]
    except ValueError:
        if current is not None:
            os.close(current)
        raise
    except (OSError, TypeError) as exc:
        if current is not None:
            os.close(current)
        raise ValueError(
            "leader_phase6_formal_shadow_context_unverified"
        ) from exc


def _capture_private_tmp_regular_file_identity(
    path: Path,
) -> _PrivateTmpFileIdentity:
    """首次验证并固定现有临时普通文件的不可变身份。"""

    parent_fd = None
    descriptor = None
    try:
        parent_fd, name = _open_private_tmp_parent(path)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        identity = _file_identity(os.fstat(descriptor))
        if _file_identity(os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )) != identity:
            raise ValueError("leader_phase6_formal_shadow_context_unverified")
        return identity
    except ValueError:
        raise
    except (OSError, TypeError) as exc:
        raise ValueError(
            "leader_phase6_formal_shadow_context_unverified"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _write_new_json(path: Path, payload: Any) -> _PrivateTmpFileIdentity:
    """安全新建 JSON 并返回写完、同步后的文件身份。"""

    parent_fd = None
    descriptor = None
    created = False
    created_inode = None
    try:
        parent_fd, name = _open_private_tmp_parent(path)
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        created = True
        created_metadata = os.fstat(descriptor)
        created_inode = (created_metadata.st_dev, created_metadata.st_ino)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            identity = _file_identity(os.fstat(stream.fileno()))
            entry_identity = _file_identity(os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            ))
            if entry_identity != identity:
                raise ValueError(
                    "leader_phase6_formal_shadow_context_unverified"
                )
        os.fsync(parent_fd)
        return identity
    except BaseException:
        if created and parent_fd is not None and created_inode is not None:
            try:
                current = os.stat(
                    name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if (current.st_dev, current.st_ino) == created_inode:
                    os.unlink(name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
            except OSError:
                pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _read_private_tmp_regular_bytes(
    path: Path,
    *,
    expected_identity: _PrivateTmpFileIdentity,
) -> bytes:
    """用固定身份读取临时工件，并验证读取前后及目录项均未变化。"""

    if type(expected_identity) is not _PrivateTmpFileIdentity:
        raise ValueError("leader_phase6_formal_shadow_context_unverified")
    parent_fd = None
    descriptor = None
    try:
        parent_fd, name = _open_private_tmp_parent(path)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        before = _file_identity(os.fstat(descriptor))
        entry_before = _file_identity(os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        ))
        if before != expected_identity or entry_before != expected_identity:
            raise ValueError("leader_phase6_formal_shadow_context_unverified")
        chunks = []
        remaining = 16 * 1024 * 1024
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError("leader_phase6_formal_shadow_context_unverified")
        after = _file_identity(os.fstat(descriptor))
        entry_after = _file_identity(os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        ))
        if after != before or entry_after != before:
            raise ValueError("leader_phase6_formal_shadow_context_unverified")
        return b"".join(chunks)
    except ValueError:
        raise
    except (OSError, TypeError) as exc:
        raise ValueError(
            "leader_phase6_formal_shadow_context_unverified"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _canonical_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _market_research_evidence_bundle(
    tradability: Any,
) -> tuple[Mapping[str, object], Optional[Mapping[str, object]]]:
    """从已冻结的同轮市场特征生成阶段9研究输出。"""

    runtime = getattr(tradability, "runtime_inputs", None)
    raw_snapshot = getattr(runtime, "market_snapshot", None)
    try:
        snapshot = MarketFeatureSnapshot.model_validate(dict(raw_snapshot))
    except (TypeError, ValueError):
        return ({
            "contractId": "radar-market-research-state-v1",
            "status": "blocked",
            "radarRunId": getattr(tradability, "radar_run_id", None),
            "asOf": (
                tradability.as_of.isoformat()
                if getattr(tradability, "as_of", None) is not None else None
            ),
            "state": None,
            "metrics": None,
            "reasons": ["market_feature_contract_unverified"],
            "snapshotSha256": None,
            "ruleVersion": "radar-market-research-rule-v1",
            "researchUsable": False,
            "formalUsable": False,
        }, None)
    snapshot_payload = snapshot.model_dump(mode="json", by_alias=True)
    snapshot_evidence = {
        "contractId": MARKET_FEATURE_SNAPSHOT_EVIDENCE_CONTRACT_ID,
        "snapshotSha256": _canonical_sha256(snapshot_payload),
        "snapshot": snapshot_payload,
    }
    if (
        snapshot.radar_run_id != getattr(tradability, "radar_run_id", None)
        or snapshot.as_of != getattr(tradability, "as_of", None)
    ):
        return ({
            **produce_market_research_state(snapshot).to_evidence(),
            "status": "blocked",
            "state": None,
            "metrics": None,
            "reasons": ["market_feature_identity_unverified"],
            "snapshotSha256": None,
            "researchUsable": False,
        }, snapshot_evidence)
    return (
        produce_market_research_state(snapshot).to_evidence(),
        snapshot_evidence,
    )


def _run_live(
    *,
    started_at: datetime,
    output_dir: Path,
    prefreeze_runner: Callable[..., Any] = run_live_prefreeze,
    candidate_selection_runner: Optional[Callable[..., Any]] = None,
    source_collection_runner: Callable[..., Any] = (
        run_leader_phase6_live_source_collection
    ),
    clock: Callable[[], datetime] = lambda: datetime.now(SHANGHAI_TZ),
    initialize_sector_state: bool = True,
    previous_sector_state_path: Optional[Path] = None,
):
    if bool(initialize_sector_state) == bool(previous_sector_state_path):
        raise ValueError(
            "leader_phase6_evidence_sector_state_lineage_unverified"
        )
    prepared, tradability = prefreeze_runner(
        started_at=started_at,
        output_dir=output_dir,
    )
    if (
        type(tradability) is not LeaderTradabilityLiveAcceptanceResult
        or tradability.status
        is not LeaderTradabilityLiveAcceptanceStatus.COMPLETED
    ):
        return prepared, tradability, None, None, (
            LeaderPhase6LiveSourceCollectionResult(
                status=(
                    LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED
                ),
                candidate_source_packet_sha256=None,
                reasons=("leader_phase6_tradability_not_completed",),
            )
        )
    next_sector_state_path = output_dir / (
        f"sector-state-{started_at:%Y%m%dT%H%M%S%f}.json"
    )
    selection = (
        candidate_selection_runner
        or _build_initial_shadow_evidence_candidate_acceptance
    )(
        tradability,
        previous_sector_state_path=previous_sector_state_path,
        next_sector_state_path=next_sector_state_path,
    )
    if selection.status is not LeaderEvidenceCandidateAcceptanceStatus.READY:
        empty = (
            selection.status
            is LeaderEvidenceCandidateAcceptanceStatus.EMPTY
        )
        return prepared, tradability, selection, next_sector_state_path, (
            LeaderPhase6LiveSourceCollectionResult(
                status=(
                    LeaderPhase6LiveSourceReadinessStatus.EMPTY
                    if empty
                    else LeaderPhase6LiveSourceReadinessStatus.SOURCE_UNVERIFIED
                ),
                candidate_source_packet_sha256=None,
                reasons=(
                    *selection.reasons,
                    *(
                        () if empty else (
                            "leader_phase6_evidence_candidate_scope_not_ready",
                        )
                    ),
                ),
            )
        )
    collected_at = clock()
    collection = source_collection_runner(
        selection.tradability,
        artifact_dir=output_dir / "five-source",
        collected_at=collected_at,
        repository=_NoManualRiskRepository(),
        industry_scope=selection.industry_scope,
    )
    return (
        prepared,
        tradability,
        selection,
        next_sector_state_path,
        collection,
    )


def _build_initial_shadow_evidence_candidate_acceptance(
    tradability: Any,
    *,
    sector_threshold_approval_binder: Callable[..., Any] = (
        bind_latest_sector_threshold_approval
    ),
    previous_sector_state_path: Optional[Path] = None,
    next_sector_state_path: Optional[Path] = None,
) -> LeaderEvidenceCandidateAcceptanceResult:
    """从同轮冻结行业状态派生最多15只影子证据候选。"""

    runtime = getattr(tradability, "runtime_inputs", None)
    context = getattr(runtime, "source_context", None)
    plan = getattr(context, "candidate_plan", None)
    prefrozen = getattr(tradability, "phase6_prefrozen_inputs", None)
    sector_rule = getattr(prefrozen, "sector_rule", None)
    source_batch = getattr(sector_rule, "source_batch", None)
    feature_batch = getattr(source_batch, "feature_batch", None)
    approval_record = getattr(prefrozen, "threshold_approval_record", None)
    if (
        plan is None
        or feature_batch is None
        or approval_record is None
        or not callable(sector_threshold_approval_binder)
    ):
        raise ValueError("leader_phase6_evidence_candidate_scope_unverified")
    industry_codes = tuple(sorted({
        item.industry_code for item in plan.items
    }))
    if previous_sector_state_path is None:
        previous_sector_state = build_initial_sector_state_snapshot(
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
        previous_sector_state = load_sector_state_snapshot(
            previous_sector_state_path,
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
    state_production = produce_sector_state_from_prefrozen(
        context,
        prefrozen_inputs=prefrozen,
        previous_snapshot=previous_sector_state,
    )
    if state_production.status is not SectorStateProductionStatus.READY:
        raise ValueError("leader_phase6_evidence_candidate_scope_unverified")
    if next_sector_state_path is not None:
        write_sector_state_snapshot(
            state_production.next_snapshot,
            next_sector_state_path,
        )
    industry_scope = build_leader_evidence_candidate_industry_scope(
        plan=plan,
        sector_rule_bridge=state_production.sector_rule_bridge,
        state_production=state_production,
    )
    connection = sqlite3.connect(":memory:")
    try:
        apply_pending_migrations(
            connection,
            migrations=STAGE6_RADAR_MIGRATIONS,
            clock=lambda: plan.as_of,
        )
        previous_state_snapshot = (
            load_leader_evidence_previous_state_snapshot(
                LeaderRepository(connection, clock=lambda: plan.as_of),
                plan=plan,
            )
        )
    finally:
        connection.close()
    return build_leader_phase6_evidence_candidate_acceptance(
        tradability,
        industry_scope=industry_scope,
        previous_state_snapshot=previous_state_snapshot,
        policy=LeaderEvidenceCandidateSelectionPolicy(),
        sector_threshold_approval_binder=(
            sector_threshold_approval_binder
        ),
    )


def run_cli(
    argv: Optional[Sequence[str]] = None,
    *,
    stdout: TextIO = sys.stdout,
    now_provider: Callable[[], datetime] = (
        lambda: datetime.now(SHANGHAI_TZ)
    ),
    live_runner: Callable[..., Any] = _run_live,
    monotonic_clock: Callable[[], float] = time.monotonic,
    lock_factory: Callable[[Path], Any] = PrivateTmpNoFollowFileLock,
    formal_shadow_input_context: Optional[
        Stage6FormalShadowInputBundleInputs
    ] = None,
    formal_shadow_input_publisher: Callable[..., Stage6FormalShadowInputBundlePublication] = (
        publish_stage6_formal_shadow_input_bundles
    ),
) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.confirm_live_five_source:
        _print({
            "status": "not_run",
            "reason": (
                "leader_phase6_live_five_source_confirmation_missing"
            ),
            "fiveSourceReadyForReview": False,
        }, stdout)
        return 2
    try:
        output_dir = _private_tmp_dir(arguments.output_dir)
    except (OSError, ValueError):
        _print({
            "status": "error",
            "reason": (
                "leader_phase6_live_five_source_output_path_unverified"
            ),
            "fiveSourceReadyForReview": False,
        }, stdout)
        return 3
    try:
        formal_shadow_input_root = (
            _private_tmp_dir(arguments.formal_shadow_input_root)
            if arguments.formal_shadow_input_root is not None
            else None
        )
        if (
            formal_shadow_input_root is not None
            and formal_shadow_input_context is not None
        ):
            raise ValueError
    except (OSError, ValueError):
        _print({
            "status": "error",
            "reason": "leader_phase6_formal_shadow_context_unverified",
            "fiveSourceReadyForReview": False,
        }, stdout)
        return 3
    if bool(arguments.initialize_sector_state) == bool(
        arguments.previous_sector_state
    ):
        _print({
            "status": "error",
            "reason": (
                "leader_phase6_evidence_sector_state_lineage_unverified"
            ),
            "fiveSourceReadyForReview": False,
        }, stdout)
        return 3
    try:
        previous_sector_state_path = _private_tmp_state_path(
            arguments.previous_sector_state
        )
    except (OSError, ValueError):
        _print({
            "status": "error",
            "reason": (
                "leader_phase6_evidence_sector_state_path_unverified"
            ),
            "fiveSourceReadyForReview": False,
        }, stdout)
        return 3
    formal_shadow_input_publication: Optional[
        Stage6FormalShadowInputBundlePublication
    ] = None
    formal_shadow_lock = None
    formal_shadow_lock_acquired = False
    formal_shadow_publish_status = "not_requested"
    formal_shadow_publish_reason = None
    try:
        started_at = now_provider()
        if (
            not isinstance(started_at, datetime)
            or started_at.tzinfo is None
            or started_at.utcoffset() is None
        ):
            raise ValueError("leader_phase6_live_five_source_clock_unverified")
        duration_started_at = None
        formal_shadow_requested = (
            formal_shadow_input_context is not None
            or formal_shadow_input_root is not None
        )
        if formal_shadow_requested:
            if type(formal_shadow_input_context) is not Stage6FormalShadowInputBundleInputs:
                if formal_shadow_input_context is not None:
                    raise ValueError("leader_phase6_formal_shadow_context_unverified")
            formal_shadow_lock = lock_factory(
                output_dir / ".leader-phase6-formal-shadow-input.lock"
            )
            try:
                formal_shadow_lock_acquired = (
                    formal_shadow_lock.acquire(blocking=False) is True
                )
            except Exception:
                formal_shadow_publish_status = "not_published"
                formal_shadow_publish_reason = (
                    "leader_phase6_formal_shadow_lock_unverified"
                )
            if formal_shadow_lock_acquired:
                duration_started_at = monotonic_clock()
                if not isinstance(duration_started_at, (int, float)) or isinstance(
                    duration_started_at, bool
                ):
                    raise ValueError("leader_phase6_formal_shadow_clock_unverified")
            elif formal_shadow_publish_reason is None:
                formal_shadow_publish_status = "contended"
                formal_shadow_publish_reason = (
                    "leader_phase6_formal_shadow_lock_contended"
                )
        (
            prepared,
            tradability,
            selection,
            sector_state_path,
            collection,
        ) = live_runner(
            started_at=started_at,
            output_dir=output_dir,
            initialize_sector_state=arguments.initialize_sector_state,
            previous_sector_state_path=previous_sector_state_path,
        )
        sector_state_identity = None
        if type(collection) is not LeaderPhase6LiveSourceCollectionResult:
            raise ValueError(
                "leader_phase6_live_five_source_result_unverified"
            )
        upstream_incomplete = selection is None and sector_state_path is None
        if upstream_incomplete:
            if (
                type(tradability)
                is not LeaderTradabilityLiveAcceptanceResult
                or tradability.status
                is LeaderTradabilityLiveAcceptanceStatus.COMPLETED
            ):
                raise ValueError(
                    "leader_phase6_live_five_source_result_unverified"
                )
        else:
            if not isinstance(sector_state_path, Path):
                raise ValueError(
                    "leader_phase6_evidence_sector_state_result_unverified"
                )
            try:
                sector_state_identity = (
                    _capture_private_tmp_regular_file_identity(
                        sector_state_path
                    )
                )
            except (OSError, ValueError) as exc:
                raise ValueError(
                    "leader_phase6_evidence_sector_state_result_unverified"
                ) from exc
            if (
                type(selection)
                is not LeaderEvidenceCandidateAcceptanceResult
                or selection.status
                not in (
                    LeaderEvidenceCandidateAcceptanceStatus.READY,
                    LeaderEvidenceCandidateAcceptanceStatus.EMPTY,
                    LeaderEvidenceCandidateAcceptanceStatus.BLOCKED,
                )
                or (
                    selection.status
                    is LeaderEvidenceCandidateAcceptanceStatus.READY
                    and (
                        selection.tradability is None
                        or not 1 <= selection.candidate_count <= 15
                    )
                )
            ):
                raise ValueError(
                    "leader_phase6_evidence_candidate_result_unverified"
                )
        candidate_source_packet = collection.candidate_source_packet
        candidate_source_artifact = None
        if collection.candidate_source_packet_sha256 is not None:
            if (
                not isinstance(candidate_source_packet, Mapping)
                or candidate_source_packet.get("packetSha256")
                != collection.candidate_source_packet_sha256
            ):
                raise ValueError(
                    "leader_phase6_live_candidate_source_packet_unverified"
                )
            candidate_source_artifact = output_dir / (
                f"stage6-live-candidate-source-"
                f"{started_at:%Y%m%dT%H%M%S%f}.json"
            )
            _write_new_json(
                candidate_source_artifact,
                candidate_source_packet,
            )
        verification_candidate_source_packet = (
            collection.verification_candidate_source_packet
        )
        verification_candidate_source_artifact = None
        if (
            collection.verification_candidate_source_packet_sha256
            is not None
        ):
            if (
                not isinstance(
                    verification_candidate_source_packet,
                    Mapping,
                )
                or verification_candidate_source_packet.get("packetSha256")
                != collection.verification_candidate_source_packet_sha256
            ):
                raise ValueError(
                    "leader_phase6_live_candidate_source_packet_unverified"
                )
            if (
                collection.verification_candidate_source_packet_sha256
                == collection.candidate_source_packet_sha256
            ):
                verification_candidate_source_artifact = (
                    candidate_source_artifact
                )
            else:
                verification_candidate_source_artifact = output_dir / (
                    f"stage6-live-verification-candidate-source-"
                    f"{started_at:%Y%m%dT%H%M%S%f}.json"
                )
                _write_new_json(
                    verification_candidate_source_artifact,
                    verification_candidate_source_packet,
                )
        artifact = output_dir / (
            f"stage6-live-five-source-"
            f"{started_at:%Y%m%dT%H%M%S%f}.json"
        )
        (
            market_research_state,
            market_feature_snapshot_evidence,
        ) = _market_research_evidence_bundle(tradability)
        artifact_identity = _write_new_json(artifact, {
            "prepared": prepared.to_evidence(),
            "tradability": tradability.to_evidence(),
            "marketResearchState": market_research_state,
            "marketFeatureSnapshotEvidence": (
                market_feature_snapshot_evidence
            ),
            "evidenceCandidateSelection": (
                selection.to_evidence() if selection is not None else None
            ),
            "evidenceCandidateSelectionMode": (
                "initial_shadow_empty_leader_state_repository"
            ),
            "sectorStateSnapshotPath": (
                str(sector_state_path)
                if sector_state_path is not None else None
            ),
            "sectorStateLineageMode": (
                "initialized"
                if arguments.initialize_sector_state
                else "continued"
            ),
            "collection": collection.to_evidence(),
            "candidateSourcePacketPath": (
                str(candidate_source_artifact)
                if candidate_source_artifact is not None
                else None
            ),
            "verificationCandidateSourcePacketPath": (
                str(verification_candidate_source_artifact)
                if verification_candidate_source_artifact is not None
                else None
            ),
            "gate": {
                "formalScoreReady": False,
                "formalGateReady": False,
                "formalUsable": False,
                "stateTransitionAllowed": False,
            },
        })
        if formal_shadow_requested and formal_shadow_lock_acquired:
            observed_at = now_provider()
            if (
                not isinstance(observed_at, datetime)
                or observed_at.tzinfo is None
                or observed_at.utcoffset() is None
            ):
                raise ValueError("leader_phase6_formal_shadow_clock_unverified")
            ended_at = monotonic_clock()
            if (
                not isinstance(ended_at, (int, float))
                or isinstance(ended_at, bool)
                or ended_at < duration_started_at
            ):
                raise ValueError("leader_phase6_formal_shadow_clock_unverified")
            duration_ms = int((ended_at - duration_started_at) * 1000)
            try:
                effective_context = formal_shadow_input_context or (
                    build_stage6_formal_shadow_input_context(
                        output_root=formal_shadow_input_root,
                        prepared=prepared,
                    )
                )
                source_artifact_json_bytes = _read_private_tmp_regular_bytes(
                    artifact,
                    expected_identity=artifact_identity,
                )
                sector_snapshot_json_bytes = _read_private_tmp_regular_bytes(
                    sector_state_path,
                    expected_identity=sector_state_identity,
                )
                formal_shadow_input_publication = formal_shadow_input_publisher(
                    output_root=effective_context.output_root,
                    source_artifact_json_bytes=source_artifact_json_bytes,
                    sector_snapshot_json_bytes=sector_snapshot_json_bytes,
                    collection_policy_json_bytes=(
                        effective_context.collection_policy_json_bytes
                    ),
                    calendar_envelope_json_bytes=(
                        effective_context.calendar_envelope_json_bytes
                    ),
                    calendar_document_bytes=(
                        effective_context.calendar_document_bytes
                    ),
                    observed_at=observed_at,
                    duration_ms=duration_ms,
                    lock_acquired=True,
                )
                if type(formal_shadow_input_publication) is not Stage6FormalShadowInputBundlePublication:
                    raise ValueError(
                        "leader_phase6_formal_shadow_context_unverified"
                    )
                formal_shadow_publish_status = (
                    "published"
                    if formal_shadow_input_publication.input_dirs
                    else "not_published"
                )
            except Exception as exc:
                formal_shadow_input_publication = None
                formal_shadow_publish_status = "not_published"
                formal_shadow_publish_reason = str(exc) or (
                    "leader_phase6_formal_shadow_context_unverified"
                )
    except Exception as exc:
        safe_reason = str(exc)
        if safe_reason not in _SAFE_PREFREEZE_FAILURE_REASONS:
            safe_reason = "leader_phase6_live_five_source_execution_failed"
        _print({
            "status": "error",
            "reason": safe_reason,
            "fiveSourceReadyForReview": False,
        }, stdout)
        return 3
    finally:
        if formal_shadow_lock is not None and formal_shadow_lock_acquired:
            try:
                formal_shadow_lock.release()
            except Exception:
                pass
    ready = (
        collection.status
        is LeaderPhase6LiveSourceReadinessStatus.READY_FOR_REVIEW
    )
    valid_empty = (
        collection.status is LeaderPhase6LiveSourceReadinessStatus.EMPTY
    )
    _print({
        "status": collection.status.value,
        "validEmptyResult": valid_empty,
        "artifactPath": str(artifact),
        "candidateSourcePacketSha256": (
            collection.candidate_source_packet_sha256
        ),
        "candidateSourcePacketPath": (
            str(candidate_source_artifact)
            if candidate_source_artifact is not None
            else None
        ),
        "verificationCandidateSourcePacketSha256": (
            collection.verification_candidate_source_packet_sha256
        ),
        "verificationCandidateSourcePacketPath": (
            str(verification_candidate_source_artifact)
            if verification_candidate_source_artifact is not None
            else None
        ),
        "preliminaryCandidateCount": (
            selection.preliminary_candidate_count
            if selection is not None else 0
        ),
        "evidenceCandidateCount": (
            selection.candidate_count if selection is not None else 0
        ),
        "evidenceCandidatePlanId": (
            selection.evidence_plan.candidate_plan.candidate_set_id
            if selection is not None
            and selection.evidence_plan is not None
            and selection.evidence_plan.candidate_plan is not None
            else None
        ),
        "qualifiedCandidateCount": (
            collection.qualification.qualified_candidate_count
            if collection.qualification is not None else 0
        ),
        "qualifiedCandidatePlanId": (
            collection.qualification.candidate_plan.candidate_set_id
            if collection.qualification is not None
            and collection.qualification.candidate_plan is not None
            else None
        ),
        "sectorStateSnapshotPath": (
            str(sector_state_path)
            if sector_state_path is not None else None
        ),
        "sectorStateLineageMode": (
            "initialized"
            if arguments.initialize_sector_state
            else "continued"
        ),
        "marketResearchState": market_research_state,
        "marketFeatureSnapshotEvidenceSha256": (
            market_feature_snapshot_evidence["snapshotSha256"]
            if market_feature_snapshot_evidence is not None else None
        ),
        "formalShadowInputPublishStatus": formal_shadow_publish_status,
        "formalShadowInputPublishReason": formal_shadow_publish_reason,
        "formalShadowInputDirs": (
            {
                module: str(path)
                for module, path in formal_shadow_input_publication.input_dirs.items()
            }
            if formal_shadow_input_publication is not None else {}
        ),
        "fiveSourceReadyForReview": ready,
        "reasons": list(collection.reasons),
        "gate": {
            "formalScoreReady": False,
            "formalGateReady": False,
            "formalUsable": False,
            "stateTransitionAllowed": False,
        },
    }, stdout)
    return 0 if ready or valid_empty else 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
