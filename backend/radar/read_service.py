from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple

import market_calendar
from radar.api_contracts import (
    RadarDeferredModule,
    RadarEtfAttempt,
    RadarEtfCandidateItem,
    RadarEtfModule,
    RadarEtfProductItem,
    RadarEtfSnapshot,
    RadarEtfSummary,
    RadarEtfsResponse,
    RadarFreshness,
    RadarLastAttempt,
    RadarLastSuccess,
    RadarMarketModule,
    RadarMarketSession,
    RadarModuleCollection,
    RadarOverviewResponse,
    RadarSectorModule,
    RadarSectorSummary,
    RadarSectorsResponse,
    RadarSourceStatus,
)
from radar.config import RadarSettings
from radar.etf_repository import EtfRepository
from radar.repository import RadarRepository


MARKET_RUN_PREFIX = "radar-shadow-market-features-"
SECTOR_RUN_PREFIX = "radar-shadow-sector-features-"
EXPECTED_AVAILABLE_ERROR_CODES = frozenset({
    "market_features_shadow_unit_unverified",
    "sector_features_shadow_partial",
})


class RadarReadService:
    def __init__(
        self,
        repository: RadarRepository,
        *,
        settings: RadarSettings,
        clock: Callable[[], datetime],
        market_status_provider=market_calendar.get_market_status,
        etf_repository: Optional[EtfRepository] = None,
    ):
        self.repository = repository
        self.settings = settings
        self.clock = clock
        self.market_status_provider = market_status_provider
        self.etf_repository = etf_repository

    def _market_session(self, now: datetime) -> RadarMarketSession:
        status, calendar_day = self.market_status_provider("cn", now)
        return RadarMarketSession(
            code=status.code,
            label=status.label,
            calendarKind=calendar_day.kind,
            calendarSourceUrl=calendar_day.source_url,
            calendarCheckedAt=calendar_day.checked_at,
        )

    @staticmethod
    def _last_attempt(row: Optional[Dict[str, Any]]) -> Optional[RadarLastAttempt]:
        return RadarLastAttempt.model_validate(row) if row is not None else None

    @staticmethod
    def _source_statuses(
        rows: Iterable[Dict[str, Any]],
    ) -> Sequence[RadarSourceStatus]:
        results = []
        for row in rows:
            details = row.get("details") or {}
            reason_codes = list(details.get("healthReasons") or [])
            reason_codes.extend(
                str(issue.get("code"))
                for issue in details.get("sourceIssues") or []
                if issue.get("code")
            )
            results.append(RadarSourceStatus(
                batchId=row["batchId"],
                source=row["source"],
                asOf=row["asOf"],
                sourceTime=row.get("sourceTime"),
                fetchedAt=row["fetchedAt"],
                status=row["status"],
                expectedCount=row.get("expectedCount"),
                returnedCount=row["returnedCount"],
                rowCoverage=row.get("rowCoverage"),
                requiredFieldCoverage=row.get("requiredFieldCoverage") or {},
                reasonCodes=list(dict.fromkeys(reason_codes)),
            ))
        return results

    @staticmethod
    def _last_success(
        row: Optional[Dict[str, Any]],
    ) -> Optional[RadarLastSuccess]:
        if row is None:
            return None
        return RadarLastSuccess(
            radarRunId=row["radarRunId"],
            asOf=row["asOf"],
            sourceTime=row.get("sourceTime"),
            fetchedAt=row["fetchedAt"],
        )

    @staticmethod
    def _attempt_is_failure(
        attempt: Optional[Dict[str, Any]],
        last_success: Optional[RadarLastSuccess],
    ) -> bool:
        if attempt is None:
            return False
        if last_success is not None and attempt["asOf"] < last_success.as_of:
            return False
        if attempt["status"] == "failed":
            return True
        error_code = attempt.get("errorCode")
        return bool(
            error_code
            and error_code not in EXPECTED_AVAILABLE_ERROR_CODES
        )

    @staticmethod
    def _public_market_data(row: Dict[str, Any]) -> Dict[str, Any]:
        turnover = dict(row.get("turnover") or {})
        turnover_is_verified = turnover.get("unitStatus") == "verified"
        return {
            "radarRunId": row.get("radarRunId"),
            "asOf": row.get("asOf"),
            "sourceTime": row.get("sourceTime"),
            "fetchedAt": row.get("fetchedAt"),
            "formalStateEnabled": False,
            "indexCompleteness": row.get("indexCompleteness") or {},
            "breadth": row.get("breadth") or {},
            "turnover": {
                "contributingCount": turnover.get("contributingCount"),
                "unitStatus": turnover.get("unitStatus"),
                "displayAllowed": turnover_is_verified,
                "completeness": turnover.get("completeness") or {},
                "reasons": list(turnover.get("reasons") or ()),
            },
            "excludedEtfCount": row.get("excludedEtfCount"),
            "duplicateSymbolCount": row.get("duplicateSymbolCount"),
            "unknownSymbolCount": row.get("unknownSymbolCount"),
            "indices": list(row.get("indices") or ()),
        }

    @staticmethod
    def _freshness(
        *,
        last_success: Optional[RadarLastSuccess],
        now: datetime,
        scan_interval_seconds: int,
        is_trading: bool,
    ) -> RadarFreshness:
        stale_after_seconds = scan_interval_seconds * 2 + 30
        if last_success is None:
            return RadarFreshness(
                ageSeconds=None,
                staleAfterSeconds=stale_after_seconds,
                isStale=False,
                reasonCodes=["snapshot_missing"],
            )
        basis = last_success.source_time or last_success.as_of
        age_seconds = max(0, int((now - basis).total_seconds()))
        is_stale = is_trading and age_seconds > stale_after_seconds
        return RadarFreshness(
            ageSeconds=age_seconds,
            staleAfterSeconds=stale_after_seconds,
            isStale=is_stale,
            reasonCodes=["snapshot_age_exceeded"] if is_stale else [],
        )

    def _source_rows(
        self,
        attempt: Optional[Dict[str, Any]],
    ) -> Sequence[RadarSourceStatus]:
        if attempt is None:
            return ()
        rows = self.repository.list_source_status_rows(attempt["radarRunId"])
        return self._source_statuses(rows)

    def _market_module(
        self,
        *,
        now: datetime,
        is_trading: bool,
    ) -> RadarMarketModule:
        try:
            row = self.repository.get_latest_market_feature_row()
        except Exception:
            row = None
            read_failed = True
        else:
            read_failed = False
        try:
            attempt = self.repository.get_latest_run_row(MARKET_RUN_PREFIX)
            sources = self._source_rows(attempt)
        except Exception:
            attempt = None
            sources = ()
            read_failed = True

        last_success = self._last_success(row)
        freshness = self._freshness(
            last_success=last_success,
            now=now,
            scan_interval_seconds=self.settings.market_scan_interval_seconds,
            is_trading=is_trading,
        )
        attempt_failed = self._attempt_is_failure(attempt, last_success)
        if read_failed or attempt_failed:
            state = "failed"
        elif row is None:
            state = "not_ready"
        elif freshness.is_stale:
            state = "stale"
        else:
            state = "available"

        quality = "unavailable"
        data = None
        if row is not None:
            data = self._public_market_data(row)
            quality = "complete"
            if (
                not data.get("indexCompleteness", {}).get("isComplete")
                or not data.get("breadth", {})
                .get("completeness", {})
                .get("isComplete")
                or not data.get("turnover", {})
                .get("completeness", {})
                .get("isComplete")
                or not data["turnover"]["displayAllowed"]
            ):
                quality = "partial"

        return RadarMarketModule(
            state=state,
            quality=quality,
            usingLastSuccess=bool(
                row is not None and state in {"failed", "stale"}
            ),
            lastAttempt=self._last_attempt(attempt),
            lastSuccess=last_success,
            freshness=freshness,
            sources=list(sources),
            data=data,
        )

    @staticmethod
    def _public_sector_item(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "divisionCode": row.get("divisionCode"),
            "divisionName": row.get("divisionName"),
            "categoryCode": row.get("categoryCode"),
            "categoryName": row.get("categoryName"),
            "asOf": row.get("asOf"),
            "sourceTime": row.get("sourceTime"),
            "fetchedAt": row.get("fetchedAt"),
            "classificationMappingCoverage": row.get(
                "classificationMappingCoverage"
            ),
            "mappedConstituentCount": row.get("mappedConstituentCount"),
            "unconfirmedStockCount": row.get("unconfirmedStockCount"),
            "expectedCount": row.get("expectedCount"),
            "returnedCount": row.get("returnedCount"),
            "freshCount": row.get("freshCount"),
            "rowCoverage": row.get("rowCoverage"),
            "isComplete": row.get("isComplete"),
            "equalReturn": row.get("equalReturn"),
            "advancers": row.get("advancers"),
            "decliners": row.get("decliners"),
            "flat": row.get("flat"),
            "unavailable": row.get("unavailable"),
            "upRatio": row.get("upRatio"),
            "shadowUsable": row.get("shadowUsable"),
            "reasons": list(row.get("reasons") or ()),
        }

    @staticmethod
    def _sort_sector_items(
        rows: Iterable[Dict[str, Any]],
    ) -> Sequence[Dict[str, Any]]:
        items = [RadarReadService._public_sector_item(row) for row in rows]
        return sorted(
            items,
            key=lambda item: (
                item["equalReturn"] is None,
                -(item["equalReturn"] or 0),
                item["divisionCode"] or "",
            ),
        )

    def _sector_module(
        self,
        *,
        now: datetime,
        is_trading: bool,
        limit: Optional[int],
    ) -> RadarSectorModule:
        try:
            rows = self.repository.list_latest_sector_feature_rows()
        except Exception:
            rows = ()
            read_failed = True
        else:
            read_failed = False
        try:
            attempt = self.repository.get_latest_run_row(SECTOR_RUN_PREFIX)
            sources = self._source_rows(attempt)
        except Exception:
            attempt = None
            sources = ()
            read_failed = True

        sorted_items = list(self._sort_sector_items(rows))
        first_row = rows[0] if rows else None
        last_success = self._last_success(first_row)
        freshness = self._freshness(
            last_success=last_success,
            now=now,
            scan_interval_seconds=self.settings.sector_scan_interval_seconds,
            is_trading=is_trading,
        )
        attempt_failed = self._attempt_is_failure(attempt, last_success)
        successful_empty = bool(
            not rows
            and attempt is not None
            and attempt["status"] == "succeeded"
            and not attempt.get("errorCode")
        )
        if read_failed or attempt_failed:
            state = "failed"
        elif successful_empty:
            state = "empty"
        elif not rows:
            state = "not_ready"
        elif freshness.is_stale:
            state = "stale"
        else:
            state = "available"

        usable_count = sum(bool(item["shadowUsable"]) for item in sorted_items)
        quality = "unavailable"
        if rows:
            quality = (
                "complete"
                if usable_count == len(sorted_items)
                else "partial"
            )
        elif successful_empty:
            quality = "complete"

        visible_items = sorted_items if limit is None else sorted_items[:limit]
        return RadarSectorModule(
            state=state,
            quality=quality,
            usingLastSuccess=bool(
                rows and state in {"failed", "stale"}
            ),
            lastAttempt=self._last_attempt(attempt),
            lastSuccess=last_success,
            freshness=freshness,
            sources=list(sources),
            summary=RadarSectorSummary(
                totalCount=len(sorted_items),
                usableCount=usable_count,
                unavailableCount=len(sorted_items) - usable_count,
            ),
            items=visible_items,
        )

    @staticmethod
    def _empty_etf_summary() -> RadarEtfSummary:
        return RadarEtfSummary(
            productCount=0,
            eligibleProductCount=0,
            candidateGroupCount=0,
            computedCount=0,
            staleCount=0,
            missingCount=0,
            coverage=0.0,
            formalUsableCount=0,
            ruleVersionId=None,
            reasonCodes=[],
        )

    @classmethod
    def _etf_not_enabled(cls, reason: str = "stage_not_enabled") -> RadarEtfModule:
        return RadarEtfModule(
            state="not_enabled",
            quality="unavailable",
            usingLastSuccess=False,
            lastAttempt=None,
            lastSuccess=None,
            freshness=RadarFreshness(
                ageSeconds=None,
                staleAfterSeconds=630,
                isStale=False,
                reasonCodes=[reason],
            ),
            sources=[],
            summary=cls._empty_etf_summary(),
            products=[],
            candidates=[],
            reasonCodes=[reason],
        )

    @staticmethod
    def _public_etf_product(row: Dict[str, Any]) -> Dict[str, Any]:
        product = row["product"].model_dump(mode="json", by_alias=True)
        return {
            "symbol": product["symbol"],
            "officialName": product["officialName"],
            "exchange": product["exchange"],
            "productType": product["productType"],
            "managementStyle": product["managementStyle"],
            "assetClass": product["assetClass"],
            "targetIndexName": product.get("targetIndexName"),
            "classificationMappingVersion": product[
                "classificationMappingVersion"
            ],
            "classificationReasons": list(
                product.get("classificationReasons") or []
            ),
            "sourceContractId": row["sourceContractId"],
            "source": product["source"],
            "sourceTime": row.get("sourceTime"),
            "fetchedAt": product["fetchedAt"],
            "versionTimeKind": row["versionTimeKind"],
            "officialEffectiveFrom": row.get("officialEffectiveFrom"),
            "firstObservedAt": row["firstObservedAt"],
            "effectiveFrom": row["effectiveFrom"],
            "historicalReplayReady": row["historicalReplayReady"],
            "evidenceUrl": row.get("evidenceUrl"),
        }

    @staticmethod
    def _public_etf_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "industryCode": row["industryCode"],
            "indexGroupKey": row["indexGroupKey"],
            "rank": row["rank"],
            "representativeSymbol": row.get("representativeSymbol"),
            "alternativeSymbols": list(row.get("alternativeSymbols") or []),
            "industryExposures": list(row.get("industryExposures") or []),
            "rankingComponents": dict(row.get("rankingComponents") or {}),
            "entryReasons": list(row.get("entryReasons") or []),
            "riskReasons": list(row.get("riskReasons") or []),
            "exitConditions": list(row.get("exitConditions") or []),
            "formalUsable": bool(row.get("formalUsable", False)),
        }

    @staticmethod
    def _public_etf_attempt(row: Dict[str, Any]) -> RadarEtfAttempt:
        issue_codes = []
        for issue in row.get("issues") or []:
            if isinstance(issue, dict) and issue.get("code"):
                issue_codes.append(str(issue["code"]))
            elif issue:
                issue_codes.append(str(issue))
        return RadarEtfAttempt.model_validate({
            **row,
            "issues": list(row.get("issues") or []),
        })

    @staticmethod
    def _etf_source_status(row: Dict[str, Any]) -> RadarSourceStatus:
        issue_codes = []
        for issue in row.get("issues") or []:
            if isinstance(issue, dict) and issue.get("code"):
                issue_codes.append(str(issue["code"]))
            elif issue:
                issue_codes.append(str(issue))
        return RadarSourceStatus(
            batchId=f'{row["productMasterRunId"]}:product-master',
            source=row["source"],
            asOf=row["asOf"],
            sourceTime=row.get("sourceTime"),
            fetchedAt=row["fetchedAt"],
            status=row["status"],
            expectedCount=row.get("expectedCount"),
            returnedCount=row["returnedCount"],
            rowCoverage=row.get("rowCoverage"),
            requiredFieldCoverage=row.get("requiredFieldCoverage") or {},
            reasonCodes=list(dict.fromkeys(issue_codes)),
        )

    def _etf_module(
        self,
        *,
        now: datetime,
        is_trading: bool,
    ) -> RadarEtfModule:
        if not self.settings.etf_stage5_enabled:
            return self._etf_not_enabled()
        if not self.settings.enabled or not self.settings.shadow_mode:
            return self._etf_not_enabled("radar_not_enabled")
        if self.etf_repository is None:
            return RadarEtfModule(
                state="not_ready",
                quality="unavailable",
                usingLastSuccess=False,
                lastAttempt=None,
                lastSuccess=None,
                freshness=RadarFreshness(
                    ageSeconds=None,
                    staleAfterSeconds=self.settings.etf_scan_interval_seconds * 2 + 30,
                    isStale=False,
                    reasonCodes=["stage5_storage_not_ready"],
                ),
                sources=[],
                summary=self._empty_etf_summary(),
                products=[],
                candidates=[],
                reasonCodes=["stage5_storage_not_ready"],
            )

        read_failed = False
        try:
            attempt_row = self.etf_repository.get_latest_product_master_run()
            success_row = self.etf_repository.get_latest_product_master_run(
                successful_only=True,
            )
            profiles = self.etf_repository.list_current_product_profiles(now)
            candidate_row = self.etf_repository.get_latest_candidate_snapshot()
        except Exception:
            attempt_row = None
            success_row = None
            profiles = ()
            candidate_row = None
            read_failed = True

        last_success = None
        if candidate_row is not None:
            last_success = RadarEtfSnapshot(
                radarRunId=candidate_row["radarRunId"],
                asOf=candidate_row["asOf"],
                sourceTime=candidate_row.get("sourceTime"),
                fetchedAt=candidate_row.get("fetchedAt", candidate_row["asOf"]),
            )
        elif success_row is not None:
            last_success = RadarEtfSnapshot(
                radarRunId=success_row["productMasterRunId"],
                asOf=success_row["asOf"],
                sourceTime=success_row.get("sourceTime"),
                fetchedAt=success_row["fetchedAt"],
            )
        freshness = self._freshness(
            last_success=(
                RadarLastSuccess(
                    radarRunId=last_success.radar_run_id,
                    asOf=last_success.as_of,
                    sourceTime=last_success.source_time,
                    fetchedAt=last_success.fetched_at,
                )
                if last_success is not None
                else None
            ),
            now=now,
            scan_interval_seconds=self.settings.etf_scan_interval_seconds,
            is_trading=is_trading,
        )
        products = [
            self._public_etf_product(row)
            for row in profiles
        ]
        candidates = [
            self._public_etf_candidate(row)
            for row in (candidate_row or {}).get("entries") or []
        ]
        summary_source = candidate_row or {}
        summary = RadarEtfSummary(
            productCount=len(products),
            eligibleProductCount=int(summary_source.get("eligibleProductCount", 0)),
            candidateGroupCount=int(summary_source.get("candidateGroupCount", 0)),
            computedCount=int(summary_source.get("computedCount", 0)),
            staleCount=int(summary_source.get("staleCount", 0)),
            missingCount=int(summary_source.get("missingCount", 0)),
            coverage=float(summary_source.get("coverage", 0.0)),
            formalUsableCount=sum(
                1 for candidate in candidates if candidate["formalUsable"]
            ),
            ruleVersionId=summary_source.get("ruleVersionId"),
            reasonCodes=list(
                dict.fromkeys(
                    list((summary_source.get("reasonCounts") or {}).keys())
                    + [
                        reason
                        for product in products
                        for reason in product["classificationReasons"]
                    ]
                )
            ),
        )
        attempt = (
            self._public_etf_attempt(attempt_row)
            if attempt_row is not None
            else None
        )
        sources = (
            [self._etf_source_status(attempt_row)]
            if attempt_row is not None
            else []
        )
        reason_codes = list(summary.reason_codes)
        if attempt_row is not None and attempt_row["status"] == "failed":
            reason_codes.append("product_master_source_failed")
        if candidate_row is None:
            reason_codes.append("candidate_snapshot_missing")
        elif candidate_row.get("quality") == "unavailable":
            reason_codes.append("etf_rule_not_frozen")
        if freshness.is_stale:
            reason_codes.append("snapshot_age_exceeded")
        reason_codes = list(dict.fromkeys(reason_codes))

        if read_failed:
            state = "failed"
            quality = "unavailable"
        elif attempt_row is not None and attempt_row["status"] == "failed":
            state = "failed"
            quality = "partial" if products or candidate_row else "unavailable"
        elif candidate_row is None or candidate_row.get("quality") == "unavailable":
            state = "not_ready"
            quality = "unavailable" if not products else "partial"
        elif freshness.is_stale:
            state = "stale"
            quality = "complete" if candidate_row.get("quality") == "complete" else "partial"
        elif not candidates:
            state = "empty"
            quality = "complete"
        else:
            state = "available"
            quality = "complete" if candidate_row.get("quality") == "complete" else "partial"

        return RadarEtfModule(
            state=state,
            quality=quality,
            usingLastSuccess=bool(
                candidate_row is not None
                and state in {"failed", "stale"}
            ),
            lastAttempt=attempt,
            lastSuccess=last_success,
            freshness=freshness,
            sources=sources,
            summary=summary,
            products=products,
            candidates=candidates,
            reasonCodes=reason_codes,
        )

    def _mode(self) -> str:
        return (
            "shadow"
            if self.settings.enabled and self.settings.shadow_mode
            else "disabled"
        )

    def build_overview(self) -> RadarOverviewResponse:
        now = self.clock()
        market_session = self._market_session(now)
        is_trading = market_session.code == "trading"
        market = self._market_module(now=now, is_trading=is_trading)
        sectors = self._sector_module(
            now=now,
            is_trading=is_trading,
            limit=3,
        )
        etf = self._etf_module(
            now=now,
            is_trading=is_trading,
        )
        module_skew_seconds = None
        if market.last_success is not None and sectors.last_success is not None:
            module_skew_seconds = int(abs(
                (
                    market.last_success.as_of
                    - sectors.last_success.as_of
                ).total_seconds()
            ))
        return RadarOverviewResponse(
            checkedAt=now,
            mode=self._mode(),
            marketSession=market_session,
            moduleSkewSeconds=module_skew_seconds,
            modules=RadarModuleCollection(
                market=market,
                sectors=sectors,
                etf=etf,
                leaders=RadarDeferredModule(enabledStage=6),
                history=RadarDeferredModule(enabledStage=9),
            ),
        )

    def build_etfs(self) -> RadarEtfsResponse:
        now = self.clock()
        market_session = self._market_session(now)
        return RadarEtfsResponse(
            checkedAt=now,
            mode=self._mode(),
            marketSession=market_session,
            module=self._etf_module(
                now=now,
                is_trading=market_session.code == "trading",
            ),
        )

    def build_sectors(self) -> RadarSectorsResponse:
        now = self.clock()
        market_session = self._market_session(now)
        return RadarSectorsResponse(
            checkedAt=now,
            mode=self._mode(),
            marketSession=market_session,
            module=self._sector_module(
                now=now,
                is_trading=market_session.code == "trading",
                limit=None,
            ),
        )
