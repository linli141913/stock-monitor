"""主线雷达独立、显式调用的SQLite版本化迁移。"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Sequence, Tuple


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


RADAR_MIGRATIONS: Tuple[Migration, ...] = (
    INITIAL_RADAR_MIGRATION,
    INDUSTRY_STORAGE_MIGRATION,
    MARKET_ENVIRONMENT_STORAGE_MIGRATION,
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

REQUIRED_RADAR_SCHEMA_OBJECTS_BY_VERSION = {
    1: REQUIRED_RADAR_SCHEMA_OBJECTS_V1,
    2: REQUIRED_RADAR_SCHEMA_OBJECTS_V2,
    3: REQUIRED_RADAR_SCHEMA_OBJECTS_V3,
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
) -> dict:
    applied = {
        int(version): (str(name), str(checksum))
        for version, name, checksum in applied_rows
    }
    known_versions = {migration.version for migration in ordered}
    unknown_versions = sorted(set(applied) - known_versions)
    if unknown_versions:
        raise MigrationDriftError(
            f"数据库包含当前代码未知的雷达迁移版本：{unknown_versions}"
        )

    missing_versions = []
    for migration in ordered:
        existing = applied.get(migration.version)
        if existing is None:
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
