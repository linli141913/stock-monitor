"""阶段6主营材料真实来源的一键只读验收入口。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from radar.leader_business_material_acceptance_report import (
    render_leader_business_material_acceptance_html,
)
from radar.leader_business_material_live_acceptance import (
    LeaderBusinessMaterialLiveAcceptanceStatus,
    run_leader_business_material_live_acceptance,
)
from radar.leader_business_material_review_submission import (
    build_leader_business_material_review_source_packet,
    build_leader_business_material_review_template,
)
from radar.leader_live_candidate_collection_batch import (
    LeaderLiveCandidateCollectionRequest,
    build_default_leader_live_candidate_collection_sources,
)
from radar.leader_tradability_live_acceptance import (
    run_leader_tradability_live_acceptance,
)
from radar.sources.leader_tradability_public_live_poc import SHANGHAI_TZ


CLASSIFICATION_PUBLICATION_PAGE_URL = (
    "https://www.capco.org.cn/xhgg/hyfl/hyfljg/202604/20260403/"
    "j_2026040315001700017751997384265508.html"
)


def _candidate_request(started_at: datetime) -> LeaderLiveCandidateCollectionRequest:
    prefix = f"stage6-business-{started_at:%Y%m%dT%H%M%S%f}"
    return LeaderLiveCandidateCollectionRequest(
        radar_run_id=prefix,
        security_master_batch_id=f"{prefix}-security-master",
        discovery_classification_batch_id=(
            f"{prefix}-classification-discovery"
        ),
        collection_classification_batch_id=(
            f"{prefix}-classification-collection"
        ),
        quote_batch_id=f"{prefix}-quotes",
        index_batch_id=f"{prefix}-indices",
    )


def main(*, report_path: Optional[Path] = None) -> int:
    started_at = datetime.now(SHANGHAI_TZ)
    tradability = run_leader_tradability_live_acceptance(
        _candidate_request(started_at),
        build_default_leader_live_candidate_collection_sources(
            classification_publication_page_url=(
                CLASSIFICATION_PUBLICATION_PAGE_URL
            ),
        ),
        clock=lambda: datetime.now(SHANGHAI_TZ),
    )
    result = run_leader_business_material_live_acceptance(
        tradability,
        window_from=date(started_at.year - 2, 1, 1),
    )
    actual_report_path = report_path or Path(
        "/private/tmp/"
        f"stage6-business-material-acceptance-{started_at:%Y%m%dT%H%M%S}.html"
    )
    payload = dict(result.to_evidence())
    review_template_path = None
    source_packet_path = None
    review_template = None
    source_packet = None
    if (
        result.status
        is LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED
        and tradability.candidate_plan is not None
        and result.review_queue is not None
    ):
        review_template_path = actual_report_path.with_name(
            f"{actual_report_path.stem}-review.json"
        )
        source_packet_path = actual_report_path.with_name(
            f"{actual_report_path.stem}-source.json"
        )
        review_template = build_leader_business_material_review_template(
            tradability.candidate_plan,
            result.review_queue,
        )
        source_packet = build_leader_business_material_review_source_packet(
            tradability.candidate_plan,
            result.review_queue,
        )
    try:
        if review_template_path is not None and review_template is not None:
            review_template_path.write_text(
                json.dumps(review_template, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if source_packet_path is not None and source_packet is not None:
            source_packet_path.write_text(
                json.dumps(source_packet, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        actual_report_path.write_text(
            render_leader_business_material_acceptance_html(
                result,
                review_template_href=(
                    review_template_path.name
                    if review_template_path is not None
                    else None
                ),
                source_packet_href=(
                    source_packet_path.name
                    if source_packet_path is not None
                    else None
                ),
            ),
            encoding="utf-8",
        )
    except OSError:
        payload.update({
            "reportPath": None,
            "reportWriteStatus": "failed",
            "reviewTemplatePath": None,
            "sourcePacketPath": None,
        })
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 3
    payload.update({
        "reportPath": str(actual_report_path),
        "reportWriteStatus": "completed",
        "reviewTemplatePath": (
            str(review_template_path)
            if review_template_path is not None
            else None
        ),
        "sourcePacketPath": (
            str(source_packet_path)
            if source_packet_path is not None
            else None
        ),
    })
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return (
        0
        if result.status
        is LeaderBusinessMaterialLiveAcceptanceStatus.COMPLETED
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
