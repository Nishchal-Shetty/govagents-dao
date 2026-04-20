"""
agents/eval.py
~~~~~~~~~~~~~~
Offline evaluation harness for GovAgents DAO.

Runs proposal files through all three AI agents (analysis only, no on-chain
transactions) and optionally compares results against human-labeled ground
truth. Useful for measuring agent agreement rate and spotting systematic bias.

Usage
-----
  # analyze all proposals in sample-proposals/, no labels
  python eval.py --proposals ../sample-proposals/

  # compare against human labels
  python eval.py --proposals ../sample-proposals/ --labels ../eval/labels.json

  # machine-readable output
  python eval.py --proposals ../sample-proposals/ --labels ../eval/labels.json --json

Environment
-----------
Requires ANTHROPIC_API_KEY in agents/.env. No Hardhat node needed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────────────────
_AGENTS_DIR = Path(__file__).resolve().parent
_ENV_PATH   = _AGENTS_DIR / ".env"

# ── Minimal per-role system prompts (mirrors base_agent.py) ───────────────────
_SYSTEM_PROMPTS: dict[str, str] = {
    "Security": (
        "You are a Security Agent in a decentralised autonomous organisation (DAO). "
        "Your mandate is to evaluate governance proposals through a security lens: "
        "smart-contract vulnerabilities, attack vectors, privilege escalation risks, "
        "fund-loss scenarios, and potential for misuse or exploitation. "
        "You are rigorous, sceptical, and focus exclusively on security implications. "
        "When uncertain, you prefer caution."
    ),
    "Economic": (
        "You are an Economic Agent in a decentralised autonomous organisation (DAO). "
        "Your mandate is to evaluate governance proposals through an economic lens: "
        "treasury impact, token-value effects, ROI, liquidity risks, incentive "
        "alignment, and long-term financial sustainability. "
        "You are analytical, data-driven, and focus exclusively on economic implications."
    ),
    "Governance": (
        "You are a Governance Agent in a decentralised autonomous organisation (DAO). "
        "Your mandate is to evaluate governance proposals through a governance lens: "
        "alignment with the DAO charter, procedural integrity, voting fairness, "
        "power concentration, transparency, and long-term organisational health. "
        "You are principled, process-oriented, and focus exclusively on governance "
        "implications."
    ),
}

_USER_TEMPLATE = (
    "Proposal title: {title}\n\n"
    "Proposal description:\n{description}\n\n"
    "Evaluate this proposal from your designated role perspective and respond "
    "with a JSON object containing exactly these three fields:\n"
    '  "recommendation" : one of "Approve", "Reject", or "Revise"\n'
    '  "confidence"     : integer from 0 (completely uncertain) to 100 (fully certain)\n'
    '  "reasoning"      : one to three sentences explaining your decision\n\n'
    "Return ONLY the JSON object — no markdown fences, no preamble, no extra text."
)

_MODEL = "claude-sonnet-4-20250514"

_REC_NORMALISE = {"approve": "Approve", "reject": "Reject", "revise": "Revise"}


# ── Lean agent — Anthropic only, no web3 ──────────────────────────────────────

class _EvalAgent:
    """Stripped-down agent that only calls the Anthropic API. No web3 stack."""

    def __init__(self, role: str, client: anthropic.Anthropic) -> None:
        self.role   = role
        self._client = client

    def analyze(self, title: str, description: str) -> dict[str, Any]:
        user_msg = _USER_TEMPLATE.format(title=title, description=description)
        response = self._client.messages.create(
            model=_MODEL,
            max_tokens=512,
            system=_SYSTEM_PROMPTS[self.role],
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()

        import re
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned).strip()
        data = json.loads(cleaned)

        rec = _REC_NORMALISE.get(str(data.get("recommendation", "")).strip().lower())
        if rec is None:
            raise ValueError(f"bad recommendation from {self.role}: {data.get('recommendation')!r}")
        conf = int(data.get("confidence", 0))
        if not (0 <= conf <= 100):
            raise ValueError(f"confidence out of range from {self.role}: {conf}")
        reasoning = str(data.get("reasoning", "")).strip()

        return {"role": self.role, "recommendation": rec, "confidence": conf, "reasoning": reasoning}


# ── Tiebreak (mirrors _derive_dry_run_consensus in runner.py) ─────────────────

def _consensus(verdicts: list[dict]) -> str | None:
    recs   = [v["recommendation"] for v in verdicts]
    counts: dict[str, int] = Counter(recs)
    conf:   dict[str, int] = {}
    for v in verdicts:
        conf[v["recommendation"]] = conf.get(v["recommendation"], 0) + v["confidence"]

    max_count = max(counts.values())
    leaders   = [r for r, c in counts.items() if c == max_count]
    if len(leaders) == 1:
        return leaders[0]

    best_conf    = max(conf.get(r, 0) for r in leaders)
    conf_leaders = [r for r in leaders if conf.get(r, 0) == best_conf]
    if len(conf_leaders) == 1:
        return conf_leaders[0]

    for priority in ("Approve", "Revise", "Reject"):
        if priority in conf_leaders:
            return priority
    return None


# ── Proposal loader ────────────────────────────────────────────────────────────

def _load_proposals(proposals_dir: Path) -> list[tuple[str, str, str]]:
    """Return list of (filename, title, description) for each .txt in the dir."""
    results = []
    for p in sorted(proposals_dir.glob("*.txt")):
        text  = p.read_text(encoding="utf-8").strip()
        lines = text.splitlines()
        title = lines[0].lstrip("#").strip() if lines else p.stem
        desc  = "\n".join(lines[1:]).strip() if len(lines) > 1 else text
        results.append((p.name, title, desc))
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Offline eval harness for GovAgents DAO")
    parser.add_argument("--proposals", required=True,
                        help="Directory of proposal .txt files")
    parser.add_argument("--labels",
                        help="Path to eval/labels.json with human ground-truth labels")
    parser.add_argument("--json", dest="json_output", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    load_dotenv(_ENV_PATH)
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("error: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    proposals_dir = Path(args.proposals).resolve()
    if not proposals_dir.is_dir():
        print(f"error: proposals directory not found: {proposals_dir}", file=sys.stderr)
        sys.exit(1)

    proposals = _load_proposals(proposals_dir)
    if not proposals:
        print(f"error: no .txt files found in {proposals_dir}", file=sys.stderr)
        sys.exit(1)

    labels: dict[str, dict] = {}
    if args.labels:
        labels_path = Path(args.labels).resolve()
        if labels_path.exists():
            labels = json.loads(labels_path.read_text(encoding="utf-8"))
        else:
            print(f"warning: labels file not found at {labels_path}, running without labels",
                  file=sys.stderr)

    client = anthropic.Anthropic(api_key=api_key)
    agents = [_EvalAgent(role, client) for role in ("Security", "Economic", "Governance")]

    results = []
    errors  = []

    for filename, title, description in proposals:
        if not args.json_output:
            print(f"  analyzing {filename} ...", end=" ", flush=True)
        try:
            verdicts = [a.analyze(title, description) for a in agents]
        except Exception as exc:
            errors.append({"file": filename, "error": str(exc)})
            if not args.json_output:
                print(f"ERROR: {exc}")
            continue

        consensus    = _consensus(verdicts)
        label_entry  = labels.get(filename, {})
        human_label  = label_entry.get("human_recommendation")
        match        = (consensus == human_label) if human_label else None

        results.append({
            "file":       filename,
            "verdicts":   verdicts,
            "consensus":  consensus,
            "human_label": human_label,
            "match":      match,
            "label_notes": label_entry.get("notes"),
        })

        if not args.json_output:
            parts = [f"{v['role'][:3]} {v['recommendation']}({v['confidence']})" for v in verdicts]
            match_str = ("✓" if match else "✗") if match is not None else "—"
            print(f"consensus={consensus}  human={human_label or '?'}  {match_str}")
            print(f"    {' | '.join(parts)}")

    if args.json_output:
        agreed  = sum(1 for r in results if r["match"] is True)
        labeled = sum(1 for r in results if r["match"] is not None)
        print(json.dumps({
            "total":      len(results),
            "labeled":    labeled,
            "agreed":     agreed,
            "agreement_rate": round(agreed / labeled, 3) if labeled else None,
            "results":    results,
            "errors":     errors,
        }, indent=2))
    else:
        labeled = [r for r in results if r["match"] is not None]
        agreed  = sum(1 for r in labeled if r["match"])
        print(f"\n  {len(results)} proposals evaluated", end="")
        if labeled:
            print(f"  |  agreement with human labels: {agreed}/{len(labeled)}"
                  f"  ({round(agreed/len(labeled)*100)}%)", end="")
        print()
        if errors:
            print(f"  {len(errors)} error(s):")
            for e in errors:
                print(f"    {e['file']}: {e['error']}")


if __name__ == "__main__":
    main()
