"""阶段5 ETF版本化仓储。

仓储只接受调用方显式提供且已经完成阶段5可选迁移的连接，
不会查找数据库路径、自动迁移或在导入时打开数据库。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

from radar.contracts import (
    EtfDailyFact,
    EtfIndexIdentityEvidence,
    EtfProductMasterRecord,
    IndexConstituentSetEvidence,
    IndexIndustryExposureResult,
    IndexMethodologyEvidence,
)
from radar.migrations import (
    ETF_STORAGE_MIGRATION,
    STAGE5_RADAR_MIGRATIONS,
    validate_applied_migrations,
)
from radar.repository import (
    RadarRepositoryError,
    RepositoryConflictError,
    RepositoryStateError,
    RepositoryWriteError,
    _canonical_json,
    _datetime_text,
    _parse_datetime,
)


UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _date_text(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _datetime_optional_text(value: Optional[datetime]) -> Optional[str]:
    return _datetime_text(value, "时间") if value is not None else None


def _json_text(value: Any) -> str:
    return _canonical_json(value)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_json_text(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _product_record_checksum(
    record: EtfProductMasterRecord,
    *,
    source_contract_id: str,
    evidence_sha256: Optional[str],
    version_time_kind: str,
    official_effective_from: Optional[str],
) -> str:
    payload = record.model_dump(mode="json", by_alias=True)
    payload.pop("fetchedAt", None)
    payload.update({
        "sourceContractId": source_contract_id,
        "evidenceSha256": evidence_sha256,
        "versionTimeKind": version_time_kind,
        "officialEffectiveFrom": official_effective_from,
    })
    return _checksum(payload)


class EtfProductVersionTimeKind(str, Enum):
    OFFICIAL_EFFECTIVE = "official_effective"
    FIRST_OBSERVED = "first_observed"


@dataclass(frozen=True)
class EtfProductProfileTransition:
    record: EtfProductMasterRecord
    profile_id: str
    source_contract_id: str
    first_observed_at: datetime
    official_effective_from: Optional[datetime] = None
    source_time: Optional[datetime] = None
    evidence_url: Optional[str] = None
    evidence_sha256: Optional[str] = None


@dataclass(frozen=True)
class EtfProductMasterBatchWriteResult:
    inserted: int
    unchanged: int

    @property
    def item_count(self) -> int:
        return self.inserted + self.unchanged


@dataclass(frozen=True)
class _PreparedProductProfileTransition:
    transition: EtfProductProfileTransition
    version_time_kind: EtfProductVersionTimeKind
    official_effective_from_text: Optional[str]
    first_observed_at_text: str
    effective_from_text: str
    record_checksum: str


class EtfRepository:
    """在调用方连接上执行阶段5 ETF仓储读写。"""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self._connection = connection
        self._clock = clock
        self._require_storage()

    def _clock_text(self) -> str:
        return _datetime_text(self._clock(), "仓储写入时间")

    def _require_storage(self) -> None:
        try:
            validate_applied_migrations(
                self._connection,
                migrations=STAGE5_RADAR_MIGRATIONS,
            )
        except Exception as exc:
            if isinstance(exc, RadarRepositoryError):
                raise
            raise RepositoryStateError(
                "数据库未完成阶段5迁移版本4或迁移记录已漂移"
            ) from exc

        row = self._connection.execute(
            "SELECT name, checksum FROM radar_schema_migrations "
            "WHERE version=?",
            (ETF_STORAGE_MIGRATION.version,),
        ).fetchone()
        expected = (ETF_STORAGE_MIGRATION.name, ETF_STORAGE_MIGRATION.checksum)
        if row is None or tuple(row) != expected:
            raise RepositoryStateError(
                "数据库未完成阶段5迁移版本4或迁移记录已漂移"
            )

    @contextmanager
    def _transaction(self):
        if self._connection.in_transaction:
            raise RepositoryStateError("ETF仓储写入前连接不能处于未提交事务中")
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("BEGIN IMMEDIATE")
            yield
            self._connection.commit()
        except RadarRepositoryError:
            self._connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise RepositoryConflictError(
                f"ETF仓储约束冲突并已回滚：{exc}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            self._connection.rollback()
            raise RepositoryWriteError(
                f"ETF仓储写入失败并已回滚：{type(exc).__name__}"
            ) from exc
        except Exception:
            self._connection.rollback()
            raise

    @staticmethod
    def _prepare_product_transition(
        transition: EtfProductProfileTransition,
    ) -> _PreparedProductProfileTransition:
        profile_id = _required_text(transition.profile_id, "profile_id")
        source_contract_id = _required_text(
            transition.source_contract_id,
            "source_contract_id",
        )
        first_observed_at_text = _datetime_text(
            transition.first_observed_at,
            "first_observed_at",
        )
        fetched_at_text = _datetime_text(
            transition.record.fetched_at,
            "fetched_at",
        )
        if fetched_at_text < first_observed_at_text:
            raise ValueError("fetched_at不能早于first_observed_at")

        official_effective_from_text = _datetime_optional_text(
            transition.official_effective_from
        )
        if official_effective_from_text is None:
            version_time_kind = EtfProductVersionTimeKind.FIRST_OBSERVED
            effective_from_text = first_observed_at_text
        else:
            version_time_kind = EtfProductVersionTimeKind.OFFICIAL_EFFECTIVE
            effective_from_text = official_effective_from_text

        normalized = EtfProductProfileTransition(
            record=transition.record,
            profile_id=profile_id,
            source_contract_id=source_contract_id,
            first_observed_at=transition.first_observed_at,
            official_effective_from=transition.official_effective_from,
            source_time=transition.source_time,
            evidence_url=transition.evidence_url,
            evidence_sha256=transition.evidence_sha256,
        )
        return _PreparedProductProfileTransition(
            transition=normalized,
            version_time_kind=version_time_kind,
            official_effective_from_text=official_effective_from_text,
            first_observed_at_text=first_observed_at_text,
            effective_from_text=effective_from_text,
            record_checksum=_product_record_checksum(
                transition.record,
                source_contract_id=source_contract_id,
                evidence_sha256=transition.evidence_sha256,
                version_time_kind=version_time_kind.value,
                official_effective_from=official_effective_from_text,
            ),
        )

    def upsert_product_profile(
        self,
        record: EtfProductMasterRecord,
        *,
        profile_id: str,
        source_contract_id: str,
        first_observed_at: datetime,
        official_effective_from: Optional[datetime] = None,
        effective_to: Optional[datetime] = None,
        source_time: Optional[datetime] = None,
        evidence_url: Optional[str] = None,
        evidence_sha256: Optional[str] = None,
    ) -> bool:
        prepared = self._prepare_product_transition(
            EtfProductProfileTransition(
                record=record,
                profile_id=profile_id,
                source_contract_id=source_contract_id,
                first_observed_at=first_observed_at,
                official_effective_from=official_effective_from,
                source_time=source_time,
                evidence_url=evidence_url,
                evidence_sha256=evidence_sha256,
            )
        )
        effective_to_text = _datetime_optional_text(effective_to)
        if (
            effective_to_text is not None
            and effective_to_text <= prepared.effective_from_text
        ):
            raise ValueError("effective_to必须晚于effective_from")
        with self._transaction():
            existing = self._connection.execute(
                "SELECT record_checksum FROM radar_etf_product_profiles "
                "WHERE profile_id=?",
                (prepared.transition.profile_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] == prepared.record_checksum:
                    return False
                raise RepositoryConflictError(
                    f"ETF产品版本{prepared.transition.profile_id}内容冲突"
                )
            self._insert_product_profile_row(
                prepared,
                effective_to_text=effective_to_text,
            )
        return True

    def transition_product_profile(
        self,
        record: EtfProductMasterRecord,
        *,
        profile_id: str,
        source_contract_id: str,
        first_observed_at: datetime,
        official_effective_from: Optional[datetime] = None,
        source_time: Optional[datetime] = None,
        evidence_url: Optional[str] = None,
        evidence_sha256: Optional[str] = None,
    ) -> bool:
        """内容变化时原子关闭当前产品版本并创建下一版本。"""

        prepared = self._prepare_product_transition(
            EtfProductProfileTransition(
                record=record,
                profile_id=profile_id,
                source_contract_id=source_contract_id,
                first_observed_at=first_observed_at,
                official_effective_from=official_effective_from,
                source_time=source_time,
                evidence_url=evidence_url,
                evidence_sha256=evidence_sha256,
            )
        )
        with self._transaction():
            return self._transition_product_profile_in_transaction(prepared)

    def sync_product_master_batch(
        self,
        *,
        product_master_run_id: str,
        as_of: datetime,
        source: str,
        source_time: Optional[datetime],
        fetched_at: datetime,
        status: str,
        expected_count: Optional[int],
        returned_count: int,
        row_coverage: Optional[float],
        required_field_coverage: Mapping[str, float],
        issues: Sequence[Mapping[str, Any]],
        transitions: Sequence[EtfProductProfileTransition],
    ) -> EtfProductMasterBatchWriteResult:
        """原子记录一轮主档审计，并仅在完整成功时推进全部产品版本。"""

        product_master_run_id = _required_text(
            product_master_run_id,
            "product_master_run_id",
        )
        source = _required_text(source, "source")
        as_of_text = _datetime_text(as_of, "as_of")
        fetched_at_text = _datetime_text(fetched_at, "fetched_at")
        if fetched_at_text < as_of_text:
            raise ValueError("fetched_at不能早于as_of")
        if status not in {"succeeded", "degraded", "failed"}:
            raise ValueError("产品主档批次状态无效")
        if expected_count is not None and expected_count < 0:
            raise ValueError("expected_count不能小于0")
        if returned_count < 0:
            raise ValueError("returned_count不能小于0")
        if row_coverage is not None and not 0 <= row_coverage <= 1:
            raise ValueError("row_coverage必须在0到1之间")

        prepared = tuple(
            self._prepare_product_transition(item)
            for item in transitions
        )
        transition_keys = [
            (
                item.transition.record.symbol,
                item.transition.source_contract_id,
            )
            for item in prepared
        ]
        if len(transition_keys) != len(set(transition_keys)):
            raise ValueError("同一产品主档批次不得重复产品与来源合同")
        if status == "succeeded":
            if (
                expected_count != returned_count
                or returned_count <= 0
                or row_coverage != 1.0
                or len(prepared) != returned_count
                or issues
            ):
                raise ValueError("成功产品主档批次必须完整且无来源问题")
        elif prepared:
            raise ValueError("失败或降级产品主档批次不得推进产品版本")

        inserted = 0
        unchanged = 0
        with self._transaction():
            for item in prepared:
                if self._transition_product_profile_in_transaction(item):
                    inserted += 1
                else:
                    unchanged += 1
            self._connection.execute(
                """
                INSERT INTO radar_etf_product_master_runs (
                    product_master_run_id, as_of, source, source_time,
                    fetched_at, status, expected_count, returned_count,
                    row_coverage, required_field_coverage_json, issues_json,
                    inserted_count, unchanged_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_master_run_id,
                    as_of_text,
                    source,
                    _datetime_optional_text(source_time),
                    fetched_at_text,
                    status,
                    expected_count,
                    returned_count,
                    row_coverage,
                    _json_text(dict(required_field_coverage)),
                    _json_text(list(issues)),
                    inserted,
                    unchanged,
                    self._clock_text(),
                ),
            )
        return EtfProductMasterBatchWriteResult(
            inserted=inserted,
            unchanged=unchanged,
        )

    def _transition_product_profile_in_transaction(
        self,
        prepared: _PreparedProductProfileTransition,
    ) -> bool:
        transition = prepared.transition
        existing_profile = self._connection.execute(
            "SELECT record_checksum FROM radar_etf_product_profiles "
            "WHERE profile_id=?",
            (transition.profile_id,),
        ).fetchone()
        if existing_profile is not None:
            if existing_profile[0] == prepared.record_checksum:
                return False
            raise RepositoryConflictError(
                f"ETF产品版本{transition.profile_id}内容冲突"
            )

        current = self._connection.execute(
            """
            SELECT profile_id, effective_from, record_checksum
            FROM radar_etf_product_profiles
            WHERE symbol=? AND source_contract_id=?
              AND effective_to IS NULL
            """,
            (transition.record.symbol, transition.source_contract_id),
        ).fetchone()
        if current is not None:
            if current[2] == prepared.record_checksum:
                return False
            if str(current[1]) >= prepared.effective_from_text:
                raise RepositoryConflictError(
                    "ETF产品新版本生效时间必须晚于当前版本"
                )
            self._connection.execute(
                "UPDATE radar_etf_product_profiles "
                "SET effective_to=? WHERE profile_id=?",
                (prepared.effective_from_text, current[0]),
            )

        self._insert_product_profile_row(
            prepared,
            effective_to_text=None,
        )
        return True

    def _insert_product_profile_row(
        self,
        prepared: _PreparedProductProfileTransition,
        *,
        effective_to_text: Optional[str],
    ) -> None:
        transition = prepared.transition
        record = transition.record
        self._connection.execute(
            """
            INSERT INTO radar_etf_product_profiles (
                profile_id, symbol, exchange, official_name,
                product_type, management_style, asset_class,
                source_category_code, source_category_name,
                source_investment_type, target_index_name,
                classification_mapping_version,
                classification_reasons_json, source_contract_id,
                source, source_time, fetched_at, version_time_kind,
                official_effective_from, first_observed_at,
                effective_from, effective_to, evidence_url, evidence_sha256,
                listing_date, manager, source_fields_json, record_checksum,
                created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                transition.profile_id,
                record.symbol,
                record.exchange,
                record.official_name,
                record.product_type.value,
                record.management_style.value,
                record.asset_class.value,
                record.source_category_code,
                record.source_category_name,
                record.source_investment_type,
                record.target_index_name,
                record.classification_mapping_version,
                _json_text(list(record.classification_reasons)),
                transition.source_contract_id,
                record.source,
                _datetime_optional_text(transition.source_time),
                _datetime_text(record.fetched_at, "fetched_at"),
                prepared.version_time_kind.value,
                prepared.official_effective_from_text,
                prepared.first_observed_at_text,
                prepared.effective_from_text,
                effective_to_text,
                transition.evidence_url,
                transition.evidence_sha256,
                _date_text(record.listing_date),
                record.manager,
                _json_text(record.source_fields),
                prepared.record_checksum,
                self._clock_text(),
            ),
        )

    def upsert_index_relation(
        self,
        evidence: EtfIndexIdentityEvidence,
        *,
        relation_id: str,
        profile_id: str,
        source_contract_id: str,
    ) -> bool:
        relation_id = _required_text(relation_id, "relation_id")
        profile_id = _required_text(profile_id, "profile_id")
        source_contract_id = _required_text(
            source_contract_id,
            "source_contract_id",
        )
        payload = evidence.model_dump(mode="json", by_alias=True)
        payload.update({
            "relationId": relation_id,
            "profileId": profile_id,
            "sourceContractId": source_contract_id,
        })
        record_checksum = _checksum(payload)
        with self._transaction():
            existing = self._connection.execute(
                "SELECT record_checksum FROM radar_etf_index_relations "
                "WHERE relation_id=?",
                (relation_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] == record_checksum:
                    return False
                raise RepositoryConflictError(
                    f"ETF指数关系{relation_id}内容冲突"
                )
            self._connection.execute(
                """
                INSERT INTO radar_etf_index_relations (
                    relation_id, profile_id, symbol, index_provider,
                    index_code, index_name, relation_type, published_at,
                    effective_from, effective_to, source_contract_id,
                    fund_evidence_url, fund_evidence_sha256,
                    provider_evidence_url, provider_evidence_sha256,
                    status, formal_ready, reasons_json, as_of, fetched_at,
                    record_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation_id,
                    profile_id,
                    evidence.symbol,
                    evidence.index_provider,
                    evidence.provider_index_code,
                    evidence.provider_index_name,
                    "primary_tracking",
                    _datetime_optional_text(evidence.published_at),
                    _datetime_optional_text(evidence.effective_from),
                    _datetime_optional_text(evidence.effective_to),
                    source_contract_id,
                    evidence.fund_evidence_url,
                    evidence.fund_evidence_sha256,
                    evidence.provider_evidence_url,
                    evidence.provider_evidence_sha256,
                    evidence.status.value,
                    int(evidence.formal_ready),
                    _json_text(list(evidence.reasons)),
                    _datetime_text(evidence.as_of, "as_of"),
                    _datetime_text(evidence.fetched_at, "fetched_at"),
                    record_checksum,
                    self._clock_text(),
                ),
            )
        return True

    def upsert_methodology(
        self,
        evidence: IndexMethodologyEvidence,
        *,
        methodology_version_id: str,
    ) -> bool:
        methodology_version_id = _required_text(
            methodology_version_id,
            "methodology_version_id",
        )
        payload = evidence.model_dump(mode="json", by_alias=True)
        payload["methodologyVersionId"] = methodology_version_id
        record_checksum = _checksum(payload)
        with self._transaction():
            existing = self._connection.execute(
                "SELECT record_checksum FROM radar_index_methodology_versions "
                "WHERE methodology_version_id=?",
                (methodology_version_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] == record_checksum:
                    return False
                raise RepositoryConflictError(
                    f"指数方法版本{methodology_version_id}内容冲突"
                )
            self._connection.execute(
                """
                INSERT INTO radar_index_methodology_versions (
                    methodology_version_id, index_provider, index_code,
                    index_name, provider_version, version_kind,
                    published_at, effective_from, effective_to,
                    universe_rule, selection_rule, weighting_method,
                    constituent_cap, rebalance_frequency, as_of,
                    evidence_url, evidence_sha256, first_observed_at,
                    fetched_at, status, formal_ready, reasons_json,
                    record_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    methodology_version_id,
                    evidence.index_provider,
                    evidence.index_code,
                    evidence.index_name,
                    evidence.provider_version,
                    evidence.version_kind.value,
                    _datetime_optional_text(evidence.published_at),
                    _datetime_optional_text(evidence.effective_from),
                    _datetime_optional_text(evidence.effective_to),
                    evidence.universe_rule,
                    evidence.selection_rule,
                    evidence.weighting_method,
                    evidence.constituent_cap,
                    evidence.rebalance_frequency,
                    _datetime_text(evidence.as_of, "as_of"),
                    evidence.evidence_url,
                    evidence.evidence_sha256,
                    _datetime_text(evidence.first_observed_at, "first_observed_at"),
                    _datetime_text(evidence.fetched_at, "fetched_at"),
                    evidence.status.value,
                    int(evidence.formal_ready),
                    _json_text(list(evidence.reasons)),
                    record_checksum,
                    self._clock_text(),
                ),
            )
        return True

    def insert_constituent_set(
        self,
        evidence: IndexConstituentSetEvidence,
        *,
        constituent_set_id: str,
    ) -> bool:
        constituent_set_id = _required_text(
            constituent_set_id,
            "constituent_set_id",
        )
        payload = evidence.model_dump(mode="json", by_alias=True)
        payload["constituentSetId"] = constituent_set_id
        record_checksum = _checksum(payload)
        with self._transaction():
            existing = self._connection.execute(
                "SELECT record_checksum FROM radar_index_constituent_sets "
                "WHERE constituent_set_id=?",
                (constituent_set_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] == record_checksum:
                    return False
                raise RepositoryConflictError(
                    f"指数成分批次{constituent_set_id}内容冲突"
                )
            self._connection.execute(
                """
                INSERT INTO radar_index_constituent_sets (
                    constituent_set_id, index_provider, index_code,
                    index_name, announced_at, effective_from, effective_to,
                    source_date, expected_count, returned_count, weight_count,
                    weight_total, as_of, evidence_url, evidence_sha256,
                    first_observed_at, fetched_at, status, formal_ready,
                    reasons_json, record_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    constituent_set_id,
                    evidence.index_provider,
                    evidence.index_code,
                    evidence.index_name,
                    _datetime_optional_text(evidence.announced_at),
                    _datetime_optional_text(evidence.effective_from),
                    _datetime_optional_text(evidence.effective_to),
                    _date_text(evidence.source_date),
                    evidence.expected_count,
                    evidence.returned_count,
                    evidence.weight_count,
                    evidence.weight_total,
                    _datetime_text(evidence.as_of, "as_of"),
                    evidence.evidence_url,
                    evidence.evidence_sha256,
                    _datetime_text(evidence.first_observed_at, "first_observed_at"),
                    _datetime_text(evidence.fetched_at, "fetched_at"),
                    evidence.status.value,
                    int(evidence.formal_ready),
                    _json_text(list(evidence.reasons)),
                    record_checksum,
                    self._clock_text(),
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO radar_index_constituents (
                    constituent_set_id, stock_code, stock_name,
                    weight, weight_unit
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        constituent_set_id,
                        item.stock_code,
                        item.stock_name,
                        item.weight,
                        item.weight_unit,
                    )
                    for item in evidence.items
                ),
            )
        return True

    def insert_industry_exposure(
        self,
        result: IndexIndustryExposureResult,
        *,
        exposure_version_id: str,
    ) -> bool:
        exposure_version_id = _required_text(
            exposure_version_id,
            "exposure_version_id",
        )
        if not result.exposures:
            raise ValueError("指数行业暴露至少需要一条行业明细")
        payload = result.model_dump(mode="json", by_alias=True)
        payload["exposureVersionId"] = exposure_version_id
        record_checksum = _checksum(payload)
        with self._transaction():
            existing = self._connection.execute(
                "SELECT DISTINCT record_checksum "
                "FROM radar_index_industry_exposures "
                "WHERE exposure_version_id=?",
                (exposure_version_id,),
            ).fetchall()
            if existing:
                checksums = {str(row[0]) for row in existing}
                if checksums == {record_checksum}:
                    return False
                raise RepositoryConflictError(
                    f"指数行业暴露版本{exposure_version_id}内容冲突"
                )
            self._connection.executemany(
                """
                INSERT INTO radar_index_industry_exposures (
                    exposure_version_id, constituent_set_id,
                    industry_release_id, index_provider, index_code,
                    index_name, constituent_source_date,
                    industry_release_period, industry_document_sha256,
                    industry_code, industry_name, raw_weight,
                    exposure_ratio, total_weight, mapped_weight,
                    unmapped_weight, mapping_coverage,
                    unmapped_symbols_json, as_of, computed_at,
                    calculation_version, formal_ready, reasons_json,
                    record_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        exposure_version_id,
                        result.constituent_set_id,
                        result.industry_release_id,
                        result.index_provider,
                        result.index_code,
                        result.index_name,
                        _date_text(result.constituent_source_date),
                        result.industry_release_period,
                        result.industry_document_sha256,
                        item.industry_code,
                        item.industry_name,
                        item.raw_weight,
                        item.exposure_ratio,
                        result.total_weight,
                        result.mapped_weight,
                        result.unmapped_weight,
                        result.mapping_coverage,
                        _json_text(list(result.unmapped_symbols)),
                        _datetime_text(result.as_of, "as_of"),
                        _datetime_text(result.computed_at, "computed_at"),
                        result.calculation_version,
                        int(result.formal_ready),
                        _json_text(list(result.reasons)),
                        record_checksum,
                        self._clock_text(),
                    )
                    for item in result.exposures
                ),
            )
        return True

    def upsert_daily_fact(self, fact: EtfDailyFact) -> bool:
        payload = fact.model_dump(mode="json", by_alias=True)
        fact_key_date = fact.source_report_date or fact.trade_date
        if fact_key_date is None:
            raise ValueError("ETF日频事实必须包含事实日期")
        payload["factKeyDate"] = fact_key_date.isoformat()
        record_checksum = _checksum(payload)
        daily_fact_id = (
            f"{fact.symbol}:{fact_key_date.isoformat()}:"
            f"{record_checksum[:16]}"
        )
        with self._transaction():
            existing = self._connection.execute(
                "SELECT record_checksum FROM radar_etf_daily_facts "
                "WHERE daily_fact_id=?",
                (daily_fact_id,),
            ).fetchone()
            if existing is not None:
                return False
            self._connection.execute(
                """
                INSERT INTO radar_etf_daily_facts (
                    daily_fact_id, symbol, fact_key_date, trade_date,
                    source_report_date, fund_size, fund_size_unit,
                    fund_shares, fund_shares_unit, nav, nav_currency,
                    share_change_5d, share_change_20d,
                    average_turnover_20d, tracking_difference,
                    tracking_error, index_correlation, window_trading_days,
                    sample_count, formula_version, field_states_json,
                    source_contract_ids_json, fetched_at, computed_at,
                    formal_usable, reasons_json, record_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    daily_fact_id,
                    fact.symbol,
                    fact_key_date.isoformat(),
                    _date_text(fact.trade_date),
                    _date_text(fact.source_report_date),
                    fact.fund_size,
                    fact.fund_size_unit,
                    fact.fund_shares,
                    fact.fund_shares_unit,
                    fact.nav,
                    fact.nav_currency,
                    fact.share_change_5d,
                    fact.share_change_20d,
                    fact.average_turnover_20d,
                    fact.tracking_difference,
                    fact.tracking_error,
                    fact.index_correlation,
                    fact.window_trading_days,
                    fact.sample_count,
                    fact.formula_version,
                    _json_text({
                        key: value.value
                        for key, value in fact.field_states.items()
                    }),
                    _json_text(fact.source_contract_ids),
                    _datetime_text(fact.fetched_at, "fetched_at"),
                    _datetime_text(fact.computed_at, "computed_at"),
                    int(fact.formal_usable),
                    _json_text(list(fact.reasons)),
                    record_checksum,
                    self._clock_text(),
                ),
            )
        return True

    def insert_feature_snapshot_batch(
        self,
        radar_run_id: str,
        snapshots: Iterable[Mapping[str, Any]],
    ) -> int:
        radar_run_id = _required_text(radar_run_id, "radar_run_id")
        items = list(snapshots)
        symbols = [str(item.get("symbol", "")).strip() for item in items]
        if any(not symbol for symbol in symbols):
            raise ValueError("ETF盘中特征必须包含symbol")
        if len(symbols) != len(set(symbols)):
            raise ValueError("ETF盘中特征批次内代码不得重复")
        inserted = 0
        with self._transaction():
            for item, symbol in zip(items, symbols):
                payload = dict(item)
                payload["radarRunId"] = radar_run_id
                record_checksum = _checksum(payload)
                existing = self._connection.execute(
                    "SELECT record_checksum FROM radar_etf_feature_snapshots "
                    "WHERE radar_run_id=? AND symbol=?",
                    (radar_run_id, symbol),
                ).fetchone()
                if existing is not None:
                    if existing[0] == record_checksum:
                        continue
                    raise RepositoryConflictError(
                        f"ETF盘中特征{radar_run_id}/{symbol}内容冲突"
                    )
                self._connection.execute(
                    """
                    INSERT INTO radar_etf_feature_snapshots (
                        radar_run_id, symbol, as_of, source_time, fetched_at,
                        price, change_percent, turnover_volume,
                        turnover_amount, bid1, ask1, spread_bps, iopv,
                        premium_discount_rate, field_states_json,
                        formal_usable, reason_codes_json,
                        record_checksum, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        radar_run_id,
                        payload["symbol"],
                        _datetime_text(payload["asOf"], "as_of"),
                        _datetime_optional_text(payload.get("sourceTime")),
                        _datetime_text(payload["fetchedAt"], "fetched_at"),
                        payload.get("price"),
                        payload.get("changePercent"),
                        payload.get("turnoverVolume"),
                        payload.get("turnoverAmount"),
                        payload.get("bid1"),
                        payload.get("ask1"),
                        payload.get("spreadBps"),
                        payload.get("iopv"),
                        payload.get("premiumDiscountRate"),
                        _json_text(payload.get("fieldStates", {})),
                        int(bool(payload.get("formalUsable", False))),
                        _json_text(payload.get("reasonCodes", [])),
                        record_checksum,
                        self._clock_text(),
                    ),
                )
                inserted += 1
        return inserted

    def save_candidate_snapshot(
        self,
        snapshot: Mapping[str, Any],
        entries: Iterable[Mapping[str, Any]],
    ) -> bool:
        payload = dict(snapshot)
        radar_run_id = _required_text(
            payload.get("radarRunId"),
            "radarRunId",
        )
        entry_items = [dict(entry) for entry in entries]
        if int(payload.get("candidateGroupCount", 0)) != len(entry_items):
            raise ValueError("candidateGroupCount必须等于候选明细数量")
        combined = {
            "snapshot": payload,
            "entries": entry_items,
        }
        record_checksum = _checksum(combined)
        with self._transaction():
            existing = self._connection.execute(
                "SELECT record_checksum FROM radar_etf_candidate_snapshots "
                "WHERE radar_run_id=?",
                (radar_run_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] == record_checksum:
                    return False
                raise RepositoryConflictError(
                    f"ETF候选批次{radar_run_id}内容冲突"
                )
            self._connection.execute(
                """
                INSERT INTO radar_etf_candidate_snapshots (
                    radar_run_id, as_of, rule_version_id, registry_count,
                    etf_count, eligible_product_count, industry_theme_count,
                    computed_count, stale_count, missing_count,
                    excluded_count, candidate_group_count, coverage,
                    quality, reason_counts_json, record_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    radar_run_id,
                    _datetime_text(payload["asOf"], "as_of"),
                    payload.get("ruleVersionId"),
                    int(payload["registryCount"]),
                    int(payload["etfCount"]),
                    int(payload["eligibleProductCount"]),
                    int(payload["industryThemeCount"]),
                    int(payload["computedCount"]),
                    int(payload["staleCount"]),
                    int(payload["missingCount"]),
                    int(payload["excludedCount"]),
                    int(payload["candidateGroupCount"]),
                    float(payload["coverage"]),
                    payload["quality"],
                    _json_text(payload.get("reasonCounts", {})),
                    record_checksum,
                    self._clock_text(),
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO radar_etf_candidate_entries (
                    radar_run_id, industry_code, index_group_key, rank,
                    representative_symbol, alternative_symbols_json,
                    industry_exposures_json, ranking_components_json,
                    entry_reasons_json, risk_reasons_json,
                    exit_conditions_json, formal_usable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? ,?)
                """,
                (
                    (
                        radar_run_id,
                        entry["industryCode"],
                        entry["indexGroupKey"],
                        int(entry["rank"]),
                        entry.get("representativeSymbol"),
                        _json_text(entry.get("alternativeSymbols", [])),
                        _json_text(entry.get("industryExposures", [])),
                        _json_text(entry.get("rankingComponents", {})),
                        _json_text(entry.get("entryReasons", [])),
                        _json_text(entry.get("riskReasons", [])),
                        _json_text(entry.get("exitConditions", [])),
                        int(bool(entry.get("formalUsable", False))),
                    )
                    for entry in entry_items
                ),
            )
        return True

    def get_daily_fact_as_of(
        self,
        symbol: str,
        as_of: datetime,
    ) -> Optional[EtfDailyFact]:
        symbol = _required_text(symbol, "symbol")
        as_of_date = _datetime_text(as_of, "as_of")[:10]
        row = self._connection.execute(
            """
            SELECT trade_date, source_report_date, fund_size,
                   fund_size_unit, fund_shares, fund_shares_unit, nav,
                   nav_currency, share_change_5d, share_change_20d,
                   average_turnover_20d, tracking_difference,
                   tracking_error, index_correlation, window_trading_days,
                   sample_count, formula_version, field_states_json,
                   source_contract_ids_json, fetched_at, computed_at,
                   formal_usable, reasons_json
            FROM radar_etf_daily_facts
            WHERE symbol=? AND fact_key_date <= ?
            ORDER BY fact_key_date DESC, computed_at DESC
            LIMIT 1
            """,
            (symbol, as_of_date),
        ).fetchone()
        if row is None:
            return None
        field_states = json.loads(row[17])
        return EtfDailyFact(
            symbol=symbol,
            tradeDate=row[0],
            sourceReportDate=row[1],
            fundSize=row[2],
            fundSizeUnit=row[3],
            fundShares=row[4],
            fundSharesUnit=row[5],
            nav=row[6],
            navCurrency=row[7],
            shareChange5d=row[8],
            shareChange20d=row[9],
            averageTurnover20d=row[10],
            trackingDifference=row[11],
            trackingError=row[12],
            indexCorrelation=row[13],
            windowTradingDays=row[14],
            sampleCount=row[15],
            formulaVersion=row[16],
            fieldStates=field_states,
            sourceContractIds=json.loads(row[18]),
            fetchedAt=_parse_datetime(row[19], "ETF日频事实fetched_at"),
            computedAt=_parse_datetime(row[20], "ETF日频事实computed_at"),
            formalUsable=bool(row[21]),
            reasons=tuple(json.loads(row[22])),
        )

    def latest_product_master_attempt_at(self) -> Optional[datetime]:
        row = self._connection.execute(
            "SELECT MAX(fetched_at) FROM radar_etf_product_master_runs"
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return _parse_datetime(row[0], "ETF产品主档最近执行时间")

    def get_latest_product_master_run(
        self,
        *,
        successful_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        status_clause = "WHERE status IN ('succeeded', 'degraded')" if successful_only else ""
        row = self._connection.execute(
            f"""
            SELECT product_master_run_id, as_of, source, source_time,
                   fetched_at, status, expected_count, returned_count,
                   row_coverage, required_field_coverage_json, issues_json,
                   inserted_count, unchanged_count
            FROM radar_etf_product_master_runs
            {status_clause}
            ORDER BY fetched_at DESC, product_master_run_id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return {
            "productMasterRunId": row[0],
            "asOf": _parse_datetime(row[1], "ETF产品主档as_of"),
            "source": row[2],
            "sourceTime": (
                _parse_datetime(row[3], "ETF产品主档source_time")
                if row[3] is not None
                else None
            ),
            "fetchedAt": _parse_datetime(row[4], "ETF产品主档fetched_at"),
            "status": row[5],
            "expectedCount": row[6],
            "returnedCount": row[7],
            "rowCoverage": row[8],
            "requiredFieldCoverage": json.loads(row[9]),
            "issues": json.loads(row[10]),
            "insertedCount": row[11],
            "unchangedCount": row[12],
        }

    def list_current_product_profiles(
        self,
        as_of: datetime,
    ) -> Sequence[Dict[str, Any]]:
        as_of_text = _datetime_text(as_of, "as_of")
        rows = self._connection.execute(
            """
            SELECT DISTINCT symbol
            FROM radar_etf_product_profiles
            WHERE effective_from <= ?
              AND (effective_to IS NULL OR effective_to > ?)
            ORDER BY symbol
            """,
            (as_of_text, as_of_text),
        ).fetchall()
        return tuple(
            profile
            for profile in (
                self.get_product_profile_as_of(row[0], as_of)
                for row in rows
            )
            if profile is not None
        )

    def get_product_profile_as_of(
        self,
        symbol: str,
        as_of: datetime,
    ) -> Optional[Dict[str, Any]]:
        symbol = _required_text(symbol, "symbol")
        as_of_text = _datetime_text(as_of, "as_of")
        row = self._connection.execute(
            """
            SELECT profile_id, official_name, exchange, product_type,
                   management_style, asset_class, source_category_code,
                   source_category_name, source_investment_type,
                   target_index_name, classification_mapping_version,
                   classification_reasons_json, source_contract_id, source,
                   source_time, fetched_at, version_time_kind,
                   official_effective_from, first_observed_at,
                   effective_from, effective_to, evidence_url, evidence_sha256,
                   listing_date, manager, source_fields_json
            FROM radar_etf_product_profiles
            WHERE symbol=? AND effective_from <= ?
              AND (effective_to IS NULL OR effective_to > ?)
            ORDER BY effective_from DESC, profile_id DESC
            LIMIT 1
            """,
            (symbol, as_of_text, as_of_text),
        ).fetchone()
        if row is None:
            return None
        product = EtfProductMasterRecord(
            symbol=symbol,
            officialName=row[1],
            exchange=row[2],
            productType=row[3],
            managementStyle=row[4],
            assetClass=row[5],
            sourceCategoryCode=row[6],
            sourceCategoryName=row[7],
            sourceInvestmentType=row[8],
            targetIndexName=row[9],
            classificationMappingVersion=row[10],
            classificationReasons=tuple(json.loads(row[11])),
            source=row[13],
            fetchedAt=_parse_datetime(row[15], "ETF产品fetched_at"),
            listingDate=row[23],
            manager=row[24],
            sourceFields=json.loads(row[25]),
        )
        return {
            "profileId": row[0],
            "product": product,
            "sourceContractId": row[12],
            "sourceTime": (
                _parse_datetime(row[14], "ETF产品source_time")
                if row[14] is not None
                else None
            ),
            "effectiveFrom": _parse_datetime(
                row[19],
                "ETF产品effective_from",
            ),
            "effectiveTo": (
                _parse_datetime(row[20], "ETF产品effective_to")
                if row[20] is not None
                else None
            ),
            "versionTimeKind": row[16],
            "officialEffectiveFrom": (
                _parse_datetime(
                    row[17],
                    "ETF产品official_effective_from",
                )
                if row[17] is not None
                else None
            ),
            "firstObservedAt": _parse_datetime(
                row[18],
                "ETF产品first_observed_at",
            ),
            "historicalReplayReady": (
                row[16] == EtfProductVersionTimeKind.OFFICIAL_EFFECTIVE.value
            ),
            "evidenceUrl": row[21],
            "evidenceSha256": row[22],
        }

    def get_feature_snapshot(
        self,
        radar_run_id: str,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        row = self._connection.execute(
            """
            SELECT as_of, source_time, fetched_at, price,
                   change_percent, turnover_volume, turnover_amount,
                   bid1, ask1, spread_bps, iopv,
                   premium_discount_rate, field_states_json,
                   formal_usable, reason_codes_json
            FROM radar_etf_feature_snapshots
            WHERE radar_run_id=? AND symbol=?
            """,
            (radar_run_id, symbol),
        ).fetchone()
        if row is None:
            return None
        return {
            "radarRunId": radar_run_id,
            "symbol": symbol,
            "asOf": _parse_datetime(row[0], "ETF盘中特征as_of"),
            "sourceTime": (
                _parse_datetime(row[1], "ETF盘中特征source_time")
                if row[1] is not None
                else None
            ),
            "fetchedAt": _parse_datetime(row[2], "ETF盘中特征fetched_at"),
            "price": row[3],
            "changePercent": row[4],
            "turnoverVolume": row[5],
            "turnoverAmount": row[6],
            "bid1": row[7],
            "ask1": row[8],
            "spreadBps": row[9],
            "iopv": row[10],
            "premiumDiscountRate": row[11],
            "fieldStates": json.loads(row[12]),
            "formalUsable": bool(row[13]),
            "reasonCodes": json.loads(row[14]),
        }

    def get_candidate_snapshot(
        self,
        radar_run_id: str,
    ) -> Optional[Dict[str, Any]]:
        row = self._connection.execute(
            """
            SELECT as_of, rule_version_id, registry_count, etf_count,
                   eligible_product_count, industry_theme_count,
                   computed_count, stale_count, missing_count,
                   excluded_count, candidate_group_count, coverage,
                   quality, reason_counts_json, created_at
            FROM radar_etf_candidate_snapshots
            WHERE radar_run_id=?
            """,
            (radar_run_id,),
        ).fetchone()
        if row is None:
            return None
        entries = self._connection.execute(
            """
            SELECT industry_code, index_group_key, rank,
                   representative_symbol, alternative_symbols_json,
                   industry_exposures_json, ranking_components_json,
                   entry_reasons_json, risk_reasons_json,
                   exit_conditions_json, formal_usable
            FROM radar_etf_candidate_entries
            WHERE radar_run_id=?
            ORDER BY industry_code, rank, index_group_key
            """,
            (radar_run_id,),
        ).fetchall()
        return {
            "radarRunId": radar_run_id,
            "asOf": _parse_datetime(row[0], "ETF候选as_of"),
            "ruleVersionId": row[1],
            "registryCount": row[2],
            "etfCount": row[3],
            "eligibleProductCount": row[4],
            "industryThemeCount": row[5],
            "computedCount": row[6],
            "staleCount": row[7],
            "missingCount": row[8],
            "excludedCount": row[9],
            "candidateGroupCount": row[10],
            "coverage": row[11],
            "quality": row[12],
            "reasonCounts": json.loads(row[13]),
            "fetchedAt": _parse_datetime(row[14], "ETF候选fetched_at"),
            "entries": [
                {
                    "industryCode": entry[0],
                    "indexGroupKey": entry[1],
                    "rank": entry[2],
                    "representativeSymbol": entry[3],
                    "alternativeSymbols": json.loads(entry[4]),
                    "industryExposures": json.loads(entry[5]),
                    "rankingComponents": json.loads(entry[6]),
                    "entryReasons": json.loads(entry[7]),
                    "riskReasons": json.loads(entry[8]),
                    "exitConditions": json.loads(entry[9]),
                    "formalUsable": bool(entry[10]),
                }
                for entry in entries
            ],
        }

    def get_latest_candidate_snapshot(self) -> Optional[Dict[str, Any]]:
        row = self._connection.execute(
            """
            SELECT radar_run_id
            FROM radar_etf_candidate_snapshots
            ORDER BY as_of DESC, radar_run_id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return self.get_candidate_snapshot(row[0])
