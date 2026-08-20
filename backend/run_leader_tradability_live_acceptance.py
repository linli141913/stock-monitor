"""阶段6候选全集可交易性真实来源的只读命令行验收入口。"""

from __future__ import annotations

import json
from datetime import datetime

from radar.leader_live_candidate_collection_batch import (
    LeaderLiveCandidateCollectionRequest,
    build_default_leader_live_candidate_collection_sources,
)
from radar.leader_tradability_live_acceptance import (
    LeaderTradabilityLiveAcceptanceStatus,
    run_leader_tradability_live_acceptance,
)
from radar.sources.leader_tradability_public_live_poc import SHANGHAI_TZ


CLASSIFICATION_PUBLICATION_PAGE_URL = (
    "https://www.capco.org.cn/xhgg/hyfl/hyfljg/202604/20260403/"
    "j_2026040315001700017751997384265508.html"
)


def main() -> int:
    started_at = datetime.now(SHANGHAI_TZ)
    batch_prefix = f"stage6-tradability-{started_at:%Y%m%dT%H%M%S%f}"
    request = LeaderLiveCandidateCollectionRequest(
        radar_run_id=batch_prefix,
        security_master_batch_id=f"{batch_prefix}-security-master",
        discovery_classification_batch_id=(
            f"{batch_prefix}-classification-discovery"
        ),
        collection_classification_batch_id=(
            f"{batch_prefix}-classification-collection"
        ),
        quote_batch_id=f"{batch_prefix}-quotes",
        index_batch_id=f"{batch_prefix}-indices",
    )
    result = run_leader_tradability_live_acceptance(
        request,
        build_default_leader_live_candidate_collection_sources(
            classification_publication_page_url=(
                CLASSIFICATION_PUBLICATION_PAGE_URL
            ),
        ),
        clock=lambda: datetime.now(SHANGHAI_TZ),
    )
    print(json.dumps(result.to_evidence(), ensure_ascii=False, indent=2))
    return (
        0
        if result.status == LeaderTradabilityLiveAcceptanceStatus.COMPLETED
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
