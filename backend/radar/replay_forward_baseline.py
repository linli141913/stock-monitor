"""阶段9真实前向基线采集器。

该入口只向显式 ``/private/tmp`` 新目录写文件，不连接 SQLite，不发布到
页面默认回放仓，也不把本轮观测倒填到过去。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

from pydantic import BaseModel
import requests

from radar.replay_contracts import (
    RadarReplayEvidence,
    RadarReplayInput,
    RadarReplaySample,
)
from radar.replay_service import (
    RadarReplayQualityReport,
    build_replay_quality_report,
)
from radar.replay_source_adapters import (
    adapt_corporate_actions,
    adapt_etf,
    adapt_index,
    adapt_industry,
    adapt_security_universe,
    adapt_trading_rules,
)
from radar.sources.etf_index_evidence import fetch_csindex_index_poc
from radar.sources.etf_product_master import fetch_etf_product_master
from radar.sources.industry_classification import fetch_industry_classification
from radar.sources.security_master import fetch_security_master
from radar.sources.corporate_actions import (
    build_cninfo_pdf_document_loader,
    fetch_corporate_action_forward_snapshot,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CAPCO_CURRENT_RELEASE_URL = (
    "https://www.capco.org.cn/xhgg/hyfl/hyfljg/202604/"
    "20260403/j_2026040315001700017751997384265508.html"
)
FORWARD_RULE_VERSION = "radar-replay-forward-baseline-v1"


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


@dataclass(frozen=True)
class ForwardEtfFormalAdmissionHooks:
    """Two-phase ETF collection keeps every fetch before sample freeze."""

    collect: Callable[..., Any]
    finalize: Callable[..., Any]


@dataclass(frozen=True)
class ForwardBaselineSources:
    security: Callable[..., Any]
    industry: Callable[..., Any]
    index: Callable[..., Any]
    etf: Callable[..., Any]
    corporate_action: Optional[Callable[..., Any]] = None
    etf_formal_admission: Optional[
        ForwardEtfFormalAdmissionHooks
    ] = None


def default_forward_baseline_sources() -> ForwardBaselineSources:
    from radar.etf_formal_admission_collector import (
        collect_live_etf_formal_admission_materials,
        finalize_etf_formal_admission_materials,
    )

    return ForwardBaselineSources(
        security=fetch_security_master,
        industry=lambda **kwargs: fetch_industry_classification(
            publication_page_url=CAPCO_CURRENT_RELEASE_URL,
            supplement_official_exchange_categories=True,
            **kwargs,
        ),
        index=fetch_csindex_index_poc,
        etf=fetch_etf_product_master,
        corporate_action=fetch_corporate_action_forward_snapshot,
        etf_formal_admission=ForwardEtfFormalAdmissionHooks(
            collect=collect_live_etf_formal_admission_materials,
            finalize=finalize_etf_formal_admission_materials,
        ),
    )


@dataclass(frozen=True)
class ForwardBaselineResult:
    output_dir: Path
    source_snapshots_path: Path
    replay_input_path: Path
    quality_report_path: Path
    manifest_path: Path
    etf_formal_admission_path: Optional[Path]
    replay: RadarReplayInput
    report: RadarReplayQualityReport


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}_timezone_required")
    return value


def _validate_output_dir(output_dir: Path) -> Path:
    resolved = output_dir.expanduser().resolve()
    private_tmp = Path("/private/tmp").resolve()
    try:
        resolved.relative_to(private_tmp)
    except ValueError as exc:
        raise ValueError("output_dir_must_be_private_tmp") from exc
    if resolved == private_tmp:
        raise ValueError("output_dir_must_be_private_tmp_child")
    if resolved.exists():
        raise ValueError("output_dir_must_be_new")
    return resolved


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _jsonable(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return value


def _encoded(payload: Any) -> bytes:
    return (
        json.dumps(
            _jsonable(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write_json(path: Path, payload: Any) -> str:
    content = _encoded(payload)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
    return hashlib.sha256(content).hexdigest()


def _call(function: Callable[..., Any], **kwargs) -> Tuple[Any, Optional[Exception]]:
    try:
        return function(**kwargs), None
    except Exception as exc:  # 外部来源按域隔离失败，报告仍须可生成
        return None, exc


def _call_with_one_network_retry(
    function: Callable[..., Any],
    **kwargs,
) -> Tuple[Any, Optional[Exception]]:
    """仅对瞬时网络故障重试一次，稳定失败仍按域隔离。"""
    result, error = _call(function, **kwargs)
    if not isinstance(
        error,
        (requests.exceptions.Timeout, requests.exceptions.ConnectionError),
    ):
        return result, error
    return _call(function, **kwargs)


def _failed_evidence(
    *,
    domain: str,
    source: str,
    fetched_at: datetime,
    error: Exception,
) -> RadarReplayEvidence:
    payload = {
        "observationKind": "forward_observed_failure",
        "errorType": type(error).__name__,
        "reasons": [f"{domain}_source_failed"],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["snapshotSha256"] = hashlib.sha256(canonical).hexdigest()
    return RadarReplayEvidence(
        evidenceId=f"{domain}:failed:{fetched_at.isoformat()}",
        domain=domain,
        sourceId=f"{domain}-official-source-failed",
        source=source,
        sourceTime=None,
        fetchedAt=fetched_at,
        effectiveFrom=None,
        status="failed",
        payload=payload,
    )


def collect_forward_replay_baseline(
    *,
    confirm_live_baseline: bool,
    output_dir: Path,
    sample_role: str = "development",
    cninfo_pdf_cache_dir: Optional[Path] = None,
    radar_run_id: Optional[str] = None,
    formal_etf_symbols: Tuple[str, ...] = (),
    sources: Optional[ForwardBaselineSources] = None,
    clock: Callable[[], datetime] = _now,
) -> ForwardBaselineResult:
    if confirm_live_baseline is not True:
        raise ValueError("confirmation_required")
    if sample_role not in {"development", "calibration", "holdout"}:
        raise ValueError("sample_role_invalid")
    resolved_output = _validate_output_dir(Path(output_dir))
    normalized_formal_etfs = tuple(dict.fromkeys(
        str(value or "").strip() for value in formal_etf_symbols
    ))
    if (
        len(normalized_formal_etfs) != len(formal_etf_symbols)
        or len(normalized_formal_etfs) > 10
        or any(
            len(symbol) != 6 or not symbol.isdigit()
            for symbol in normalized_formal_etfs
        )
    ):
        raise ValueError("formal_etf_symbols_invalid")
    cninfo_document_loader = (
        build_cninfo_pdf_document_loader(Path(cninfo_pdf_cache_dir))
        if cninfo_pdf_cache_dir is not None
        else None
    )
    active_sources = sources or default_forward_baseline_sources()
    if (
        normalized_formal_etfs
        and active_sources.etf_formal_admission is None
    ):
        raise ValueError("formal_etf_collector_unavailable")

    started_at = _aware(clock(), "startedAt")
    stamp = started_at.strftime("%Y%m%dT%H%M%S%f")
    if radar_run_id is None:
        radar_run_id = f"stage9-forward-{stamp}"
    elif (
        not isinstance(radar_run_id, str)
        or not radar_run_id.strip()
        or len(radar_run_id.strip()) > 240
    ):
        raise ValueError("radar_run_id_unverified")
    else:
        radar_run_id = radar_run_id.strip()

    security, security_error = _call(
        active_sources.security,
        radar_run_id=radar_run_id,
        batch_id=f"security-{stamp}",
        as_of=started_at,
    )
    security_items = list(security.items) if security_error is None else []
    if security_error is None:
        industry, industry_error = _call(
            active_sources.industry,
            radar_run_id=radar_run_id,
            batch_id=f"industry-{stamp}",
            as_of=started_at,
            current_security_master=security_items,
        )
    else:
        industry = None
        industry_error = RuntimeError(
            "industry_security_master_dependency_failed"
        )
    index, index_error = _call_with_one_network_retry(
        active_sources.index,
        index_code="000300",
        as_of=started_at,
    )
    etf, etf_error = _call(
        active_sources.etf,
        radar_run_id=radar_run_id,
        batch_id=f"etf-{stamp}",
        as_of=started_at,
    )
    if active_sources.corporate_action is None:
        corporate_action = None
        corporate_action_error = None
    else:
        corporate_action_kwargs = {"as_of": started_at}
        if cninfo_document_loader is not None:
            corporate_action_kwargs["cninfo_document_loader"] = (
                cninfo_document_loader
            )
        corporate_action, corporate_action_error = _call(
            active_sources.corporate_action,
            **corporate_action_kwargs,
        )

    formal_etf_materials = None
    if normalized_formal_etfs:
        if etf_error is not None or industry_error is not None:
            raise ValueError("formal_etf_dependencies_unavailable")
        hooks = active_sources.etf_formal_admission
        assert hooks is not None
        formal_etf_materials = hooks.collect(
            symbols=normalized_formal_etfs,
            radar_run_id=radar_run_id,
            product_master=etf,
            industry_classification=industry,
            started_at=started_at,
        )

    # 所有来源调用完成之后才冻结样本时点，确保 fetchedAt 不会晚于 asOf。
    sample_as_of = _aware(clock(), "sampleAsOf")
    evidence = [
        (
            adapt_security_universe(security, sample_as_of=sample_as_of)
            if security_error is None
            else _failed_evidence(
                domain="security_universe",
                source="证券交易所官方证券名册",
                fetched_at=sample_as_of,
                error=security_error,
            )
        ),
        adapt_trading_rules(
            sample_as_of=sample_as_of,
            fetched_at=started_at,
        ),
        (
            adapt_industry(industry, sample_as_of=sample_as_of)
            if industry_error is None
            else _failed_evidence(
                domain="industry",
                source="中国上市公司协会行业分类",
                fetched_at=sample_as_of,
                error=industry_error,
            )
        ),
        (
            adapt_index(index, sample_as_of=sample_as_of)
            if index_error is None
            else _failed_evidence(
                domain="index",
                source="中证指数有限公司",
                fetched_at=sample_as_of,
                error=index_error,
            )
        ),
        (
            adapt_etf(etf, sample_as_of=sample_as_of)
            if etf_error is None
            else _failed_evidence(
                domain="etf",
                source="证券交易所官方ETF产品名册",
                fetched_at=sample_as_of,
                error=etf_error,
            )
        ),
        (
            adapt_corporate_actions(
                corporate_action,
                sample_as_of=sample_as_of,
                observed_at=started_at,
                allowed_symbols={item.symbol for item in security_items},
            )
            if corporate_action_error is None
            else _failed_evidence(
                domain="corporate_action",
                source="证券交易所官方公司行为",
                fetched_at=sample_as_of,
                error=corporate_action_error,
            )
        ),
    ]
    sample_stamp = sample_as_of.strftime("%Y%m%dT%H%M%S%f")
    sample = RadarReplaySample(
        sampleId=f"forward-{sample_role}-{sample_stamp}",
        role=sample_role,
        asOf=sample_as_of,
        radarRunId=radar_run_id,
        ruleVersion=FORWARD_RULE_VERSION,
        evidence=evidence,
        expectedLabels=[],
    )
    formal_etf_bundle = None
    if formal_etf_materials is not None:
        hooks = active_sources.etf_formal_admission
        assert hooks is not None
        formal_etf_bundle = hooks.finalize(
            materials=formal_etf_materials,
            sample_id=sample.sample_id,
            radar_run_id=radar_run_id,
            as_of=sample_as_of,
        )
    created_at = _aware(clock(), "createdAt")
    replay = RadarReplayInput(
        replayRunId=f"replay-{radar_run_id}",
        createdAt=created_at,
        samples=[sample],
    )
    report = build_replay_quality_report(replay)

    source_payload = {
        "radarRunId": radar_run_id,
        "startedAt": started_at,
        "sampleAsOf": sample_as_of,
        "sampleRole": sample_role,
        "sources": {
            "security_universe": (
                _jsonable(security)
                if security_error is None
                else {"errorType": type(security_error).__name__}
            ),
            "industry": (
                _jsonable(industry)
                if industry_error is None
                else {"errorType": type(industry_error).__name__}
            ),
            "index": (
                _jsonable(index)
                if index_error is None
                else {"errorType": type(index_error).__name__}
            ),
            "etf": (
                _jsonable(etf)
                if etf_error is None
                else {"errorType": type(etf_error).__name__}
            ),
            "corporate_action": (
                _jsonable(corporate_action)
                if corporate_action_error is None
                and corporate_action is not None
                else (
                    {"errorType": type(corporate_action_error).__name__}
                    if corporate_action_error is not None
                    else {
                        "status": "missing",
                        "reasons": [
                            "corporate_action_versioned_source_missing"
                        ],
                    }
                )
            ),
            "etf_formal_admission_collection": (
                formal_etf_materials.to_summary()
                if formal_etf_materials is not None
                and hasattr(formal_etf_materials, "to_summary")
                else None
            ),
        },
    }

    resolved_output.mkdir(parents=True, exist_ok=False)
    source_path = resolved_output / "source-snapshots.json"
    replay_path = resolved_output / "replay-input.json"
    report_path = resolved_output / "quality-report.json"
    manifest_path = resolved_output / "manifest.json"
    etf_formal_admission_path = (
        resolved_output / "etf-formal-admission.json"
        if formal_etf_bundle is not None
        else None
    )
    files = {
        "sourceSnapshots": {
            "path": source_path.name,
            "sha256": _atomic_write_json(source_path, source_payload),
        },
        "replayInput": {
            "path": replay_path.name,
            "sha256": _atomic_write_json(replay_path, replay),
        },
        "qualityReport": {
            "path": report_path.name,
            "sha256": _atomic_write_json(report_path, report),
        },
    }
    if (
        formal_etf_bundle is not None
        and etf_formal_admission_path is not None
    ):
        files["etfFormalAdmission"] = {
            "path": etf_formal_admission_path.name,
            "sha256": _atomic_write_json(
                etf_formal_admission_path,
                formal_etf_bundle.to_evidence(),
            ),
            "snapshotSha256": formal_etf_bundle.snapshot_sha256,
        }
    manifest = {
        "contractId": "radar-replay-forward-baseline-manifest-v1",
        "replayRunId": replay.replay_run_id,
        "radarRunId": radar_run_id,
        "sampleAsOf": sample_as_of,
        "sampleRole": sample_role,
        "createdAt": created_at,
        "status": report.status,
        "files": files,
    }
    _atomic_write_json(manifest_path, manifest)

    return ForwardBaselineResult(
        output_dir=resolved_output,
        source_snapshots_path=source_path,
        replay_input_path=replay_path,
        quality_report_path=report_path,
        manifest_path=manifest_path,
        etf_formal_admission_path=etf_formal_admission_path,
        replay=replay,
        report=report,
    )
