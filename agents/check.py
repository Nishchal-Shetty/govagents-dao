#!/usr/bin/env python3
"""
agents/check.py
~~~~~~~~~~~~~~~
Pre-flight environment check — verifies all prerequisites before running
the main pipeline.

Checks performed
----------------
1. ANTHROPIC_API_KEY is set in agents/.env
2. Hardhat / JSON-RPC node is reachable at RPC_URL
3. contract_info.json exists and contains a valid address + ABI
4. All four private keys (PROPOSER, SECURITY, ECONOMIC, GOVERNANCE) are set
5. All three agent wallets are registered on-chain

Usage
-----
    cd agents
    python check.py

Exit codes
----------
    0  All checks passed
    1  One or more checks failed
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_AGENTS_DIR = Path(__file__).resolve().parent
_ENV_PATH   = _AGENTS_DIR / ".env"
_INFO_PATH  = _AGENTS_DIR / "contract_info.json"

_OK   = "  \033[1;32m✔\033[0m"
_FAIL = "  \033[1;31m✗\033[0m"
_WARN = "  \033[1;33m⚠\033[0m"


def _ok(msg: str)   -> None: print(f"{_OK}  {msg}")
def _fail(msg: str) -> None: print(f"{_FAIL}  {msg}")
def _warn(msg: str) -> None: print(f"{_WARN}  {msg}")


def main() -> None:
    load_dotenv(_ENV_PATH)

    passed = True

    print("\nDAOGovernance — pre-flight check\n" + "─" * 36)

    # ── 1. Anthropic API key ──────────────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        _ok(f"ANTHROPIC_API_KEY is set ({api_key[:8]}…)")
    else:
        _fail("ANTHROPIC_API_KEY is missing — add it to agents/.env")
        passed = False

    # ── 2. RPC connectivity ───────────────────────────────────────────────────
    rpc_url = os.getenv("RPC_URL", "http://127.0.0.1:8545").strip()
    w3 = contract = contract_info = None
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 3}))
        if w3.is_connected():
            block = w3.eth.block_number
            _ok(f"RPC reachable at {rpc_url}  (latest block #{block})")
        else:
            _fail(f"RPC not reachable at {rpc_url} — run: npx hardhat node")
            passed = False
            w3 = None
    except Exception as exc:
        _fail(f"RPC error ({rpc_url}): {exc}")
        passed = False

    # ── 3. contract_info.json ─────────────────────────────────────────────────
    if _INFO_PATH.exists():
        try:
            contract_info = json.loads(_INFO_PATH.read_text(encoding="utf-8"))
            addr = contract_info.get("contractAddress", "")
            abi  = contract_info.get("abi", [])
            if addr and abi:
                _ok(f"contract_info.json present — contract at {addr}")
            else:
                _fail("contract_info.json is missing 'contractAddress' or 'abi'")
                passed = False
                contract_info = None
        except Exception as exc:
            _fail(f"contract_info.json parse error: {exc}")
            passed = False
    else:
        _fail(
            "contract_info.json not found — run: "
            "npx hardhat run scripts/deploy.js --network localhost"
        )
        passed = False

    # ── 4. Private keys ───────────────────────────────────────────────────────
    key_vars = [
        ("PROPOSER_KEY",         "Proposer"),
        ("SECURITY_AGENT_KEY",   "Security agent"),
        ("ECONOMIC_AGENT_KEY",   "Economic agent"),
        ("GOVERNANCE_AGENT_KEY", "Governance agent"),
    ]
    for var, label in key_vars:
        val = os.getenv(var, "").strip()
        if val:
            _ok(f"{var} is set  ({label})")
        else:
            _fail(f"{var} is missing  ({label})")
            passed = False

    # ── 5. On-chain agent registration ────────────────────────────────────────
    if w3 and contract_info:
        try:
            from web3 import Web3 as _Web3
            c = w3.eth.contract(
                address=_Web3.to_checksum_address(contract_info["contractAddress"]),
                abi=contract_info["abi"],
            )
            count   = c.functions.registeredAgentCount().call()
            slots   = c.functions.getAgents().call()
            if count == 3:
                _ok(f"All 3 agent wallets registered on-chain")
            else:
                _warn(f"Only {count}/3 agents registered — re-run the deploy script")
                passed = False
            for i, addr in enumerate(slots[:count]):
                print(f"       slot {i}: {addr}")
        except Exception as exc:
            _fail(f"Could not read on-chain agent registration: {exc}")
            passed = False

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if passed:
        print("  All checks passed — ready to run runner.py\n")
    else:
        print("  One or more checks failed. Fix the issues above before running runner.py.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
