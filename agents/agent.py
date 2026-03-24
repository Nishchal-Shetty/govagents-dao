"""
DAO Governance Agent
--------------------
Uses the Anthropic API to evaluate governance proposals and submit votes
(with a confidence score) to the AgentDAO smart contract via web3.py.

Usage:
    python agent.py --proposal-id 0 --rpc http://127.0.0.1:8545 \
                    --contract 0xYourContractAddress \
                    --private-key 0xYourPrivateKey
"""

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic
from web3 import Web3

# ── ABI (only the functions this agent needs) ────────────────────────────────

AGENT_DAO_ABI = [
    {
        "inputs": [],
        "name": "proposalCount",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "name": "proposals",
        "outputs": [
            {"internalType": "uint256", "name": "id", "type": "uint256"},
            {"internalType": "string", "name": "description", "type": "string"},
            {"internalType": "address", "name": "proposer", "type": "address"},
            {"internalType": "uint256", "name": "createdAt", "type": "uint256"},
            {"internalType": "uint256", "name": "deadline", "type": "uint256"},
            {"internalType": "bool", "name": "finalized", "type": "bool"},
            {"internalType": "uint256", "name": "weightedYes", "type": "uint256"},
            {"internalType": "uint256", "name": "weightedNo", "type": "uint256"},
            {"internalType": "uint256", "name": "weightedAbstain", "type": "uint256"},
            {"internalType": "string", "name": "finalRecommendation", "type": "string"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "proposalId", "type": "uint256"},
            {"internalType": "uint8", "name": "option", "type": "uint8"},
            {"internalType": "uint8", "name": "confidence", "type": "uint8"},
            {"internalType": "string", "name": "rationale", "type": "string"},
        ],
        "name": "submitVote",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

# ── Vote option mapping ───────────────────────────────────────────────────────

VOTE_OPTIONS = {"yes": 1, "no": 2, "abstain": 3}

# ── Claude evaluation ─────────────────────────────────────────────────────────

def evaluate_proposal(description: str) -> dict:
    """
    Ask Claude to evaluate a governance proposal.

    Returns a dict with keys:
        vote       – "yes" | "no" | "abstain"
        confidence – int 1-100
        rationale  – str
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""You are an AI governance agent evaluating a DAO proposal.

Proposal description:
{description}

Respond with a JSON object containing exactly these fields:
  "vote"       : one of "yes", "no", or "abstain"
  "confidence" : integer between 1 and 100 (your confidence in the decision)
  "rationale"  : one or two sentences explaining your reasoning

Return only the JSON object, no additional text."""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    result = json.loads(raw)

    # Validate
    assert result["vote"] in VOTE_OPTIONS, f"Unexpected vote value: {result['vote']}"
    confidence = int(result["confidence"])
    assert 1 <= confidence <= 100, f"Confidence out of range: {confidence}"

    return {
        "vote": result["vote"],
        "confidence": confidence,
        "rationale": result["rationale"],
    }


# ── On-chain submission ───────────────────────────────────────────────────────

def submit_vote_on_chain(
    w3: Web3,
    contract,
    account,
    proposal_id: int,
    vote: str,
    confidence: int,
    rationale: str,
) -> str:
    option = VOTE_OPTIONS[vote]
    tx = contract.functions.submitVote(
        proposal_id, option, confidence, rationale
    ).build_transaction(
        {
            "from": account.address,
            "nonce": w3.eth.get_transaction_count(account.address),
            "gas": 300_000,
            "gasPrice": w3.eth.gas_price,
        }
    )
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    return receipt.transactionHash.hex()


# ── CLI entry-point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AgentDAO governance voter")
    parser.add_argument("--proposal-id", type=int, required=True)
    parser.add_argument("--rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--contract", required=True, help="AgentDAO contract address")
    parser.add_argument("--private-key", required=True, help="Agent wallet private key")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: ANTHROPIC_API_KEY environment variable is not set.")

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.exit(f"Cannot connect to RPC at {args.rpc}")

    account = w3.eth.account.from_key(args.private_key)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(args.contract),
        abi=AGENT_DAO_ABI,
    )

    # Fetch proposal description
    proposal = contract.functions.proposals(args.proposal_id).call()
    description = proposal[1]  # index 1 = description field
    print(f"Evaluating proposal #{args.proposal_id}: {description!r}")

    # Ask Claude
    result = evaluate_proposal(description)
    print(
        f"Claude decision → vote={result['vote']}, "
        f"confidence={result['confidence']}, "
        f"rationale={result['rationale']!r}"
    )

    # Submit on-chain
    tx_hash = submit_vote_on_chain(
        w3,
        contract,
        account,
        args.proposal_id,
        result["vote"],
        result["confidence"],
        result["rationale"],
    )
    print(f"Vote submitted. tx hash: {tx_hash}")


if __name__ == "__main__":
    main()
