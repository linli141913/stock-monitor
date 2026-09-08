"""阶段10离线影子台账使用的上交所官方日历证据。

输入封套只声明来源身份、抓取时刻和原始文档哈希；休市日期始终从随附的
上交所原始页面字节重新解析，调用方不能直接提交 ``tradingDates`` 冒充结果。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import json
import re
from typing import Iterable, Literal, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

SSE_CALENDAR_URL = "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
SSE_SOURCE_NAME = "上海证券交易所"
_SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))
_MAX_OFFICIAL_CLOSURE_DAYS = 31


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("formal_shadow_calendar_json_duplicate_key")
        value[key] = item
    return value


def _reject_json_constant(_value: str):
    raise ValueError("formal_shadow_calendar_json_non_finite")


class _CalendarModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class OfficialSseCalendarInput(_CalendarModel):
    contract_version: Literal["radar-formal-shadow-calendar-input-v1"] = Field(
        alias="contractVersion",
    )
    market: Literal["cn"]
    source_name: Literal["上海证券交易所"] = Field(alias="sourceName")
    source_url: Literal[
        "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
    ] = Field(alias="sourceUrl")
    year: StrictInt = Field(ge=2000, le=2100)
    fetched_at: datetime = Field(alias="fetchedAt")
    observed_through: date = Field(alias="observedThrough")
    source_document_sha256: str = Field(
        alias="sourceDocumentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("fetched_at")
    @classmethod
    def require_aware_fetched_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("formal_shadow_calendar_fetchedAt_timezone_required")
        return value

    @model_validator(mode="after")
    def validate_year_and_cutoff(self) -> "OfficialSseCalendarInput":
        if self.observed_through.year != self.year:
            raise ValueError("formal_shadow_calendar_observedThrough_year_mismatch")
        if self.fetched_at.astimezone(_SHANGHAI_TIMEZONE).date() != self.observed_through:
            raise ValueError("formal_shadow_calendar_cutoff_fetchedAt_mismatch")
        return self


class FormalShadowCalendarEvidence(_CalendarModel):
    contract_version: Literal["radar-formal-shadow-calendar-evidence-v1"] = Field(
        default="radar-formal-shadow-calendar-evidence-v1",
        alias="contractVersion",
    )
    market: Literal["cn"] = "cn"
    source_name: Literal["上海证券交易所"] = Field(
        default=SSE_SOURCE_NAME,
        alias="sourceName",
    )
    source_url: Literal[
        "https://www.sse.com.cn/disclosure/dealinstruc/closed/"
    ] = Field(default=SSE_CALENDAR_URL, alias="sourceUrl")
    year: StrictInt = Field(ge=2000, le=2100)
    fetched_at: datetime = Field(alias="fetchedAt")
    observed_through: date = Field(alias="observedThrough")
    source_document_sha256: str = Field(
        alias="sourceDocumentSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    closed_days: Tuple[date, ...] = Field(alias="closedDays", min_length=1)

    @field_validator("fetched_at")
    @classmethod
    def require_aware_fetched_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("formal_shadow_calendar_fetchedAt_timezone_required")
        return value

    @field_validator("closed_days")
    @classmethod
    def validate_closed_days(cls, value: Tuple[date, ...]) -> Tuple[date, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("formal_shadow_calendar_closed_days_invalid")
        return value

    @model_validator(mode="after")
    def validate_year_and_cutoff(self) -> "FormalShadowCalendarEvidence":
        if self.observed_through.year != self.year or any(
            item.year != self.year for item in self.closed_days
        ):
            raise ValueError("formal_shadow_calendar_year_mismatch")
        if self.fetched_at.astimezone(_SHANGHAI_TIMEZONE).date() != self.observed_through:
            raise ValueError("formal_shadow_calendar_cutoff_fetchedAt_mismatch")
        return self


def _decode_official_document(raw: bytes) -> str:
    declared = re.search(
        br"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)",
        raw[:4096],
        flags=re.IGNORECASE,
    )
    candidates = []
    if declared:
        candidates.append(declared.group(1).decode("ascii").lower())
    candidates.extend(("utf-8-sig", "gb18030"))
    for encoding in dict.fromkeys(candidates):
        if encoding not in {"utf-8", "utf-8-sig", "gb18030", "gbk"}:
            continue
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("formal_shadow_calendar_document_invalid")


def _official_closed_days(page_text: str, year: int) -> Tuple[date, ...]:
    title_pattern = rf"<strong\b[^>]*>\s*{year}年休市安排\s*</strong>"
    titles = re.findall(
        title_pattern,
        page_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    sections = re.findall(
        rf"{title_pattern}.*?</table>",
        page_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if len(titles) != 1 or len(sections) != 1:
        raise ValueError("formal_shadow_calendar_document_invalid")
    closed = set()
    recognized_rows = 0
    interval = re.compile(
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日.*?至\s*"
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日.*?休市"
    )
    single = re.compile(
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日.*?休市"
    )
    for raw_row in re.findall(
        r"<tr\b[^>]*>.*?</tr>",
        sections[0],
        flags=re.DOTALL | re.IGNORECASE,
    ):
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw_row))
        text = re.sub(r"\s+", "", text)
        if "休市" not in text:
            continue
        if (
            "休市安排" in text
            and re.search(r"\d{1,2}月\d{1,2}日", text) is None
        ):
            continue
        matched = interval.search(text)
        try:
            if matched is not None:
                start_year = int(matched.group(1) or year)
                start = date(
                    start_year,
                    int(matched.group(2)),
                    int(matched.group(3)),
                )
                end_year = (
                    int(matched.group(4))
                    if matched.group(4)
                    else start_year + (
                        1 if int(matched.group(5)) < start.month else 0
                    )
                )
                end = date(
                    end_year,
                    int(matched.group(5)),
                    int(matched.group(6)),
                )
                if end < start:
                    raise ValueError
                if start.year != end.year and end.year != start.year + 1:
                    raise ValueError
                if year not in {start.year, end.year}:
                    raise ValueError
                if (end - start).days + 1 > _MAX_OFFICIAL_CLOSURE_DAYS:
                    raise ValueError
                cursor = start
                contributed = False
                while cursor <= end:
                    if cursor.year == year:
                        closed.add(cursor)
                        contributed = True
                    cursor += timedelta(days=1)
                if not contributed:
                    raise ValueError
            else:
                matched = single.search(text)
                if matched is None:
                    raise ValueError
                item = date(
                    int(matched.group(1) or year),
                    int(matched.group(2)),
                    int(matched.group(3)),
                )
                if item.year != year:
                    raise ValueError
                closed.add(item)
        except (TypeError, ValueError):
            raise ValueError("formal_shadow_calendar_document_invalid") from None
        recognized_rows += 1
    if recognized_rows == 0 or not closed:
        raise ValueError("formal_shadow_calendar_document_invalid")
    return tuple(sorted(closed))


def verify_official_sse_calendar_document(
    evidence: FormalShadowCalendarEvidence,
    source_document_bytes: bytes,
) -> FormalShadowCalendarEvidence:
    """以已存 evidence 重新校验原文哈希和严格派生结果，供内容仓重放。"""

    if not isinstance(evidence, FormalShadowCalendarEvidence):
        raise ValueError("formal_shadow_calendar_evidence_invalid")
    evidence = FormalShadowCalendarEvidence.model_validate(
        evidence.model_dump(mode="json", by_alias=True)
    )
    if type(source_document_bytes) is not bytes:
        raise ValueError("formal_shadow_calendar_document_bytes_required")
    if hashlib.sha256(source_document_bytes).hexdigest() != evidence.source_document_sha256:
        raise ValueError("formal_shadow_calendar_document_hash_mismatch")
    derived = _official_closed_days(
        _decode_official_document(source_document_bytes),
        evidence.year,
    )
    if derived != evidence.closed_days:
        raise ValueError("formal_shadow_calendar_closed_days_mismatch")
    return evidence


def load_official_sse_calendar_evidence(
    envelope_json_bytes: bytes,
    source_document_bytes: bytes,
) -> FormalShadowCalendarEvidence:
    """校验封套、原文字节哈希并从原文派生休市日。"""

    if type(envelope_json_bytes) is not bytes:
        raise ValueError("formal_shadow_calendar_input_bytes_required")
    if type(source_document_bytes) is not bytes:
        raise ValueError("formal_shadow_calendar_document_bytes_required")
    try:
        payload = json.loads(
            envelope_json_bytes,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict):
            raise TypeError
        envelope = OfficialSseCalendarInput.model_validate(payload)
    except Exception as error:
        raise ValueError("formal_shadow_calendar_input_invalid") from error
    actual_sha256 = hashlib.sha256(source_document_bytes).hexdigest()
    if actual_sha256 != envelope.source_document_sha256:
        raise ValueError("formal_shadow_calendar_document_hash_mismatch")
    try:
        closed_days = _official_closed_days(
            _decode_official_document(source_document_bytes),
            envelope.year,
        )
    except Exception as error:
        raise ValueError("formal_shadow_calendar_document_invalid") from error
    evidence = FormalShadowCalendarEvidence(
        year=envelope.year,
        fetchedAt=envelope.fetched_at,
        observedThrough=envelope.observed_through,
        sourceDocumentSha256=actual_sha256,
        closedDays=closed_days,
    )
    return verify_official_sse_calendar_document(evidence, source_document_bytes)


class OfficialSseCalendarProvider:
    """由已验证官方证据提供交易日真值；缺年份或超过截止日返回未知。"""

    def __init__(self, evidence: Iterable[FormalShadowCalendarEvidence]) -> None:
        by_year = {}
        for raw in evidence:
            item = FormalShadowCalendarEvidence.model_validate(
                raw.model_dump(mode="json", by_alias=True)
            )
            prior = by_year.get(item.year)
            if prior is not None and prior != item:
                raise ValueError("formal_shadow_calendar_year_conflict")
            by_year[item.year] = item
        self._by_year = by_year

    @property
    def evidence(self) -> Tuple[FormalShadowCalendarEvidence, ...]:
        return tuple(self._by_year[year] for year in sorted(self._by_year))

    def is_trading_day(self, value: date) -> Optional[bool]:
        if not isinstance(value, date):
            raise ValueError("formal_shadow_calendar_date_invalid")
        if value.weekday() >= 5:
            return False
        item = self._by_year.get(value.year)
        if item is None or value > item.observed_through:
            return None
        return value not in item.closed_days


def merge_calendar_evidence(
    existing: Iterable[FormalShadowCalendarEvidence],
    current: FormalShadowCalendarEvidence,
) -> Tuple[FormalShadowCalendarEvidence, ...]:
    """同年新原文只可在休市派生结果一致时推进截止日。"""

    by_year = {item.year: item for item in existing}
    prior = by_year.get(current.year)
    if prior is not None:
        if prior.closed_days != current.closed_days:
            raise ValueError("formal_shadow_calendar_year_conflict")
        if current.observed_through < prior.observed_through:
            raise ValueError("formal_shadow_calendar_cutoff_regression")
        if current.observed_through == prior.observed_through:
            if current.source_document_sha256 != prior.source_document_sha256:
                raise ValueError("formal_shadow_calendar_same_cutoff_conflict")
            # 同一官方原文在同一截止日再次抓取只改变抓取时间，
            # 保留首份证据使已发布台账可幂等重放。
            by_year[current.year] = prior
            return tuple(by_year[year] for year in sorted(by_year))
    by_year[current.year] = current
    return tuple(by_year[year] for year in sorted(by_year))
