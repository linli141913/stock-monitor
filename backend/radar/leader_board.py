"""阶段6F三级龙头只读榜单投影。

本模块只把一轮全量候选快照投影为每级最多5只的展示榜单。溢出项仍保留
在候选快照和状态历史中；本模块不改写状态、不连接数据库，也不启用正式
业务结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from radar.leader_state_machine import (
    BusinessExposureStatus,
    LeaderDataStatus,
    LeaderState,
)


DEFAULT_MAX_LEADERS_PER_STATE = 5
BOARD_STATES = (
    LeaderState.CONFIRMED,
    LeaderState.CANDIDATE,
    LeaderState.PRELIMINARY,
)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _required_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name}必须是对象")
    return value


@dataclass(frozen=True)
class LeaderBoardEntry:
    symbol: str
    name: str
    state: LeaderState
    score: float
    business_exposure_status: BusinessExposureStatus
    data_status: LeaderDataStatus
    first_rejection_reason: Optional[str]
    reasons: Tuple[str, ...]
    evidence: Mapping[str, Any]
    invalidation: Mapping[str, Any]
    state_age_periods: int
    formal_usable: bool
    industry_code: Optional[str] = None
    industry_name: Optional[str] = None

    def __post_init__(self):
        if len(self.symbol) != 6 or not self.symbol.isdigit():
            raise ValueError("symbol必须是6位数字")
        _required_text(self.name, "name")
        if self.state not in BOARD_STATES:
            raise ValueError("榜单条目只能是预备、候选或已确认状态")
        if not 0 <= self.score <= 100:
            raise ValueError("榜单分数必须在0到100之间")
        if self.state_age_periods < 0:
            raise ValueError("状态保持周期不能小于0")
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("榜单原因不得重复")


@dataclass(frozen=True)
class LeaderBoardProjection:
    radar_run_id: str
    as_of: datetime
    rule_version: str
    quality: str
    formal_usable: bool
    preliminary: Tuple[LeaderBoardEntry, ...]
    candidate: Tuple[LeaderBoardEntry, ...]
    confirmed: Tuple[LeaderBoardEntry, ...]
    overflow_counts: Tuple[Tuple[LeaderState, int], ...]

    def entries_for(
        self,
        state: LeaderState,
    ) -> Tuple[LeaderBoardEntry, ...]:
        return {
            LeaderState.PRELIMINARY: self.preliminary,
            LeaderState.CANDIDATE: self.candidate,
            LeaderState.CONFIRMED: self.confirmed,
        }.get(state, ())

    def overflow_count(self, state: LeaderState) -> int:
        return dict(self.overflow_counts).get(state, 0)


def _entry_from_mapping(payload: Mapping[str, Any]) -> LeaderBoardEntry:
    score = payload.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError("榜单条目score必须是数字")
    reasons = payload.get("reasons", ())
    if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)):
        raise TypeError("榜单条目reasons必须是数组")
    reason_values = tuple(
        _required_text(reason, "reason")
        for reason in reasons
    )
    formal_usable = payload.get("formalUsable")
    if not isinstance(formal_usable, bool):
        raise TypeError("榜单条目formalUsable必须是布尔值")
    state_age_periods = payload.get("stateAgePeriods")
    if isinstance(state_age_periods, bool) or not isinstance(
        state_age_periods,
        int,
    ):
        raise TypeError("榜单条目stateAgePeriods必须是整数")
    first_rejection_reason = payload.get("firstRejectionReason")
    if first_rejection_reason is not None:
        first_rejection_reason = _required_text(
            first_rejection_reason,
            "firstRejectionReason",
        )
    return LeaderBoardEntry(
        symbol=_required_text(payload.get("symbol"), "symbol"),
        name=_required_text(payload.get("name"), "name"),
        industry_code=payload.get("industryCode"),
        industry_name=payload.get("industryName"),
        state=LeaderState(payload.get("state")),
        score=float(score),
        business_exposure_status=BusinessExposureStatus(
            payload.get("businessExposureStatus")
        ),
        data_status=LeaderDataStatus(payload.get("dataStatus")),
        first_rejection_reason=first_rejection_reason,
        reasons=reason_values,
        evidence=dict(
            _required_mapping(payload.get("evidence"), "evidence")
        ),
        invalidation=dict(
            _required_mapping(
                payload.get("invalidation"),
                "invalidation",
            )
        ),
        state_age_periods=state_age_periods,
        formal_usable=formal_usable,
    )


def build_leader_board_projection(
    snapshot: Mapping[str, Any],
    *,
    maximum_per_state: int = DEFAULT_MAX_LEADERS_PER_STATE,
) -> LeaderBoardProjection:
    """按分数降序、代码升序稳定投影每级榜单。"""

    if isinstance(maximum_per_state, bool) or not isinstance(
        maximum_per_state,
        int,
    ):
        raise TypeError("maximum_per_state必须是整数")
    if maximum_per_state < 1:
        raise ValueError("maximum_per_state必须至少为1")
    snapshot = _required_mapping(snapshot, "snapshot")
    as_of = snapshot.get("asOf")
    if not isinstance(as_of, datetime):
        raise TypeError("snapshot.asOf必须是datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("snapshot.asOf必须包含时区")
    formal_usable = snapshot.get("formalUsable")
    if not isinstance(formal_usable, bool):
        raise TypeError("snapshot.formalUsable必须是布尔值")
    raw_entries = snapshot.get("entries")
    if not isinstance(raw_entries, Sequence) or isinstance(
        raw_entries,
        (str, bytes),
    ):
        raise TypeError("snapshot.entries必须是数组")

    entries_by_state: Dict[LeaderState, list[LeaderBoardEntry]] = {
        state: []
        for state in BOARD_STATES
    }
    symbols = set()
    for raw_entry in raw_entries:
        entry_payload = _required_mapping(raw_entry, "entry")
        symbol = _required_text(entry_payload.get("symbol"), "symbol")
        if symbol in symbols:
            raise ValueError(f"榜单快照包含重复证券：{symbol}")
        symbols.add(symbol)
        state = LeaderState(entry_payload.get("state"))
        if state == LeaderState.OUT:
            continue
        entry = _entry_from_mapping(entry_payload)
        entries_by_state[state].append(entry)

    selected: Dict[LeaderState, Tuple[LeaderBoardEntry, ...]] = {}
    overflow_counts = []
    for state in BOARD_STATES:
        ordered = sorted(
            entries_by_state[state],
            key=lambda entry: (-entry.score, entry.symbol),
        )
        selected[state] = tuple(ordered[:maximum_per_state])
        overflow_counts.append((
            state,
            max(0, len(ordered) - maximum_per_state),
        ))

    return LeaderBoardProjection(
        radar_run_id=_required_text(
            snapshot.get("radarRunId"),
            "radarRunId",
        ),
        as_of=as_of,
        rule_version=_required_text(
            snapshot.get("ruleVersion"),
            "ruleVersion",
        ),
        quality=_required_text(snapshot.get("quality"), "quality"),
        formal_usable=formal_usable,
        preliminary=selected[LeaderState.PRELIMINARY],
        candidate=selected[LeaderState.CANDIDATE],
        confirmed=selected[LeaderState.CONFIRMED],
        overflow_counts=tuple(overflow_counts),
    )
