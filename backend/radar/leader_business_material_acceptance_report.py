"""把主营材料只读验收结果渲染为可离线查看的本地 HTML。"""

from __future__ import annotations

from html import escape
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from radar.leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceResult,
    LeaderBusinessMaterialLiveAcceptanceStatus,
)
from radar.leader_business_material_review_queue import (
    LeaderBusinessMaterialReviewItemStatus,
)


_STATUS_LABELS = {
    LeaderBusinessMaterialReviewItemStatus.PENDING_REVIEW: "待人工复核",
    LeaderBusinessMaterialReviewItemStatus.MISSING: "未发现材料",
    LeaderBusinessMaterialReviewItemStatus.SOURCE_FAILED: "来源失败",
    LeaderBusinessMaterialReviewItemStatus.SOURCE_UNVERIFIED: "来源未核实",
}
_LOCAL_ARTIFACT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,180}$")


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _official_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "static.cninfo.com.cn"
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return escape(value, quote=True)


def _local_artifact_href(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _LOCAL_ARTIFACT_PATTERN.fullmatch(value) is None
    ):
        return ""
    return escape(value, quote=True)


def _reason_list(reasons: Iterable[str]) -> str:
    items = "".join(f"<li>{_text(reason)}</li>" for reason in reasons)
    return f'<ul class="reason-list">{items}</ul>' if items else ""


def _document_rows(documents: Any) -> str:
    if not documents:
        return '<p class="empty-copy">本候选未发现可进入人工复核的官方材料。</p>'
    rows = []
    for document in documents:
        url = _official_url(document.source_url)
        title = _text(document.title)
        title_markup = (
            f'<a href="{url}" target="_blank" rel="noreferrer">'
            f"{title}<span aria-hidden=\"true\"> ↗</span></a>"
            if url
            else title
        )
        rows.append(
            '<div class="document">'
            f'<div class="document-title">{title_markup}</div>'
            '<div class="document-meta">'
            f'<span>{_text(document.source_name)}</span>'
            f'<span>{_text(document.published_at.isoformat())}</span>'
            f'<span>{_text(document.document_id)}</span>'
            "</div></div>"
        )
    return "".join(rows)


def _candidate_rows(result: LeaderBusinessMaterialLiveAcceptanceResult) -> str:
    queue = result.review_queue
    if queue is None:
        return ""
    rows = []
    for item in queue.items:
        label = _STATUS_LABELS[item.status]
        tone = item.status.value.replace("_", "-")
        rows.append(
            '<article class="candidate" data-candidate-row>'
            '<div class="candidate-head">'
            '<div>'
            f'<span class="candidate-index">{item.index + 1:02d}</span>'
            f'<h2>{_text(item.symbol)}</h2>'
            f'<p>行业 {_text(item.industry_code)} · 版本 '
            f'{_text(item.industry_release_id)}</p>'
            "</div>"
            f'<span class="status status-{tone}">{label}</span>'
            "</div>"
            f'{_document_rows(item.documents)}'
            f'{_reason_list(item.reasons)}'
            "</article>"
        )
    return "".join(rows)


def render_leader_business_material_acceptance_html(
    result: Any,
    *,
    review_template_href: Any = None,
    source_packet_href: Any = None,
) -> str:
    if type(result) is not LeaderBusinessMaterialLiveAcceptanceResult:
        raise ValueError("business_material_acceptance_report_contract_unverified")
    summary = result.review_summary
    completed = (
        result.status is LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED
    )
    state_title = "材料发现已完成" if completed else "本轮未就绪"
    state_copy = (
        "官方材料已按同轮候选全集完成发现，等待真实人工复核。"
        if completed
        else "未生成任何候选或材料替代数据；请按失败原因恢复真实来源后重跑。"
    )
    as_of = result.as_of.isoformat() if result.as_of else "—"
    reasons = _reason_list(result.reasons)
    candidates = _candidate_rows(result)
    template_href = _local_artifact_href(review_template_href)
    packet_href = _local_artifact_href(source_packet_href)
    review_actions = ""
    if template_href and packet_href:
        review_actions = (
            '<section class="review-actions">'
            '<div><span class="action-step">NEXT / HUMAN ONLY</span>'
            '<h2>把判断留给人，把边界交给合同</h2>'
            '<p>下载人工复核文件，仅填写 review 区域；候选、行业和官方文档字段不得修改。'
            '来源清单只读保留，后续导入会逐字段复核并检查摘要。</p></div>'
            '<code>PYTHONPATH=backend backend/venv/bin/python '
            'backend/import_leader_business_material_review.py '
            f'{packet_href} {template_href}</code>'
            '<div class="action-links">'
            f'<a class="action-primary" href="{template_href}" download>'
            '下载人工复核文件</a>'
            f'<a href="{packet_href}" download>下载只读来源清单</a>'
            '</div></section>'
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>阶段6 · 主营证据验收台</title>
  <style>
    :root {{ color-scheme: dark; --ink:#e9f0f2; --muted:#829298; --line:#26353a;
      --panel:#11191c; --base:#090e10; --cyan:#57d8d0; --amber:#f0b35b;
      --red:#ef746f; --blue:#7ea7ff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:radial-gradient(circle at 82% -10%,#183238 0,transparent 32%),
      var(--base); color:var(--ink); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
    body::before {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.14;
      background-image:linear-gradient(#ffffff08 1px,transparent 1px),
      linear-gradient(90deg,#ffffff08 1px,transparent 1px); background-size:32px 32px; }}
    main {{ width:min(1320px,calc(100% - 40px)); margin:0 auto; padding:42px 0 70px; position:relative; }}
    .eyebrow {{ color:var(--cyan); font-size:12px; letter-spacing:.18em; text-transform:uppercase; }}
    h1 {{ margin:10px 0 8px; font:700 clamp(28px,4vw,52px)/1.05 system-ui,sans-serif; letter-spacing:-.04em; }}
    .subtitle {{ max-width:760px; color:#a9b7bb; line-height:1.7; }}
    .run-strip {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid var(--line);
      background:#0d1416; margin:28px 0 16px; }}
    .run-strip div {{ padding:14px 16px; min-width:0; border-right:1px solid var(--line); }}
    .run-strip div:last-child {{ border:0; }} .run-strip small {{ color:var(--muted); display:block; margin-bottom:6px; }}
    .run-strip strong {{ display:block; overflow-wrap:anywhere; font-size:13px; }}
    .state {{ display:flex; justify-content:space-between; gap:24px; align-items:center; padding:22px;
      border:1px solid var(--line); border-left:4px solid {'var(--cyan)' if completed else 'var(--red)'};
      background:linear-gradient(100deg,#111c1f,#0d1416); }}
    .state h2 {{ margin:0 0 7px; font:650 20px system-ui,sans-serif; }} .state p {{ margin:0;color:var(--muted); }}
    .gate {{ white-space:nowrap; color:var(--red); border:1px solid #713d3a; padding:8px 10px; font-size:12px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:16px 0 34px; }}
    .metric {{ padding:18px; background:var(--panel); border:1px solid var(--line); }}
    .metric span {{ display:block; color:var(--muted); font-size:12px; }} .metric b {{ font-size:30px; line-height:1.2; }}
    .section-label {{ display:flex; align-items:center; gap:12px; color:var(--muted); margin:28px 0 12px; font-size:12px; }}
    .section-label::after {{ content:""; flex:1; border-top:1px solid var(--line); }}
    .candidate {{ background:var(--panel); border:1px solid var(--line); padding:20px; margin:0 0 12px; }}
    .candidate-head {{ display:flex; justify-content:space-between; gap:16px; margin-bottom:16px; }}
    .candidate-head>div {{ display:grid; grid-template-columns:auto auto; align-items:baseline; gap:6px 12px; }}
    .candidate-index {{ color:var(--cyan); font-size:12px; }} .candidate h2 {{ margin:0;font-size:20px; }}
    .candidate p {{ grid-column:2; margin:0;color:var(--muted);font-size:12px; }}
    .status {{ align-self:start; padding:6px 9px; border:1px solid currentColor; font-size:12px; }}
    .status-pending-review {{ color:var(--amber); }} .status-missing {{ color:var(--muted); }}
    .status-source-failed {{ color:var(--red); }} .status-source-unverified {{ color:var(--blue); }}
    .document {{ padding:12px 0; border-top:1px solid var(--line); }}
    .document-title a {{ color:var(--ink); text-decoration:none; }} .document-title a:hover {{ color:var(--cyan); }}
    .document-meta {{ display:flex; gap:18px; flex-wrap:wrap; color:var(--muted);font-size:11px;margin-top:8px; }}
    .reason-list {{ color:var(--red); padding-left:20px; font-size:12px; }} .empty-copy {{ color:var(--muted); }}
    .review-actions {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:18px 28px; align-items:center;
      margin:18px 0 34px; padding:24px; border:1px solid #376a6a; background:linear-gradient(110deg,#102326,#101719); }}
    .review-actions h2 {{ margin:6px 0 8px; font:650 20px system-ui,sans-serif; }}
    .review-actions p {{ margin:0; color:#a9b7bb; max-width:760px; line-height:1.65; }}
    .action-step {{ color:var(--cyan); font-size:11px; letter-spacing:.14em; }}
    .review-actions code {{ grid-column:1/-1; display:block; color:#a9c8ca; background:#091113;
      border-left:3px solid var(--cyan); padding:11px 13px; overflow-wrap:anywhere; font-size:11px; }}
    .action-links {{ display:flex; flex-direction:column; gap:9px; min-width:220px; }}
    .action-links a {{ color:var(--ink); text-decoration:none; text-align:center; border:1px solid var(--line); padding:10px 14px; font-size:12px; }}
    .action-links a:hover {{ border-color:var(--cyan); color:var(--cyan); }}
    .action-links .action-primary {{ color:#071112; border-color:var(--cyan); background:var(--cyan); font-weight:700; }}
    footer {{ margin-top:34px; padding-top:18px; border-top:1px solid var(--line); color:var(--muted); line-height:1.7; font-size:12px; }}
    footer strong {{ color:var(--amber); }}
    @media(max-width:760px) {{ main {{ width:min(100% - 24px,1320px);padding-top:24px; }}
      .run-strip,.metrics {{ grid-template-columns:1fr 1fr; }} .run-strip div:nth-child(2) {{ border-right:0; }}
      .state {{ align-items:flex-start;flex-direction:column; }} .review-actions {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body><main>
  <header><div class="eyebrow">Phase 06 / Official evidence / Read only</div>
    <h1>阶段6 · 主营证据验收台</h1>
    <p class="subtitle">把同轮冻结候选、巨潮发行人身份和官方年度报告材料放在一张可审计工作台上。这里负责发现与复核，不负责替人下结论。</p>
  </header>
  <section class="run-strip" aria-label="批次信息">
    <div><small>验收状态</small><strong>{_text(result.status.value)}</strong></div>
    <div><small>雷达轮次</small><strong>{_text(result.radar_run_id or '—')}</strong></div>
    <div><small>候选计划</small><strong>{_text(result.candidate_plan_id or '—')}</strong></div>
    <div><small>数据时点</small><strong>{_text(as_of)}</strong></div>
  </section>
  <section class="state"><div><h2>{state_title}</h2><p>{state_copy}</p></div>
    <div class="gate">formalGateReady · false</div></section>
  <section class="metrics" aria-label="复核状态计数">
    <div class="metric"><b>{summary.get('pendingReview', 0)}</b><span>待人工复核</span></div>
    <div class="metric"><b>{summary.get('missing', 0)}</b><span>未发现材料</span></div>
    <div class="metric"><b>{summary.get('sourceFailed', 0)}</b><span>来源失败</span></div>
    <div class="metric"><b>{summary.get('sourceUnverified', 0)}</b><span>来源未核实</span></div>
  </section>
  {review_actions}
  {('<div class="section-label">失败与阻塞原因</div>' + reasons) if reasons else ''}
  {('<div class="section-label">候选全集 · ' + _text(result.candidate_count) + ' 只</div>' + candidates) if candidates else ''}
  <footer><strong>人工复核前不进入正式门。</strong> 页面中的材料链接来自巨潮资讯公开元数据；材料存在不代表主营关联成立。所有正式评分、正式门和状态迁移均保持关闭。本报告不构成投资建议。</footer>
</main></body></html>"""
