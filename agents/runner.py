"""
agents/runner.py
~~~~~~~~~~~~~~~~
End-to-end runner for the DAOGovernance multi-agent voting pipeline.

Steps
-----
1. Parse CLI args → load proposal text (inline or from /sample-proposals/).
2. Submit the proposal to DAOGovernance and capture the on-chain proposal ID.
   (Skipped in --dry-run mode.)
3. Instantiate all three agents (Security, Economic, Governance).
4. Run each agent's analyze() + submit_vote() pipeline concurrently.
   (submit_vote skipped in --dry-run mode.)
5. Print each verdict live as it arrives.
6. Wait for all three votes; the contract auto-finalises on the third.
7. Read the final on-chain recommendation from getFinalRecommendation().
8. Print a formatted terminal summary, or JSON when --json is set.
9. Save full results to /results/proposal_<id>_results.json.

Usage examples
--------------
  # Inline title + description
  python runner.py --title "Treasury Grant" --description "Allocate 50k USDC..."

  # Load from sample-proposals/
  python runner.py --file proposal-001-treasury-grant.txt

  # Analyze only — no Hardhat node needed
  python runner.py --file proposal-001-treasury-grant.txt --dry-run

  # Machine-readable JSON output (CI-friendly)
  python runner.py --file proposal-001-treasury-grant.txt --dry-run --json

  # Suppress ANSI colours
  python runner.py --file proposal-001-treasury-grant.txt --no-color

Environment variables (agents/.env)
------------------------------------
  ANTHROPIC_API_KEY    – Anthropic API key
  RPC_URL              – JSON-RPC endpoint (default: http://127.0.0.1:8545)
  PROPOSER_KEY         – Private key used to submit the proposal on-chain
  SECURITY_AGENT_KEY   – accounts[1] private key
  ECONOMIC_AGENT_KEY   – accounts[2] private key
  GOVERNANCE_AGENT_KEY – accounts[3] private key
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from web3 import Web3

from _tally import tally_consensus

# ── Paths ─────────────────────────────────────────────────────────────────────
_AGENTS_DIR          = Path(__file__).resolve().parent
_PROJECT_ROOT        = _AGENTS_DIR.parent
_ENV_PATH            = _AGENTS_DIR / ".env"
_INFO_PATH           = _AGENTS_DIR / "contract_info.json"
_SAMPLE_PROPOSALS_DIR = _PROJECT_ROOT / "sample-proposals"
_RESULTS_DIR         = _PROJECT_ROOT / "results"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger("runner")

# ── On-chain recommendation enum (mirrors DAOGovernance.sol) ──────────────────
_REC_LABEL = {0: "APPROVE", 1: "REJECT", 2: "REVISE"}

# ── ANSI colour helpers ───────────────────────────────────────────────────────
_COLOUR_ENABLED = True  # toggled by --no-color


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR_ENABLED else text


def _bold(t: str)   -> str: return _c("1",     t)
def _dim(t: str)    -> str: return _c("2",     t)
def _green(t: str)  -> str: return _c("1;32",  t)
def _red(t: str)    -> str: return _c("1;31",  t)
def _yellow(t: str) -> str: return _c("1;33",  t)
def _cyan(t: str)   -> str: return _c("1;36",  t)
def _white(t: str)  -> str: return _c("1;37",  t)


def _colour_rec(label: str) -> str:
    return {"APPROVE": _green, "REJECT": _red, "REVISE": _yellow}.get(
        label.upper(), _white
    )(label.upper())


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the DAOGovernance multi-agent voting pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              python runner.py --title "Grant" --description "Allocate 50k USDC..."
              python runner.py --file proposal-001-treasury-grant.txt
              python runner.py --file proposal-001-treasury-grant.txt --dry-run
              python runner.py --file proposal-001-treasury-grant.txt --dry-run --json
              python runner.py --file /tmp/custom.txt --no-color
        """),
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--file", "-f",
        metavar="PATH",
        help=(
            "Proposal text file. A bare filename is resolved relative to "
            "/sample-proposals/; an absolute or relative path is used as-is."
        ),
    )
    src.add_argument(
        "--title", "-t",
        metavar="TEXT",
        help="Proposal title (requires --description).",
    )

    parser.add_argument(
        "--description", "-d",
        metavar="TEXT",
        help="Proposal description body (required when --title is used).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run Claude analysis but skip all on-chain transactions. "
            "No Hardhat node or deployed contract required."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help=(
            "Print results as a JSON object to stdout instead of the "
            "ANSI terminal summary. Useful for CI pipelines and scripting."
        ),
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour output.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    if args.title and not args.description:
        parser.error("--description is required when --title is used.")

    return args


# ── Proposal loading ──────────────────────────────────────────────────────────

def _resolve_proposal_file(raw: str) -> Path:
    p = Path(raw)
    if p.parent == Path(".") and not p.is_absolute() and not p.exists():
        candidate = _SAMPLE_PROPOSALS_DIR / p
        if candidate.exists():
            return candidate
    resolved = p if p.is_absolute() else Path.cwd() / p
    return resolved


def _parse_proposal_file(path: Path) -> tuple[str, str]:
    if not path.exists():
        _fatal(f"Proposal file not found: {path}")

    text  = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    first_idx = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first_idx is None:
        _fatal(f"Proposal file is empty: {path}")

    first_line = lines[first_idx].strip()
    dash_match = re.search(r"[—–]\s*(.+)$", first_line)
    title = dash_match.group(1).strip() if dash_match else first_line

    description = "\n".join(lines[first_idx + 1:]).strip()
    if not description:
        _fatal(f"Proposal file has a title but no description body: {path}")

    return title, description


def _load_proposal(args: argparse.Namespace) -> tuple[str, str]:
    if args.file:
        path = _resolve_proposal_file(args.file)
        logger.info("Loading proposal from %s", path)
        return _parse_proposal_file(path)
    return args.title.strip(), args.description.strip()


# ── Web3 / contract setup ─────────────────────────────────────────────────────

def _load_contract_info() -> tuple[str, list]:
    if not _INFO_PATH.exists():
        _fatal(
            f"contract_info.json not found at {_INFO_PATH}.\n"
            "  Run: npx hardhat run scripts/deploy.js --network localhost"
        )
    info = json.loads(_INFO_PATH.read_text(encoding="utf-8"))
    return info["contractAddress"], info["abi"]


def _connect(rpc_url: str, contract_address: str, abi: list) -> tuple[Web3, Any]:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        _fatal(
            f"Cannot connect to the Hardhat node at {rpc_url}.\n"
            "  Run: npx hardhat node"
        )
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(contract_address),
        abi=abi,
    )
    return w3, contract


# ── Proposal submission ───────────────────────────────────────────────────────

def _submit_proposal(
    w3: Web3,
    contract: Any,
    proposer_key: str,
    title: str,
    description: str,
) -> tuple[int, str, int]:
    try:
        account = w3.eth.account.from_key(proposer_key)
    except Exception as exc:
        _fatal(f"Invalid PROPOSER_KEY: {exc}")

    fn = contract.functions.submitProposal(title, description)
    gas_estimate = fn.estimate_gas({"from": account.address})
    tx = fn.build_transaction({
        "from":     account.address,
        "nonce":    w3.eth.get_transaction_count(account.address),
        "gas":      int(gas_estimate * 1.2),
        "gasPrice": w3.eth.gas_price,
    })

    signed  = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

    if receipt.status != 1:
        _fatal(f"submitProposal transaction reverted. tx={tx_hash.hex()}")

    events = contract.events.ProposalSubmitted().process_receipt(receipt)
    if not events:
        _fatal("ProposalSubmitted event not found in receipt.")

    proposal_id = int(events[0]["args"]["proposalId"])
    return proposal_id, receipt.transactionHash.hex(), receipt.blockNumber


# ── Per-agent pipeline (runs in worker thread) ────────────────────────────────

def _agent_pipeline(
    agent: Any,
    proposal_id: int,
    title: str,
    description: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role":           agent.role,
        "address":        agent.address,
        "recommendation": None,
        "confidence":     None,
        "reasoning":      None,
        "vote_tx":        None,
        "vote_block":     None,
        "status":         "error",
        "error":          None,
    }

    try:
        verdict = agent.analyze(title, description)
        result["recommendation"] = verdict["recommendation"]
        result["confidence"]     = verdict["confidence"]
        result["reasoning"]      = verdict["reasoning"]

        if not dry_run:
            receipt = agent.submit_vote(
                proposal_id    = proposal_id,
                recommendation = verdict["recommendation"],
                confidence     = verdict["confidence"],
                reasoning      = verdict["reasoning"],
            )
            result["vote_tx"]    = receipt["tx_hash"]
            result["vote_block"] = receipt["block_number"]

        result["status"] = "success"

    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        logger.error("Agent %s pipeline failed: %s", agent.role, exc)

    return result


# ── Live per-agent output ─────────────────────────────────────────────────────

def _print_agent_live(result: dict[str, Any]) -> None:
    """Print a compact one-line verdict as an individual agent completes."""
    role = result.get("role", "?")
    rec  = result.get("recommendation") or ""
    conf = result.get("confidence")
    ok   = result.get("status") == "success"
    err  = result.get("error") or ""

    icon  = _green("✔") if ok else _red("✗")
    label = _cyan(f"{role.upper():<12}")
    if ok and rec:
        print(f"  {icon}  {label}  {_colour_rec(rec):<30}  {_dim(f'confidence: {conf}/100')}")
    else:
        short_err = (err[:60] + "…") if len(err) > 60 else err
        print(f"  {icon}  {label}  {_red('ERROR')}  {_dim(short_err)}")


# ── Parallel execution ────────────────────────────────────────────────────────

def _run_agents_parallel(
    agents: list[Any],
    proposal_id: int,
    title: str,
    description: str,
    dry_run: bool = False,
    json_output: bool = False,
) -> list[dict[str, Any]]:
    """
    Submit all three agent pipelines to a thread pool and collect results.

    When *json_output* is False, prints each agent's verdict live as it
    arrives. Returns results in canonical order: Security → Economic → Governance.
    """
    role_order = ["Security", "Economic", "Governance"]
    results_by_role: dict[str, dict] = {}

    if not json_output:
        print(f"\n  {_dim('Agents running in parallel — verdicts will appear as each finishes:')}\n")

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="agent") as executor:
        future_to_role: dict[Future, str] = {
            executor.submit(
                _agent_pipeline, agent, proposal_id, title, description, dry_run
            ): agent.role
            for agent in agents
        }

        for future in as_completed(future_to_role):
            role = future_to_role[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = {
                    "role": role, "status": "error", "error": str(exc),
                    "recommendation": None, "confidence": None, "reasoning": None,
                    "vote_tx": None, "vote_block": None, "address": None,
                }
            results_by_role[role] = result
            if not json_output:
                _print_agent_live(result)

    if not json_output:
        print("")

    return [results_by_role[r] for r in role_order if r in results_by_role]


# ── On-chain result read ──────────────────────────────────────────────────────

def _read_final_recommendation(contract: Any, proposal_id: int) -> str | None:
    try:
        rec_int = contract.functions.getFinalRecommendation(proposal_id).call()
        return _REC_LABEL.get(int(rec_int), f"UNKNOWN({rec_int})")
    except Exception as exc:
        logger.warning("Could not read final recommendation: %s", exc)
        return None


# ── Terminal output ───────────────────────────────────────────────────────────

_WIDTH = 66


def _sep(char: str = "═") -> str:
    return _dim(char * _WIDTH)


def _box_top()    -> str: return _dim("╔" + "═" * (_WIDTH - 2) + "╗")
def _box_bottom() -> str: return _dim("╚" + "═" * (_WIDTH - 2) + "╝")
def _box_line(text: str, pad: int = 2) -> str:
    inner = " " * pad + text
    visible_len = len(re.sub(r"\033\[[0-9;]*m", "", inner))
    fill = max(0, _WIDTH - 2 - visible_len)
    return _dim("║") + inner + " " * fill + _dim("║")


def _wrap_reasoning(text: str, indent: int = 4, width: int = 62) -> str:
    lines = textwrap.wrap(text, width=width - indent)
    prefix = " " * indent
    return ("\n" + prefix).join(lines)


def _print_summary(
    proposal_id: int,
    title: str,
    proposal_tx: str,
    proposal_block: int,
    agent_results: list[dict],
    final_rec: str | None,
    save_path: Path,
    dry_run: bool = False,
) -> None:
    p = print

    p("")
    p(_box_top())
    p(_box_line(_bold("  DAOGovernance — Proposal Analysis")))
    p(_box_bottom())
    p("")

    p(f"  {_bold('Proposal #' + str(proposal_id))}  ·  {title}")
    if dry_run:
        p(f"  {_yellow('dry-run mode — no transactions submitted')}")
    else:
        p(f"  {_dim('Tx hash')}  :  {_dim(proposal_tx)}")
        p(f"  {_dim('Block')}    :  {_dim(str(proposal_block))}")
    p("")

    p(_sep())
    p(f"  {_bold('Agent Verdicts')}")
    p(_sep())
    p("")

    for r in agent_results:
        role = r.get("role", "?")
        rec  = r.get("recommendation") or ""
        conf = r.get("confidence")
        text = r.get("reasoning") or ""
        vtx  = r.get("vote_tx")
        vblk = r.get("vote_block")
        err  = r.get("error")
        ok   = r.get("status") == "success"

        role_label = _cyan(f"■ {role.upper():<12}")

        if ok and rec:
            p(f"  {role_label}  {_colour_rec(rec):<30}  {_dim(f'confidence: {conf}/100')}")
            if text:
                p(f"    {_dim(_wrap_reasoning(text))}")
            if vtx:
                p(f"    {_dim('vote tx:')}  {_dim(vtx)}  {_dim('block ' + str(vblk))}")
        else:
            p(f"  {role_label}  {_red('ERROR')}")
            if err:
                p(f"    {_dim(_wrap_reasoning(err))}")
        p("")

    p(_sep())
    if final_rec:
        label = _colour_rec(final_rec)
        verdict_label = "Consensus Recommendation (dry-run)" if dry_run else "Final On-Chain Recommendation"
        p(f"  {_bold('✦  ' + verdict_label + ':')}  {label}")
    else:
        p(f"  {_yellow('⚠  Proposal not fully decided (fewer than 3 votes cast).')}")
    p(_sep())
    p("")

    p(f"  {_dim('Results saved →')}  {save_path}")
    p("")


# ── Results persistence ───────────────────────────────────────────────────────

def _build_payload(
    proposal_id: int,
    title: str,
    description: str,
    proposal_tx: str,
    proposal_block: int,
    agent_results: list[dict],
    final_rec: str | None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Construct the results payload dict."""
    return {
        "proposal_id":          proposal_id,
        "title":                title,
        "description":          description,
        "proposal_tx":          proposal_tx,
        "proposal_block":       proposal_block,
        "run_at":               datetime.now(timezone.utc).isoformat(),
        "dry_run":              dry_run,
        "agents":               agent_results,
        "final_recommendation": final_rec,
    }


def _save_results(payload: dict[str, Any]) -> Path:
    """Serialise *payload* to /results/proposal_<id>_results.json."""
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _RESULTS_DIR / f"proposal_{payload['proposal_id']}_results.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


# ── dry-run consensus helper ──────────────────────────────────────────────────
# tally_consensus (from _tally.py) implements the shared logic; the wrapper
# below adds the dry-run-specific < 3 votes guard before delegating.

def _derive_dry_run_consensus(agent_results: list[dict]) -> str | None:
    """Mirror the on-chain _finalise tally logic in Python for dry-run mode."""
    recs = [r["recommendation"] for r in agent_results if r.get("recommendation")]
    if len(recs) < 3:
        return None
    return tally_consensus(agent_results)
            return priority

    return None


# ── Utilities ─────────────────────────────────────────────────────────────────

def _fatal(message: str) -> None:
    print(f"\n{_red('✗  Error:')} {message}\n", file=sys.stderr)
    sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    global _COLOUR_ENABLED
    _COLOUR_ENABLED = not args.no_color and sys.stdout.isatty()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    load_dotenv(_ENV_PATH)

    dry_run     = args.dry_run
    json_output = args.json_output

    # ── 1. Load proposal text ─────────────────────────────────────────────────
    title, description = _load_proposal(args)

    # ── 2. On-chain setup (skipped in dry-run) ────────────────────────────────
    if dry_run:
        if not json_output:
            print(f"\n{_yellow('dry-run mode — skipping all on-chain transactions')}")
        proposal_id    = 0
        proposal_tx    = "dry-run"
        proposal_block = 0
        contract       = None
    else:
        rpc_url      = os.getenv("RPC_URL", "http://127.0.0.1:8545").strip()
        proposer_key = os.getenv("PROPOSER_KEY", "").strip()
        if not proposer_key:
            _fatal(
                "PROPOSER_KEY is not set in agents/.env.\n"
                "  For a local Hardhat node use accounts[0]:\n"
                "  PROPOSER_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
            )

        contract_address, abi = _load_contract_info()
        w3, contract = _connect(rpc_url, contract_address, abi)

        if not json_output:
            print(f"\n{_dim('Submitting proposal to DAOGovernance…')}")
        proposal_id, proposal_tx, proposal_block = _submit_proposal(
            w3, contract, proposer_key, title, description
        )
        if not json_output:
            print(f"{_dim('  ✔ Proposal')} #{proposal_id} {_dim('confirmed (block ' + str(proposal_block) + ')')}")

    # ── 3. Instantiate agents ─────────────────────────────────────────────────
    try:
        from security_agent   import SecurityAgent
        from economic_agent   import EconomicAgent
        from governance_agent import GovernanceAgent
    except ImportError as exc:
        _fatal(f"Could not import agent class: {exc}")

    if not json_output:
        print(_dim("Initialising agents…"))
    try:
        agents = [SecurityAgent(), EconomicAgent(), GovernanceAgent()]
    except Exception as exc:
        _fatal(f"Agent initialisation failed: {exc}")

    if not json_output:
        print(_dim(f"  ✔ {len(agents)} agents ready — running analysis in parallel…"))

    # ── 4. Run agents concurrently ────────────────────────────────────────────
    agent_results = _run_agents_parallel(
        agents, proposal_id, title, description,
        dry_run=dry_run, json_output=json_output,
    )

    successes = sum(1 for r in agent_results if r["status"] == "success")
    failures  = len(agent_results) - successes
    if failures and not json_output:
        print(_yellow(f"  ⚠  {failures} agent(s) encountered errors."), file=sys.stderr)

    # ── 5. Get final recommendation ───────────────────────────────────────────
    if dry_run:
        final_rec = _derive_dry_run_consensus(agent_results)
    else:
        final_rec = _read_final_recommendation(contract, proposal_id)

    # ── 6. Build and persist payload ──────────────────────────────────────────
    payload   = _build_payload(
        proposal_id, title, description,
        proposal_tx, proposal_block,
        agent_results, final_rec,
        dry_run=dry_run,
    )
    save_path = _save_results(payload)

    # ── 7. Output ─────────────────────────────────────────────────────────────
    if json_output:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_summary(
            proposal_id, title,
            proposal_tx, proposal_block,
            agent_results, final_rec,
            save_path,
            dry_run=dry_run,
        )

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
