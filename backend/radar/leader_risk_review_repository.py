"""阶段6风险官方批次与人工审核版本的显式SQLite仓储。

仓储只接受调用方提供且已完成可选迁移6的连接。数据库保存可追溯输入，
读取时重新构建事实、候选、人工工件和补录关系，不把派生结果作为可信事实。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple

from radar.leader_research_features import ResearchFeatureStatus
from radar.leader_risk_document_facts import (
    MAXIMUM_PAGE_CHARACTERS,
    MAXIMUM_PAGE_COUNT,
    MAXIMUM_TOTAL_CHARACTERS,
    OfficialRiskDocumentFactInput,
    OfficialRiskDocumentPage,
    RiskDocumentFactKind,
    RiskDocumentRelationKind,
    RiskDocumentVersionReview,
    extract_official_risk_document_facts,
)
from radar.leader_risk_invalidation_features import (
    ALL_RISK_CATEGORIES,
    LeaderRiskEventEvidence,
    RiskCategory,
    RiskEventSubtype,
    RiskEvidenceSourceKind,
    RiskOfficialStatus,
)
from radar.leader_risk_lifecycle_batch import (
    CANONICAL_DISCOVERY_SEARCH_KEYS,
    LeaderRiskLifecycleReviewVersion,
)
from radar.leader_risk_review_artifacts import (
    ManualRiskDocumentFactSubmission,
    ManualRiskReviewArtifactInput,
    ManualRiskReviewSubmission,
    build_manual_risk_review_artifact,
)
from radar.leader_risk_review_replay import (
    RiskDocumentResearchReplayInput,
    replay_risk_document_research_evidence,
)
from radar.leader_risk_supplemented_relation import (
    SupplementedRiskDocumentRelationInput,
    review_supplemented_risk_document_relation,
)
from radar.migrations import (
    LEADER_RISK_REVIEW_STORAGE_MIGRATION,
    STAGE6_REVIEW_RADAR_MIGRATIONS,
    validate_applied_migrations,
)
from radar.repository import (
    RadarRepositoryError,
    RepositoryConflictError,
    RepositoryStateError,
    RepositoryWriteError,
    _begin_immediate,
    _canonical_json,
    _datetime_text,
    _parse_datetime,
)
from radar.sources.leader_risk_document_content import (
    OfficialRiskDocumentContentResult,
    RiskDocumentReviewCandidate,
    RiskDocumentReviewCandidateKind,
    build_risk_document_review_candidate,
)
from radar.sources.leader_risk_official import (
    OfficialRiskDocumentMetadata,
)


UTC = timezone.utc
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SYMBOL_PATTERN = re.compile(r"^[036][0-9]{5}$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}不能为空")
    return value.strip()


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("可选文本字段类型无效")
    return value.strip() or None


def _symbol(value: Any) -> str:
    text = _required_text(value, "symbol")
    if SYMBOL_PATTERN.fullmatch(text) is None:
        raise ValueError("symbol必须是阶段6支持的6位A股代码")
    return text


def _integer(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name}必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是整数") from exc
    if parsed < minimum:
        raise ValueError(f"{field_name}不能小于{minimum}")
    return parsed


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name}必须是布尔值")
    return value


def _date_text(value: Any, field_name: str) -> str:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{field_name}必须是日期")
    return value.isoformat()


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise RepositoryStateError(
            f"数据库中的{field_name}不是有效日期"
        ) from exc


def _checksum(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _json_value(value: str, expected: type, field_name: str) -> Any:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise RepositoryStateError(
            f"数据库中的{field_name}不是有效JSON"
        ) from exc
    if not isinstance(parsed, expected):
        raise RepositoryStateError(
            f"数据库中的{field_name}结构无效"
        )
    return parsed


def _enum_value(enum_type, value: Any, field_name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise RepositoryStateError(
            f"数据库中的{field_name}枚举值无效"
        ) from exc


@dataclass(frozen=True)
class _PreparedDocument:
    document: OfficialRiskDocumentMetadata
    catalog_payload: Mapping[str, Any]
    catalog_checksum: str
    search_key: str


class LeaderRiskReviewRepository:
    """在显式连接上保存D2审核批次和D5-D8只增审核版本。"""

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
                migrations=STAGE6_REVIEW_RADAR_MIGRATIONS,
            )
        except Exception as exc:
            raise RepositoryStateError(
                "数据库未完成阶段6风险审核可选迁移版本6或迁移记录已漂移"
            ) from exc
        row = self._connection.execute(
            "SELECT name, checksum FROM radar_schema_migrations WHERE version=?",
            (LEADER_RISK_REVIEW_STORAGE_MIGRATION.version,),
        ).fetchone()
        expected = (
            LEADER_RISK_REVIEW_STORAGE_MIGRATION.name,
            LEADER_RISK_REVIEW_STORAGE_MIGRATION.checksum,
        )
        if row is None or tuple(row) != expected:
            raise RepositoryStateError(
                "数据库未完成阶段6风险审核可选迁移版本6或迁移记录已漂移"
            )

    @contextmanager
    def _transaction(self):
        if self._connection.in_transaction:
            raise RepositoryStateError(
                "风险审核仓储写入前连接不能处于未提交事务中"
            )
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            _begin_immediate(self._connection)
            yield
            self._connection.commit()
        except RadarRepositoryError:
            self._connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            raise RepositoryConflictError(
                f"风险审核仓储约束冲突并已回滚：{exc}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            self._connection.rollback()
            raise RepositoryWriteError(
                f"风险审核仓储写入失败并已回滚：{type(exc).__name__}"
            ) from exc
        except Exception:
            self._connection.rollback()
            raise

    @staticmethod
    def _catalog_payload(document: OfficialRiskDocumentMetadata) -> Mapping[str, Any]:
        return {
            "sourceContractId": document.source_contract_id,
            "documentId": document.document_id,
            "symbol": document.symbol,
            "issuerIdentity": document.issuer_identity,
            "issuerName": document.issuer_name,
            "title": document.title,
            "publishedAt": _datetime_text(document.published_at, "published_at"),
            "sourceName": document.source_name,
            "sourceUrl": document.source_url,
            "rawColumnIds": list(document.raw_column_ids),
            "rawAnnouncementTypes": list(document.raw_announcement_types),
            "rawPageColumn": document.raw_page_column,
            "associationReported": document.association_reported,
            "formalUsable": False,
        }

    @classmethod
    def _prepare_document(
        cls,
        value: Any,
        source_contract_id: str,
    ) -> _PreparedDocument:
        if not isinstance(value, OfficialRiskDocumentMetadata):
            raise ValueError("documents必须是官方公告元数据")
        if value.formal_usable is not False:
            raise ValueError("审核公告不能标记为正式可用")
        if value.source_contract_id != source_contract_id:
            raise ValueError("公告来源合同与审核批次不一致")
        _required_text(value.document_id, "documentId")
        _symbol(value.symbol)
        for field_name, field_value in (
            ("issuerIdentity", value.issuer_identity),
            ("issuerName", value.issuer_name),
            ("title", value.title),
            ("sourceName", value.source_name),
            ("sourceUrl", value.source_url),
        ):
            _required_text(field_value, field_name)
        if not isinstance(value.candidate_category, RiskCategory):
            raise ValueError("candidateCategory无效")
        if not isinstance(value.raw_column_ids, tuple) or any(
            not isinstance(item, str) for item in value.raw_column_ids
        ):
            raise ValueError("rawColumnIds结构无效")
        if not isinstance(value.raw_announcement_types, tuple) or any(
            not isinstance(item, str)
            for item in value.raw_announcement_types
        ):
            raise ValueError("rawAnnouncementTypes结构无效")
        payload = cls._catalog_payload(value)
        return _PreparedDocument(
            document=value,
            catalog_payload=payload,
            catalog_checksum=_checksum(payload),
            search_key=CANONICAL_DISCOVERY_SEARCH_KEYS[
                value.candidate_category
            ],
        )

    @classmethod
    def _prepare_batch(
        cls,
        batch: Mapping[str, Any],
        documents: Iterable[OfficialRiskDocumentMetadata],
    ) -> Tuple[Mapping[str, Any], Tuple[_PreparedDocument, ...], str]:
        if not isinstance(batch, Mapping):
            raise ValueError("reviewBatch必须是映射")
        source_contract_id = _required_text(
            batch.get("sourceContractId"),
            "sourceContractId",
        )
        as_of = _datetime_text(batch.get("asOf"), "asOf")
        window_from = _date_text(batch.get("windowFrom"), "windowFrom")
        window_until = _date_text(batch.get("windowUntil"), "windowUntil")
        if window_until < window_from:
            raise ValueError("windowUntil不能早于windowFrom")
        prepared_documents = tuple(
            cls._prepare_document(item, source_contract_id)
            for item in documents
        )
        catalog_by_id: dict[str, str] = {}
        link_keys = []
        for prepared in prepared_documents:
            document = prepared.document
            previous = catalog_by_id.setdefault(
                document.document_id,
                prepared.catalog_checksum,
            )
            if previous != prepared.catalog_checksum:
                raise RepositoryConflictError(
                    "同一documentId包含冲突公告元数据"
                )
            link_keys.append((
                document.document_id,
                document.candidate_category.value,
            ))
        if len(link_keys) != len(set(link_keys)):
            raise RepositoryConflictError("审核批次包含重复公告关联")
        document_count = _integer(
            batch.get("documentCount"),
            "documentCount",
        )
        if document_count != len(catalog_by_id):
            raise ValueError("documentCount与唯一公告数量不一致")
        category_count = _integer(
            batch.get("categoryCount"),
            "categoryCount",
            minimum=1,
        )
        complete_flags = {
            name: _boolean(batch.get(name), name)
            for name in (
                "queryCategoriesComplete",
                "queryPagesComplete",
                "queryWindowContinuous",
            )
        }
        if (
            category_count != len(ALL_RISK_CATEGORIES)
            or not all(complete_flags.values())
        ):
            raise ValueError("只允许持久化通过完整性门禁的D2审核批次")
        payload = {
            "reviewBatchId": _required_text(
                batch.get("reviewBatchId"),
                "reviewBatchId",
            ),
            "candidatePlanId": _required_text(
                batch.get("candidatePlanId"),
                "candidatePlanId",
            ),
            "radarRunId": _required_text(
                batch.get("radarRunId"),
                "radarRunId",
            ),
            "asOf": as_of,
            "windowFrom": window_from,
            "windowUntil": window_until,
            "candidateCount": _integer(
                batch.get("candidateCount"),
                "candidateCount",
            ),
            "shardCount": _integer(
                batch.get("shardCount"),
                "shardCount",
                minimum=1,
            ),
            "categoryCount": category_count,
            "documentCount": document_count,
            **complete_flags,
            "sourceContractId": source_contract_id,
            "documentLinks": sorted(
                [{
                    "documentId": item.document.document_id,
                    "candidateCategory": (
                        item.document.candidate_category.value
                    ),
                    "searchKey": item.search_key,
                    "documentChecksum": item.catalog_checksum,
                } for item in prepared_documents],
                key=lambda item: (
                    item["documentId"], item["candidateCategory"]
                ),
            ),
        }
        return payload, prepared_documents, _checksum(payload)

    def save_review_batch(
        self,
        batch: Mapping[str, Any],
        documents: Iterable[OfficialRiskDocumentMetadata],
    ) -> bool:
        payload, prepared_documents, checksum = self._prepare_batch(
            batch,
            documents,
        )
        existing = self._connection.execute(
            "SELECT record_checksum FROM radar_leader_risk_review_batches "
            "WHERE review_batch_id=?",
            (payload["reviewBatchId"],),
        ).fetchone()
        if existing is not None:
            if existing[0] != checksum:
                raise RepositoryConflictError(
                    "同一reviewBatchId已存在不同内容"
                )
            self.get_review_batch(payload["reviewBatchId"])
            return False

        created_at = self._clock_text()
        with self._transaction():
            for prepared in prepared_documents:
                document = prepared.document
                existing_document = self._connection.execute(
                    "SELECT record_checksum FROM radar_leader_risk_documents "
                    "WHERE document_id=?",
                    (document.document_id,),
                ).fetchone()
                if existing_document is not None:
                    if existing_document[0] != prepared.catalog_checksum:
                        raise RepositoryConflictError(
                            "同一documentId已存在不同公告元数据"
                        )
                    continue
                catalog = prepared.catalog_payload
                self._connection.execute(
                    """
                    INSERT INTO radar_leader_risk_documents (
                        document_id, source_contract_id, symbol,
                        issuer_identity, issuer_name, title, published_at,
                        source_name, source_url, raw_column_ids_json,
                        raw_announcement_types_json, raw_page_column,
                        association_reported, formal_usable,
                        record_checksum, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        catalog["documentId"], catalog["sourceContractId"],
                        catalog["symbol"], catalog["issuerIdentity"],
                        catalog["issuerName"], catalog["title"],
                        catalog["publishedAt"], catalog["sourceName"],
                        catalog["sourceUrl"],
                        _canonical_json(catalog["rawColumnIds"]),
                        _canonical_json(catalog["rawAnnouncementTypes"]),
                        catalog["rawPageColumn"],
                        int(catalog["associationReported"]),
                        prepared.catalog_checksum, created_at,
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO radar_leader_risk_review_batches (
                    review_batch_id, candidate_plan_id, radar_run_id, as_of,
                    window_from, window_until, candidate_count, shard_count,
                    category_count, document_count,
                    query_categories_complete, query_pages_complete,
                    query_window_continuous, source_contract_id,
                    record_checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["reviewBatchId"], payload["candidatePlanId"],
                    payload["radarRunId"], payload["asOf"],
                    payload["windowFrom"], payload["windowUntil"],
                    payload["candidateCount"], payload["shardCount"],
                    payload["categoryCount"], payload["documentCount"],
                    int(payload["queryCategoriesComplete"]),
                    int(payload["queryPagesComplete"]),
                    int(payload["queryWindowContinuous"]),
                    payload["sourceContractId"], checksum, created_at,
                ),
            )
            for link in payload["documentLinks"]:
                self._connection.execute(
                    """
                    INSERT INTO radar_leader_risk_review_batch_documents (
                        review_batch_id, document_id,
                        candidate_category, search_key
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        payload["reviewBatchId"], link["documentId"],
                        link["candidateCategory"], link["searchKey"],
                    ),
                )
        return True

    def _load_document(
        self,
        review_batch_id: str,
        document_id: str,
        candidate_category: str,
    ) -> OfficialRiskDocumentMetadata:
        row = self._connection.execute(
            """
            SELECT d.source_contract_id, d.document_id, d.symbol,
                   d.issuer_identity, d.issuer_name, d.title,
                   d.published_at, d.source_name, d.source_url,
                   d.raw_column_ids_json, d.raw_announcement_types_json,
                   d.raw_page_column, d.association_reported,
                   d.formal_usable, d.record_checksum, l.search_key
            FROM radar_leader_risk_review_batch_documents AS l
            JOIN radar_leader_risk_documents AS d
              ON d.document_id = l.document_id
            WHERE l.review_batch_id=? AND l.document_id=?
              AND l.candidate_category=?
            """,
            (review_batch_id, document_id, candidate_category),
        ).fetchone()
        if row is None:
            raise RepositoryStateError("审核批次中的公告关联不存在")
        category = _enum_value(
            RiskCategory,
            candidate_category,
            "candidate_category",
        )
        if row[15] != CANONICAL_DISCOVERY_SEARCH_KEYS[category]:
            raise RepositoryStateError("审核批次中的公告搜索键已漂移")
        try:
            document = OfficialRiskDocumentMetadata(
                source_contract_id=row[0],
                document_id=row[1],
                symbol=row[2],
                issuer_identity=row[3],
                issuer_name=row[4],
                title=row[5],
                published_at=_parse_datetime(row[6], "published_at"),
                source_name=row[7],
                source_url=row[8],
                candidate_category=category,
                raw_column_ids=tuple(_json_value(
                    row[9], list, "raw_column_ids_json"
                )),
                raw_announcement_types=tuple(_json_value(
                    row[10], list, "raw_announcement_types_json"
                )),
                raw_page_column=row[11],
                association_reported=bool(row[12]),
                formal_usable=bool(row[13]),
            )
            prepared = self._prepare_document(
                document,
                document.source_contract_id,
            )
        except ValueError as exc:
            raise RepositoryStateError("公告目录记录结构无效") from exc
        if prepared.catalog_checksum != row[14]:
            raise RepositoryStateError("公告目录记录校验失败")
        return document

    def get_review_batch(self, review_batch_id: str) -> Mapping[str, Any]:
        batch_id = _required_text(review_batch_id, "reviewBatchId")
        row = self._connection.execute(
            """
            SELECT candidate_plan_id, radar_run_id, as_of,
                   window_from, window_until, candidate_count,
                   shard_count, category_count, document_count,
                   query_categories_complete, query_pages_complete,
                   query_window_continuous, source_contract_id,
                   record_checksum
            FROM radar_leader_risk_review_batches
            WHERE review_batch_id=?
            """,
            (batch_id,),
        ).fetchone()
        if row is None:
            raise RepositoryStateError("审核批次不存在")
        links = self._connection.execute(
            """
            SELECT document_id, candidate_category, search_key
            FROM radar_leader_risk_review_batch_documents
            WHERE review_batch_id=?
            ORDER BY document_id, candidate_category
            """,
            (batch_id,),
        ).fetchall()
        documents = tuple(
            self._load_document(batch_id, item[0], item[1])
            for item in links
        )
        payload = {
            "reviewBatchId": batch_id,
            "candidatePlanId": row[0],
            "radarRunId": row[1],
            "asOf": row[2],
            "windowFrom": row[3],
            "windowUntil": row[4],
            "candidateCount": row[5],
            "shardCount": row[6],
            "categoryCount": row[7],
            "documentCount": row[8],
            "queryCategoriesComplete": bool(row[9]),
            "queryPagesComplete": bool(row[10]),
            "queryWindowContinuous": bool(row[11]),
            "sourceContractId": row[12],
            "documentLinks": [
                {
                    "documentId": item[0],
                    "candidateCategory": item[1],
                    "searchKey": item[2],
                    "documentChecksum": _checksum(
                        self._catalog_payload(document)
                    ),
                }
                for item, document in zip(links, documents)
            ],
        }
        if (
            _checksum(payload) != row[13]
            or row[8] != len({document.document_id for document in documents})
        ):
            raise RepositoryStateError("审核批次记录校验失败")
        return {
            **payload,
            "asOf": _parse_datetime(row[2], "as_of"),
            "windowFrom": _parse_date(row[3], "window_from"),
            "windowUntil": _parse_date(row[4], "window_until"),
            "documents": documents,
        }

    def get_latest_review_batch_summary(self) -> Optional[Mapping[str, Any]]:
        """读取最新D2审核批次的轻量进度，不加载公告正文或全量目录。"""

        row = self._connection.execute(
            """
            SELECT review_batch_id, candidate_plan_id, radar_run_id,
                   as_of, window_from, window_until, candidate_count,
                   shard_count, category_count, document_count,
                   query_categories_complete, query_pages_complete,
                   query_window_continuous, source_contract_id, created_at
            FROM radar_leader_risk_review_batches
            ORDER BY as_of DESC, created_at DESC, review_batch_id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        batch_id = str(row[0])
        counts = self._connection.execute(
            """
            SELECT
                (SELECT COUNT(*)
                 FROM radar_leader_risk_review_batch_documents
                 WHERE review_batch_id=?),
                (SELECT COUNT(*)
                 FROM radar_leader_risk_document_contents AS c
                 WHERE EXISTS (
                    SELECT 1
                    FROM radar_leader_risk_review_batch_documents AS l
                    WHERE l.review_batch_id=?
                      AND l.document_id=c.document_id
                 )),
                (SELECT COUNT(DISTINCT document_id)
                 FROM radar_leader_risk_manual_review_versions
                 WHERE review_batch_id=?),
                (SELECT COUNT(*)
                 FROM radar_leader_risk_manual_review_versions
                 WHERE review_batch_id=?)
            """,
            (batch_id, batch_id, batch_id, batch_id),
        ).fetchone()
        if counts is None or any(
            not isinstance(value, int) or value < 0 for value in counts
        ):
            raise RepositoryStateError("风险审核队列计数无效")
        if (
            row[10] not in (0, 1)
            or row[11] not in (0, 1)
            or row[12] not in (0, 1)
            or row[9] < 0
            or counts[0] < row[9]
        ):
            raise RepositoryStateError("风险审核批次摘要结构无效")
        return {
            "reviewBatchId": batch_id,
            "candidatePlanId": str(row[1]),
            "radarRunId": str(row[2]),
            "asOf": _parse_datetime(str(row[3]), "as_of"),
            "windowFrom": _parse_date(str(row[4]), "window_from"),
            "windowUntil": _parse_date(str(row[5]), "window_until"),
            "candidateCount": int(row[6]),
            "shardCount": int(row[7]),
            "categoryCount": int(row[8]),
            "documentCount": int(row[9]),
            "documentLinkCount": counts[0],
            "contentSnapshotCount": counts[1],
            "reviewedDocumentCount": counts[2],
            "reviewVersionCount": counts[3],
            "queryCategoriesComplete": bool(row[10]),
            "queryPagesComplete": bool(row[11]),
            "queryWindowContinuous": bool(row[12]),
            "sourceContractId": str(row[13]),
            "createdAt": _parse_datetime(str(row[14]), "created_at"),
        }

    def list_review_batch_documents(
        self,
        review_batch_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Mapping[str, Any]:
        """分页读取公告审核队列；每个公告类别关联是一条待审工作项。"""

        batch_id = _required_text(review_batch_id, "reviewBatchId")
        page_limit = _integer(limit, "limit", minimum=1)
        page_offset = _integer(offset, "offset")
        if page_limit > 100:
            raise ValueError("limit不能大于100")
        if self._connection.execute(
            "SELECT 1 FROM radar_leader_risk_review_batches "
            "WHERE review_batch_id=?",
            (batch_id,),
        ).fetchone() is None:
            raise RepositoryStateError("审核批次不存在")
        total = self._connection.execute(
            "SELECT COUNT(*) FROM radar_leader_risk_review_batch_documents "
            "WHERE review_batch_id=?",
            (batch_id,),
        ).fetchone()[0]
        rows = self._connection.execute(
            """
            SELECT l.document_id, l.candidate_category
            FROM radar_leader_risk_review_batch_documents AS l
            JOIN radar_leader_risk_documents AS d
              ON d.document_id=l.document_id
            WHERE l.review_batch_id=?
            ORDER BY d.published_at DESC, l.document_id, l.candidate_category
            LIMIT ? OFFSET ?
            """,
            (batch_id, page_limit, page_offset),
        ).fetchall()
        items = []
        for document_id, candidate_category in rows:
            document = self._load_document(
                batch_id,
                str(document_id),
                str(candidate_category),
            )
            items.append(self._document_review_state(batch_id, document))
        return {
            "reviewBatchId": batch_id,
            "total": int(total),
            "limit": page_limit,
            "offset": page_offset,
            "items": tuple(items),
        }

    def _document_review_state(
        self,
        review_batch_id: str,
        document: OfficialRiskDocumentMetadata,
    ) -> Mapping[str, Any]:
        content_row = self._connection.execute(
            "SELECT COUNT(*), MAX(fetched_at) "
            "FROM radar_leader_risk_document_contents "
            "WHERE document_id=?",
            (document.document_id,),
        ).fetchone()
        review_count = self._connection.execute(
            "SELECT COUNT(*) FROM "
            "radar_leader_risk_manual_review_versions "
            "WHERE review_batch_id=? AND document_id=? "
            "AND candidate_category=?",
            (
                review_batch_id,
                document.document_id,
                document.candidate_category.value,
            ),
        ).fetchone()[0]
        content_count = int(content_row[0])
        return {
            "document": document,
            "hasContentSnapshot": content_count > 0,
            "contentSnapshotCount": content_count,
            "contentFetchedAt": (
                _parse_datetime(content_row[1], "content_fetched_at")
                if content_row[1] is not None else None
            ),
            "contentStatus": (
                "available" if content_count > 0 else "not_fetched"
            ),
            "reviewVersionCount": int(review_count),
        }

    def get_review_batch_document(
        self,
        review_batch_id: str,
        document_id: str,
        candidate_category: str,
    ) -> Mapping[str, Any]:
        """读取单条公告审核状态；只读，不抓取正文，不生成审核版本。"""

        batch_id = _required_text(review_batch_id, "reviewBatchId")
        document_key = _required_text(document_id, "documentId")
        category = _enum_value(
            RiskCategory,
            _required_text(candidate_category, "candidateCategory"),
            "candidateCategory",
        )
        document = self._load_document(
            batch_id,
            document_key,
            category.value,
        )
        return self._document_review_state(batch_id, document)

    def get_review_batch_document_content(
        self,
        review_batch_id: str,
        document_id: str,
        candidate_category: str,
    ) -> Tuple[
        OfficialRiskDocumentMetadata,
        OfficialRiskDocumentContentResult,
        Tuple[LeaderRiskLifecycleReviewVersion, ...],
    ]:
        """读取一条待审公告、唯一正文快照和已有人工版本。"""

        state = self.get_review_batch_document(
            review_batch_id,
            document_id,
            candidate_category,
        )
        if not state["hasContentSnapshot"]:
            raise RepositoryStateError("审核正文快照尚未交付")
        document = state["document"]
        return (
            document,
            self._load_content(document),
            self.list_review_versions(
                review_batch_id,
                document_id,
                candidate_category,
            ),
        )

    @staticmethod
    def _event_payload(event: LeaderRiskEventEvidence) -> Mapping[str, Any]:
        if not isinstance(event, LeaderRiskEventEvidence):
            raise ValueError("eventVersions包含无效事件")
        return {
            "eventId": event.event_id,
            "eventVersion": event.event_version,
            "symbol": event.symbol,
            "issuerIdentity": event.issuer_identity,
            "category": event.category.value,
            "eventSubtype": event.event_subtype.value,
            "caseId": event.case_id,
            "sourceKind": event.source_kind.value,
            "sourceName": event.source_name,
            "sourceUrl": event.source_url,
            "documentId": event.document_id,
            "publishedAt": _datetime_text(event.published_at, "published_at"),
            "effectiveFrom": _datetime_text(event.effective_from, "effective_from"),
            "effectiveUntil": (
                _datetime_text(event.effective_until, "effective_until")
                if event.effective_until is not None else None
            ),
            "reportingPeriod": event.reporting_period,
            "factSummary": event.fact_summary,
            "officialStatus": event.official_status.value,
        }

    @staticmethod
    def _event_from_payload(value: Any) -> LeaderRiskEventEvidence:
        if not isinstance(value, dict) or set(value) != {
            "eventId", "eventVersion", "symbol", "issuerIdentity",
            "category", "eventSubtype", "caseId", "sourceKind",
            "sourceName", "sourceUrl", "documentId", "publishedAt",
            "effectiveFrom", "effectiveUntil", "reportingPeriod",
            "factSummary", "officialStatus",
        }:
            raise RepositoryStateError("数据库中的事件版本结构无效")
        return LeaderRiskEventEvidence(
            event_id=value["eventId"],
            event_version=value["eventVersion"],
            symbol=value["symbol"],
            issuer_identity=value["issuerIdentity"],
            category=_enum_value(RiskCategory, value["category"], "category"),
            event_subtype=_enum_value(
                RiskEventSubtype, value["eventSubtype"], "event_subtype"
            ),
            case_id=value["caseId"],
            source_kind=_enum_value(
                RiskEvidenceSourceKind, value["sourceKind"], "source_kind"
            ),
            source_name=value["sourceName"],
            source_url=value["sourceUrl"],
            document_id=value["documentId"],
            published_at=_parse_datetime(value["publishedAt"], "published_at"),
            effective_from=_parse_datetime(
                value["effectiveFrom"], "effective_from"
            ),
            effective_until=(
                _parse_datetime(value["effectiveUntil"], "effective_until")
                if value["effectiveUntil"] is not None else None
            ),
            reporting_period=value["reportingPeriod"],
            fact_summary=value["factSummary"],
            official_status=_enum_value(
                RiskOfficialStatus, value["officialStatus"], "official_status"
            ),
        )

    @staticmethod
    def _relation_payload(
        review: Optional[RiskDocumentVersionReview],
    ) -> Optional[Mapping[str, Any]]:
        if review is None:
            return None
        if not isinstance(review, RiskDocumentVersionReview):
            raise ValueError("relationReview结构无效")
        return {
            "reviewId": review.review_id,
            "mappingVersion": review.mapping_version,
            "relationKind": review.relation_kind.value,
            "reviewMethod": review.review_method,
            "reviewerKey": review.reviewer_key,
            "reviewedAt": _datetime_text(review.reviewed_at, "reviewed_at"),
            "effectiveUntil": (
                _datetime_text(review.effective_until, "effective_until")
                if review.effective_until is not None else None
            ),
            "sourceDocumentId": review.source_document_id,
            "targetEventId": review.target_event_id,
            "targetEventVersion": review.target_event_version,
            "targetDocumentId": review.target_document_id,
            "replacementEventVersion": review.replacement_event_version,
            "basisFactIds": list(review.basis_fact_ids),
            "decisionSummary": review.decision_summary,
        }

    @staticmethod
    def _relation_from_payload(value: Any) -> RiskDocumentVersionReview:
        if not isinstance(value, dict) or set(value) != {
            "reviewId", "mappingVersion", "relationKind", "reviewMethod",
            "reviewerKey", "reviewedAt", "effectiveUntil",
            "sourceDocumentId", "targetEventId", "targetEventVersion",
            "targetDocumentId", "replacementEventVersion",
            "basisFactIds", "decisionSummary",
        } or not isinstance(value["basisFactIds"], list):
            raise RepositoryStateError("数据库中的关系审核结构无效")
        return RiskDocumentVersionReview(
            review_id=value["reviewId"],
            mapping_version=value["mappingVersion"],
            relation_kind=_enum_value(
                RiskDocumentRelationKind,
                value["relationKind"],
                "relation_kind",
            ),
            review_method=value["reviewMethod"],
            reviewer_key=value["reviewerKey"],
            reviewed_at=_parse_datetime(value["reviewedAt"], "reviewed_at"),
            effective_until=(
                _parse_datetime(value["effectiveUntil"], "effective_until")
                if value["effectiveUntil"] is not None else None
            ),
            source_document_id=value["sourceDocumentId"],
            target_event_id=value["targetEventId"],
            target_event_version=value["targetEventVersion"],
            target_document_id=value["targetDocumentId"],
            replacement_event_version=value["replacementEventVersion"],
            basis_fact_ids=tuple(value["basisFactIds"]),
            decision_summary=value["decisionSummary"],
        )

    @staticmethod
    def _fact_submission_payload(
        value: ManualRiskDocumentFactSubmission,
    ) -> Mapping[str, Any]:
        if not isinstance(value, ManualRiskDocumentFactSubmission):
            raise ValueError("factSupplements结构无效")
        return {
            "factKind": value.fact_kind.value,
            "sourceValue": value.source_value,
            "pageNumber": value.page_number,
            "sourceFragment": value.source_fragment,
            "mappedDocumentId": value.mapped_document_id,
        }

    @staticmethod
    def _fact_submission_from_payload(
        value: Any,
    ) -> ManualRiskDocumentFactSubmission:
        if not isinstance(value, dict) or set(value) != {
            "factKind", "sourceValue", "pageNumber", "sourceFragment",
            "mappedDocumentId",
        }:
            raise RepositoryStateError("数据库中的人工事实结构无效")
        return ManualRiskDocumentFactSubmission(
            fact_kind=_enum_value(
                RiskDocumentFactKind, value["factKind"], "fact_kind"
            ),
            source_value=value["sourceValue"],
            page_number=value["pageNumber"],
            source_fragment=value["sourceFragment"],
            mapped_document_id=value["mappedDocumentId"],
        )

    @classmethod
    def _content_payload(
        cls,
        document: OfficialRiskDocumentMetadata,
        content: OfficialRiskDocumentContentResult,
    ) -> Mapping[str, Any]:
        if not isinstance(content, OfficialRiskDocumentContentResult):
            raise ValueError("content结构无效")
        if (
            content.status != ResearchFeatureStatus.READY
            or content.formal_usable is not False
            or content.reasons
            or content.document_id != document.document_id
            or content.symbol != document.symbol
            or content.issuer_identity != document.issuer_identity
            or not isinstance(content.content_sha256, str)
            or SHA256_PATTERN.fullmatch(content.content_sha256) is None
            or content.byte_count is None
            or content.page_count is None
            or content.fetched_at is None
            or not isinstance(content.pages, tuple)
            or content.page_count != len(content.pages)
            or not 1 <= content.page_count <= MAXIMUM_PAGE_COUNT
        ):
            raise ValueError("只允许保存身份完整且状态为ready的审核正文")
        pages = []
        total_characters = 0
        for expected_number, page in enumerate(content.pages, start=1):
            if (
                not isinstance(page, OfficialRiskDocumentPage)
                or page.page_number != expected_number
                or not isinstance(page.text, str)
                or len(page.text) > MAXIMUM_PAGE_CHARACTERS
            ):
                raise ValueError("审核正文页码或单页长度无效")
            total_characters += len(page.text)
            pages.append({
                "pageNumber": page.page_number,
                "text": page.text,
                "pageSha256": hashlib.sha256(
                    page.text.encode("utf-8")
                ).hexdigest(),
            })
        if total_characters > MAXIMUM_TOTAL_CHARACTERS:
            raise ValueError("审核正文总字符数超过上限")
        return {
            "documentId": content.document_id,
            "contentSha256": content.content_sha256,
            "symbol": content.symbol,
            "issuerIdentity": content.issuer_identity,
            "status": content.status.value,
            "byteCount": _integer(content.byte_count, "byteCount"),
            "pageCount": content.page_count,
            "pages": pages,
            "fetchedAt": _datetime_text(content.fetched_at, "fetched_at"),
            "contentContractId": _required_text(
                content.content_contract_id, "contentContractId"
            ),
            "formalUsable": False,
        }

    def _save_content(
        self,
        document: OfficialRiskDocumentMetadata,
        content: OfficialRiskDocumentContentResult,
        created_at: str,
    ) -> bool:
        payload = self._content_payload(document, content)
        checksum = _checksum(payload)
        existing = self._connection.execute(
            "SELECT record_checksum FROM radar_leader_risk_document_contents "
            "WHERE document_id=? AND content_sha256=?",
            (payload["documentId"], payload["contentSha256"]),
        ).fetchone()
        if existing is not None:
            if existing[0] != checksum:
                raise RepositoryConflictError(
                    "同一公告正文指纹已存在不同内容"
                )
            self._load_content(document, payload["contentSha256"])
            return False
        self._connection.execute(
            """
            INSERT INTO radar_leader_risk_document_contents (
                document_id, content_sha256, symbol, issuer_identity,
                status, byte_count, page_count, fetched_at,
                content_contract_id, formal_usable,
                record_checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                payload["documentId"], payload["contentSha256"],
                payload["symbol"], payload["issuerIdentity"],
                payload["status"], payload["byteCount"],
                payload["pageCount"], payload["fetchedAt"],
                payload["contentContractId"], checksum, created_at,
            ),
        )
        for page in payload["pages"]:
            self._connection.execute(
                """
                INSERT INTO radar_leader_risk_document_pages (
                    document_id, content_sha256, page_number,
                    page_text, character_count, page_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["documentId"], payload["contentSha256"],
                    page["pageNumber"], page["text"], len(page["text"]),
                    page["pageSha256"],
                ),
            )
        return True

    def save_document_content_snapshots(
        self,
        review_batch_id: str,
        snapshots: Sequence[
            Tuple[str, OfficialRiskDocumentContentResult]
        ],
    ) -> int:
        """原子保存一组已验证正文快照，不生成任何人工审核版本。"""

        batch_id = _required_text(review_batch_id, "reviewBatchId")
        snapshot_tuple = tuple(snapshots)
        if not snapshot_tuple:
            raise ValueError("snapshots不能为空")
        prepared = []
        seen_document_ids = set()
        for candidate_category, content in snapshot_tuple:
            category = _enum_value(
                RiskCategory,
                _required_text(
                    candidate_category,
                    "candidateCategory",
                ),
                "candidateCategory",
            )
            if not isinstance(content, OfficialRiskDocumentContentResult):
                raise ValueError("snapshots包含无效正文")
            document_id = _required_text(
                content.document_id,
                "documentId",
            )
            if document_id in seen_document_ids:
                raise ValueError("同一公告不能在一次正文交付中重复")
            seen_document_ids.add(document_id)
            document = self._load_document(
                batch_id,
                document_id,
                category.value,
            )
            self._content_payload(document, content)
            prepared.append((document, content))

        created_at = self._clock_text()
        inserted = 0
        with self._transaction():
            for document, content in prepared:
                inserted += int(
                    self._save_content(document, content, created_at)
                )
        return inserted

    def _load_content(
        self,
        document: OfficialRiskDocumentMetadata,
        content_sha256: Optional[str] = None,
    ) -> OfficialRiskDocumentContentResult:
        parameters: Tuple[Any, ...]
        where = "document_id=?"
        parameters = (document.document_id,)
        if content_sha256 is not None:
            where += " AND content_sha256=?"
            parameters = (document.document_id, content_sha256)
        rows = self._connection.execute(
            "SELECT content_sha256, symbol, issuer_identity, status, "
            "byte_count, page_count, fetched_at, content_contract_id, "
            "formal_usable, record_checksum "
            "FROM radar_leader_risk_document_contents WHERE " + where,
            parameters,
        ).fetchall()
        if len(rows) != 1:
            raise RepositoryStateError("审核正文快照不存在或不唯一")
        row = rows[0]
        page_rows = self._connection.execute(
            """
            SELECT page_number, page_text, character_count, page_sha256
            FROM radar_leader_risk_document_pages
            WHERE document_id=? AND content_sha256=?
            ORDER BY page_number
            """,
            (document.document_id, row[0]),
        ).fetchall()
        for page in page_rows:
            if (
                page[2] != len(page[1])
                or page[3] != hashlib.sha256(
                    page[1].encode("utf-8")
                ).hexdigest()
            ):
                raise RepositoryStateError("审核正文页校验失败")
        content = OfficialRiskDocumentContentResult(
            status=_enum_value(
                ResearchFeatureStatus, row[3], "content_status"
            ),
            document_id=document.document_id,
            symbol=row[1],
            issuer_identity=row[2],
            content_sha256=row[0],
            byte_count=row[4],
            page_count=row[5],
            pages=tuple(
                OfficialRiskDocumentPage(item[0], item[1])
                for item in page_rows
            ),
            fetched_at=_parse_datetime(row[6], "fetched_at"),
            content_contract_id=row[7],
            formal_usable=bool(row[8]),
            reasons=(),
        )
        payload = self._content_payload(document, content)
        if _checksum(payload) != row[9]:
            raise RepositoryStateError("审核正文快照校验失败")
        return content

    @classmethod
    def _version_payload(
        cls,
        review_batch_id: str,
        version: LeaderRiskLifecycleReviewVersion,
    ) -> Mapping[str, Any]:
        if not isinstance(version, LeaderRiskLifecycleReviewVersion):
            raise ValueError("reviewVersion结构无效")
        document = version.document
        if not isinstance(document, OfficialRiskDocumentMetadata):
            raise ValueError("reviewVersion.document结构无效")
        content_payload = cls._content_payload(document, version.content)
        if not isinstance(version.event_versions, tuple) or any(
            not isinstance(item, LeaderRiskEventEvidence)
            for item in version.event_versions
        ):
            raise ValueError("eventVersions结构无效")
        event_payloads = [
            cls._event_payload(item) for item in version.event_versions
        ]
        event_keys = [
            (item["eventId"], item["eventVersion"])
            for item in event_payloads
        ]
        if len(event_keys) != len(set(event_keys)):
            raise ValueError("eventVersions包含重复版本")
        facts = extract_official_risk_document_facts(
            OfficialRiskDocumentFactInput(
                as_of=version.as_of,
                document=document,
                content_sha256=version.content.content_sha256 or "",
                pages=version.content.pages,
                extracted_at=version.content.fetched_at or version.as_of,
                source_status=version.content.status,
                event_versions=version.event_versions,
                reviews=(),
            )
        )
        candidate_result = build_risk_document_review_candidate(
            version.content,
            facts,
        )
        if (
            facts != version.facts
            or candidate_result.status != ResearchFeatureStatus.READY
            or candidate_result.candidate != version.candidate
            or not isinstance(version.candidate, RiskDocumentReviewCandidate)
        ):
            raise ValueError("审核版本的事实或候选不能由原始输入重建")
        submission = version.submission
        if not isinstance(submission, ManualRiskReviewSubmission):
            raise ValueError("submission结构无效")
        fact_supplements = [
            cls._fact_submission_payload(item)
            for item in submission.fact_supplements
        ]
        submission_relation = cls._relation_payload(
            submission.relation_review
        )
        lifecycle_relation = cls._relation_payload(
            version.relation_review
        )
        if lifecycle_relation is None:
            raise ValueError("lifecycleRelation不能为空")
        payload = {
            "reviewBatchId": _required_text(
                review_batch_id, "reviewBatchId"
            ),
            "documentId": document.document_id,
            "candidateCategory": document.candidate_category.value,
            "contentSha256": content_payload["contentSha256"],
            "symbol": document.symbol,
            "issuerIdentity": document.issuer_identity,
            "candidateId": version.candidate.candidate_id,
            "candidateKind": version.candidate.candidate_kind.value,
            "asOf": _datetime_text(version.as_of, "as_of"),
            "reviewVersion": submission.review_version,
            "supersedesReviewVersion": submission.supersedes_review_version,
            "reviewMethod": submission.review_method,
            "reviewerKey": submission.reviewer_key,
            "reviewedAt": _datetime_text(
                submission.reviewed_at, "reviewed_at"
            ),
            "effectiveUntil": (
                _datetime_text(submission.effective_until, "effective_until")
                if submission.effective_until is not None else None
            ),
            "eventVersions": event_payloads,
            "factSupplements": fact_supplements,
            "submissionRelation": submission_relation,
            "lifecycleRelation": lifecycle_relation,
            "documentChecksum": _checksum(
                cls._catalog_payload(document)
            ),
            "contentChecksum": _checksum(content_payload),
        }
        return payload

    @staticmethod
    def _validate_chain(
        versions: Sequence[LeaderRiskLifecycleReviewVersion],
        *,
        database_state: bool,
    ) -> None:
        artifacts = []
        for version in versions:
            artifact_result = build_manual_risk_review_artifact(
                ManualRiskReviewArtifactInput(
                    as_of=version.as_of,
                    document=version.document,
                    content=version.content,
                    facts=version.facts,
                    candidate=version.candidate,
                    event_versions=version.event_versions,
                    submission=version.submission,
                    previous_artifacts=tuple(artifacts),
                )
            )
            if (
                artifact_result.status != ResearchFeatureStatus.READY
                or artifact_result.artifact is None
            ):
                error = RepositoryStateError if database_state else ValueError
                raise error(
                    "人工审核版本链未通过验证："
                    + ",".join(artifact_result.reasons)
                )
            artifacts.append(artifact_result.artifact)
            replay_input = RiskDocumentResearchReplayInput(
                as_of=version.as_of,
                document=version.document,
                content=version.content,
                facts=version.facts,
                event_versions=version.event_versions,
                artifacts=tuple(artifacts),
            )
            replay = replay_risk_document_research_evidence(replay_input)
            relation = review_supplemented_risk_document_relation(
                SupplementedRiskDocumentRelationInput(
                    replay_input=replay_input,
                    replay_result=replay,
                    review=version.relation_review,
                )
            )
            if (
                relation.status != ResearchFeatureStatus.READY
                or relation.relation is None
            ):
                error = RepositoryStateError if database_state else ValueError
                raise error(
                    "补录关系审核未通过验证："
                    + ",".join(relation.reasons)
                )

    def _decode_version_row(
        self,
        row: Sequence[Any],
    ) -> LeaderRiskLifecycleReviewVersion:
        document = self._load_document(row[1], row[2], row[3])
        content = self._load_content(document, row[4])
        event_values = _json_value(row[15], list, "event_versions_json")
        fact_values = _json_value(row[16], list, "fact_supplements_json")
        submission_relation_value = (
            _json_value(row[17], dict, "submission_relation_json")
            if row[17] is not None else None
        )
        lifecycle_relation_value = _json_value(
            row[18], dict, "lifecycle_relation_json"
        )
        events = tuple(
            self._event_from_payload(item) for item in event_values
        )
        as_of = _parse_datetime(row[9], "as_of")
        facts = extract_official_risk_document_facts(
            OfficialRiskDocumentFactInput(
                as_of=as_of,
                document=document,
                content_sha256=content.content_sha256 or "",
                pages=content.pages,
                extracted_at=content.fetched_at or as_of,
                source_status=content.status,
                event_versions=events,
                reviews=(),
            )
        )
        candidate_result = build_risk_document_review_candidate(
            content,
            facts,
        )
        if (
            candidate_result.status != ResearchFeatureStatus.READY
            or candidate_result.candidate is None
            or candidate_result.candidate.candidate_id != row[7]
            or candidate_result.candidate.candidate_kind.value != row[8]
        ):
            raise RepositoryStateError("审核候选不能由持久化输入重建")
        submission = ManualRiskReviewSubmission(
            review_version=row[10],
            supersedes_review_version=row[11],
            review_method=row[12],
            reviewer_key=row[13],
            reviewed_at=_parse_datetime(row[14], "reviewed_at"),
            effective_until=(
                _parse_datetime(row[19], "effective_until")
                if row[19] is not None else None
            ),
            candidate_id=row[7],
            document_id=row[2],
            symbol=row[5],
            issuer_identity=row[6],
            content_sha256=row[4],
            fact_supplements=tuple(
                self._fact_submission_from_payload(item)
                for item in fact_values
            ),
            relation_review=(
                self._relation_from_payload(submission_relation_value)
                if submission_relation_value is not None else None
            ),
        )
        version = LeaderRiskLifecycleReviewVersion(
            as_of=as_of,
            document=document,
            content=content,
            facts=facts,
            candidate=candidate_result.candidate,
            event_versions=events,
            submission=submission,
            relation_review=self._relation_from_payload(
                lifecycle_relation_value
            ),
        )
        payload = self._version_payload(row[1], version)
        if _checksum(payload) != row[20]:
            raise RepositoryStateError("人工审核版本记录校验失败")
        return version

    def _history(
        self,
        document_id: str,
        candidate_id: str,
    ) -> Tuple[Tuple[str, LeaderRiskLifecycleReviewVersion], ...]:
        rows = self._connection.execute(
            """
            SELECT review_record_id, review_batch_id, document_id,
                   candidate_category, content_sha256, symbol,
                   issuer_identity, candidate_id, candidate_kind, as_of,
                   review_version, supersedes_review_version,
                   review_method, reviewer_key, reviewed_at,
                   event_versions_json, fact_supplements_json,
                   submission_relation_json, lifecycle_relation_json,
                   effective_until, record_checksum
            FROM radar_leader_risk_manual_review_versions
            WHERE document_id=? AND candidate_id=?
            ORDER BY reviewed_at, review_version
            """,
            (document_id, candidate_id),
        ).fetchall()
        values = tuple(
            (row[1], self._decode_version_row(row))
            for row in rows
        )
        self._validate_chain(
            tuple(item[1] for item in values),
            database_state=True,
        )
        return values

    def save_review_version(
        self,
        review_batch_id: str,
        version: LeaderRiskLifecycleReviewVersion,
    ) -> bool:
        payload = self._version_payload(review_batch_id, version)
        checksum = _checksum(payload)
        document = version.document
        link = self._connection.execute(
            """
            SELECT 1 FROM radar_leader_risk_review_batch_documents
            WHERE review_batch_id=? AND document_id=?
              AND candidate_category=?
            """,
            (
                payload["reviewBatchId"], payload["documentId"],
                payload["candidateCategory"],
            ),
        ).fetchone()
        if link is None:
            raise RepositoryConflictError("审核版本不属于指定D2审核批次")
        stored_document = self._load_document(
            payload["reviewBatchId"],
            payload["documentId"],
            payload["candidateCategory"],
        )
        if stored_document != document:
            raise RepositoryConflictError("审核版本公告与批次目录不一致")

        existing = self._connection.execute(
            """
            SELECT record_checksum
            FROM radar_leader_risk_manual_review_versions
            WHERE document_id=? AND candidate_id=? AND review_version=?
            """,
            (
                payload["documentId"], payload["candidateId"],
                payload["reviewVersion"],
            ),
        ).fetchone()
        if existing is not None:
            if existing[0] != checksum:
                raise RepositoryConflictError(
                    "同一人工审核版本已存在不同内容"
                )
            self._history(payload["documentId"], payload["candidateId"])
            return False

        history = self._history(
            payload["documentId"],
            payload["candidateId"],
        )
        expected_predecessor = (
            history[-1][1].submission.review_version
            if history else None
        )
        if payload["supersedesReviewVersion"] != expected_predecessor:
            raise RepositoryConflictError(
                "人工审核版本必须直接引用当前最后版本"
            )
        self._validate_chain(
            (*tuple(item[1] for item in history), version),
            database_state=False,
        )
        review_record_id = (
            "risk-review-version:"
            + hashlib.sha256(
                "|".join((
                    payload["documentId"], payload["candidateId"],
                    payload["reviewVersion"],
                )).encode("utf-8")
            ).hexdigest()
        )
        created_at = self._clock_text()
        with self._transaction():
            self._save_content(document, version.content, created_at)
            self._connection.execute(
                """
                INSERT INTO radar_leader_risk_manual_review_versions (
                    review_record_id, review_batch_id, document_id,
                    candidate_category, content_sha256, symbol,
                    issuer_identity, candidate_id, candidate_kind, as_of,
                    review_version, supersedes_review_version,
                    review_method, reviewer_key, reviewed_at,
                    effective_until, event_versions_json,
                    fact_supplements_json, submission_relation_json,
                    lifecycle_relation_json, record_checksum, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    review_record_id, payload["reviewBatchId"],
                    payload["documentId"], payload["candidateCategory"],
                    payload["contentSha256"], payload["symbol"],
                    payload["issuerIdentity"], payload["candidateId"],
                    payload["candidateKind"], payload["asOf"],
                    payload["reviewVersion"],
                    payload["supersedesReviewVersion"],
                    payload["reviewMethod"], payload["reviewerKey"],
                    payload["reviewedAt"], payload["effectiveUntil"],
                    _canonical_json(payload["eventVersions"]),
                    _canonical_json(payload["factSupplements"]),
                    (
                        _canonical_json(payload["submissionRelation"])
                        if payload["submissionRelation"] is not None else None
                    ),
                    _canonical_json(payload["lifecycleRelation"]),
                    checksum, created_at,
                ),
            )
        return True

    def list_review_versions(
        self,
        review_batch_id: str,
        document_id: str,
        candidate_category: Optional[str] = None,
    ) -> Tuple[LeaderRiskLifecycleReviewVersion, ...]:
        batch_id = _required_text(review_batch_id, "reviewBatchId")
        document_key = _required_text(document_id, "documentId")
        category = (
            _enum_value(
                RiskCategory,
                _required_text(candidate_category, "candidateCategory"),
                "candidateCategory",
            ).value
            if candidate_category is not None else None
        )
        candidate_rows = self._connection.execute(
            """
            SELECT DISTINCT candidate_id
            FROM radar_leader_risk_manual_review_versions
            WHERE review_batch_id=? AND document_id=?
            ORDER BY candidate_id
            """,
            (batch_id, document_key),
        ).fetchall()
        values = []
        for row in candidate_rows:
            values.extend(
                version
                for stored_batch_id, version in self._history(
                    document_key, row[0]
                )
                if stored_batch_id == batch_id
            )
        return tuple(sorted(
            (
                value
                for value in values
                if category is None
                or value.document.candidate_category.value == category
            ),
            key=lambda item: (
                item.submission.reviewed_at,
                item.submission.review_version,
            ),
        ))
