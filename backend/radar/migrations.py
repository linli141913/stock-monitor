"""主线雷达独立、显式调用的SQLite版本化迁移。"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional, Sequence, Tuple


class MigrationError(RuntimeError):
    """雷达迁移基础错误。"""


class MigrationDriftError(MigrationError):
    """数据库记录的迁移版本与当前代码不一致。"""


class MigrationApplyError(MigrationError):
    """某个迁移执行失败且已经回滚。"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: Tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement boundary --\n".join(
            statement.strip()
            for statement in self.statements
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS radar_schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


INITIAL_RADAR_MIGRATION = Migration(
    version=1,
    name="initial_radar_foundation",
    statements=(
        """
        CREATE TABLE radar_rule_versions (
            rule_version_id TEXT PRIMARY KEY,
            scope TEXT NOT NULL CHECK (
                scope IN ('source_health', 'market', 'sector', 'etf', 'leader')
            ),
            version TEXT NOT NULL,
            definition_json TEXT NOT NULL,
            checksum TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('draft', 'shadow', 'frozen', 'retired')
            ),
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (scope, version),
            CHECK (effective_to IS NULL OR effective_to > effective_from)
        )
        """,
        """
        CREATE INDEX idx_radar_rule_scope_status
        ON radar_rule_versions (scope, status, effective_from)
        """,
        """
        CREATE TABLE radar_runs (
            radar_run_id TEXT PRIMARY KEY,
            as_of TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'running', 'succeeded', 'degraded', 'failed')
            ),
            shadow_mode INTEGER NOT NULL DEFAULT 1 CHECK (shadow_mode IN (0, 1)),
            rule_version_id TEXT,
            expected_stock_count INTEGER CHECK (
                expected_stock_count IS NULL OR expected_stock_count >= 0
            ),
            returned_stock_count INTEGER CHECK (
                returned_stock_count IS NULL OR returned_stock_count >= 0
            ),
            stock_coverage REAL CHECK (
                stock_coverage IS NULL OR stock_coverage BETWEEN 0 AND 1
            ),
            expected_etf_count INTEGER CHECK (
                expected_etf_count IS NULL OR expected_etf_count >= 0
            ),
            returned_etf_count INTEGER CHECK (
                returned_etf_count IS NULL OR returned_etf_count >= 0
            ),
            etf_coverage REAL CHECK (
                etf_coverage IS NULL OR etf_coverage BETWEEN 0 AND 1
            ),
            started_at TEXT NOT NULL,
            completed_at TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (rule_version_id)
                REFERENCES radar_rule_versions(rule_version_id)
                ON DELETE RESTRICT,
            CHECK (completed_at IS NULL OR completed_at >= started_at),
            CHECK (
                returned_stock_count IS NULL
                OR expected_stock_count IS NULL
                OR returned_stock_count <= expected_stock_count
            ),
            CHECK (
                returned_etf_count IS NULL
                OR expected_etf_count IS NULL
                OR returned_etf_count <= expected_etf_count
            )
        )
        """,
        """
        CREATE INDEX idx_radar_runs_as_of_status
        ON radar_runs (as_of, status)
        """,
        """
        CREATE TABLE radar_source_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            radar_run_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            source TEXT NOT NULL,
            as_of TEXT NOT NULL,
            source_time TEXT,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('healthy', 'degraded', 'stale', 'failed')
            ),
            expected_count INTEGER CHECK (
                expected_count IS NULL OR expected_count >= 0
            ),
            returned_count INTEGER NOT NULL DEFAULT 0 CHECK (returned_count >= 0),
            row_coverage REAL CHECK (
                row_coverage IS NULL OR row_coverage BETWEEN 0 AND 1
            ),
            required_field_coverage_json TEXT NOT NULL DEFAULT '{}',
            issues_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            UNIQUE (radar_run_id, batch_id, source),
            FOREIGN KEY (radar_run_id)
                REFERENCES radar_runs(radar_run_id)
                ON DELETE RESTRICT,
            CHECK (
                (expected_count IS NULL AND row_coverage IS NULL)
                OR (
                    expected_count IS NOT NULL
                    AND row_coverage IS NOT NULL
                    AND returned_count <= expected_count
                )
            )
        )
        """,
        """
        CREATE INDEX idx_radar_source_status_source_time
        ON radar_source_status (source, source_time, status)
        """,
        """
        CREATE TABLE security_master_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            name TEXT NOT NULL,
            exchange TEXT NOT NULL CHECK (exchange IN ('sse', 'szse', 'bse')),
            board TEXT NOT NULL,
            listing_date TEXT,
            total_shares REAL,
            circulating_shares REAL,
            source_industry TEXT,
            source_report_date TEXT,
            announced_at TEXT,
            source TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            source_fields_json TEXT NOT NULL,
            record_checksum TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (symbol, source, effective_from),
            CHECK (effective_to IS NULL OR effective_to > effective_from)
        )
        """,
        """
        CREATE INDEX idx_security_master_effective
        ON security_master_history (symbol, effective_from, effective_to)
        """,
        """
        CREATE UNIQUE INDEX uq_security_master_current_source
        ON security_master_history (symbol, source)
        WHERE effective_to IS NULL
        """,
        """
        CREATE TABLE etf_product_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            name TEXT NOT NULL,
            exchange TEXT NOT NULL CHECK (exchange IN ('sse', 'szse')),
            source_type TEXT,
            investment_type TEXT,
            listing_date TEXT,
            fund_shares REAL,
            manager TEXT,
            sponsor TEXT,
            custodian TEXT,
            nav REAL,
            source_report_date TEXT,
            announced_at TEXT,
            source TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            source_fields_json TEXT NOT NULL,
            record_checksum TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (symbol, source, effective_from),
            CHECK (effective_to IS NULL OR effective_to > effective_from)
        )
        """,
        """
        CREATE INDEX idx_etf_registry_effective
        ON etf_product_registry (symbol, effective_from, effective_to)
        """,
        """
        CREATE UNIQUE INDEX uq_etf_registry_current_source
        ON etf_product_registry (symbol, source)
        WHERE effective_to IS NULL
        """,
    ),
)


INDUSTRY_STORAGE_MIGRATION = Migration(
    version=2,
    name="industry_classification_and_sector_features",
    statements=(
        """
        CREATE UNIQUE INDEX uq_radar_runs_id_as_of
        ON radar_runs (radar_run_id, as_of)
        """,
        """
        CREATE TABLE industry_classification_releases (
            industry_release_id TEXT PRIMARY KEY,
            classification_system TEXT NOT NULL,
            scheme_version TEXT NOT NULL,
            release_period TEXT NOT NULL CHECK (
                length(release_period) = 6
                AND substr(release_period, 1, 4) NOT GLOB '*[^0-9]*'
                AND substr(release_period, 5, 2) IN ('H1', 'H2')
            ),
            source_page_title TEXT NOT NULL CHECK (trim(source_page_title) <> ''),
            publication_page_url TEXT NOT NULL CHECK (
                trim(publication_page_url) <> ''
            ),
            document_url TEXT NOT NULL CHECK (trim(document_url) <> ''),
            document_sha256 TEXT NOT NULL CHECK (
                length(document_sha256) = 64
                AND document_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            published_date TEXT NOT NULL CHECK (
                length(published_date) = 10
                AND date(published_date) = published_date
            ),
            first_observed_at TEXT NOT NULL CHECK (
                julianday(first_observed_at) IS NOT NULL
            ),
            fetched_at TEXT NOT NULL CHECK (julianday(fetched_at) IS NOT NULL),
            knowledge_effective_from TEXT NOT NULL CHECK (
                julianday(knowledge_effective_from) IS NOT NULL
            ),
            knowledge_effective_to TEXT,
            classification_start_date TEXT NOT NULL CHECK (
                length(classification_start_date) = 10
                AND date(classification_start_date) = classification_start_date
            ),
            history_status TEXT NOT NULL CHECK (
                history_status IN ('forward_observed', 'retrospective_unverified')
            ),
            source_record_count INTEGER NOT NULL CHECK (source_record_count >= 0),
            unique_source_symbol_count INTEGER NOT NULL CHECK (
                unique_source_symbol_count >= 0
                AND unique_source_symbol_count <= source_record_count
            ),
            required_field_coverage_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (classification_system, release_period),
            CHECK (julianday(fetched_at) >= julianday(first_observed_at)),
            CHECK (
                julianday(knowledge_effective_from)
                >= julianday(first_observed_at)
            ),
            CHECK (
                knowledge_effective_to IS NULL
                OR (
                    julianday(knowledge_effective_to) IS NOT NULL
                    AND julianday(knowledge_effective_to)
                        > julianday(knowledge_effective_from)
                )
            ),
            CHECK (julianday(first_observed_at) >= julianday(published_date)),
            CHECK (classification_start_date <= published_date)
        )
        """,
        """
        CREATE INDEX idx_industry_release_knowledge_effective
        ON industry_classification_releases (
            classification_system,
            knowledge_effective_from,
            knowledge_effective_to
        )
        """,
        """
        CREATE UNIQUE INDEX uq_industry_release_current_system
        ON industry_classification_releases (classification_system)
        WHERE knowledge_effective_to IS NULL
        """,
        """
        CREATE TABLE industry_classification_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            industry_release_id TEXT NOT NULL,
            source_symbol TEXT NOT NULL CHECK (
                length(source_symbol) = 6
                AND source_symbol NOT GLOB '*[^0-9]*'
            ),
            source_name TEXT NOT NULL CHECK (trim(source_name) <> ''),
            security_identity TEXT CHECK (
                security_identity IS NULL
                OR (
                    length(security_identity) = 6
                    AND security_identity NOT GLOB '*[^0-9]*'
                )
            ),
            identity_status TEXT NOT NULL CHECK (
                identity_status IN ('exact', 'verified_alias', 'unresolved')
            ),
            category_code TEXT NOT NULL CHECK (
                length(category_code) = 1
                AND category_code GLOB '[A-T]'
            ),
            category_name TEXT NOT NULL CHECK (trim(category_name) <> ''),
            division_code TEXT NOT NULL CHECK (
                length(division_code) = 2
                AND division_code NOT GLOB '*[^0-9]*'
            ),
            division_name TEXT NOT NULL CHECK (trim(division_name) <> ''),
            manufacturing_subclass_code TEXT CHECK (
                manufacturing_subclass_code IS NULL
                OR (
                    length(manufacturing_subclass_code) = 2
                    AND manufacturing_subclass_code GLOB '[A-Z][A-Z]'
                )
            ),
            manufacturing_subclass_name TEXT,
            record_status TEXT NOT NULL CHECK (
                record_status IN (
                    'accepted', 'unconfirmed', 'conflict', 'source_failed'
                )
            ),
            issue_codes_json TEXT NOT NULL DEFAULT '[]',
            source_fields_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (industry_release_id, source_symbol),
            UNIQUE (industry_release_id, security_identity),
            FOREIGN KEY (industry_release_id)
                REFERENCES industry_classification_releases(industry_release_id)
                ON DELETE RESTRICT,
            CHECK (
                (
                    identity_status = 'unresolved'
                    AND security_identity IS NULL
                    AND record_status <> 'accepted'
                )
                OR (
                    identity_status IN ('exact', 'verified_alias')
                    AND security_identity IS NOT NULL
                )
            ),
            CHECK (
                (
                    category_code = 'C'
                    AND manufacturing_subclass_code IS NOT NULL
                    AND manufacturing_subclass_name IS NOT NULL
                    AND trim(manufacturing_subclass_name) <> ''
                )
                OR (
                    category_code <> 'C'
                    AND manufacturing_subclass_code IS NULL
                    AND manufacturing_subclass_name IS NULL
                )
            )
        )
        """,
        """
        CREATE INDEX idx_industry_record_release_division
        ON industry_classification_records (
            industry_release_id,
            division_code,
            security_identity
        )
        """,
        """
        CREATE TABLE sector_feature_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            radar_run_id TEXT NOT NULL,
            industry_release_id TEXT NOT NULL,
            classification_batch_id TEXT NOT NULL CHECK (
                trim(classification_batch_id) <> ''
            ),
            quote_batch_id TEXT NOT NULL CHECK (trim(quote_batch_id) <> ''),
            category_code TEXT NOT NULL CHECK (
                length(category_code) = 1
                AND category_code GLOB '[A-T]'
            ),
            category_name TEXT NOT NULL CHECK (trim(category_name) <> ''),
            division_code TEXT NOT NULL CHECK (
                length(division_code) = 2
                AND division_code NOT GLOB '*[^0-9]*'
            ),
            division_name TEXT NOT NULL CHECK (trim(division_name) <> ''),
            as_of TEXT NOT NULL CHECK (julianday(as_of) IS NOT NULL),
            source_time TEXT CHECK (
                source_time IS NULL OR julianday(source_time) IS NOT NULL
            ),
            fetched_at TEXT NOT NULL CHECK (julianday(fetched_at) IS NOT NULL),
            classification_mapping_coverage REAL CHECK (
                classification_mapping_coverage IS NULL
                OR classification_mapping_coverage BETWEEN 0 AND 1
            ),
            mapped_constituent_count INTEGER NOT NULL CHECK (
                mapped_constituent_count >= 0
            ),
            unconfirmed_stock_count INTEGER NOT NULL CHECK (
                unconfirmed_stock_count >= 0
            ),
            expected_count INTEGER NOT NULL CHECK (expected_count >= 0),
            returned_count INTEGER NOT NULL CHECK (
                returned_count >= 0 AND returned_count <= expected_count
            ),
            fresh_count INTEGER NOT NULL CHECK (
                fresh_count >= 0 AND fresh_count <= returned_count
            ),
            valid_return_count INTEGER NOT NULL CHECK (
                valid_return_count >= 0 AND valid_return_count <= returned_count
            ),
            valid_market_cap_count INTEGER NOT NULL CHECK (
                valid_market_cap_count >= 0
                AND valid_market_cap_count <= returned_count
            ),
            valid_turnover_count INTEGER NOT NULL CHECK (
                valid_turnover_count >= 0
                AND valid_turnover_count <= returned_count
            ),
            row_coverage REAL NOT NULL CHECK (row_coverage BETWEEN 0 AND 1),
            required_field_coverage_json TEXT NOT NULL DEFAULT '{}',
            is_complete INTEGER NOT NULL CHECK (is_complete IN (0, 1)),
            equal_return REAL,
            cap_weighted_return REAL,
            ex_top_return REAL,
            top_contributor_symbol TEXT CHECK (
                top_contributor_symbol IS NULL
                OR (
                    length(top_contributor_symbol) = 6
                    AND top_contributor_symbol NOT GLOB '*[^0-9]*'
                )
            ),
            top_contribution_percent_points REAL,
            market_cap_basis TEXT NOT NULL CHECK (
                market_cap_basis = 'total_market_cap_source'
            ),
            market_cap_unit_status TEXT NOT NULL CHECK (
                market_cap_unit_status IN ('verified', 'unverified')
            ),
            advancers INTEGER NOT NULL CHECK (advancers >= 0),
            decliners INTEGER NOT NULL CHECK (decliners >= 0),
            flat INTEGER NOT NULL CHECK (flat >= 0),
            unavailable INTEGER NOT NULL CHECK (unavailable >= 0),
            up_ratio REAL CHECK (up_ratio IS NULL OR up_ratio BETWEEN 0 AND 1),
            turnover_raw_value REAL CHECK (
                turnover_raw_value IS NULL OR turnover_raw_value >= 0
            ),
            turnover_contributing_count INTEGER NOT NULL CHECK (
                turnover_contributing_count >= 0
                AND turnover_contributing_count <= expected_count
            ),
            turnover_unit_status TEXT NOT NULL CHECK (
                turnover_unit_status IN ('verified', 'unverified')
            ),
            shadow_usable INTEGER NOT NULL CHECK (shadow_usable IN (0, 1)),
            reasons_json TEXT NOT NULL DEFAULT '[]',
            evidence_summary_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (radar_run_id, division_code),
            FOREIGN KEY (radar_run_id, as_of)
                REFERENCES radar_runs(radar_run_id, as_of)
                ON DELETE RESTRICT,
            FOREIGN KEY (industry_release_id)
                REFERENCES industry_classification_releases(industry_release_id)
                ON DELETE RESTRICT,
            CHECK (julianday(fetched_at) >= julianday(as_of)),
            CHECK (
                advancers + decliners + flat + unavailable = expected_count
            ),
            CHECK (
                (cap_weighted_return IS NULL
                    AND top_contributor_symbol IS NULL
                    AND top_contribution_percent_points IS NULL)
                OR (cap_weighted_return IS NOT NULL
                    AND top_contributor_symbol IS NOT NULL
                    AND top_contribution_percent_points IS NOT NULL)
            )
        )
        """,
        """
        CREATE INDEX idx_sector_feature_division_as_of
        ON sector_feature_snapshots (
            division_code,
            as_of,
            industry_release_id
        )
        """,
        """
        CREATE TRIGGER trg_sector_feature_release_time_insert
        BEFORE INSERT ON sector_feature_snapshots
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1
                FROM industry_classification_releases AS release
                WHERE release.industry_release_id = NEW.industry_release_id
                  AND julianday(release.knowledge_effective_from)
                      <= julianday(NEW.as_of)
                  AND (
                      release.knowledge_effective_to IS NULL
                      OR julianday(NEW.as_of)
                          < julianday(release.knowledge_effective_to)
                  )
            ) THEN RAISE(
                ABORT,
                'industry classification knowledge is not effective at snapshot as_of'
            ) END;
        END
        """,
        """
        CREATE TRIGGER trg_sector_feature_release_time_update
        BEFORE UPDATE OF industry_release_id, as_of ON sector_feature_snapshots
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1
                FROM industry_classification_releases AS release
                WHERE release.industry_release_id = NEW.industry_release_id
                  AND julianday(release.knowledge_effective_from)
                      <= julianday(NEW.as_of)
                  AND (
                      release.knowledge_effective_to IS NULL
                      OR julianday(NEW.as_of)
                          < julianday(release.knowledge_effective_to)
                  )
            ) THEN RAISE(
                ABORT,
                'industry classification knowledge is not effective at snapshot as_of'
            ) END;
        END
        """,
        """
        CREATE TRIGGER trg_industry_release_interval_update
        BEFORE UPDATE OF knowledge_effective_from, knowledge_effective_to
        ON industry_classification_releases
        BEGIN
            SELECT CASE WHEN EXISTS (
                SELECT 1
                FROM sector_feature_snapshots AS snapshot
                WHERE snapshot.industry_release_id = OLD.industry_release_id
                  AND (
                      julianday(NEW.knowledge_effective_from)
                          > julianday(snapshot.as_of)
                      OR (
                          NEW.knowledge_effective_to IS NOT NULL
                          AND julianday(snapshot.as_of)
                              >= julianday(NEW.knowledge_effective_to)
                      )
                  )
            ) THEN RAISE(
                ABORT,
                'industry classification interval would invalidate snapshots'
            ) END;
        END
        """,
    ),
)


MARKET_ENVIRONMENT_STORAGE_MIGRATION = Migration(
    version=3,
    name="market_environment_and_index_features",
    statements=(
        """
        CREATE TABLE market_environment_snapshots (
            radar_run_id TEXT PRIMARY KEY,
            index_batch_id TEXT NOT NULL CHECK (trim(index_batch_id) <> ''),
            quote_batch_id TEXT NOT NULL CHECK (trim(quote_batch_id) <> ''),
            as_of TEXT NOT NULL CHECK (julianday(as_of) IS NOT NULL),
            source_time TEXT CHECK (
                source_time IS NULL OR julianday(source_time) IS NOT NULL
            ),
            fetched_at TEXT NOT NULL CHECK (julianday(fetched_at) IS NOT NULL),
            index_expected_count INTEGER NOT NULL CHECK (
                index_expected_count = 4
            ),
            index_returned_count INTEGER NOT NULL CHECK (
                index_returned_count BETWEEN 0 AND index_expected_count
            ),
            index_valid_count INTEGER NOT NULL CHECK (
                index_valid_count BETWEEN 0 AND index_returned_count
            ),
            index_row_coverage REAL NOT NULL CHECK (
                index_row_coverage BETWEEN 0 AND 1
            ),
            index_required_field_coverage_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    CASE WHEN json_valid(index_required_field_coverage_json)
                        THEN json_type(index_required_field_coverage_json) = 'object'
                        ELSE 0
                    END
                ),
            index_is_complete INTEGER NOT NULL CHECK (
                index_is_complete IN (0, 1)
            ),
            index_reasons_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    CASE WHEN json_valid(index_reasons_json)
                        THEN json_type(index_reasons_json) = 'array'
                        ELSE 0
                    END
                ),
            breadth_expected_count INTEGER NOT NULL CHECK (
                breadth_expected_count >= 0
            ),
            breadth_returned_count INTEGER NOT NULL CHECK (
                breadth_returned_count BETWEEN 0 AND breadth_expected_count
            ),
            breadth_valid_count INTEGER NOT NULL CHECK (
                breadth_valid_count BETWEEN 0 AND breadth_returned_count
            ),
            breadth_row_coverage REAL NOT NULL CHECK (
                breadth_row_coverage BETWEEN 0 AND 1
            ),
            breadth_required_field_coverage_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    CASE WHEN json_valid(breadth_required_field_coverage_json)
                        THEN json_type(breadth_required_field_coverage_json) = 'object'
                        ELSE 0
                    END
                ),
            breadth_is_complete INTEGER NOT NULL CHECK (
                breadth_is_complete IN (0, 1)
            ),
            breadth_reasons_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    CASE WHEN json_valid(breadth_reasons_json)
                        THEN json_type(breadth_reasons_json) = 'array'
                        ELSE 0
                    END
                ),
            advancers INTEGER NOT NULL CHECK (advancers >= 0),
            decliners INTEGER NOT NULL CHECK (decliners >= 0),
            flat INTEGER NOT NULL CHECK (flat >= 0),
            unavailable INTEGER NOT NULL CHECK (unavailable >= 0),
            turnover_raw_value REAL CHECK (
                turnover_raw_value IS NULL OR turnover_raw_value >= 0
            ),
            turnover_contributing_count INTEGER NOT NULL CHECK (
                turnover_contributing_count >= 0
            ),
            turnover_unit_status TEXT NOT NULL CHECK (
                turnover_unit_status IN ('verified', 'unverified')
            ),
            turnover_expected_count INTEGER NOT NULL CHECK (
                turnover_expected_count >= 0
            ),
            turnover_returned_count INTEGER NOT NULL CHECK (
                turnover_returned_count BETWEEN 0 AND turnover_expected_count
            ),
            turnover_valid_count INTEGER NOT NULL CHECK (
                turnover_valid_count BETWEEN 0 AND turnover_returned_count
            ),
            turnover_row_coverage REAL NOT NULL CHECK (
                turnover_row_coverage BETWEEN 0 AND 1
            ),
            turnover_required_field_coverage_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    CASE WHEN json_valid(turnover_required_field_coverage_json)
                        THEN json_type(turnover_required_field_coverage_json) = 'object'
                        ELSE 0
                    END
                ),
            turnover_is_complete INTEGER NOT NULL CHECK (
                turnover_is_complete IN (0, 1)
            ),
            turnover_reasons_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    CASE WHEN json_valid(turnover_reasons_json)
                        THEN json_type(turnover_reasons_json) = 'array'
                        ELSE 0
                    END
                ),
            excluded_etf_count INTEGER NOT NULL CHECK (excluded_etf_count >= 0),
            duplicate_symbol_count INTEGER NOT NULL CHECK (
                duplicate_symbol_count >= 0
            ),
            unknown_symbol_count INTEGER NOT NULL CHECK (
                unknown_symbol_count >= 0
            ),
            evidence_summary_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    CASE WHEN json_valid(evidence_summary_json)
                        THEN json_type(evidence_summary_json) = 'object'
                        ELSE 0
                    END
                ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (radar_run_id, as_of),
            FOREIGN KEY (radar_run_id, as_of)
                REFERENCES radar_runs(radar_run_id, as_of)
                ON DELETE RESTRICT,
            CHECK (julianday(fetched_at) >= julianday(as_of)),
            CHECK (
                source_time IS NULL
                OR julianday(source_time) <= julianday(fetched_at)
            ),
            CHECK (julianday(created_at) >= julianday(fetched_at)),
            CHECK (
                abs(
                    index_row_coverage
                    - (1.0 * index_returned_count / index_expected_count)
                ) < 0.000000001
            ),
            CHECK (
                (breadth_expected_count = 0 AND breadth_row_coverage = 0)
                OR (
                    breadth_expected_count > 0
                    AND abs(
                        breadth_row_coverage
                        - (
                            1.0 * breadth_returned_count
                            / breadth_expected_count
                        )
                    ) < 0.000000001
                )
            ),
            CHECK (
                advancers + decliners + flat + unavailable
                    = breadth_expected_count
            ),
            CHECK (
                (turnover_expected_count = 0 AND turnover_row_coverage = 0)
                OR (
                    turnover_expected_count > 0
                    AND abs(
                        turnover_row_coverage
                        - (
                            1.0 * turnover_returned_count
                            / turnover_expected_count
                        )
                    ) < 0.000000001
                )
            ),
            CHECK (turnover_expected_count = breadth_expected_count),
            CHECK (turnover_returned_count = breadth_returned_count),
            CHECK (turnover_contributing_count = turnover_valid_count),
            CHECK (
                (turnover_raw_value IS NULL AND turnover_contributing_count = 0)
                OR (
                    turnover_raw_value IS NOT NULL
                    AND turnover_contributing_count > 0
                )
            )
        )
        """,
        """
        CREATE INDEX idx_market_environment_as_of
        ON market_environment_snapshots (as_of, radar_run_id)
        """,
        """
        CREATE TABLE market_index_feature_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            radar_run_id TEXT NOT NULL,
            as_of TEXT NOT NULL CHECK (julianday(as_of) IS NOT NULL),
            index_key TEXT NOT NULL CHECK (
                index_key IN (
                    'sse_composite', 'szse_component', 'chinext', 'star50'
                )
            ),
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            name TEXT NOT NULL CHECK (trim(name) <> ''),
            exchange TEXT NOT NULL CHECK (exchange IN ('sse', 'szse')),
            source_symbol TEXT NOT NULL CHECK (
                length(source_symbol) = 8
                AND substr(source_symbol, 1, 2) IN ('sh', 'sz')
                AND substr(source_symbol, 3) NOT GLOB '*[^0-9]*'
            ),
            source_time TEXT CHECK (
                source_time IS NULL OR julianday(source_time) IS NOT NULL
            ),
            fetched_at TEXT NOT NULL CHECK (julianday(fetched_at) IS NOT NULL),
            price REAL CHECK (price IS NULL OR price >= 0),
            change_percent REAL,
            source TEXT NOT NULL CHECK (trim(source) <> ''),
            missing_fields_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    CASE WHEN json_valid(missing_fields_json)
                        THEN json_type(missing_fields_json) = 'array'
                        ELSE 0
                    END
                ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (radar_run_id, index_key),
            FOREIGN KEY (radar_run_id, as_of)
                REFERENCES market_environment_snapshots(radar_run_id, as_of)
                ON DELETE RESTRICT,
            CHECK (julianday(fetched_at) >= julianday(as_of)),
            CHECK (
                source_time IS NULL
                OR julianday(source_time) <= julianday(fetched_at)
            ),
            CHECK (julianday(created_at) >= julianday(fetched_at)),
            CHECK (
                (
                    index_key = 'sse_composite'
                    AND symbol = '000001'
                    AND name = '上证指数'
                    AND exchange = 'sse'
                    AND source_symbol = 'sh000001'
                )
                OR (
                    index_key = 'szse_component'
                    AND symbol = '399001'
                    AND name = '深证成指'
                    AND exchange = 'szse'
                    AND source_symbol = 'sz399001'
                )
                OR (
                    index_key = 'chinext'
                    AND symbol = '399006'
                    AND name = '创业板指'
                    AND exchange = 'szse'
                    AND source_symbol = 'sz399006'
                )
                OR (
                    index_key = 'star50'
                    AND symbol = '000688'
                    AND name = '科创50'
                    AND exchange = 'sse'
                    AND source_symbol = 'sh000688'
                )
            )
        )
        """,
        """
        CREATE INDEX idx_market_index_feature_key_as_of
        ON market_index_feature_snapshots (index_key, as_of, radar_run_id)
        """,
    ),
)


ETF_STORAGE_MIGRATION = Migration(
    version=4,
    name="etf_versioned_storage",
    statements=(
        """
        CREATE TABLE radar_etf_product_profiles (
            profile_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            exchange TEXT NOT NULL CHECK (exchange IN ('sse', 'szse')),
            official_name TEXT NOT NULL,
            product_type TEXT NOT NULL,
            management_style TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            source_category_code TEXT,
            source_category_name TEXT,
            source_investment_type TEXT,
            target_index_name TEXT,
            classification_mapping_version TEXT NOT NULL,
            classification_reasons_json TEXT NOT NULL DEFAULT '[]',
            source_contract_id TEXT NOT NULL,
            source TEXT NOT NULL,
            source_time TEXT,
            fetched_at TEXT NOT NULL,
            version_time_kind TEXT NOT NULL CHECK (
                version_time_kind IN ('official_effective', 'first_observed')
            ),
            official_effective_from TEXT,
            first_observed_at TEXT NOT NULL,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            evidence_url TEXT,
            evidence_sha256 TEXT,
            listing_date TEXT,
            manager TEXT,
            source_fields_json TEXT NOT NULL DEFAULT '{}',
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            UNIQUE (symbol, source_contract_id, effective_from),
            CHECK (effective_to IS NULL OR effective_to > effective_from),
            CHECK (fetched_at >= first_observed_at),
            CHECK (
                (
                    version_time_kind = 'first_observed'
                    AND official_effective_from IS NULL
                    AND effective_from = first_observed_at
                )
                OR (
                    version_time_kind = 'official_effective'
                    AND official_effective_from IS NOT NULL
                    AND effective_from = official_effective_from
                )
            ),
            CHECK (evidence_sha256 IS NULL OR (
                length(evidence_sha256) = 64
                AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'
            ))
        )
        """,
        """
        CREATE INDEX idx_etf_profile_symbol_effective
        ON radar_etf_product_profiles (symbol, effective_from, effective_to)
        """,
        """
        CREATE UNIQUE INDEX uq_etf_profile_current_source
        ON radar_etf_product_profiles (symbol, source_contract_id)
        WHERE effective_to IS NULL
        """,
        """
        CREATE TABLE radar_etf_product_master_runs (
            product_master_run_id TEXT PRIMARY KEY,
            as_of TEXT NOT NULL,
            source TEXT NOT NULL,
            source_time TEXT,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('succeeded', 'degraded', 'failed')
            ),
            expected_count INTEGER CHECK (
                expected_count IS NULL OR expected_count >= 0
            ),
            returned_count INTEGER NOT NULL CHECK (returned_count >= 0),
            row_coverage REAL CHECK (
                row_coverage IS NULL
                OR (row_coverage >= 0 AND row_coverage <= 1)
            ),
            required_field_coverage_json TEXT NOT NULL DEFAULT '{}',
            issues_json TEXT NOT NULL DEFAULT '[]',
            inserted_count INTEGER NOT NULL CHECK (inserted_count >= 0),
            unchanged_count INTEGER NOT NULL CHECK (unchanged_count >= 0),
            created_at TEXT NOT NULL,
            CHECK (fetched_at >= as_of),
            CHECK (
                status <> 'succeeded'
                OR (
                    expected_count = returned_count
                    AND returned_count > 0
                    AND row_coverage = 1.0
                )
            )
        )
        """,
        """
        CREATE INDEX idx_etf_product_master_run_fetched
        ON radar_etf_product_master_runs (fetched_at DESC)
        """,
        """
        CREATE TABLE radar_etf_index_relations (
            relation_id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            index_provider TEXT NOT NULL,
            index_code TEXT NOT NULL,
            index_name TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            published_at TEXT,
            effective_from TEXT,
            effective_to TEXT,
            source_contract_id TEXT NOT NULL,
            fund_evidence_url TEXT NOT NULL,
            fund_evidence_sha256 TEXT NOT NULL CHECK (
                length(fund_evidence_sha256) = 64
                AND fund_evidence_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            provider_evidence_url TEXT NOT NULL,
            provider_evidence_sha256 TEXT NOT NULL CHECK (
                length(provider_evidence_sha256) = 64
                AND provider_evidence_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            status TEXT NOT NULL,
            formal_ready INTEGER NOT NULL CHECK (formal_ready IN (0, 1)),
            reasons_json TEXT NOT NULL DEFAULT '[]',
            as_of TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            FOREIGN KEY (profile_id)
                REFERENCES radar_etf_product_profiles(profile_id)
                ON DELETE RESTRICT,
            CHECK (
                effective_to IS NULL
                OR effective_from IS NULL
                OR effective_to > effective_from
            ),
            UNIQUE (profile_id, source_contract_id, effective_from)
        )
        """,
        """
        CREATE INDEX idx_etf_relation_symbol_effective
        ON radar_etf_index_relations (symbol, effective_from, effective_to)
        """,
        """
        CREATE TABLE radar_index_methodology_versions (
            methodology_version_id TEXT PRIMARY KEY,
            index_provider TEXT NOT NULL,
            index_code TEXT NOT NULL,
            index_name TEXT NOT NULL,
            provider_version TEXT,
            version_kind TEXT NOT NULL,
            published_at TEXT,
            effective_from TEXT,
            effective_to TEXT,
            universe_rule TEXT,
            selection_rule TEXT,
            weighting_method TEXT,
            constituent_cap INTEGER CHECK (
                constituent_cap IS NULL OR constituent_cap > 0
            ),
            rebalance_frequency TEXT,
            as_of TEXT NOT NULL,
            evidence_url TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL CHECK (
                length(evidence_sha256) = 64
                AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            first_observed_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL,
            formal_ready INTEGER NOT NULL CHECK (formal_ready IN (0, 1)),
            reasons_json TEXT NOT NULL DEFAULT '[]',
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            CHECK (
                effective_to IS NULL
                OR effective_from IS NULL
                OR effective_to > effective_from
            ),
            UNIQUE (index_provider, index_code, effective_from, evidence_sha256)
        )
        """,
        """
        CREATE INDEX idx_index_methodology_effective
        ON radar_index_methodology_versions (
            index_provider, index_code, effective_from, effective_to
        )
        """,
        """
        CREATE TABLE radar_index_constituent_sets (
            constituent_set_id TEXT PRIMARY KEY,
            index_provider TEXT NOT NULL,
            index_code TEXT NOT NULL,
            index_name TEXT NOT NULL,
            announced_at TEXT,
            effective_from TEXT,
            effective_to TEXT,
            source_date TEXT,
            expected_count INTEGER CHECK (
                expected_count IS NULL OR expected_count >= 0
            ),
            returned_count INTEGER NOT NULL CHECK (returned_count >= 0),
            weight_count INTEGER NOT NULL CHECK (weight_count >= 0),
            weight_total REAL CHECK (weight_total IS NULL OR weight_total >= 0),
            as_of TEXT NOT NULL,
            evidence_url TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL CHECK (
                length(evidence_sha256) = 64
                AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            first_observed_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            status TEXT NOT NULL,
            formal_ready INTEGER NOT NULL CHECK (formal_ready IN (0, 1)),
            reasons_json TEXT NOT NULL DEFAULT '[]',
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            CHECK (
                expected_count IS NULL OR returned_count <= expected_count
            ),
            UNIQUE (
                index_provider, index_code, source_date, evidence_sha256
            )
        )
        """,
        """
        CREATE INDEX idx_index_constituent_set_as_of
        ON radar_index_constituent_sets (
            index_provider, index_code, source_date, as_of
        )
        """,
        """
        CREATE TABLE radar_index_constituents (
            constituent_set_id TEXT NOT NULL,
            stock_code TEXT NOT NULL CHECK (
                length(stock_code) = 6 AND stock_code NOT GLOB '*[^0-9]*'
            ),
            stock_name TEXT NOT NULL,
            weight REAL CHECK (weight IS NULL OR weight >= 0),
            weight_unit TEXT NOT NULL,
            PRIMARY KEY (constituent_set_id, stock_code),
            FOREIGN KEY (constituent_set_id)
                REFERENCES radar_index_constituent_sets(constituent_set_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX idx_index_constituent_stock
        ON radar_index_constituents (stock_code, constituent_set_id)
        """,
        """
        CREATE TABLE radar_index_industry_exposures (
            exposure_version_id TEXT NOT NULL,
            constituent_set_id TEXT NOT NULL,
            industry_release_id TEXT NOT NULL,
            index_provider TEXT NOT NULL,
            index_code TEXT NOT NULL,
            index_name TEXT NOT NULL,
            constituent_source_date TEXT,
            industry_release_period TEXT NOT NULL,
            industry_document_sha256 TEXT NOT NULL CHECK (
                length(industry_document_sha256) = 64
                AND industry_document_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            industry_code TEXT NOT NULL,
            industry_name TEXT NOT NULL,
            raw_weight REAL NOT NULL CHECK (raw_weight >= 0),
            exposure_ratio REAL NOT NULL CHECK (
                exposure_ratio BETWEEN 0 AND 1
            ),
            total_weight REAL NOT NULL CHECK (total_weight > 0),
            mapped_weight REAL NOT NULL CHECK (mapped_weight >= 0),
            unmapped_weight REAL NOT NULL CHECK (unmapped_weight >= 0),
            mapping_coverage REAL NOT NULL CHECK (
                mapping_coverage BETWEEN 0 AND 1
            ),
            unmapped_symbols_json TEXT NOT NULL DEFAULT '[]',
            as_of TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            calculation_version TEXT NOT NULL,
            formal_ready INTEGER NOT NULL CHECK (formal_ready IN (0, 1)),
            reasons_json TEXT NOT NULL DEFAULT '[]',
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            PRIMARY KEY (exposure_version_id, industry_code),
            FOREIGN KEY (constituent_set_id)
                REFERENCES radar_index_constituent_sets(constituent_set_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (industry_release_id)
                REFERENCES industry_classification_releases(industry_release_id)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX idx_index_exposure_as_of
        ON radar_index_industry_exposures (
            index_provider, index_code, industry_code, as_of
        )
        """,
        """
        CREATE TABLE radar_etf_daily_facts (
            daily_fact_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            fact_key_date TEXT NOT NULL,
            trade_date TEXT,
            source_report_date TEXT,
            fund_size REAL CHECK (fund_size IS NULL OR fund_size >= 0),
            fund_size_unit TEXT,
            fund_shares REAL CHECK (fund_shares IS NULL OR fund_shares >= 0),
            fund_shares_unit TEXT,
            nav REAL CHECK (nav IS NULL OR nav >= 0),
            nav_currency TEXT,
            share_change_5d REAL,
            share_change_20d REAL,
            average_turnover_20d REAL CHECK (
                average_turnover_20d IS NULL OR average_turnover_20d >= 0
            ),
            tracking_difference REAL,
            tracking_error REAL CHECK (
                tracking_error IS NULL OR tracking_error >= 0
            ),
            index_correlation REAL CHECK (
                index_correlation IS NULL OR index_correlation BETWEEN -1 AND 1
            ),
            window_trading_days INTEGER CHECK (
                window_trading_days IS NULL OR window_trading_days >= 0
            ),
            sample_count INTEGER CHECK (
                sample_count IS NULL OR sample_count >= 0
            ),
            formula_version TEXT,
            field_states_json TEXT NOT NULL,
            source_contract_ids_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            formal_usable INTEGER NOT NULL CHECK (formal_usable IN (0, 1)),
            reasons_json TEXT NOT NULL DEFAULT '[]',
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            UNIQUE (symbol, fact_key_date, record_checksum)
        )
        """,
        """
        CREATE INDEX idx_etf_daily_fact_symbol_date
        ON radar_etf_daily_facts (symbol, fact_key_date, computed_at)
        """,
        """
        CREATE INDEX idx_etf_daily_fact_formal_date
        ON radar_etf_daily_facts (fact_key_date, formal_usable)
        """,
        """
        CREATE TABLE radar_etf_feature_snapshots (
            radar_run_id TEXT NOT NULL,
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            as_of TEXT NOT NULL,
            source_time TEXT,
            fetched_at TEXT NOT NULL,
            price REAL,
            change_percent REAL,
            turnover_volume REAL,
            turnover_amount REAL,
            bid1 REAL,
            ask1 REAL,
            spread_bps REAL,
            iopv REAL,
            premium_discount_rate REAL,
            field_states_json TEXT NOT NULL,
            formal_usable INTEGER NOT NULL CHECK (formal_usable IN (0, 1)),
            reason_codes_json TEXT NOT NULL DEFAULT '[]',
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            PRIMARY KEY (radar_run_id, symbol),
            FOREIGN KEY (radar_run_id)
                REFERENCES radar_runs(radar_run_id)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX idx_etf_feature_symbol_as_of
        ON radar_etf_feature_snapshots (symbol, as_of)
        """,
        """
        CREATE TABLE radar_etf_candidate_snapshots (
            radar_run_id TEXT PRIMARY KEY,
            as_of TEXT NOT NULL,
            rule_version_id TEXT,
            registry_count INTEGER NOT NULL CHECK (registry_count >= 0),
            etf_count INTEGER NOT NULL CHECK (etf_count >= 0),
            eligible_product_count INTEGER NOT NULL CHECK (
                eligible_product_count >= 0
            ),
            industry_theme_count INTEGER NOT NULL CHECK (
                industry_theme_count >= 0
            ),
            computed_count INTEGER NOT NULL CHECK (computed_count >= 0),
            stale_count INTEGER NOT NULL CHECK (stale_count >= 0),
            missing_count INTEGER NOT NULL CHECK (missing_count >= 0),
            excluded_count INTEGER NOT NULL CHECK (excluded_count >= 0),
            candidate_group_count INTEGER NOT NULL CHECK (
                candidate_group_count >= 0
            ),
            coverage REAL NOT NULL CHECK (coverage BETWEEN 0 AND 1),
            quality TEXT NOT NULL,
            reason_counts_json TEXT NOT NULL DEFAULT '{}',
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL,
            FOREIGN KEY (radar_run_id)
                REFERENCES radar_runs(radar_run_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (rule_version_id)
                REFERENCES radar_rule_versions(rule_version_id)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX idx_etf_candidate_snapshot_as_of
        ON radar_etf_candidate_snapshots (as_of)
        """,
        """
        CREATE TABLE radar_etf_candidate_entries (
            radar_run_id TEXT NOT NULL,
            industry_code TEXT NOT NULL,
            index_group_key TEXT NOT NULL,
            rank INTEGER NOT NULL CHECK (rank >= 1),
            representative_symbol TEXT CHECK (
                representative_symbol IS NULL
                OR (
                    length(representative_symbol) = 6
                    AND representative_symbol NOT GLOB '*[^0-9]*'
                )
            ),
            alternative_symbols_json TEXT NOT NULL DEFAULT '[]',
            industry_exposures_json TEXT NOT NULL DEFAULT '[]',
            ranking_components_json TEXT NOT NULL DEFAULT '{}',
            entry_reasons_json TEXT NOT NULL DEFAULT '[]',
            risk_reasons_json TEXT NOT NULL DEFAULT '[]',
            exit_conditions_json TEXT NOT NULL DEFAULT '[]',
            formal_usable INTEGER NOT NULL CHECK (formal_usable IN (0, 1)),
            PRIMARY KEY (radar_run_id, industry_code, index_group_key),
            FOREIGN KEY (radar_run_id)
                REFERENCES radar_etf_candidate_snapshots(radar_run_id)
                ON DELETE CASCADE,
            CHECK (
                formal_usable = 0 OR representative_symbol IS NOT NULL
            )
        )
        """,
        """
        CREATE INDEX idx_etf_candidate_entry_industry
        ON radar_etf_candidate_entries (industry_code, rank, radar_run_id)
        """,
        """
        CREATE TRIGGER trg_etf_profile_interval_insert
        BEFORE INSERT ON radar_etf_product_profiles
        WHEN EXISTS (
            SELECT 1
            FROM radar_etf_product_profiles existing
            WHERE existing.symbol = NEW.symbol
              AND existing.source_contract_id = NEW.source_contract_id
              AND existing.effective_from <
                    COALESCE(
                        NEW.effective_to,
                        '9999-12-31T23:59:59.999999+00:00'
                    )
              AND COALESCE(
                    existing.effective_to,
                    '9999-12-31T23:59:59.999999+00:00'
                  ) > NEW.effective_from
        )
        BEGIN
            SELECT RAISE(ABORT, 'ETF产品归一化版本时间区间重叠');
        END
        """,
        """
        CREATE TRIGGER trg_etf_profile_interval_update
        BEFORE UPDATE OF
            symbol, source_contract_id, effective_from, effective_to
        ON radar_etf_product_profiles
        WHEN EXISTS (
            SELECT 1
            FROM radar_etf_product_profiles existing
            WHERE existing.profile_id <> OLD.profile_id
              AND existing.symbol = NEW.symbol
              AND existing.source_contract_id = NEW.source_contract_id
              AND existing.effective_from <
                    COALESCE(
                        NEW.effective_to,
                        '9999-12-31T23:59:59.999999+00:00'
                    )
              AND COALESCE(
                    existing.effective_to,
                    '9999-12-31T23:59:59.999999+00:00'
                  ) > NEW.effective_from
        )
        BEGIN
            SELECT RAISE(ABORT, 'ETF产品归一化版本时间区间重叠');
        END
        """,
        """
        CREATE TRIGGER trg_etf_relation_interval_insert
        BEFORE INSERT ON radar_etf_index_relations
        WHEN NEW.effective_from IS NOT NULL
         AND EXISTS (
            SELECT 1
            FROM radar_etf_index_relations existing
            WHERE existing.profile_id = NEW.profile_id
              AND existing.source_contract_id = NEW.source_contract_id
              AND existing.effective_from IS NOT NULL
              AND existing.effective_from <
                    COALESCE(
                        NEW.effective_to,
                        '9999-12-31T23:59:59.999999+00:00'
                    )
              AND COALESCE(
                    existing.effective_to,
                    '9999-12-31T23:59:59.999999+00:00'
                  ) > NEW.effective_from
        )
        BEGIN
            SELECT RAISE(ABORT, 'ETF指数关系时间区间重叠');
        END
        """,
        """
        CREATE TRIGGER trg_etf_relation_interval_update
        BEFORE UPDATE OF
            profile_id, source_contract_id, effective_from, effective_to
        ON radar_etf_index_relations
        WHEN NEW.effective_from IS NOT NULL
         AND EXISTS (
            SELECT 1
            FROM radar_etf_index_relations existing
            WHERE existing.relation_id <> OLD.relation_id
              AND existing.profile_id = NEW.profile_id
              AND existing.source_contract_id = NEW.source_contract_id
              AND existing.effective_from IS NOT NULL
              AND existing.effective_from <
                    COALESCE(
                        NEW.effective_to,
                        '9999-12-31T23:59:59.999999+00:00'
                    )
              AND COALESCE(
                    existing.effective_to,
                    '9999-12-31T23:59:59.999999+00:00'
                  ) > NEW.effective_from
        )
        BEGIN
            SELECT RAISE(ABORT, 'ETF指数关系时间区间重叠');
        END
        """,
        """
        CREATE TRIGGER trg_etf_methodology_interval_insert
        BEFORE INSERT ON radar_index_methodology_versions
        WHEN NEW.effective_from IS NOT NULL
         AND EXISTS (
            SELECT 1
            FROM radar_index_methodology_versions existing
            WHERE existing.index_provider = NEW.index_provider
              AND existing.index_code = NEW.index_code
              AND existing.effective_from IS NOT NULL
              AND existing.effective_from <
                    COALESCE(
                        NEW.effective_to,
                        '9999-12-31T23:59:59.999999+00:00'
                    )
              AND COALESCE(
                    existing.effective_to,
                    '9999-12-31T23:59:59.999999+00:00'
                  ) > NEW.effective_from
        )
        BEGIN
            SELECT RAISE(ABORT, '指数方法时间区间重叠');
        END
        """,
        """
        CREATE TRIGGER trg_etf_methodology_interval_update
        BEFORE UPDATE OF
            index_provider, index_code, effective_from, effective_to
        ON radar_index_methodology_versions
        WHEN NEW.effective_from IS NOT NULL
         AND EXISTS (
            SELECT 1
            FROM radar_index_methodology_versions existing
            WHERE existing.methodology_version_id
                    <> OLD.methodology_version_id
              AND existing.index_provider = NEW.index_provider
              AND existing.index_code = NEW.index_code
              AND existing.effective_from IS NOT NULL
              AND existing.effective_from <
                    COALESCE(
                        NEW.effective_to,
                        '9999-12-31T23:59:59.999999+00:00'
                    )
              AND COALESCE(
                    existing.effective_to,
                    '9999-12-31T23:59:59.999999+00:00'
                  ) > NEW.effective_from
        )
        BEGIN
            SELECT RAISE(ABORT, '指数方法时间区间重叠');
        END
        """,
    ),
)


RADAR_MIGRATIONS: Tuple[Migration, ...] = (
    INITIAL_RADAR_MIGRATION,
    INDUSTRY_STORAGE_MIGRATION,
    MARKET_ENVIRONMENT_STORAGE_MIGRATION,
)

# 5F临时演练和未来受控启用使用；默认生产运行仍只要求版本1至3。
STAGE5_RADAR_MIGRATIONS: Tuple[Migration, ...] = (
    *RADAR_MIGRATIONS,
    ETF_STORAGE_MIGRATION,
)


LEADER_STORAGE_MIGRATION = Migration(
    version=5,
    name="leader_state_storage",
    statements=(
        """
        CREATE TABLE radar_leader_candidate_snapshots (
            radar_run_id TEXT PRIMARY KEY,
            as_of TEXT NOT NULL CHECK (julianday(as_of) IS NOT NULL),
            rule_version TEXT NOT NULL CHECK (trim(rule_version) <> ''),
            rule_version_id TEXT,
            eligible_count INTEGER NOT NULL CHECK (eligible_count >= 0),
            preliminary_count INTEGER NOT NULL CHECK (preliminary_count >= 0),
            candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
            confirmed_count INTEGER NOT NULL CHECK (confirmed_count >= 0),
            removed_count INTEGER NOT NULL CHECK (removed_count >= 0),
            coverage REAL NOT NULL CHECK (coverage BETWEEN 0 AND 1),
            quality TEXT NOT NULL CHECK (trim(quality) <> ''),
            reason_counts_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    CASE WHEN json_valid(reason_counts_json)
                        THEN json_type(reason_counts_json) = 'object'
                        ELSE 0
                    END
                ),
            formal_usable INTEGER NOT NULL DEFAULT 0 CHECK (
                formal_usable IN (0, 1)
            ),
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            FOREIGN KEY (radar_run_id)
                REFERENCES radar_runs(radar_run_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (rule_version_id)
                REFERENCES radar_rule_versions(rule_version_id)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX idx_leader_candidate_snapshot_as_of
        ON radar_leader_candidate_snapshots (as_of, radar_run_id)
        """,
        """
        CREATE TABLE radar_leader_candidate_entries (
            radar_run_id TEXT NOT NULL,
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            name TEXT NOT NULL CHECK (trim(name) <> ''),
            industry_code TEXT,
            industry_name TEXT,
            state TEXT NOT NULL CHECK (
                state IN ('out', 'preliminary', 'candidate', 'confirmed')
            ),
            score REAL NOT NULL CHECK (score BETWEEN 0 AND 100),
            business_exposure_status TEXT NOT NULL CHECK (
                business_exposure_status IN (
                    'verified', 'unconfirmed', 'missing', 'disproved'
                )
            ),
            data_status TEXT NOT NULL CHECK (
                data_status IN ('healthy', 'missing', 'stale', 'source_failed')
            ),
            first_rejection_reason TEXT,
            reasons_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    CASE WHEN json_valid(reasons_json)
                        THEN json_type(reasons_json) = 'array'
                        ELSE 0
                    END
                ),
            evidence_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    CASE WHEN json_valid(evidence_json)
                        THEN json_type(evidence_json) = 'object'
                        ELSE 0
                    END
                ),
            invalidation_json TEXT NOT NULL DEFAULT '{}'
                CHECK (
                    CASE WHEN json_valid(invalidation_json)
                        THEN json_type(invalidation_json) = 'object'
                        ELSE 0
                    END
                ),
            state_age_periods INTEGER NOT NULL DEFAULT 0 CHECK (
                state_age_periods >= 0
            ),
            formal_usable INTEGER NOT NULL DEFAULT 0 CHECK (
                formal_usable IN (0, 1)
            ),
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            PRIMARY KEY (radar_run_id, symbol),
            FOREIGN KEY (radar_run_id)
                REFERENCES radar_leader_candidate_snapshots(radar_run_id)
                ON DELETE CASCADE,
            CHECK (formal_usable = 0 OR state IN ('candidate', 'confirmed'))
        )
        """,
        """
        CREATE INDEX idx_leader_candidate_entry_state
        ON radar_leader_candidate_entries (state, radar_run_id)
        """,
        """
        CREATE TABLE radar_leader_state_history (
            transition_id TEXT PRIMARY KEY CHECK (trim(transition_id) <> ''),
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            radar_run_id TEXT NOT NULL,
            as_of TEXT NOT NULL CHECK (julianday(as_of) IS NOT NULL),
            from_state TEXT NOT NULL CHECK (
                from_state IN ('out', 'preliminary', 'candidate', 'confirmed')
            ),
            to_state TEXT NOT NULL CHECK (
                to_state IN ('out', 'preliminary', 'candidate', 'confirmed')
            ),
            action TEXT NOT NULL CHECK (
                action IN (
                    'blocked', 'hold', 'enter', 'upgrade',
                    'downgrade', 'remove'
                )
            ),
            rule_version TEXT NOT NULL CHECK (trim(rule_version) <> ''),
            rule_version_id TEXT,
            reasons_json TEXT NOT NULL DEFAULT '[]'
                CHECK (
                    CASE WHEN json_valid(reasons_json)
                        THEN json_type(reasons_json) = 'array'
                        ELSE 0
                    END
                ),
            first_rejection_reason TEXT,
            state_age_periods INTEGER NOT NULL CHECK (state_age_periods >= 0),
            cooldown_until TEXT CHECK (
                cooldown_until IS NULL OR julianday(cooldown_until) IS NOT NULL
            ),
            formal_usable INTEGER NOT NULL DEFAULT 0 CHECK (
                formal_usable IN (0, 1)
            ),
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            FOREIGN KEY (radar_run_id)
                REFERENCES radar_runs(radar_run_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (rule_version_id)
                REFERENCES radar_rule_versions(rule_version_id)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX idx_leader_state_history_symbol_as_of
        ON radar_leader_state_history (symbol, as_of, created_at)
        """,
        """
        CREATE INDEX idx_leader_state_history_run
        ON radar_leader_state_history (radar_run_id, as_of)
        """,
    ),
)


STAGE6_RADAR_MIGRATIONS: Tuple[Migration, ...] = (
    *STAGE5_RADAR_MIGRATIONS,
    LEADER_STORAGE_MIGRATION,
)


LEADER_RISK_REVIEW_STORAGE_MIGRATION = Migration(
    version=6,
    name="leader_risk_review_storage",
    statements=(
        """
        CREATE TABLE radar_leader_risk_review_batches (
            review_batch_id TEXT PRIMARY KEY CHECK (
                trim(review_batch_id) <> ''
            ),
            candidate_plan_id TEXT NOT NULL CHECK (
                trim(candidate_plan_id) <> ''
            ),
            radar_run_id TEXT NOT NULL,
            as_of TEXT NOT NULL CHECK (julianday(as_of) IS NOT NULL),
            window_from TEXT NOT NULL CHECK (date(window_from) IS NOT NULL),
            window_until TEXT NOT NULL CHECK (date(window_until) IS NOT NULL),
            candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
            shard_count INTEGER NOT NULL CHECK (shard_count > 0),
            category_count INTEGER NOT NULL CHECK (category_count > 0),
            document_count INTEGER NOT NULL CHECK (document_count >= 0),
            query_categories_complete INTEGER NOT NULL CHECK (
                query_categories_complete IN (0, 1)
            ),
            query_pages_complete INTEGER NOT NULL CHECK (
                query_pages_complete IN (0, 1)
            ),
            query_window_continuous INTEGER NOT NULL CHECK (
                query_window_continuous IN (0, 1)
            ),
            source_contract_id TEXT NOT NULL CHECK (
                trim(source_contract_id) <> ''
            ),
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            FOREIGN KEY (radar_run_id)
                REFERENCES radar_runs(radar_run_id)
                ON DELETE RESTRICT,
            CHECK (date(window_until) >= date(window_from))
        )
        """,
        """
        CREATE INDEX idx_leader_risk_review_batch_as_of
        ON radar_leader_risk_review_batches (as_of, review_batch_id)
        """,
        """
        CREATE TABLE radar_leader_risk_documents (
            document_id TEXT PRIMARY KEY CHECK (trim(document_id) <> ''),
            source_contract_id TEXT NOT NULL CHECK (
                trim(source_contract_id) <> ''
            ),
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            issuer_identity TEXT NOT NULL CHECK (
                trim(issuer_identity) <> ''
            ),
            issuer_name TEXT NOT NULL CHECK (trim(issuer_name) <> ''),
            title TEXT NOT NULL CHECK (trim(title) <> ''),
            published_at TEXT NOT NULL CHECK (
                julianday(published_at) IS NOT NULL
            ),
            source_name TEXT NOT NULL CHECK (trim(source_name) <> ''),
            source_url TEXT NOT NULL CHECK (trim(source_url) <> ''),
            raw_column_ids_json TEXT NOT NULL DEFAULT '[]' CHECK (
                CASE WHEN json_valid(raw_column_ids_json)
                    THEN json_type(raw_column_ids_json) = 'array'
                    ELSE 0
                END
            ),
            raw_announcement_types_json TEXT NOT NULL DEFAULT '[]' CHECK (
                CASE WHEN json_valid(raw_announcement_types_json)
                    THEN json_type(raw_announcement_types_json) = 'array'
                    ELSE 0
                END
            ),
            raw_page_column TEXT,
            association_reported INTEGER NOT NULL DEFAULT 0 CHECK (
                association_reported IN (0, 1)
            ),
            formal_usable INTEGER NOT NULL DEFAULT 0 CHECK (
                formal_usable = 0
            ),
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL)
        )
        """,
        """
        CREATE INDEX idx_leader_risk_document_symbol_published
        ON radar_leader_risk_documents (symbol, published_at, document_id)
        """,
        """
        CREATE TABLE radar_leader_risk_review_batch_documents (
            review_batch_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            candidate_category TEXT NOT NULL CHECK (
                candidate_category IN (
                    'reduction', 'unlock', 'regulatory', 'investigation',
                    'litigation', 'earnings', 'audit'
                )
            ),
            search_key TEXT NOT NULL CHECK (trim(search_key) <> ''),
            PRIMARY KEY (
                review_batch_id, document_id, candidate_category
            ),
            FOREIGN KEY (review_batch_id)
                REFERENCES radar_leader_risk_review_batches(review_batch_id)
                ON DELETE CASCADE,
            FOREIGN KEY (document_id)
                REFERENCES radar_leader_risk_documents(document_id)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX idx_leader_risk_batch_document_document
        ON radar_leader_risk_review_batch_documents (
            document_id, review_batch_id
        )
        """,
        """
        CREATE TABLE radar_leader_risk_document_contents (
            document_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL CHECK (
                length(content_sha256) = 64
                AND content_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            issuer_identity TEXT NOT NULL CHECK (
                trim(issuer_identity) <> ''
            ),
            status TEXT NOT NULL CHECK (status = 'ready'),
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            page_count INTEGER NOT NULL CHECK (
                page_count BETWEEN 1 AND 200
            ),
            fetched_at TEXT NOT NULL CHECK (julianday(fetched_at) IS NOT NULL),
            content_contract_id TEXT NOT NULL CHECK (
                trim(content_contract_id) <> ''
            ),
            formal_usable INTEGER NOT NULL DEFAULT 0 CHECK (
                formal_usable = 0
            ),
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            PRIMARY KEY (document_id, content_sha256),
            FOREIGN KEY (document_id)
                REFERENCES radar_leader_risk_documents(document_id)
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE radar_leader_risk_document_pages (
            document_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            page_number INTEGER NOT NULL CHECK (page_number > 0),
            page_text TEXT NOT NULL,
            character_count INTEGER NOT NULL CHECK (
                character_count BETWEEN 0 AND 100000
                AND character_count = length(page_text)
            ),
            page_sha256 TEXT NOT NULL CHECK (
                length(page_sha256) = 64
                AND page_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            PRIMARY KEY (document_id, content_sha256, page_number),
            FOREIGN KEY (document_id, content_sha256)
                REFERENCES radar_leader_risk_document_contents(
                    document_id, content_sha256
                )
                ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE radar_leader_risk_manual_review_versions (
            review_record_id TEXT PRIMARY KEY CHECK (
                trim(review_record_id) <> ''
            ),
            review_batch_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            candidate_category TEXT NOT NULL CHECK (
                candidate_category IN (
                    'reduction', 'unlock', 'regulatory', 'investigation',
                    'litigation', 'earnings', 'audit'
                )
            ),
            content_sha256 TEXT NOT NULL,
            symbol TEXT NOT NULL CHECK (
                length(symbol) = 6 AND symbol NOT GLOB '*[^0-9]*'
            ),
            issuer_identity TEXT NOT NULL CHECK (
                trim(issuer_identity) <> ''
            ),
            candidate_id TEXT NOT NULL CHECK (trim(candidate_id) <> ''),
            candidate_kind TEXT NOT NULL CHECK (
                candidate_kind IN (
                    'fact_extraction_missing',
                    'relation_review_required'
                )
            ),
            as_of TEXT NOT NULL CHECK (julianday(as_of) IS NOT NULL),
            review_version TEXT NOT NULL CHECK (trim(review_version) <> ''),
            supersedes_review_version TEXT,
            review_method TEXT NOT NULL CHECK (review_method = 'manual'),
            reviewer_key TEXT NOT NULL CHECK (trim(reviewer_key) <> ''),
            reviewed_at TEXT NOT NULL CHECK (julianday(reviewed_at) IS NOT NULL),
            effective_until TEXT CHECK (
                effective_until IS NULL
                OR julianday(effective_until) IS NOT NULL
            ),
            event_versions_json TEXT NOT NULL CHECK (
                CASE WHEN json_valid(event_versions_json)
                    THEN json_type(event_versions_json) = 'array'
                    ELSE 0
                END
            ),
            fact_supplements_json TEXT NOT NULL CHECK (
                CASE WHEN json_valid(fact_supplements_json)
                    THEN json_type(fact_supplements_json) = 'array'
                    ELSE 0
                END
            ),
            submission_relation_json TEXT CHECK (
                submission_relation_json IS NULL
                OR CASE WHEN json_valid(submission_relation_json)
                    THEN json_type(submission_relation_json) = 'object'
                    ELSE 0
                END
            ),
            lifecycle_relation_json TEXT NOT NULL CHECK (
                CASE WHEN json_valid(lifecycle_relation_json)
                    THEN json_type(lifecycle_relation_json) = 'object'
                    ELSE 0
                END
            ),
            record_checksum TEXT NOT NULL CHECK (
                length(record_checksum) = 64
                AND record_checksum NOT GLOB '*[^0-9a-f]*'
            ),
            created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
            UNIQUE (document_id, candidate_id, review_version),
            FOREIGN KEY (
                review_batch_id, document_id, candidate_category
            ) REFERENCES radar_leader_risk_review_batch_documents (
                review_batch_id, document_id, candidate_category
            ) ON DELETE RESTRICT,
            FOREIGN KEY (document_id, content_sha256)
                REFERENCES radar_leader_risk_document_contents(
                    document_id, content_sha256
                )
                ON DELETE RESTRICT,
            FOREIGN KEY (
                document_id, candidate_id, supersedes_review_version
            ) REFERENCES radar_leader_risk_manual_review_versions (
                document_id, candidate_id, review_version
            ) ON DELETE RESTRICT,
            CHECK (
                effective_until IS NULL
                OR julianday(effective_until) >= julianday(reviewed_at)
            )
        )
        """,
        """
        CREATE INDEX idx_leader_risk_manual_review_history
        ON radar_leader_risk_manual_review_versions (
            document_id, candidate_id, reviewed_at, review_version
        )
        """,
        """
        CREATE TRIGGER trg_leader_risk_manual_review_no_update
        BEFORE UPDATE ON radar_leader_risk_manual_review_versions
        BEGIN
            SELECT RAISE(ABORT, '人工审核版本只允许追加');
        END
        """,
        """
        CREATE TRIGGER trg_leader_risk_manual_review_no_delete
        BEFORE DELETE ON radar_leader_risk_manual_review_versions
        BEGIN
            SELECT RAISE(ABORT, '人工审核版本只允许追加');
        END
        """,
    ),
)


STAGE6_REVIEW_RADAR_MIGRATIONS: Tuple[Migration, ...] = (
    *STAGE6_RADAR_MIGRATIONS,
    LEADER_RISK_REVIEW_STORAGE_MIGRATION,
)

REQUIRED_RADAR_SCHEMA_OBJECTS_V1 = frozenset({
    ("table", "radar_schema_migrations"),
    ("table", "radar_rule_versions"),
    ("table", "radar_runs"),
    ("table", "radar_source_status"),
    ("table", "security_master_history"),
    ("table", "etf_product_registry"),
    ("index", "idx_radar_rule_scope_status"),
    ("index", "idx_radar_runs_as_of_status"),
    ("index", "idx_radar_source_status_source_time"),
    ("index", "idx_security_master_effective"),
    ("index", "uq_security_master_current_source"),
    ("index", "idx_etf_registry_effective"),
    ("index", "uq_etf_registry_current_source"),
})

REQUIRED_RADAR_SCHEMA_OBJECTS_V2 = frozenset({
    ("table", "industry_classification_releases"),
    ("table", "industry_classification_records"),
    ("table", "sector_feature_snapshots"),
    ("index", "uq_radar_runs_id_as_of"),
    ("index", "idx_industry_release_knowledge_effective"),
    ("index", "uq_industry_release_current_system"),
    ("index", "idx_industry_record_release_division"),
    ("index", "idx_sector_feature_division_as_of"),
    ("trigger", "trg_sector_feature_release_time_insert"),
    ("trigger", "trg_sector_feature_release_time_update"),
    ("trigger", "trg_industry_release_interval_update"),
})

REQUIRED_RADAR_SCHEMA_OBJECTS_V3 = frozenset({
    ("table", "market_environment_snapshots"),
    ("table", "market_index_feature_snapshots"),
    ("index", "idx_market_environment_as_of"),
    ("index", "idx_market_index_feature_key_as_of"),
})

REQUIRED_RADAR_SCHEMA_OBJECTS_V4 = frozenset({
    ("table", "radar_etf_product_profiles"),
    ("table", "radar_etf_product_master_runs"),
    ("table", "radar_etf_index_relations"),
    ("table", "radar_index_methodology_versions"),
    ("table", "radar_index_constituent_sets"),
    ("table", "radar_index_constituents"),
    ("table", "radar_index_industry_exposures"),
    ("table", "radar_etf_daily_facts"),
    ("table", "radar_etf_feature_snapshots"),
    ("table", "radar_etf_candidate_snapshots"),
    ("table", "radar_etf_candidate_entries"),
    ("index", "idx_etf_profile_symbol_effective"),
    ("index", "uq_etf_profile_current_source"),
    ("index", "idx_etf_product_master_run_fetched"),
    ("index", "idx_etf_relation_symbol_effective"),
    ("index", "idx_index_methodology_effective"),
    ("index", "idx_index_constituent_set_as_of"),
    ("index", "idx_index_constituent_stock"),
    ("index", "idx_index_exposure_as_of"),
    ("index", "idx_etf_daily_fact_symbol_date"),
    ("index", "idx_etf_daily_fact_formal_date"),
    ("index", "idx_etf_feature_symbol_as_of"),
    ("index", "idx_etf_candidate_snapshot_as_of"),
    ("index", "idx_etf_candidate_entry_industry"),
    ("trigger", "trg_etf_profile_interval_insert"),
    ("trigger", "trg_etf_profile_interval_update"),
    ("trigger", "trg_etf_relation_interval_insert"),
    ("trigger", "trg_etf_relation_interval_update"),
    ("trigger", "trg_etf_methodology_interval_insert"),
    ("trigger", "trg_etf_methodology_interval_update"),
})

REQUIRED_RADAR_SCHEMA_OBJECTS_V5 = frozenset({
    ("table", "radar_leader_candidate_snapshots"),
    ("table", "radar_leader_candidate_entries"),
    ("table", "radar_leader_state_history"),
    ("index", "idx_leader_candidate_snapshot_as_of"),
    ("index", "idx_leader_candidate_entry_state"),
    ("index", "idx_leader_state_history_symbol_as_of"),
    ("index", "idx_leader_state_history_run"),
})

REQUIRED_RADAR_SCHEMA_OBJECTS_V6 = frozenset({
    ("table", "radar_leader_risk_review_batches"),
    ("table", "radar_leader_risk_documents"),
    ("table", "radar_leader_risk_review_batch_documents"),
    ("table", "radar_leader_risk_document_contents"),
    ("table", "radar_leader_risk_document_pages"),
    ("table", "radar_leader_risk_manual_review_versions"),
    ("index", "idx_leader_risk_review_batch_as_of"),
    ("index", "idx_leader_risk_document_symbol_published"),
    ("index", "idx_leader_risk_batch_document_document"),
    ("index", "idx_leader_risk_manual_review_history"),
    ("trigger", "trg_leader_risk_manual_review_no_update"),
    ("trigger", "trg_leader_risk_manual_review_no_delete"),
})

REQUIRED_RADAR_SCHEMA_OBJECTS_BY_VERSION = {
    1: REQUIRED_RADAR_SCHEMA_OBJECTS_V1,
    2: REQUIRED_RADAR_SCHEMA_OBJECTS_V2,
    3: REQUIRED_RADAR_SCHEMA_OBJECTS_V3,
    4: REQUIRED_RADAR_SCHEMA_OBJECTS_V4,
    5: REQUIRED_RADAR_SCHEMA_OBJECTS_V5,
    6: REQUIRED_RADAR_SCHEMA_OBJECTS_V6,
}

REQUIRED_RADAR_SCHEMA_OBJECTS = frozenset().union(
    *REQUIRED_RADAR_SCHEMA_OBJECTS_BY_VERSION.values()
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validated_migrations(migrations: Iterable[Migration]) -> Tuple[Migration, ...]:
    ordered = tuple(migrations)
    versions = [migration.version for migration in ordered]
    if any(version <= 0 for version in versions):
        raise ValueError("迁移版本必须是正整数")
    if versions != sorted(versions) or len(versions) != len(set(versions)):
        raise ValueError("迁移版本必须严格递增且不能重复")
    for migration in ordered:
        if not migration.name.strip():
            raise ValueError("迁移名称不能为空")
        if not migration.statements or any(
            not statement.strip() for statement in migration.statements
        ):
            raise ValueError(f"迁移{migration.version}不能包含空SQL")
    return ordered


def _validate_applied_rows(
    ordered: Sequence[Migration],
    applied_rows: Sequence[tuple],
    *,
    require_all: bool,
    known_migrations: Optional[Sequence[Migration]] = None,
) -> dict:
    applied = {
        int(version): (str(name), str(checksum))
        for version, name, checksum in applied_rows
    }
    known = tuple(known_migrations or ordered)
    known_versions = {migration.version for migration in known}
    unknown_versions = sorted(set(applied) - known_versions)
    if unknown_versions:
        raise MigrationDriftError(
            f"数据库包含当前代码未知的雷达迁移版本：{unknown_versions}"
        )

    missing_versions = []
    required_versions = {migration.version for migration in ordered}
    for migration in known:
        existing = applied.get(migration.version)
        if existing is None:
            if migration.version in required_versions:
                missing_versions.append(migration.version)
            continue
        if existing != (migration.name, migration.checksum):
            raise MigrationDriftError(
                f"雷达迁移版本{migration.version}的名称或校验值已漂移"
            )
    if require_all and missing_versions:
        raise MigrationDriftError(
            f"数据库缺少当前代码要求的雷达迁移版本：{missing_versions}"
        )
    return applied


def _known_migration_lineage(
    ordered: Sequence[Migration],
) -> Sequence[Migration]:
    """让正式迁移前缀识别同一代码中的后续可选版本。"""

    canonical = STAGE6_REVIEW_RADAR_MIGRATIONS
    if tuple(ordered) == canonical[:len(ordered)]:
        return canonical
    return ordered


def validate_applied_migrations(
    connection: sqlite3.Connection,
    *,
    migrations: Sequence[Migration] = RADAR_MIGRATIONS,
) -> list[int]:
    """只读确认生产运行所需迁移和基础对象完整且未漂移。"""

    ordered = _validated_migrations(migrations)
    schema_objects = {
        (str(object_type), str(name))
        for object_type, name in connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger')"
        ).fetchall()
    }
    required_objects = frozenset().union(*(
        REQUIRED_RADAR_SCHEMA_OBJECTS_BY_VERSION.get(
            migration.version,
            frozenset(),
        )
        for migration in ordered
    ))
    missing_objects = sorted(required_objects - schema_objects)
    if missing_objects:
        names = [name for _, name in missing_objects]
        raise MigrationDriftError(
            f"数据库缺少雷达运行所需结构：{names}"
        )
    try:
        applied_rows = connection.execute(
            "SELECT version, name, checksum FROM radar_schema_migrations"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise MigrationDriftError("雷达迁移记录不可读取") from exc

    applied = _validate_applied_rows(
        ordered,
        applied_rows,
        require_all=True,
        known_migrations=_known_migration_lineage(ordered),
    )
    return sorted(applied)


def apply_pending_migrations(
    connection: sqlite3.Connection,
    *,
    migrations: Sequence[Migration] = RADAR_MIGRATIONS,
    clock: Callable[[], datetime] = _utc_now,
) -> list[int]:
    """在调用方显式提供的连接上应用待执行迁移。

    本函数不会查找数据库路径、不会自动连接生产库，也不会提供破坏性降级。
    """
    if connection.in_transaction:
        raise MigrationError("执行迁移前连接不能处于未提交事务中")

    ordered = _validated_migrations(migrations)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(MIGRATION_TABLE_SQL)
    connection.commit()

    applied_rows = connection.execute(
        "SELECT version, name, checksum FROM radar_schema_migrations"
    ).fetchall()
    applied = _validate_applied_rows(
        ordered,
        applied_rows,
        require_all=False,
        known_migrations=_known_migration_lineage(ordered),
    )

    applied_now = []
    for migration in ordered:
        if migration.version in applied:
            continue
        applied_at = clock()
        if applied_at.tzinfo is None or applied_at.utcoffset() is None:
            raise ValueError("迁移时间必须包含时区")
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO radar_schema_migrations "
                "(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    applied_at.isoformat(timespec="seconds"),
                ),
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise MigrationApplyError(
                f"雷达迁移版本{migration.version}执行失败并已回滚："
                f"{type(exc).__name__}"
            ) from exc
        applied_now.append(migration.version)
    return applied_now
