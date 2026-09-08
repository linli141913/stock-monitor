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
    RadarLeaderItem,
    RadarLeaderModule,
    RadarLeaderObservation,
    RadarLeaderObservationItem,
    RadarLeaderReviewQueue,
    RadarLeaderReviewDocument,
    RadarLeaderReviewQueueResponse,
    RadarLeaderReviewDocumentResponse,
    RadarLeaderSnapshot,
    RadarLeaderSourceSummaryItem,
    RadarLeaderSummary,
    RadarLeadersResponse,
    RadarStockResponse,
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
from radar.leader_board import (
    LeaderBoardEntry,
    build_leader_board_projection,
)
from radar.leader_repository import LeaderRepository
from radar.leader_observation_store import (
    LEADER_OBSERVATION_COVERAGE_SCOPE,
    LeaderObservationStoreReadResult,
)
from radar.leader_risk_review_repository import LeaderRiskReviewRepository
from radar.repository import RadarRepository
from radar.sources.leader_risk_official import CNINFO_SOURCE_CONTRACT_ID


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
        leader_repository: Optional[LeaderRepository] = None,
        risk_review_repository: Optional[LeaderRiskReviewRepository] = None,
        leader_observation_loader: Optional[Callable] = None,
    ):
        self.repository = repository
        self.settings = settings
        self.clock = clock
        self.market_status_provider = market_status_provider
        self.etf_repository = etf_repository
        self.leader_repository = leader_repository
        self.risk_review_repository = risk_review_repository
        self.leader_observation_loader = leader_observation_loader

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

    @staticmethod
    def _empty_leader_summary(
        reason_codes: Sequence[str] = (),
    ) -> RadarLeaderSummary:
        return RadarLeaderSummary(
            eligibleCount=0,
            preliminaryCount=0,
            candidateCount=0,
            confirmedCount=0,
            removedCount=0,
            overflowCounts={
                "preliminary": 0,
                "candidate": 0,
                "confirmed": 0,
            },
            coverage=0.0,
            formalUsableCount=0,
            ruleVersion=None,
            reasonCodes=list(reason_codes),
        )

    def _empty_leader_module(
        self,
        *,
        state: str,
        reason_codes: Sequence[str],
        observation: Optional[RadarLeaderObservation] = None,
    ) -> RadarLeaderModule:
        stale_after_seconds = (
            self.settings.stock_scan_interval_seconds * 2 + 30
        )
        selected_observation = (
            observation
            or RadarLeaderObservation(
                status=state,
                quality="unavailable",
                displayAllowed=False,
                coverageScope=LEADER_OBSERVATION_COVERAGE_SCOPE,
                freshness=RadarFreshness(
                    ageSeconds=None,
                    staleAfterSeconds=stale_after_seconds,
                    isStale=False,
                    reasonCodes=list(reason_codes),
                ),
                reasonCodes=list(reason_codes),
            )
        )
        review_queue = self._leader_review_queue()
        return RadarLeaderModule(
            state=state,
            quality="unavailable",
            usingLastSuccess=False,
            lastAttempt=None,
            lastSuccess=None,
            freshness=RadarFreshness(
                ageSeconds=None,
                staleAfterSeconds=stale_after_seconds,
                isStale=False,
                reasonCodes=list(reason_codes),
            ),
            sources=[],
            sourceSummary=self._leader_source_summary(
                selected_observation,
                review_queue=review_queue,
            ),
            summary=self._empty_leader_summary(reason_codes),
            observation=selected_observation,
            reviewQueue=review_queue,
            preliminary=[],
            candidates=[],
            confirmed=[],
            reasonCodes=list(reason_codes),
        )


    @staticmethod
    def _leader_source_summary(
        observation: RadarLeaderObservation,
        *,
        review_queue: RadarLeaderReviewQueue,
        board_entries: Sequence[LeaderBoardEntry] = (),
        board_as_of: Optional[datetime] = None,
        board_fetched_at: Optional[datetime] = None,
    ) -> Sequence[RadarLeaderSourceSummaryItem]:
        observation_available = observation.status in {
            "available", "stale",
        } and bool(observation.items)
        observation_status = (
            "stale"
            if observation.status == "stale" and observation.items
            else ("available" if observation_available else "missing")
        )
        quote_contracts = sorted({
            item.quote_source_contract_id
            for item in observation.items
            if item.quote_source_contract_id
        })
        sector_contracts = sorted({
            item.sector_source_contract_id
            for item in observation.items
            if item.sector_source_contract_id
        })
        observation_reason = (
            []
            if observation_available
            else ["leader_observation_source_contracts_missing"]
        )

        business_contracts = set()
        business_ready_count = 0
        business_unverified = False
        for entry in board_entries:
            research = entry.evidence.get("researchFeatures")
            business = (
                research.get("businessCatalyst")
                if isinstance(research, dict)
                else None
            )
            if not isinstance(business, dict):
                continue
            references = business.get("references")
            reference_contracts = {
                reference.get("sourceContractId")
                for reference in references or ()
                if isinstance(reference, dict)
                and isinstance(reference.get("sourceContractId"), str)
                and reference.get("sourceContractId")
            }
            if business.get("status") == "ready" and reference_contracts:
                business_ready_count += 1
                business_contracts.update(reference_contracts)
            elif business.get("status") in {"source_unverified", "failed"}:
                business_unverified = True
        if board_entries and business_ready_count == len(board_entries):
            business_status = "available"
            business_reasons = []
        elif business_ready_count:
            business_status = "partial"
            business_reasons = ["leader_business_source_summary_partial"]
        elif business_unverified:
            business_status = "unverified"
            business_reasons = ["leader_business_source_summary_unverified"]
        else:
            business_status = "missing"
            business_reasons = ["leader_business_source_summary_missing"]

        announcement_status = "missing"
        announcement_reasons = ["leader_announcement_source_summary_missing"]
        announcement_contracts = []
        announcement_covered_count = 0
        if review_queue.status == "failed":
            announcement_status = "failed"
            announcement_reasons = list(review_queue.reason_codes)
        elif review_queue.status == "ready":
            if (
                observation.candidate_plan_id is None
                or review_queue.candidate_plan_id
                != observation.candidate_plan_id
            ):
                announcement_status = "unverified"
                announcement_reasons = [
                    "leader_announcement_candidate_plan_mismatch",
                ]
            else:
                announcement_status = "available"
                announcement_reasons = list(review_queue.reason_codes)
                announcement_contracts = [CNINFO_SOURCE_CONTRACT_ID]
                announcement_covered_count = review_queue.candidate_count

        return (
            RadarLeaderSourceSummaryItem(
                domain="quote",
                status=observation_status,
                scope="current_observation_candidates",
                sourceContractIds=quote_contracts,
                asOf=observation.as_of,
                sourceTime=min(
                    (item.source_time for item in observation.items),
                    default=None,
                ),
                fetchedAt=observation.published_at,
                coveredCount=(len(observation.items) if quote_contracts else 0),
                expectedCount=observation.candidate_count,
                reasonCodes=observation_reason,
            ),
            RadarLeaderSourceSummaryItem(
                domain="sector",
                status=observation_status,
                scope="current_observation_candidates",
                sourceContractIds=sector_contracts,
                asOf=observation.as_of,
                sourceTime=None,
                fetchedAt=observation.published_at,
                coveredCount=(len(observation.items) if sector_contracts else 0),
                expectedCount=observation.candidate_count,
                reasonCodes=observation_reason,
            ),
            RadarLeaderSourceSummaryItem(
                domain="business",
                status=business_status,
                scope="current_public_tiers",
                sourceContractIds=sorted(business_contracts),
                asOf=board_as_of,
                sourceTime=None,
                fetchedAt=board_fetched_at,
                coveredCount=business_ready_count,
                expectedCount=(len(board_entries) if board_entries else None),
                reasonCodes=business_reasons,
            ),
            RadarLeaderSourceSummaryItem(
                domain="announcement",
                status=announcement_status,
                scope="current_observation_candidates",
                sourceContractIds=announcement_contracts,
                asOf=review_queue.as_of,
                sourceTime=None,
                fetchedAt=review_queue.as_of,
                coveredCount=announcement_covered_count,
                expectedCount=(
                    review_queue.candidate_count
                    if review_queue.status == "ready"
                    else None
                ),
                reasonCodes=announcement_reasons,
            ),
        )

    def _leader_review_queue(self) -> RadarLeaderReviewQueue:
        if (
            not self.settings.enabled
            or not self.settings.shadow_mode
            or not self.settings.leader_stage6_enabled
        ):
            return RadarLeaderReviewQueue(
                reasonCodes=["stage_not_enabled"],
            )
        if self.risk_review_repository is None:
            return RadarLeaderReviewQueue(
                reasonCodes=["d2_review_storage_not_ready"],
            )
        try:
            summary = (
                self.risk_review_repository
                .get_latest_review_batch_summary()
            )
        except Exception:
            return RadarLeaderReviewQueue(
                status="failed",
                reasonCodes=["d2_review_queue_read_failed"],
            )
        if summary is None:
            return RadarLeaderReviewQueue(
                reasonCodes=["d2_review_batch_missing"],
            )
        complete = all((
            summary.get("queryCategoriesComplete") is True,
            summary.get("queryPagesComplete") is True,
            summary.get("queryWindowContinuous") is True,
        ))
        if not complete:
            return RadarLeaderReviewQueue(
                status="failed",
                reasonCodes=["d2_review_completeness_unverified"],
            )
        reason_codes = ["official_risk_scan_ready"]
        return RadarLeaderReviewQueue(
            status="ready",
            semanticCoverageStatus="partial",
            reviewBatchId=summary["reviewBatchId"],
            candidatePlanId=summary["candidatePlanId"],
            asOf=summary["asOf"],
            windowFrom=summary["windowFrom"],
            windowUntil=summary["windowUntil"],
            candidateCount=summary["candidateCount"],
            documentCount=summary["documentCount"],
            documentLinkCount=summary["documentLinkCount"],
            contentSnapshotCount=summary["contentSnapshotCount"],
            reviewedDocumentCount=summary["reviewedDocumentCount"],
            reviewVersionCount=summary["reviewVersionCount"],
            queryCategoriesComplete=True,
            queryPagesComplete=True,
            queryWindowContinuous=True,
            reasonCodes=reason_codes,
        )

    @staticmethod
    def _public_leader_item(
        entry: LeaderBoardEntry,
    ) -> RadarLeaderItem:
        return RadarLeaderItem(
            symbol=entry.symbol,
            name=entry.name,
            industryCode=entry.industry_code,
            industryName=entry.industry_name,
            state=entry.state.value,
            score=entry.score,
            businessExposureStatus=entry.business_exposure_status.value,
            dataStatus=entry.data_status.value,
            firstRejectionReason=entry.first_rejection_reason,
            reasons=list(entry.reasons),
            evidence=dict(entry.evidence),
            invalidation=dict(entry.invalidation),
            stateAgePeriods=entry.state_age_periods,
            formalUsable=False,
        )

    def _leader_observation(
        self,
        *,
        now: datetime,
        is_trading: bool,
    ) -> RadarLeaderObservation:
        stale_after_seconds = (
            self.settings.stock_scan_interval_seconds * 2 + 30
        )
        if (
            not self.settings.enabled
            or not self.settings.shadow_mode
            or not self.settings.leader_stage6_enabled
        ):
            return RadarLeaderObservation(
                status="not_enabled",
                quality="unavailable",
                displayAllowed=False,
                coverageScope=LEADER_OBSERVATION_COVERAGE_SCOPE,
                freshness=RadarFreshness(
                    ageSeconds=None,
                    staleAfterSeconds=stale_after_seconds,
                    isStale=False,
                    reasonCodes=["stage_not_enabled"],
                ),
                reasonCodes=["stage_not_enabled"],
            )
        if self.leader_observation_loader is None:
            result = LeaderObservationStoreReadResult(
                status="not_ready",
                reasons=("leader_observation_snapshot_missing",),
            )
        else:
            try:
                result = self.leader_observation_loader()
            except Exception:
                result = LeaderObservationStoreReadResult(
                    status="failed",
                    reasons=("leader_observation_read_failed",),
                )
        if (
            not isinstance(result, LeaderObservationStoreReadResult)
            or result.status != "available"
            or result.snapshot is None
        ):
            status = (
                "failed"
                if getattr(result, "status", None) == "failed"
                else "not_ready"
            )
            reasons = list(getattr(result, "reasons", ()) or (
                "leader_observation_snapshot_missing",
            ))
            return RadarLeaderObservation(
                status=status,
                quality="unavailable",
                displayAllowed=False,
                coverageScope=LEADER_OBSERVATION_COVERAGE_SCOPE,
                freshness=RadarFreshness(
                    ageSeconds=None,
                    staleAfterSeconds=stale_after_seconds,
                    isStale=False,
                    reasonCodes=reasons,
                ),
                reasonCodes=reasons,
            )
        snapshot = result.snapshot
        if (snapshot.as_of - now).total_seconds() > 5:
            reasons = ["leader_observation_from_future"]
            return RadarLeaderObservation(
                status="failed",
                quality="unavailable",
                displayAllowed=False,
                coverageScope=LEADER_OBSERVATION_COVERAGE_SCOPE,
                freshness=RadarFreshness(
                    ageSeconds=None,
                    staleAfterSeconds=stale_after_seconds,
                    isStale=False,
                    reasonCodes=reasons,
                ),
                reasonCodes=reasons,
            )
        freshness = self._freshness(
            last_success=RadarLastSuccess(
                radarRunId=snapshot.radar_run_id,
                asOf=snapshot.as_of,
                sourceTime=max(
                    (item.source_time for item in snapshot.items),
                    default=None,
                ),
                fetchedAt=snapshot.published_at,
            ),
            now=now,
            scan_interval_seconds=self.settings.stock_scan_interval_seconds,
            is_trading=is_trading,
        )
        status = "stale" if freshness.is_stale else (
            "available" if snapshot.items else "empty"
        )
        reasons = ["leader_observation_only"]
        if freshness.is_stale:
            reasons.append("leader_observation_stale")
        return RadarLeaderObservation(
            status=status,
            quality="partial",
            displayAllowed=status in {"available", "stale"},
            radarRunId=snapshot.radar_run_id,
            candidatePlanId=snapshot.candidate_plan_id,
            asOf=snapshot.as_of,
            publishedAt=snapshot.published_at,
            scannedCount=snapshot.scanned_count,
            mappedCount=snapshot.mapped_count,
            candidateCount=snapshot.candidate_count,
            coverageScope=snapshot.coverage_scope,
            freshness=freshness,
            items=[RadarLeaderObservationItem(
                symbol=item.symbol,
                name=item.name,
                industryCode=item.industry_code,
                industryName=item.industry_name,
                withinIndustryRank=item.within_industry_rank,
                price=item.price,
                changePercent=item.change_percent,
                sourceTime=item.source_time,
                quoteSourceContractId=item.quote_source_contract_id,
                sectorSourceContractId=item.sector_source_contract_id,
            ) for item in snapshot.items],
            reasonCodes=reasons,
        )

    def _observation_only_leader_module(
        self,
        observation: RadarLeaderObservation,
        *,
        extra_reasons: Sequence[str] = (),
    ) -> RadarLeaderModule:
        reasons = list(dict.fromkeys((
            *observation.reason_codes,
            *extra_reasons,
        )))
        coverage = (
            observation.mapped_count / observation.scanned_count
            if observation.scanned_count
            else 0.0
        )
        review_queue = self._leader_review_queue()
        return RadarLeaderModule(
            state=observation.status,
            quality=observation.quality,
            usingLastSuccess=observation.status == "stale",
            lastAttempt=None,
            lastSuccess=None,
            freshness=observation.freshness,
            sources=[],
            sourceSummary=self._leader_source_summary(
                observation,
                review_queue=review_queue,
            ),
            summary=RadarLeaderSummary(
                eligibleCount=observation.candidate_count,
                preliminaryCount=0,
                candidateCount=0,
                confirmedCount=0,
                removedCount=0,
                overflowCounts={
                    "preliminary": 0,
                    "candidate": 0,
                    "confirmed": 0,
                },
                coverage=coverage,
                formalUsableCount=0,
                ruleVersion=None,
                reasonCodes=reasons,
            ),
            observation=observation,
            reviewQueue=review_queue,
            preliminary=[],
            candidates=[],
            confirmed=[],
            reasonCodes=reasons,
        )

    def _leader_failure_or_observation(
        self,
        observation: RadarLeaderObservation,
        *,
        state: str,
        reason_code: str,
    ) -> RadarLeaderModule:
        if observation.status in {"available", "empty", "stale"}:
            return self._observation_only_leader_module(
                observation,
                extra_reasons=[reason_code],
            )
        return self._empty_leader_module(
            state=state,
            reason_codes=[reason_code],
            observation=observation,
        )

    def _leader_module(
        self,
        *,
        now: datetime,
        is_trading: bool,
    ) -> RadarLeaderModule:
        observation = self._leader_observation(
            now=now,
            is_trading=is_trading,
        )
        if not self.settings.leader_stage6_enabled:
            return self._empty_leader_module(
                state="not_enabled",
                reason_codes=["stage_not_enabled"],
                observation=observation,
            )
        if not self.settings.enabled or not self.settings.shadow_mode:
            return self._empty_leader_module(
                state="not_enabled",
                reason_codes=["radar_not_enabled"],
                observation=observation,
            )
        if self.leader_repository is None:
            if observation.status in {"available", "empty", "stale", "failed"}:
                return self._observation_only_leader_module(
                    observation,
                    extra_reasons=["stage6_storage_not_ready"],
                )
            return self._empty_leader_module(
                state="not_ready",
                reason_codes=["stage6_storage_not_ready"],
                observation=observation,
            )
        try:
            snapshot = self.leader_repository.get_latest_candidate_snapshot()
        except Exception:
            if observation.status in {"available", "empty", "stale"}:
                return self._observation_only_leader_module(
                    observation,
                    extra_reasons=["stage6_read_failed"],
                )
            return self._empty_leader_module(
                state="failed",
                reason_codes=["stage6_read_failed"],
                observation=observation,
            )
        if snapshot is None:
            if observation.status in {"available", "empty", "stale", "failed"}:
                return self._observation_only_leader_module(
                    observation,
                    extra_reasons=["candidate_snapshot_missing"],
                )
            return self._empty_leader_module(
                state="not_ready",
                reason_codes=["candidate_snapshot_missing"],
                observation=observation,
            )
        try:
            board = build_leader_board_projection(snapshot)
        except Exception:
            return self._leader_failure_or_observation(
                observation,
                state="failed",
                reason_code="stage6_snapshot_invalid",
            )
        board_entries = (
            *board.preliminary,
            *board.candidate,
            *board.confirmed,
        )
        if board.formal_usable or any(
            entry.formal_usable
            for entry in board_entries
        ):
            return self._leader_failure_or_observation(
                observation,
                state="not_ready",
                reason_code="stage6_formal_state_forbidden",
            )
        created_at = snapshot.get("createdAt")
        if (
            not isinstance(created_at, datetime)
            or created_at.tzinfo is None
            or created_at.utcoffset() is None
        ):
            return self._leader_failure_or_observation(
                observation,
                state="failed",
                reason_code="stage6_snapshot_invalid",
            )
        if (board.as_of - now).total_seconds() > 5:
            return self._leader_failure_or_observation(
                observation,
                state="failed",
                reason_code="stage6_snapshot_from_future",
            )
        if created_at < board.as_of:
            return self._leader_failure_or_observation(
                observation,
                state="failed",
                reason_code="stage6_snapshot_invalid",
            )
        reason_codes = list(
            (snapshot.get("reasonCounts") or {}).keys()
        )
        snapshot_quality = snapshot.get("quality")
        if (
            float(snapshot.get("coverage", 0.0)) <= 0
            and snapshot_quality != "empty"
        ):
            if observation.status in {"available", "empty", "stale"}:
                return self._observation_only_leader_module(
                    observation,
                    extra_reasons=(
                        reason_codes
                        or ["leader_inputs_unavailable"]
                    ),
                )
            return self._empty_leader_module(
                state="not_ready",
                reason_codes=(
                    reason_codes
                    or ["leader_inputs_unavailable"]
                ),
                observation=observation,
            )

        last_success = RadarLeaderSnapshot(
            radarRunId=board.radar_run_id,
            asOf=board.as_of,
            createdAt=created_at,
            ruleVersion=board.rule_version,
        )
        freshness = self._freshness(
            last_success=RadarLastSuccess(
                radarRunId=board.radar_run_id,
                asOf=board.as_of,
                sourceTime=None,
                fetchedAt=created_at,
            ),
            now=now,
            scan_interval_seconds=self.settings.stock_scan_interval_seconds,
            is_trading=is_trading,
        )
        preliminary = [
            self._public_leader_item(entry)
            for entry in board.preliminary
        ]
        candidates = [
            self._public_leader_item(entry)
            for entry in board.candidate
        ]
        confirmed = [
            self._public_leader_item(entry)
            for entry in board.confirmed
        ]
        if snapshot_quality == "unavailable":
            if observation.status in {"available", "empty", "stale"}:
                return self._observation_only_leader_module(
                    observation,
                    extra_reasons=(
                        reason_codes
                        or ["leader_rule_not_ready"]
                    ),
                )
            return self._empty_leader_module(
                state="not_ready",
                reason_codes=reason_codes or ["leader_rule_not_ready"],
                observation=observation,
            )
        if freshness.is_stale:
            state = "stale"
        elif not preliminary and not candidates and not confirmed:
            state = "empty"
        else:
            state = "available"
        quality = (
            "complete"
            if snapshot_quality in {"complete", "empty"}
            else "partial"
        )
        overflow_counts = {
            leader_state.value: count
            for leader_state, count in board.overflow_counts
        }
        review_queue = self._leader_review_queue()
        return RadarLeaderModule(
            state=state,
            quality=quality,
            usingLastSuccess=state == "stale",
            lastAttempt=None,
            lastSuccess=last_success,
            freshness=freshness,
            sources=[],
            sourceSummary=self._leader_source_summary(
                observation,
                review_queue=review_queue,
                board_entries=board_entries,
                board_as_of=board.as_of,
                board_fetched_at=created_at,
            ),
            summary=RadarLeaderSummary(
                eligibleCount=int(snapshot.get("eligibleCount", 0)),
                preliminaryCount=len(preliminary),
                candidateCount=len(candidates),
                confirmedCount=len(confirmed),
                removedCount=int(snapshot.get("removedCount", 0)),
                overflowCounts=overflow_counts,
                coverage=float(snapshot.get("coverage", 0.0)),
                formalUsableCount=0,
                ruleVersion=board.rule_version,
                reasonCodes=reason_codes,
            ),
            observation=observation,
            reviewQueue=review_queue,
            preliminary=preliminary,
            candidates=candidates,
            confirmed=confirmed,
            reasonCodes=reason_codes,
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
        leaders = (
            self._leader_module(
                now=now,
                is_trading=is_trading,
            )
            if self.settings.leader_stage6_enabled
            else RadarDeferredModule(enabledStage=6)
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
                leaders=leaders,
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

    def build_leaders(self) -> RadarLeadersResponse:
        now = self.clock()
        market_session = self._market_session(now)
        return RadarLeadersResponse(
            checkedAt=now,
            mode=self._mode(),
            marketSession=market_session,
            module=self._leader_module(
                now=now,
                is_trading=market_session.code == "trading",
            ),
        )

    def build_stock(self, symbol: str) -> RadarStockResponse:
        now = self.clock()
        market_session = self._market_session(now)
        module = self._leader_module(
            now=now,
            is_trading=market_session.code == "trading",
        )
        normalized_symbol = str(symbol).strip().lower()
        if normalized_symbol.startswith(("sh", "sz", "hk", "bj")):
            normalized_symbol = normalized_symbol[2:]

        status_by_module = {
            "not_enabled": "not_enabled",
            "failed": "failed",
            "stale": "stale",
        }
        status = status_by_module.get(module.state)
        if module.state == "not_ready":
            status = "no_snapshot"
        leader = next(
            (
                item
                for item in (
                    *module.preliminary,
                    *module.candidates,
                    *module.confirmed,
                )
                if item.symbol == normalized_symbol
            ),
            None,
        )
        observation_item = next(
            (
                item
                for item in module.observation.items
                if item.symbol == normalized_symbol
            ),
            None,
        )
        if status is None:
            status = (
                "matched"
                if leader is not None
                else "observed"
                if observation_item is not None
                else "not_listed"
            )
        reason_codes = list(module.reason_codes)
        if status == "not_listed":
            reason_codes = [
                "stock_not_in_observation_candidates"
                if module.observation.display_allowed
                else "stock_not_in_public_tiers"
            ]

        return RadarStockResponse(
            checkedAt=now,
            mode=self._mode(),
            symbol=normalized_symbol,
            status=status,
            snapshot=module.last_success,
            freshness=module.freshness,
            leader=leader,
            observationItem=observation_item,
            reasonCodes=reason_codes,
        )

    def build_leader_review_queue(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> RadarLeaderReviewQueueResponse:
        now = self.clock()
        market_session = self._market_session(now)
        summary = self._leader_review_queue()
        page = {
            "total": 0,
            "limit": limit,
            "offset": offset,
            "items": (),
        }
        if (
            summary.status == "ready"
            and summary.review_batch_id is not None
            and self.risk_review_repository is not None
        ):
            page = self.risk_review_repository.list_review_batch_documents(
                summary.review_batch_id,
                limit=limit,
                offset=offset,
            )
        items = []
        for row in page["items"]:
            document = row["document"]
            items.append(RadarLeaderReviewDocument(
                documentId=document.document_id,
                symbol=document.symbol,
                issuerName=document.issuer_name,
                title=document.title,
                publishedAt=document.published_at,
                sourceName=document.source_name,
                sourceUrl=document.source_url,
                candidateCategory=document.candidate_category.value,
                hasContentSnapshot=row["hasContentSnapshot"],
                contentSnapshotCount=row["contentSnapshotCount"],
                contentStatus=row["contentStatus"],
                contentFetchedAt=row["contentFetchedAt"],
                reviewVersionCount=row["reviewVersionCount"],
            ))
        return RadarLeaderReviewQueueResponse(
            checkedAt=now,
            mode=self._mode(),
            marketSession=market_session,
            summary=summary,
            total=page["total"],
            limit=page["limit"],
            offset=page["offset"],
            items=items,
        )

    def build_leader_review_document(
        self,
        *,
        review_batch_id: str,
        document_id: str,
        candidate_category: str,
    ) -> RadarLeaderReviewDocumentResponse:
        now = self.clock()
        market_session = self._market_session(now)
        summary = self._leader_review_queue()
        if (
            summary.status != "ready"
            or self.risk_review_repository is None
        ):
            raise ValueError("D2审核队列当前不可读")
        if summary.review_batch_id != review_batch_id:
            raise ValueError("公告不属于当前审核批次")
        row = self.risk_review_repository.get_review_batch_document(
            review_batch_id,
            document_id,
            candidate_category,
        )
        document = row["document"]
        item = RadarLeaderReviewDocument(
            documentId=document.document_id,
            symbol=document.symbol,
            issuerName=document.issuer_name,
            title=document.title,
            publishedAt=document.published_at,
            sourceName=document.source_name,
            sourceUrl=document.source_url,
            candidateCategory=document.candidate_category.value,
            hasContentSnapshot=row["hasContentSnapshot"],
            contentSnapshotCount=row["contentSnapshotCount"],
            contentStatus=row["contentStatus"],
            contentFetchedAt=row["contentFetchedAt"],
            reviewVersionCount=row["reviewVersionCount"],
        )
        return RadarLeaderReviewDocumentResponse(
            checkedAt=now,
            mode=self._mode(),
            marketSession=market_session,
            summary=summary,
            item=item,
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
