"""阶段6官方主营自动证据的共用失败关闭状态。"""

from enum import Enum


DETERMINISTIC_BUSINESS_RELATION_RULE_VERSION = (
    "radar-leader-business-deterministic-relation-v31"
)
MAXIMUM_DETERMINISTIC_COLLECTION_DELAY_SECONDS = 24 * 60 * 60


class AutomaticBusinessEvidenceStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    SOURCE_FAILED = "source_failed"
    SOURCE_UNVERIFIED = "source_unverified"


class OfficialBusinessDocumentKind(str, Enum):
    ANNUAL_REPORT = "annual_report"
    CATALYST = "catalyst"
