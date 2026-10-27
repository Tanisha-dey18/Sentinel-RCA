"""Human-readable incident narratives: Groq LLM (optional) with a deterministic fallback."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior Site Reliability Engineer writing an incident root-cause analysis.
You are given STRUCTURED EVIDENCE extracted from logs by a detection engine. Rules:
- Use ONLY the evidence provided. Never invent services, errors, numbers, timestamps or causes.
- Cite evidence inline using the provided IDs, e.g. [F2], [S1], [log:1234], [anomaly:17].
- Keep three things clearly separate, using exactly these markdown headings:
  ### What happened (observed facts)
  ### Statistical signals
  ### Probable root cause (inference)
  ### Recommended next steps
- In the inference section, state the confidence given in the evidence and mention the main alternative
  hypothesis if its score is close. If evidence is insufficient, say so explicitly.
- Be concise: at most ~350 words. Markdown only, no preamble."""


def deterministic_narrative(incident: dict[str, Any]) -> str:
    rc = incident.get("root_cause") or {}
    primary = rc.get("primary")
    lines: list[str] = []

    lines.append("### What happened (observed facts)")
    for f in rc.get("facts", [])[:8]:
        refs = ", ".join(f"log:{i}" for i in f.get("evidence", {}).get("log_ids", [])[:3])
        lines.append(f"- **[{f['id']}]** {f['statement']}" + (f" _({refs})_" if refs else ""))
    if not rc.get("facts"):
        lines.append("- No signature matches were found; the incident is based on statistical signals only.")

    lines.append("")
    lines.append("### Statistical signals")
    if rc.get("signals"):
        for s in rc["signals"][:6]:
            lines.append(f"- **[{s['id']}]** {s['statement']} _(anomaly:{s['anomaly_id']})_")
    else:
        lines.append("- No statistical (robust z-score) anomalies; detection is signature-based.")

    lines.append("")
    lines.append("### Probable root cause (inference)")
    if primary:
        lines.append(f"**{primary['title']}** — {primary['confidence_label']} confidence "
                     f"({primary['confidence']:.0%}, score {primary['score']:.2f}).")
        lines.append("")
        lines.append(primary["explanation"])
        lines.append("")
        lines.append("Why this hypothesis ranks first:")
        for r in primary.get("reasoning", []):
            lines.append(f"- {r}")
        chain = rc.get("causal_chain", [])
        if chain:
            lines.append("")
            lines.append("Inferred propagation: " + " → ".join(
                dict.fromkeys([c["cause_name"] for c in chain] + [chain[-1]["effect_name"]])))
        alts = rc.get("hypotheses", [])[1:3]
        if alts:
            lines.append("")
            lines.append("Alternative hypotheses: " + "; ".join(
                f"{h['title']} (score {h['score']:.2f})" for h in alts) + ".")
    else:
        lines.append("Insufficient evidence to infer a root cause.")

    lines.append("")
    lines.append("### Recommended next steps")
    for a in (primary or {}).get("recommendations", [])[:5]:
        lines.append(f"1. {a}")
    lines.append("")
    lines.append("_Generated deterministically from detected evidence (no LLM)._")
    return "\n".join(lines)


def _evidence_payload(incident: dict[str, Any]) -> dict[str, Any]:
    rc = incident.get("root_cause") or {}
    return {
        "incident": {k: incident.get(k) for k in ("id", "title", "severity", "started_at", "ended_at", "services")},
        "facts": rc.get("facts", [])[:12],
        "signals": [{k: s.get(k) for k in ("id", "anomaly_id", "service", "metric", "robust_z", "observed",
                                           "baseline_median", "statement")} for s in rc.get("signals", [])[:8]],
        "causal_chain": [{k: c.get(k) for k in ("cause_name", "effect_name", "cause_services", "effect_services",
                                                "lag_seconds", "trace_linked", "explanation")}
                         for c in rc.get("causal_chain", [])[:8]],
        "hypotheses": [{k: h.get(k) for k in ("rank", "service", "title", "explanation", "score", "confidence",
                                              "confidence_label", "reasoning", "recommendations")}
                       for h in rc.get("hypotheses", [])[:3]],
        "sample_logs": incident.get("sample_logs", [])[:15],
    }


def llm_narrative(incident: dict[str, Any]) -> str:
    s = get_settings()
    payload = {
        "model": s.groq_model,
        "temperature": 0.1,
        "max_tokens": 900,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "EVIDENCE (JSON):\n" + json.dumps(_evidence_payload(incident), default=str)},
        ],
    }
    with httpx.Client(timeout=s.llm_timeout_seconds) as client:
        r = client.post(f"{s.groq_base_url.rstrip('/')}/chat/completions", json=payload,
                        headers={"Authorization": f"Bearer {s.groq_api_key}"})
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"].strip()
    if not content:
        raise ValueError("empty LLM response")
    return content


def generate_narrative(incident: dict[str, Any], use_llm: bool) -> tuple[str, str, str | None]:
    """Return (markdown, source, error). Falls back to deterministic text on any LLM failure."""
    s = get_settings()
    if use_llm and s.llm_enabled:
        try:
            return llm_narrative(incident), f"llm:groq:{s.groq_model}", None
        except Exception as exc:  # network, auth, quota, malformed response...
            log.warning("LLM narrative failed, using deterministic fallback: %s", exc)
            return deterministic_narrative(incident), "deterministic", f"LLM unavailable: {exc}"[:300]
    reason = None if not use_llm else "No GROQ_API_KEY configured"
    return deterministic_narrative(incident), "deterministic", reason
