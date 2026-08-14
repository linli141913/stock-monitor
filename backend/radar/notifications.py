from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

import alert_repository
import notification_service


@dataclass(frozen=True)
class RadarStateChange:
    scope_type: str
    scope_id: str
    scope_name: str
    radar_run_id: str
    as_of: datetime
    from_state: str
    to_state: str
    action: str
    rule_version: str
    formal_usable: bool
    shadow_mode: bool
    first_rejection_reason: Optional[str] = None

    def __post_init__(self):
        if self.scope_type not in {"sector", "etf", "leader"}:
            raise ValueError("unsupported_radar_notification_scope")
        for field_name in (
            "scope_id",
            "scope_name",
            "radar_run_id",
            "from_state",
            "to_state",
            "action",
            "rule_version",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name}_required")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("radar_notification_as_of_timezone_required")


@dataclass(frozen=True)
class RadarNotificationResult:
    status: str
    alert: Optional[Dict] = None
    reason: Optional[str] = None


def _event(change: RadarStateChange) -> Dict:
    if change.scope_type == "leader":
        event_type = f"radar_leader_{change.to_state}"
        priority = "P2" if change.to_state in {"candidate", "confirmed"} else "P3"
        direction = "positive" if change.action in {"enter", "upgrade"} else "negative"
        title = f"{change.scope_name}龙头梯队状态变化：{change.to_state}"
    elif change.scope_type == "sector":
        event_type = f"radar_sector_{change.to_state}"
        priority = "P3"
        direction = "negative" if change.to_state in {"retreat", "invalid"} else "positive"
        title = f"{change.scope_name}行业主线状态变化：{change.to_state}"
    else:
        event_type = f"radar_etf_{change.to_state}"
        priority = "P3"
        direction = "negative" if change.action in {"downgrade", "remove"} else "positive"
        title = f"{change.scope_name}ETF观察状态变化：{change.to_state}"
    reason = (
        f"；首次否决原因：{change.first_rejection_reason}"
        if change.first_rejection_reason
        else ""
    )
    source_event_id = "|".join((
        change.scope_type,
        change.scope_id,
        change.from_state,
        change.to_state,
        change.radar_run_id,
        change.rule_version,
    ))
    return {
        "symbol": change.scope_id,
        "stock_name": change.scope_name,
        "event_type": event_type,
        "direction": direction,
        "priority": priority,
        "evidence_level": "RULE",
        "title": title,
        "summary": (
            f"确定性雷达状态由{change.from_state}变为{change.to_state}，"
            f"规则版本{change.rule_version}，数据时间"
            f"{change.as_of.isoformat(timespec='seconds')}{reason}。"
            "这是规则状态提醒，不是投资建议。"
        ),
        "source": "mainline_radar",
        "source_url": None,
        "source_event_id": source_event_id,
        "published_at": change.as_of.isoformat(timespec="seconds"),
    }


def process_radar_state_change(
    change: RadarStateChange,
) -> RadarNotificationResult:
    if change.shadow_mode:
        return RadarNotificationResult("suppressed", reason="shadow_suppressed")
    if not change.formal_usable:
        return RadarNotificationResult("suppressed", reason="formal_state_required")
    if change.from_state == change.to_state or change.action == "hold":
        return RadarNotificationResult("suppressed", reason="state_unchanged")
    preferences = alert_repository.get_radar_notification_preferences()
    if not preferences["siteEnabled"]:
        return RadarNotificationResult("suppressed", reason="site_notification_disabled")
    alert, created = alert_repository.save_alert_event(_event(change))
    if not created:
        return RadarNotificationResult("duplicate", alert=alert)
    notification_service.process_new_alert(
        alert,
        email_enabled=preferences["emailEnabled"],
        p2_email=preferences["p2Email"],
        p3_email=preferences["p3Email"],
        trigger_old_ai=False,
    )
    return RadarNotificationResult("created", alert=alert)
