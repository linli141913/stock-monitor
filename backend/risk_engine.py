from statistics import median
from typing import Any, Dict, List, Optional


_ALERT_RULE_LABELS = {
    "limit_move": "极端涨跌区间",
    "extreme_price_move": "涨跌幅≥5%",
    "high_amplitude": "振幅≥8%",
    "high_volume_ratio": "量比≥2",
    "turnover_warning": "换手率警惕",
    "consecutive_fund_inflow": "连续资金净流入",
    "consecutive_fund_outflow": "连续资金净流出",
    "price_fund_divergence": "价格与资金背离",
    "ma_breakdown": "均线破位",
}

_LINKAGE_RULE_LABELS = {
    "sector_decline": "板块跌幅≥3%",
    "sector_breadth_weak": "板块上涨家数占比<20%",
    "sector_leader_decline": "板块龙头跌幅≥8%或触及跌停",
    "sector_fund_inflow_top": "板块资金净流入前5",
    "sector_fund_outflow_top": "板块资金净流出前5",
    "overseas_index_extreme": "精确映射海外指数极端波动",
    "overseas_company_extreme": "精确映射海外核心公司极端波动",
}


def _number(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _market_amount(value) -> Optional[float]:
    direct = _number(value)
    if direct is not None:
        return direct
    text = str(value or "").strip().replace(",", "")
    units = {"亿": 100_000_000, "万": 10_000}
    for suffix, multiplier in units.items():
        if not text.endswith(suffix):
            continue
        number = _number(text[:-1])
        return None if number is None else number * multiplier
    return None


def _amplitude(snapshot: Dict[str, Any]) -> Optional[float]:
    high = _number(snapshot.get("high"))
    low = _number(snapshot.get("low"))
    previous_close = _number(snapshot.get("previous_close"))
    if high is None or low is None or previous_close is None or previous_close <= 0:
        return None
    return round((high - low) / previous_close * 100, 2)


def _turnover_risk(snapshot: Dict[str, Any], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    if snapshot.get("market") != "cn":
        return {
            "status": "unavailable",
            "label": "暂无判断",
            "baseline": None,
            "multiple": None,
            "reason": "港股换手率历史口径尚未验证",
        }

    current_rate = _number(snapshot.get("turnover_rate"))
    if current_rate is None:
        return {
            "status": "unavailable",
            "label": "暂无判断",
            "baseline": None,
            "multiple": None,
            "reason": "当前换手率数据缺失",
        }

    historical_rates = [
        value
        for value in (_number(item.get("turnover_rate")) for item in history)
        if value is not None
    ]
    if len(historical_rates) < 20:
        return {
            "status": "insufficient",
            "label": "样本不足",
            "baseline": None,
            "multiple": None,
            "reason": f"同一时点有效历史仅 {len(historical_rates)} 个交易日，需要 20 个",
        }

    baseline = median(historical_rates[-20:])
    if baseline <= 0:
        return {
            "status": "unavailable",
            "label": "暂无判断",
            "baseline": baseline,
            "multiple": None,
            "reason": "历史换手率基线无效",
        }

    multiple = current_rate / baseline
    rounded_baseline = round(baseline, 4)
    rounded_multiple = round(multiple, 2)
    if multiple < 1.5:
        return {
            "status": "normal",
            "label": "正常",
            "baseline": rounded_baseline,
            "multiple": rounded_multiple,
            "reason": f"当前为近20日同一时点中位数的 {rounded_multiple} 倍",
        }

    amount_values = [
        value
        for value in (_number(item.get("turnover_amount")) for item in history)
        if value is not None
    ]
    current_amount = _number(snapshot.get("turnover_amount"))
    amount_multiple = None
    if len(amount_values) >= 20 and current_amount is not None:
        amount_baseline = median(amount_values[-20:])
        if amount_baseline > 0:
            amount_multiple = current_amount / amount_baseline

    change_percent = _number(snapshot.get("change_percent"))
    volume_ratio = _number(snapshot.get("volume_ratio"))
    amplitude = _amplitude(snapshot)
    auxiliary = (
        (amount_multiple is not None and amount_multiple >= 2)
        or (volume_ratio is not None and volume_ratio >= 2)
        or (amplitude is not None and amplitude >= 8)
        or (change_percent is not None and abs(change_percent) >= 5)
    )

    if multiple >= 2.5 and auxiliary:
        return {
            "status": "warning",
            "label": "警惕",
            "baseline": rounded_baseline,
            "multiple": rounded_multiple,
            "reason": f"当前为近20日同一时点中位数的 {rounded_multiple} 倍，并伴随其他量价异常",
        }
    return {
        "status": "active",
        "label": "活跃",
        "baseline": rounded_baseline,
        "multiple": rounded_multiple,
        "reason": f"当前为近20日同一时点中位数的 {rounded_multiple} 倍",
    }


def _limit_threshold(symbol: str) -> float:
    pure = symbol.lower()
    for prefix in ("sh", "sz", "bj"):
        if pure.startswith(prefix):
            pure = pure[2:]
            break
    if pure.startswith(("30", "68")):
        return 19.8
    if pure.startswith(("4", "8")):
        return 29.8
    return 9.8


def build_exact_overseas_mappings(
    context: Dict[str, Any],
    business_text: str,
) -> List[Dict[str, Any]]:
    industry_name = str(context.get("industry_name") or "").strip()
    business = str(business_text or "").strip()
    mappings = []

    if any(keyword in industry_name for keyword in ("半导体", "集成电路")):
        mappings.append({
            "symbol": "SOXX",
            "query_symbol": "gb_soxx",
            "name": "费城半导体指数",
            "kind": "index",
            "mapping_verified": True,
            "mapping_basis": f"公司官方所属行业为{industry_name}",
        })

    business_rules = (
        (("晶圆代工",), "TSM", "gb_tsm", "台积电", "晶圆代工业务精确匹配"),
        (("GPU", "图形处理器"), "NVDA", "gb_nvda", "英伟达", "GPU业务精确匹配"),
        (("光刻机",), "ASML", "gb_asml", "阿斯麦", "光刻机业务精确匹配"),
        (("DRAM", "NAND", "存储芯片"), "MU", "gb_mu", "美光科技", "存储芯片业务精确匹配"),
    )
    for keywords, symbol, query_symbol, name, basis in business_rules:
        if any(keyword.lower() in business.lower() for keyword in keywords):
            mappings.append({
                "symbol": symbol,
                "query_symbol": query_symbol,
                "name": name,
                "kind": "company",
                "mapping_verified": True,
                "mapping_basis": basis,
            })

    deduped = {}
    for item in mappings:
        deduped[item["symbol"]] = item
    return list(deduped.values())


def merge_verified_market_history(
    fund_rows: List[Dict[str, Any]],
    kline_rows: List[Dict[str, Any]],
    expected_trade_date: str,
) -> List[Dict[str, Any]]:
    def keyed(rows):
        result = {}
        for row in rows:
            trade_date = str(row.get("trade_date") or row.get("time") or "")[:10]
            if trade_date:
                result[trade_date] = dict(row)
        return result

    funds = keyed(fund_rows)
    klines = keyed(kline_rows)
    dates = sorted(klines)
    if not dates or dates[-1] != expected_trade_date:
        return []
    merged = []
    for trade_date in dates:
        fund_row = funds.get(trade_date) or {}
        kline_row = klines[trade_date]
        merged.append({
            **fund_row,
            **kline_row,
            "trade_date": trade_date,
            "fund_flow": _number(fund_row.get("fund_flow")),
            "fund_close": _number(
                fund_row.get("fund_close", fund_row.get("close"))
            ),
            "close": _number(kline_row.get("close")),
            "ma5": _number(kline_row.get("ma5")),
            "ma10": _number(kline_row.get("ma10")),
            "ma20": _number(kline_row.get("ma20")),
        })
    return merged


def build_verified_sector_snapshot(
    industry_name: str,
    sector_rows: List[Dict[str, Any]],
    quote_rows: List[Dict[str, Any]],
    expected_constituents: int,
) -> Dict[str, Any]:
    normalized_industry = str(industry_name or "").strip()
    exact_matches = [
        row for row in sector_rows
        if str(row.get("行业") or "").strip() == normalized_industry
    ]
    if exact_matches:
        matched = exact_matches[0]
    else:
        fuzzy_matches = [
            row for row in sector_rows
            if normalized_industry
            and (
                normalized_industry in str(row.get("行业") or "").strip()
                or str(row.get("行业") or "").strip() in normalized_industry
            )
        ]
        if len(fuzzy_matches) != 1:
            return {
                "status": "unavailable",
                "name": normalized_industry,
                "change_percent": None,
                "advancers": None,
                "total": None,
                "leader": None,
                "leaders": None,
                "fund_flow": {"verified": False},
                "reason": "行业资金流列表无法唯一匹配所属板块",
            }
        matched = fuzzy_matches[0]

    valid_quotes = [
        row for row in quote_rows
        if _number(row.get("change_percent")) is not None
    ]
    reported_company_count_value = _number(matched.get("公司家数"))
    reported_company_count = (
        int(reported_company_count_value)
        if reported_company_count_value is not None
        and reported_company_count_value > 0
        else None
    )
    quotes_complete = (
        expected_constituents > 1
        and reported_company_count == expected_constituents
        and len(valid_quotes) == expected_constituents
    )
    if expected_constituents <= 0:
        constituent_reason = "板块成分抓取为空，上涨家数和龙头暂无判断"
    elif expected_constituents == 1:
        constituent_reason = "板块成分只有1只，上涨家数和龙头暂无判断"
    elif reported_company_count is None:
        constituent_reason = "行业公司家数缺失，无法确认成分口径一致"
    elif reported_company_count != expected_constituents:
        constituent_reason = (
            f"行业公司家数{reported_company_count}与成分集合"
            f"{expected_constituents}不一致，不跨口径计算上涨家数和龙头"
        )
    elif len(valid_quotes) != expected_constituents:
        constituent_reason = (
            f"成分行情仅返回{len(valid_quotes)}/{expected_constituents}只，"
            "上涨家数和龙头暂无判断"
        )
    else:
        constituent_reason = None
    advancers = None
    total = None
    leader = None
    leaders = None
    if quotes_complete:
        advancers = sum(
            1 for row in valid_quotes
            if _number(row.get("change_percent")) > 0
        )
        total = len(valid_quotes)
        if all(_number(row.get("market_cap")) is not None for row in valid_quotes):
            ranked_quotes = sorted(
                valid_quotes,
                key=lambda row: _number(row.get("market_cap")),
                reverse=True,
            )
            leaders = []
            for rank, row in enumerate(ranked_quotes[:3], start=1):
                leader_change = _number(row.get("change_percent"))
                leader_symbol = str(row.get("symbol") or "")
                leaders.append({
                    "rank": rank,
                    "symbol": leader_symbol,
                    "name": str(row.get("name") or leader_symbol),
                    "market_cap": _number(row.get("market_cap")),
                    "change_percent": leader_change,
                    "is_limit_down": (
                        leader_change is not None
                        and leader_change <= -_limit_threshold(leader_symbol)
                    ),
                })
            leader = dict(leaders[0]) if leaders else None

    flow_value = _market_amount(matched.get("净额"))
    fund_flow = {"verified": False}
    if flow_value is not None:
        direction = "inflow" if flow_value > 0 else (
            "outflow" if flow_value < 0 else "flat"
        )
        same_direction = []
        if direction == "inflow":
            same_direction = sorted(
                [
                    value for value in (_market_amount(row.get("净额")) for row in sector_rows)
                    if value is not None and value > 0
                ],
                reverse=True,
            )
        elif direction == "outflow":
            same_direction = sorted([
                value for value in (_market_amount(row.get("净额")) for row in sector_rows)
                if value is not None and value < 0
            ])
        if direction in {"inflow", "outflow"} and flow_value in same_direction:
            fund_flow = {
                "value": flow_value,
                "direction": direction,
                "rank": same_direction.index(flow_value) + 1,
                "total": len(same_direction),
                "verified": True,
            }

    return {
        "status": "available",
        "name": str(matched.get("行业") or normalized_industry),
        "change_percent": _number(matched.get("行业-涨跌幅")),
        "advancers": advancers,
        "total": total,
        "leader": leader,
        "leaders": leaders,
        "fund_flow": fund_flow,
        "reason": constituent_reason,
    }


def _verified_market_signals(
    verified_history: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    rows = sorted(
        [row for row in verified_history or [] if row.get("trade_date")],
        key=lambda row: str(row["trade_date"]),
    )
    signals = []

    fund_risk = {
        "status": "unavailable",
        "label": "暂无判断",
        "reason": "真实历史资金数据不足3个交易日",
    }
    def has_complete_fund_data(row: Dict[str, Any]) -> bool:
        return (
            _number(row.get("fund_flow")) is not None
            and _number(row.get("fund_close", row.get("close"))) is not None
        )

    latest_complete_index = next(
        (
            index for index in range(len(rows) - 1, -1, -1)
            if has_complete_fund_data(rows[index])
        ),
        None,
    )
    if latest_complete_index is not None:
        candidate_rows = rows[:latest_complete_index + 1]
        recent = candidate_rows[-3:]
        if len(recent) < 3 or not all(has_complete_fund_data(row) for row in recent):
            fund_risk["reason"] = "最近3个交易日资金或收盘价存在缺失，暂无判断"
            recent = []
    else:
        recent = []
    if len(recent) == 3:
        flows = [_number(row.get("fund_flow")) for row in recent]
        closes = [
            _number(row.get("fund_close", row.get("close")))
            for row in recent
        ]
        if all(value is not None for value in flows + closes) and closes[0] > 0:
            fund_risk = {
                "status": "no_signal",
                "label": "未触发",
                "reason": "近3个交易日资金与收盘价数据完整，未触发连续或背离规则",
            }
            if all(value > 0 for value in flows):
                signals.append({
                    "code": "consecutive_fund_inflow",
                    "label": "连续3个交易日主力资金净流入",
                })
            elif all(value < 0 for value in flows):
                signals.append({
                    "code": "consecutive_fund_outflow",
                    "label": "连续3个交易日主力资金净流出",
                })

            price_change = (closes[-1] - closes[0]) / closes[0] * 100
            total_flow = sum(flows)
            if (price_change >= 3 and total_flow < 0) or (
                price_change <= -3 and total_flow > 0
            ):
                signals.append({
                    "code": "price_fund_divergence",
                    "label": "近3日价格方向与主力资金净流方向背离",
                })
            fund_codes = {
                "consecutive_fund_inflow",
                "consecutive_fund_outflow",
                "price_fund_divergence",
            }
            triggered = [item for item in signals if item["code"] in fund_codes]
            if triggered:
                fund_risk = {
                    "status": "triggered",
                    "label": "已触发",
                    "reason": "；".join(item["label"] for item in triggered),
                }
        else:
            fund_risk["reason"] = "近3个交易日资金或收盘价存在缺失，暂无判断"

    moving_average_risk = {
        "status": "unavailable",
        "label": "暂无判断",
        "periods": [],
        "reason": "均线交叉数据不足，暂无判断",
    }
    if len(rows) >= 2:
        previous, current = rows[-2:]
        previous_close = _number(previous.get("close"))
        current_close = _number(current.get("close"))
        comparable = []
        broken = []
        for field, label in (("ma5", "MA5"), ("ma10", "MA10"), ("ma20", "MA20")):
            previous_ma = _number(previous.get(field))
            current_ma = _number(current.get(field))
            if None in {previous_close, current_close, previous_ma, current_ma}:
                continue
            comparable.append(label)
            if previous_close >= previous_ma and current_close < current_ma:
                broken.append(label)
        if comparable:
            if broken:
                signals.append({
                    "code": "ma_breakdown",
                    "label": f"收盘价由上方跌破{'/'.join(broken)}",
                })
                moving_average_risk = {
                    "status": "triggered",
                    "label": "已触发",
                    "periods": broken,
                    "reason": f"连续两个交易日可验证数据确认跌破{'/'.join(broken)}",
                }
            else:
                moving_average_risk = {
                    "status": "no_signal",
                    "label": "未触发",
                    "periods": [],
                    "reason": "可比均线未出现由上向下破位",
                }

    return {
        "signals": signals,
        "fundFlowRisk": fund_risk,
        "movingAverageRisk": moving_average_risk,
    }


def evaluate_market_risk(
    snapshot: Dict[str, Any],
    history: List[Dict[str, Any]],
    verified_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    turnover_risk = _turnover_risk(snapshot, history)
    signals = []
    change_percent = _number(snapshot.get("change_percent"))
    volume_ratio = _number(snapshot.get("volume_ratio"))
    amplitude = _amplitude(snapshot)
    symbol = str(snapshot.get("symbol") or "")

    is_limit_move = (
        change_percent is not None
        and abs(change_percent) >= _limit_threshold(symbol)
    )
    if is_limit_move:
        signals.append({
            "code": "limit_move",
            "label": "股价达到极端涨跌区间",
        })
    elif change_percent is not None and abs(change_percent) >= 5:
        signals.append({
            "code": "extreme_price_move",
            "label": "单日涨跌幅绝对值不低于5%",
        })

    if amplitude is not None and amplitude >= 8:
        signals.append({
            "code": "high_amplitude",
            "label": "当日振幅不低于8%",
        })
    if volume_ratio is not None and volume_ratio >= 2:
        signals.append({
            "code": "high_volume_ratio",
            "label": "量比不低于2",
        })
    if turnover_risk["status"] == "warning":
        signals.append({
            "code": "turnover_warning",
            "label": "换手率相对自身常态达到警惕",
        })

    verified = _verified_market_signals(verified_history)
    signals.extend(verified["signals"])

    if is_limit_move:
        priority = "P1"
        risk_status = "critical"
    elif len(signals) >= 2:
        priority = "P2"
        risk_status = "warning"
    elif len(signals) == 1:
        priority = "P3"
        risk_status = "watch"
    else:
        priority = None
        risk_status = "normal"

    signal_codes = {item["code"] for item in signals}
    if signal_codes & {
        "consecutive_fund_outflow",
        "price_fund_divergence",
        "ma_breakdown",
    }:
        direction = "negative"
    elif "consecutive_fund_inflow" in signal_codes:
        direction = "positive"
    elif change_percent is None or change_percent == 0:
        direction = "neutral"
    else:
        direction = "positive" if change_percent > 0 else "negative"

    return {
        "riskStatus": risk_status,
        "priority": priority,
        "direction": direction,
        "signals": signals,
        "turnoverRisk": turnover_risk,
        "fundFlowRisk": verified["fundFlowRisk"],
        "movingAverageRisk": verified["movingAverageRisk"],
        "reason": "；".join(item["label"] for item in signals) if signals else "当前未触发量价风险规则",
        "dataComplete": all(
            snapshot.get(field) is not None
            for field in ("change_percent", "high", "low", "previous_close")
        ),
    }


def evaluate_linkage_risk(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    signals = []
    sector = snapshot.get("sector") or {}
    constituent_reason = str(sector.get("reason") or "").strip()
    sector_dimensions = {
        "decline": {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": "板块涨跌幅数据缺失或口径未验证",
        },
        "breadth": {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": constituent_reason or "板块成分行情未完整返回，上涨家数暂无判断",
        },
        "leader": {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": constituent_reason or "板块成分行情或市值数据不完整，龙头暂无判断",
        },
        "fundFlow": {
            "status": "unavailable",
            "label": "暂无判断",
            "reason": "板块资金流排名缺失或口径未验证",
        },
    }
    if sector.get("status") == "available":
        change_percent = _number(sector.get("change_percent"))
        if change_percent is not None:
            decline_triggered = change_percent <= -3
            decline_reason = (
                f"{sector.get('name') or '所属板块'}下跌 {abs(change_percent):.2f}%"
                if decline_triggered
                else f"板块涨跌幅 {change_percent:+.2f}%，未达到下跌3%阈值"
            )
            sector_dimensions["decline"] = {
                "status": "triggered" if decline_triggered else "no_signal",
                "label": "已触发" if decline_triggered else "未触发",
                "reason": decline_reason,
                "details": {
                    "changePercent": change_percent,
                    "triggerThreshold": -3.0,
                },
            }
            if decline_triggered:
                signals.append({
                    "code": "sector_decline",
                    "label": decline_reason,
                    "direction": "negative",
                })

        advancers = _number(sector.get("advancers"))
        total = _number(sector.get("total"))
        if advancers is not None and total is not None and total > 0:
            ratio = advancers / total
            breadth_triggered = ratio < 0.2
            breadth_reason = (
                f"板块上涨家数占比 {ratio * 100:.1f}%"
                if breadth_triggered
                else f"板块上涨家数占比 {ratio * 100:.1f}%，未低于20%"
            )
            sector_dimensions["breadth"] = {
                "status": "triggered" if breadth_triggered else "no_signal",
                "label": "已触发" if breadth_triggered else "未触发",
                "reason": breadth_reason,
                "details": {
                    "advancers": int(advancers),
                    "total": int(total),
                    "ratioPercent": round(ratio * 100, 1),
                    "triggerThresholdPercent": 20.0,
                },
            }
            if breadth_triggered:
                signals.append({
                    "code": "sector_breadth_weak",
                    "label": breadth_reason,
                    "direction": "negative",
                })

        leader = sector.get("leader") or {}
        leader_change = _number(leader.get("change_percent"))
        if leader_change is not None or leader.get("is_limit_down") is True:
            leader_triggered = leader.get("is_limit_down") is True or (
                leader_change is not None and leader_change <= -8
            )
            if leader_triggered:
                leader_reason = (
                    f"板块龙头{leader.get('name') or leader.get('symbol') or ''}"
                    f"下跌 {abs(leader_change):.2f}%"
                    if leader_change is not None
                    else "板块龙头触及跌停"
                )
            else:
                leader_reason = (
                    f"板块龙头{leader.get('name') or leader.get('symbol') or ''}"
                    f"涨跌幅 {leader_change:+.2f}%，未达到下跌8%或跌停阈值"
                )
            sector_dimensions["leader"] = {
                "status": "triggered" if leader_triggered else "no_signal",
                "label": "已触发" if leader_triggered else "未触发",
                "reason": leader_reason,
                "details": {
                    "selectionMethod": "按本轮完整成分行情中的市值从高到低排序",
                    "triggerThresholdPercent": -8.0,
                    "leaders": sector.get("leaders") or [leader],
                },
            }
            if leader_triggered:
                signals.append({
                    "code": "sector_leader_decline",
                    "label": leader_reason,
                    "direction": "negative",
                })

        fund_flow = sector.get("fund_flow") or {}
        rank = _number(fund_flow.get("rank"))
        rank_total = _number(fund_flow.get("total"))
        direction = str(fund_flow.get("direction") or "")
        if (
            fund_flow.get("verified") is True
            and rank is not None
            and rank_total is not None
            and rank_total >= rank > 0
            and direction in {"inflow", "outflow"}
        ):
            flow_triggered = rank <= 5
            flow_label = f"板块资金净{'流入' if direction == 'inflow' else '流出'}排名第 {int(rank)}"
            sector_dimensions["fundFlow"] = {
                "status": "triggered" if flow_triggered else "no_signal",
                "label": "已触发" if flow_triggered else "未触发",
                "reason": flow_label if flow_triggered else f"{flow_label}，未进入前5",
                "details": {
                    "direction": direction,
                    "value": fund_flow.get("value"),
                    "rank": int(rank),
                    "total": int(rank_total),
                    "triggerRank": 5,
                },
            }
            if flow_triggered:
                flow_direction = "positive" if direction == "inflow" else "negative"
                signals.append({
                    "code": f"sector_fund_{direction}_top",
                    "label": flow_label,
                    "direction": flow_direction,
                })

    sector_evaluated = any(
        item["status"] != "unavailable"
        for item in sector_dimensions.values()
    )
    sector_complete = all(
        item["status"] != "unavailable"
        for item in sector_dimensions.values()
    )
    sector_signals = [item for item in signals if item["code"].startswith("sector_")]
    sector_risk = {
        "status": "triggered" if sector_signals else ("no_signal" if sector_evaluated else "unavailable"),
        "label": "已触发" if sector_signals else ("未触发" if sector_evaluated else "暂无判断"),
        "reason": (
            "；".join(item["label"] for item in sector_signals)
            if sector_signals
            else ("已验证板块字段未触发固定阈值" if sector_evaluated else "板块数据缺失或口径未验证")
        ),
        "dataComplete": sector_complete,
        "dimensions": sector_dimensions,
    }

    overseas_evaluated = False
    for item in snapshot.get("overseas") or []:
        if item.get("mapping_verified") is not True or not str(
            item.get("mapping_basis") or ""
        ).strip():
            continue
        change_percent = _number(item.get("change_percent"))
        kind = str(item.get("kind") or "")
        if change_percent is None or kind not in {"index", "company"}:
            continue
        overseas_evaluated = True
        threshold = 3 if kind == "index" else 8
        if abs(change_percent) < threshold:
            continue
        signals.append({
            "code": f"overseas_{kind}_extreme",
            "label": f"{item.get('name') or item.get('symbol')}波动 {change_percent:+.2f}%",
            "direction": "positive" if change_percent > 0 else "negative",
        })

    overseas_signals = [item for item in signals if item["code"].startswith("overseas_")]
    overseas_risk = {
        "status": "triggered" if overseas_signals else ("no_signal" if overseas_evaluated else "unavailable"),
        "label": "已触发" if overseas_signals else ("未触发" if overseas_evaluated else "暂无判断"),
        "reason": (
            "；".join(item["label"] for item in overseas_signals)
            if overseas_signals
            else (
                "已精确映射的海外标的未达到极端波动阈值"
                if overseas_evaluated
                else "没有经过业务精确映射且带真实行情的海外标的"
            )
        ),
    }

    priority = "P2" if len(signals) >= 2 else ("P3" if signals else None)
    directions = {item.get("direction") for item in signals}
    direction = "negative" if "negative" in directions else (
        "positive" if "positive" in directions else "neutral"
    )
    return {
        "riskStatus": "warning" if priority == "P2" else ("watch" if priority else "normal"),
        "priority": priority,
        "direction": direction,
        "signals": signals,
        "sectorRisk": sector_risk,
        "overseasRisk": overseas_risk,
        "reason": "；".join(item["label"] for item in signals) if signals else "板块与海外联动暂无触发信号",
        "dataComplete": sector_complete or overseas_evaluated,
    }


def build_linkage_alert_event(
    snapshot: Dict[str, Any],
    result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    priority = result.get("priority")
    if priority is None:
        return None
    source_time = str(snapshot.get("source_time") or "")
    trade_date = source_time[:10] if len(source_time) >= 10 else "unknown-date"
    signal_key = ",".join(sorted(item["code"] for item in result["signals"]))
    rule_text = "、".join(
        _LINKAGE_RULE_LABELS.get(item["code"], item["label"])
        for item in result["signals"]
    )
    level_text = f"{priority}强提醒" if priority == "P2" else f"{priority}观察提醒"
    stock_name = snapshot.get("stock_name") or snapshot["symbol"]
    return {
        "symbol": snapshot["symbol"],
        "stock_name": stock_name,
        "event_type": "linkage_risk",
        "direction": result["direction"],
        "priority": priority,
        "evidence_level": "A" if priority == "P2" else "B",
        "title": f"{stock_name}触发{level_text}：{rule_text}",
        "summary": (
            f"固定板块与海外联动规则触发：{result['reason']}。"
            "海外标的仅使用已记录业务映射的真实行情；缺失口径不参与判断。"
        ),
        "source": "板块与海外联动规则",
        "source_url": None,
        "source_event_id": f"linkage:{trade_date}:{signal_key}",
        "published_at": snapshot.get("source_time"),
    }


def _prepare_episode_alert(
    event: Dict[str, Any],
    snapshot: Dict[str, Any],
    risk_kind: str,
) -> Optional[Dict[str, Any]]:
    import alert_repository

    source_time = str(snapshot.get("source_time") or "")
    if not source_time:
        return event
    recent_states = alert_repository.get_recent_risk_states(
        snapshot["symbol"],
        source_time,
        risk_kind,
        limit=2,
    )
    direction = str(event.get("direction") or "neutral")
    source_event_parts = str(event.get("source_event_id") or "").split(":", 2)
    signal_codes = {
        code
        for code in (
            source_event_parts[2].split(",")
            if len(source_event_parts) == 3
            else []
        )
        if code
    }
    two_cleared = len(recent_states) >= 2 and all(
        _state_confirms_episode_clear(state, signal_codes, risk_kind)
        for state in recent_states[:2]
    )
    trade_date = source_time[:10]
    existing = alert_repository.get_latest_risk_episode_alert(
        snapshot["symbol"],
        str(event["event_type"]),
        direction,
        trade_date,
    )
    if existing is not None and not two_cleared:
        incoming_rank = {"P1": 1, "P2": 2, "P3": 3}.get(
            str(event.get("priority")),
            99,
        )
        existing_rank = {"P1": 1, "P2": 2, "P3": 3}.get(
            str(existing.get("priority")),
            99,
        )
        if incoming_rank >= existing_rank:
            return None
        return {
            **event,
            "source_event_id": existing["sourceEventId"],
        }

    episode_time = source_time[11:19].replace(":", "") or "unknown"
    prefix = "linkage" if risk_kind == "linkage" else "risk"
    return {
        **event,
        "source_event_id": (
            f"{prefix}:{trade_date}:{direction}:episode:{episode_time}:"
            f"{','.join(sorted(signal_codes))}"
        ),
    }


def _state_confirms_episode_clear(
    state: Dict[str, Any],
    signal_codes: set[str],
    risk_kind: str,
) -> bool:
    if state.get("priority") or not signal_codes:
        return False

    if risk_kind == "market":
        fund_codes = {
            "consecutive_fund_inflow",
            "consecutive_fund_outflow",
            "price_fund_divergence",
        }
        for code in signal_codes:
            if code in fund_codes:
                confirmed = state.get("fundFlowStatus") == "no_signal"
            elif code == "ma_breakdown":
                confirmed = state.get("movingAverageStatus") == "no_signal"
            elif code == "turnover_warning":
                confirmed = state.get("turnoverStatus") in {"normal", "active"}
            elif code in {"limit_move", "extreme_price_move"}:
                confirmed = state.get("changeAvailable") is True
            elif code == "high_amplitude":
                confirmed = state.get("amplitudeAvailable") is True
            elif code == "high_volume_ratio":
                confirmed = state.get("volumeRatioAvailable") is True
            else:
                confirmed = False
            if not confirmed:
                return False
        return True

    if risk_kind == "linkage":
        dimension_by_code = {
            "sector_decline": "decline",
            "sector_breadth_weak": "breadth",
            "sector_leader_decline": "leader",
            "sector_fund_inflow_top": "fundFlow",
            "sector_fund_outflow_top": "fundFlow",
        }
        dimensions = state.get("sectorDimensions") or {}
        for code in signal_codes:
            dimension = dimension_by_code.get(code)
            if dimension is not None:
                item = dimensions.get(dimension) or {}
                confirmed = item.get("status") == "no_signal"
            elif code.startswith("overseas_"):
                confirmed = state.get("overseasStatus") == "no_signal"
            else:
                confirmed = False
            if not confirmed:
                return False
        return True

    return False


def process_linkage_snapshot(
    snapshot: Dict[str, Any],
    create_alert: bool = False,
    persist_snapshot: bool = True,
) -> Dict[str, Any]:
    import alert_repository

    result = evaluate_linkage_risk(snapshot)
    episode_event = None
    if create_alert and result.get("priority"):
        event = build_linkage_alert_event(snapshot, result)
        if event is not None:
            episode_event = _prepare_episode_alert(event, snapshot, "linkage")
    if persist_snapshot:
        alert_repository.save_linkage_snapshot(snapshot, result)
    if episode_event is not None:
        alert, created = alert_repository.save_alert_event(episode_event)
        if created:
            from notification_service import process_new_alert
            process_new_alert(alert)
    return result


def build_risk_alert_event(
    snapshot: Dict[str, Any],
    result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    priority = result.get("priority")
    if priority is None:
        return None
    source_time = str(snapshot.get("source_time") or "")
    trade_date = source_time[:10] if len(source_time) >= 10 else "unknown-date"
    signal_key = ",".join(sorted(item["code"] for item in result["signals"]))
    direction = result["direction"]
    rule_labels = [
        _ALERT_RULE_LABELS.get(item["code"], item["label"])
        for item in result["signals"]
    ]
    rule_text = "、".join(rule_labels)
    level_text = f"{priority}强提醒" if priority in {"P1", "P2"} else f"{priority}观察提醒"

    metric_parts = []
    change_percent = _number(snapshot.get("change_percent"))
    volume_ratio = _number(snapshot.get("volume_ratio"))
    amplitude = _amplitude(snapshot)
    turnover_rate = _number(snapshot.get("turnover_rate"))
    if change_percent is not None:
        metric_parts.append(f"涨跌幅 {change_percent:.2f}%")
    if volume_ratio is not None:
        metric_parts.append(f"量比 {volume_ratio:.2f}")
    if amplitude is not None:
        metric_parts.append(f"振幅 {amplitude:.2f}%")
    if turnover_rate is not None:
        metric_parts.append(f"换手率 {turnover_rate:.2f}%")
    metric_text = "、".join(metric_parts) if metric_parts else "关键行情字段暂缺"
    source_time_text = f"{source_time} " if source_time else ""
    return {
        "symbol": snapshot["symbol"],
        "stock_name": snapshot.get("stock_name") or snapshot["symbol"],
        "event_type": "market_risk",
        "direction": direction,
        "priority": priority,
        "evidence_level": "A" if priority in {"P1", "P2"} else "B",
        "title": f"{snapshot.get('stock_name') or snapshot['symbol']}触发{level_text}：{rule_text}",
        "summary": (
            f"{source_time_text}腾讯财经行情显示：{metric_text}。"
            f"固定规则触发：{rule_text}；当前为{level_text}。"
            "该提醒只表示风险或活跃度升高，不代表确定性涨跌。"
        ),
        "source": "腾讯财经行情规则",
        "source_url": None,
        "source_event_id": f"risk:{trade_date}:{signal_key}",
        "published_at": snapshot.get("source_time"),
    }


def process_market_snapshot(
    snapshot: Dict[str, Any],
    persist_snapshot: bool = True,
    create_alert: bool = False,
) -> Dict[str, Any]:
    import alert_repository

    source_time = snapshot.get("source_time")
    history = []
    if source_time:
        history = alert_repository.get_signal_history(
            snapshot["symbol"],
            str(source_time),
            limit_days=20,
        )
    result = evaluate_market_risk(
        snapshot,
        history,
        verified_history=snapshot.get("verified_history"),
    )
    if not source_time:
        result["turnoverRisk"] = {
            "status": "unavailable",
            "label": "暂无判断",
            "baseline": None,
            "multiple": None,
            "reason": "行情原始时间缺失",
        }
    episode_event = None
    if create_alert and result.get("priority"):
        event = build_risk_alert_event(snapshot, result)
        if event is not None:
            episode_event = _prepare_episode_alert(event, snapshot, "market")
    if persist_snapshot and source_time:
        alert_repository.save_signal_snapshot({**snapshot, "risk": result})

    if episode_event is not None:
        alert, created = alert_repository.save_alert_event(episode_event)
        if created:
            from notification_service import process_new_alert
            process_new_alert(alert)
    return result
