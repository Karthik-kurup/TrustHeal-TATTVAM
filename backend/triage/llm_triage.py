"""
triage/llm_triage.py — Triage provider interface.

Default: deterministic template implementation (works offline).
Optional: LLM implementation configured by environment variables.

Usage:
    from triage.llm_triage import get_triage_provider, TriageProvider
    provider = get_triage_provider()
    summary = await provider.triage(incident)

Environment variables:
    TRUSTHEAL_LLM_PROVIDER = template | openai | ollama
    TRUSTHEAL_LLM_URL      = http://localhost:11434  (for ollama)
    TRUSTHEAL_LLM_API_KEY  = sk-...                  (for openai)
    TRUSTHEAL_LLM_MODEL    = gpt-4o-mini             (model name)

SECURITY:
    - Log and event text is treated as untrusted data.
    - Secrets are never included in model prompts.
    - Triage is never used in AUTO gate decisions.
    - Calls time out to template on any error.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

from triage.templates import build_template_triage, TriageSummary

logger = logging.getLogger(__name__)

TRIAGE_TIMEOUT_SECONDS = 5.0


# ── Provider ABC ──────────────────────────────────────────────────────────────

class TriageProvider(ABC):
    """Abstract triage provider."""

    @abstractmethod
    async def triage(
        self,
        incident_id: str,
        service: str,
        diagnosis: dict[str, Any],
        trust: dict[str, Any] | None,
        gate_decision: str,
        vetoes: list[str],
        action_name: str,
        sim_time: float,
    ) -> TriageSummary:
        ...


# ── Template provider (default, offline) ─────────────────────────────────────

class TemplateProvider(TriageProvider):
    """Deterministic template-based triage. Always works offline."""

    async def triage(
        self,
        incident_id: str,
        service: str,
        diagnosis: dict[str, Any],
        trust: dict[str, Any] | None,
        gate_decision: str,
        vetoes: list[str],
        action_name: str,
        sim_time: float,
    ) -> TriageSummary:
        return build_template_triage(
            incident_id=incident_id,
            service=service,
            diagnosis=diagnosis,
            trust=trust,
            gate_decision=gate_decision,
            vetoes=vetoes,
            action_name=action_name,
            sim_time=sim_time,
        )


# ── LLM provider (optional) ───────────────────────────────────────────────────

class LLMProvider(TriageProvider):
    """
    LLM-backed triage provider. Falls back to TemplateProvider on any error.

    Supports: openai-compatible APIs (openai, ollama, local models).
    NEVER called in AUTO decisions.
    Secrets are redacted from all prompts.
    """

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._template = TemplateProvider()

    def _build_prompt(
        self,
        service: str,
        diagnosis: dict[str, Any],
        trust: dict[str, Any] | None,
        gate_decision: str,
        vetoes: list[str],
    ) -> str:
        label = diagnosis.get("label", "UNKNOWN")
        conf = diagnosis.get("confidence", 0.0)
        top = dict(list(diagnosis.get("top_features", {}).items())[:5])
        signals = trust or {}
        # SECURITY: only structured data in prompt — no log text, no secrets
        context = json.dumps({
            "service": service,
            "diagnosis": label,
            "confidence": round(conf, 3),
            "top_features": {k: round(v, 3) for k, v in top.items()},
            "trust_signals": {k: round(float(v), 3) for k, v in signals.items()},
            "gate_decision": gate_decision,
            "vetoes": vetoes,
        }, indent=2)
        return (
            "You are a Kubernetes SRE assistant. Given the following structured incident data "
            "(no free-text logs), produce a one-paragraph triage summary explaining what is "
            "likely happening and why the gate made its decision. Be concise and factual.\n\n"
            f"Incident data:\n{context}\n\n"
            "Response format: one paragraph, 3-5 sentences. No markdown. No invented facts."
        )

    async def _call_llm(self, prompt: str) -> str | None:
        try:
            import urllib.request
            payload = json.dumps({
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 200,
                "temperature": 0.2,
            }).encode()
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            }
            req = urllib.request.Request(
                f"{self._base_url}/v1/chat/completions",
                data=payload,
                headers=headers,
                method="POST",
            )
            loop = asyncio.get_event_loop()
            response_data = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: urllib.request.urlopen(req, timeout=TRIAGE_TIMEOUT_SECONDS).read(),
                ),
                timeout=TRIAGE_TIMEOUT_SECONDS,
            )
            result = json.loads(response_data.decode())
            return result["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("LLM triage call failed (%s), falling back to template", exc)
            return None

    async def triage(
        self,
        incident_id: str,
        service: str,
        diagnosis: dict[str, Any],
        trust: dict[str, Any] | None,
        gate_decision: str,
        vetoes: list[str],
        action_name: str,
        sim_time: float,
    ) -> TriageSummary:
        # Build template summary first (always succeeds)
        template_summary = await self._template.triage(
            incident_id, service, diagnosis, trust,
            gate_decision, vetoes, action_name, sim_time,
        )
        # Try to enhance description with LLM
        prompt = self._build_prompt(service, diagnosis, trust, gate_decision, vetoes)
        llm_text = await self._call_llm(prompt)
        if llm_text:
            template_summary = dataclasses.replace(template_summary, description=llm_text)
        return template_summary


# ── Factory ───────────────────────────────────────────────────────────────────

_provider: TriageProvider | None = None


def get_triage_provider() -> TriageProvider:
    """Return the configured triage provider (singleton)."""
    global _provider
    if _provider is not None:
        return _provider

    provider_type = os.getenv("TRUSTHEAL_LLM_PROVIDER", "template").lower()

    if provider_type == "template":
        _provider = TemplateProvider()
        logger.info("Triage: using template provider (offline)")
    else:
        url = os.getenv("TRUSTHEAL_LLM_URL", "http://localhost:11434")
        api_key = os.getenv("TRUSTHEAL_LLM_API_KEY", "")
        model = os.getenv("TRUSTHEAL_LLM_MODEL", "llama3.2")
        _provider = LLMProvider(base_url=url, api_key=api_key, model=model)
        logger.info("Triage: using LLM provider (provider=%s model=%s url=%s)",
                    provider_type, model, url)

    return _provider


def reset_provider() -> None:
    """Reset the singleton (for testing)."""
    global _provider
    _provider = None
