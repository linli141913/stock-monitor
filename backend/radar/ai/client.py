from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict

import requests

from radar.ai.contracts import FrozenRadarEvidencePackage


@dataclass(frozen=True)
class RadarAiClientResponse:
    payload: Dict[str, Any]
    model: str
    prompt_tokens: int
    completion_tokens: int


class OpenAiCompatibleRadarClient:
    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: int):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def analyze(
        self,
        *,
        package: FrozenRadarEvidencePackage,
        system_prompt: str,
        prompt_version: str,
        model: str,
    ) -> RadarAiClientResponse:
        response = requests.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "promptVersion": prompt_version,
                                "frozenEvidence": package.model_dump(
                                    mode="json",
                                    by_alias=True,
                                ),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                ],
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        payload = json.loads(content)
        usage = body.get("usage") or {}
        return RadarAiClientResponse(
            payload=payload,
            model=str(body.get("model") or model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
