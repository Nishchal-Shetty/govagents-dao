"""
agents/base_agent.py
~~~~~~~~~~~~~~~~~~~~
BaseAgent – shared foundation for all DAOGovernance AI voting agents.

Concrete agents (security_agent.py, economic_agent.py, governance_agent.py)
inherit from this class, set the class-level ``_role`` attribute, and
optionally override ``_system_prompt()`` to sharpen their analysis focus.

Typical usage
-------------
    from agents.security_agent import SecurityAgent

    agent = SecurityAgent(private_key="0xabc...")
    verdict = agent.analyze("Treasury Grant", "Allocate 50 000 USDC…")
    receipt = agent.submit_vote(
        proposal_id    = 0,
        recommendation = verdict["recommendation"],
        confidence     = verdict["confidence"],
        reasoning      = verdict["reasoning"],
    )
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError

# ── Module logger ─────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ── Filesystem paths ──────────────────────────────────────────────────────────
_AGENTS_DIR = Path(__file__).resolve().parent
_ENV_PATH   = _AGENTS_DIR / ".env"
_INFO_PATH  = _AGENTS_DIR / "contract_info.json"

# ── On-chain enum mirrors (must stay in sync with DAOGovernance.sol) ──────────
#   enum AgentRole      { Security=0, Economic=1, Governance=2 }
#   enum Recommendation { Approve=0,  Reject=1,   Revise=2    }

AGENT_ROLE: dict[str, int] = {"Security": 0, "Economic": 1, "Governance": 2}

RECOMMENDATION: dict[str, int]     = {"Approve": 0, "Reject": 1, "Revise": 2}
RECOMMENDATION_INV: dict[int, str] = {v: k for k, v in RECOMMENDATION.items()}

# Case-insensitive lookup used when normalising Claude's free-text response
_REC_NORMALISE: dict[str, str] = {k.lower(): k for k in RECOMMENDATION}

# Claude model used for all analysis calls
_MODEL = "claude-sonnet-4-20250514"

# Retry configuration for transient Anthropic API errors
_MAX_RETRIES   = 3
_RETRY_BASE_DELAY = 2  # seconds; actual delay = _RETRY_BASE_DELAY ** attempt


# ── Custom exceptions ─────────────────────────────────────────────────────────

class AgentConfigError(Exception):
    """Raised when the agent cannot initialise due to missing config or connectivity."""


class AnalysisError(Exception):
    """Raised when the Anthropic API call fails or its response cannot be parsed."""


class VoteSubmissionError(Exception):
    """Raised when the on-chain vote transaction cannot be built, sent, or confirmed."""


# ── Base class ────────────────────────────────────────────────────────────────

class BaseAgent:
    """
    Foundation for a DAOGovernance AI voting agent.

    Subclass responsibilities
    -------------------------
    * Set ``_role`` to ``"Security"``, ``"Economic"``, or ``"Governance"``.
    * Optionally override ``_system_prompt()`` with role-specific instructions.

    Constructor arguments
    ---------------------
    private_key : str
        Hex private key of the wallet registered on-chain for this agent
        (e.g. the accounts[1] / accounts[2] / accounts[3] key printed by
        ``npx hardhat node``).
    """

    # Subclasses must assign one of: "Security" | "Economic" | "Governance"
    _role: str = ""

    # ── Initialisation ────────────────────────────────────────────────────────

    def __init__(self, private_key: str) -> None:
        self._validate_role()

        # 1. Load environment variables from agents/.env
        loaded = load_dotenv(_ENV_PATH)
        if not loaded:
            logger.warning("No .env file found at %s; falling back to environment", _ENV_PATH)

        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise AgentConfigError(
                "ANTHROPIC_API_KEY is missing. "
                "Copy agents/.env.example → agents/.env and fill it in."
            )

        rpc_url = os.getenv("RPC_URL", "http://127.0.0.1:8545").strip()

        # 2. Load contract address and ABI from contract_info.json
        contract_address, abi = self._load_contract_info()

        # 3. Connect to the Hardhat node
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not self._w3.is_connected():
            raise AgentConfigError(
                f"Cannot connect to the Hardhat node at {rpc_url}. "
                "Start it with: npx hardhat node"
            )

        # 4. Derive the on-chain account from the private key
        try:
            self._account = self._w3.eth.account.from_key(private_key)
        except Exception as exc:
            raise AgentConfigError(f"Invalid private key: {exc}") from exc

        # 5. Bind the contract
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=abi,
        )

        # 6. Create the Anthropic client
        self._anthropic = anthropic.Anthropic(api_key=api_key)

        logger.info(
            "%s initialised | address=%s  contract=%s  rpc=%s",
            type(self).__name__,
            self._account.address,
            contract_address,
            rpc_url,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def role(self) -> str:
        """Agent role: ``"Security"``, ``"Economic"``, or ``"Governance"``."""
        return self._role

    @property
    def address(self) -> str:
        """Checksummed wallet address this agent signs transactions with."""
        return self._account.address

    # ── Subclass extension point ──────────────────────────────────────────────

    def _system_prompt(self) -> str:
        """
        Role-specific system instruction sent as the Claude ``system`` parameter.

        Subclasses may override this to refine the analysis focus.
        The default implementation provides a solid role-aware starting prompt.
        """
        prompts: dict[str, str] = {
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
        return prompts[self._role]

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(
        self,
        proposal_title: str,
        proposal_description: str,
    ) -> dict[str, Any]:
        """
        Evaluate a proposal via the Anthropic API and return a structured verdict.

        The method sends a role-specific system prompt together with the proposal
        text to ``claude-sonnet-4-20250514`` and parses the JSON response.

        Parameters
        ----------
        proposal_title:
            Short title of the proposal (used as context for Claude).
        proposal_description:
            Full body text of the proposal.

        Returns
        -------
        dict with keys:
            ``role``           (str) – this agent's role label
            ``recommendation`` (str) – ``"Approve"`` | ``"Reject"`` | ``"Revise"``
            ``confidence``     (int) – 0–100
            ``reasoning``      (str) – one-to-three sentence rationale

        Raises
        ------
        AnalysisError
            On API connection failure, rate-limit, non-200 status, JSON parse
            error, missing fields, or out-of-range values.
        """
        if not proposal_title.strip():
            raise AnalysisError("proposal_title must not be empty.")
        if not proposal_description.strip():
            raise AnalysisError("proposal_description must not be empty.")

        user_message = (
            f"Proposal title: {proposal_title}\n\n"
            f"Proposal description:\n{proposal_description}\n\n"
            "Evaluate this proposal from your designated role perspective and respond "
            "with a JSON object containing exactly these three fields:\n"
            '  "recommendation" : one of "Approve", "Reject", or "Revise"\n'
            '  "confidence"     : integer from 0 (completely uncertain) to '
            "100 (fully certain)\n"
            '  "reasoning"      : one to three sentences explaining your decision\n\n'
            "Return ONLY the JSON object — no markdown fences, no preamble, no extra text."
        )

        logger.debug(
            "Calling %s | role=%s title=%r", _MODEL, self._role, proposal_title
        )

        raw = self._call_anthropic(user_message)
        result = self._parse_claude_response(raw)

        logger.info(
            "Analysis done | role=%s rec=%s confidence=%s",
            self._role,
            result["recommendation"],
            result["confidence"],
        )
        return result

    def submit_vote(
        self,
        proposal_id: int,
        recommendation: str | int,
        confidence: int,
        reasoning: str,
    ) -> dict[str, Any]:
        """
        Build, sign, and broadcast a ``submitVote`` transaction to DAOGovernance.

        Parameters
        ----------
        proposal_id:
            On-chain proposal ID (uint256).
        recommendation:
            ``"Approve"`` / ``"Reject"`` / ``"Revise"``  or the equivalent
            integer (0 / 1 / 2).
        confidence:
            Self-reported confidence score, 0–100.
        reasoning:
            Plain-text rationale stored on-chain as a string.

        Returns
        -------
        dict with keys:
            ``tx_hash``        (str)  – 0x-prefixed transaction hash
            ``block_number``   (int)  – block the transaction was mined in
            ``proposal_id``    (int)  – echoed back
            ``role``           (str)  – this agent's role label
            ``role_index``     (int)  – on-chain ``AgentRole`` enum value
            ``recommendation`` (str)  – human-readable label
            ``rec_index``      (int)  – on-chain ``Recommendation`` enum value
            ``confidence``     (int)  – echoed back
            ``reasoning``      (str)  – echoed back
            ``status``         (str)  – ``"success"``

        Raises
        ------
        VoteSubmissionError
            If confidence is out of range, the transaction cannot be built
            (e.g. agent not registered, already voted, proposal decided),
            fails to broadcast, or is reverted on-chain.
        """
        if not (0 <= confidence <= 100):
            raise VoteSubmissionError(
                f"confidence must be 0–100, got {confidence}."
            )

        rec_index  = self._resolve_recommendation(recommendation)
        role_index = AGENT_ROLE[self._role]
        rec_label  = RECOMMENDATION_INV[rec_index]

        logger.debug(
            "Building vote tx | proposal=%s role=%s(%s) rec=%s(%s) confidence=%s",
            proposal_id, self._role, role_index, rec_label, rec_index, confidence,
        )

        # ── Build transaction ─────────────────────────────────────────────
        try:
            fn = self._contract.functions.submitVote(
                proposal_id,
                role_index,
                rec_index,
                confidence,
                reasoning,
            )
            gas_estimate = fn.estimate_gas({"from": self._account.address})
            tx = fn.build_transaction({
                "from":     self._account.address,
                "nonce":    self._w3.eth.get_transaction_count(self._account.address),
                "gas":      int(gas_estimate * 1.2),
                "gasPrice": self._w3.eth.gas_price,
            })
        except ContractLogicError as exc:
            # Contract reverted during eth_call simulation (e.g. already voted,
            # proposal decided, agent not registered).
            raise VoteSubmissionError(
                f"Contract rejected the vote (simulation): {exc}"
            ) from exc
        except Exception as exc:
            raise VoteSubmissionError(
                f"Failed to build vote transaction: {exc}"
            ) from exc

        # ── Sign & broadcast ──────────────────────────────────────────────
        try:
            signed  = self._account.sign_transaction(tx)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception as exc:
            raise VoteSubmissionError(
                f"Failed to broadcast vote transaction: {exc}"
            ) from exc

        # ── Wait for receipt ──────────────────────────────────────────────
        try:
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        except Exception as exc:
            raise VoteSubmissionError(
                f"Timed out waiting for vote receipt (tx={tx_hash.hex()}): {exc}"
            ) from exc

        # ── Check on-chain status ─────────────────────────────────────────
        if receipt.status != 1:
            raise VoteSubmissionError(
                f"Vote transaction was reverted on-chain. "
                f"tx_hash={receipt.transactionHash.hex()}"
            )

        logger.info(
            "Vote confirmed | tx=%s block=%s proposal=%s role=%s rec=%s",
            receipt.transactionHash.hex(),
            receipt.blockNumber,
            proposal_id,
            self._role,
            rec_label,
        )

        return {
            "tx_hash":        receipt.transactionHash.hex(),
            "block_number":   receipt.blockNumber,
            "proposal_id":    proposal_id,
            "role":           self._role,
            "role_index":     role_index,
            "recommendation": rec_label,
            "rec_index":      rec_index,
            "confidence":     confidence,
            "reasoning":      reasoning,
            "status":         "success",
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _validate_role(self) -> None:
        """Raise AgentConfigError if the subclass forgot to set _role."""
        if not self._role or self._role not in AGENT_ROLE:
            raise AgentConfigError(
                f"{type(self).__name__}._role must be one of "
                f"{list(AGENT_ROLE)}, got {self._role!r}. "
                "Did you forget to set it in your subclass?"
            )

    @staticmethod
    def _load_contract_info() -> tuple[str, list]:
        """Read contract_info.json and return (address, abi)."""
        if not _INFO_PATH.exists():
            raise AgentConfigError(
                f"contract_info.json not found at {_INFO_PATH}. "
                "Run the deploy script first: "
                "npx hardhat run scripts/deploy.js --network localhost"
            )
        try:
            info = json.loads(_INFO_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AgentConfigError(
                f"contract_info.json is not valid JSON: {exc}"
            ) from exc

        address = info.get("contractAddress", "")
        abi     = info.get("abi", [])

        if not address:
            raise AgentConfigError(
                "contract_info.json is missing 'contractAddress'."
            )
        if not abi:
            raise AgentConfigError(
                "contract_info.json is missing 'abi' or it is empty."
            )

        return address, abi

    def _call_anthropic(self, user_message: str) -> str:
        """
        Send a message to Claude and return the raw text response.

        Retries up to ``_MAX_RETRIES`` times on transient errors
        (rate-limit or connection failures) with exponential backoff.
        Non-retryable errors (bad status codes, etc.) are raised immediately.
        """
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                response = self._anthropic.messages.create(
                    model=_MODEL,
                    max_tokens=512,
                    system=self._system_prompt(),
                    messages=[{"role": "user", "content": user_message}],
                )
                if not response.content:
                    raise AnalysisError("Anthropic returned an empty response.")
                return response.content[0].text.strip()

            except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BASE_DELAY ** (attempt + 1)
                    logger.warning(
                        "Transient error on attempt %d/%d (%s) — retrying in %ds",
                        attempt + 1, _MAX_RETRIES, type(exc).__name__, delay,
                    )
                    time.sleep(delay)

            except anthropic.APIStatusError as exc:
                raise AnalysisError(
                    f"Anthropic API returned HTTP {exc.status_code}: {exc.message}"
                ) from exc

            except anthropic.APIError as exc:
                raise AnalysisError(f"Anthropic API error: {exc}") from exc

        raise AnalysisError(
            f"Anthropic API call failed after {_MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc

    def _parse_claude_response(self, raw: str) -> dict[str, Any]:
        """
        Parse the JSON verdict returned by Claude.

        Strips markdown code fences defensively, then validates all three
        required fields.
        """
        # Strip markdown code fences (```json ... ```) if Claude adds them
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise AnalysisError(
                f"Claude response is not valid JSON.\n"
                f"Parse error: {exc}\n"
                f"Raw response:\n{raw}"
            ) from exc

        if not isinstance(data, dict):
            raise AnalysisError(
                f"Expected a JSON object, got {type(data).__name__}.\n"
                f"Raw response:\n{raw}"
            )

        # ── recommendation ────────────────────────────────────────────────
        rec_raw   = str(data.get("recommendation", "")).strip()
        rec_label = _REC_NORMALISE.get(rec_raw.lower())
        if rec_label is None:
            raise AnalysisError(
                f"Invalid recommendation {rec_raw!r}. "
                f"Expected one of: {list(RECOMMENDATION)}."
            )

        # ── confidence ────────────────────────────────────────────────────
        raw_conf = data.get("confidence")
        if raw_conf is None:
            raise AnalysisError("Claude response is missing the 'confidence' field.")
        try:
            confidence = int(raw_conf)
        except (TypeError, ValueError) as exc:
            raise AnalysisError(
                f"'confidence' must be an integer, got {raw_conf!r}."
            ) from exc
        if not (0 <= confidence <= 100):
            raise AnalysisError(
                f"'confidence' value {confidence} is out of range [0, 100]."
            )

        # ── reasoning ─────────────────────────────────────────────────────
        reasoning = str(data.get("reasoning", "")).strip()
        if not reasoning:
            raise AnalysisError("Claude returned an empty 'reasoning' field.")

        return {
            "role":           self._role,
            "recommendation": rec_label,
            "confidence":     confidence,
            "reasoning":      reasoning,
        }

    def _resolve_recommendation(self, value: str | int) -> int:
        """
        Normalise a recommendation to its on-chain integer enum value.

        Accepts: ``"Approve"`` / ``"approve"`` / ``0``  (and equivalents).
        """
        if isinstance(value, int):
            if value not in RECOMMENDATION_INV:
                raise VoteSubmissionError(
                    f"Recommendation integer {value} is not valid. "
                    f"Expected one of {list(RECOMMENDATION_INV)}."
                )
            return value

        label = _REC_NORMALISE.get(str(value).strip().lower())
        if label is None:
            raise VoteSubmissionError(
                f"Recommendation string {value!r} is not valid. "
                f"Expected one of: {list(RECOMMENDATION)}."
            )
        return RECOMMENDATION[label]

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} "
            f"role={self._role} "
            f"address={self.address}>"
        )
