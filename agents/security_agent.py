"""
agents/security_agent.py
~~~~~~~~~~~~~~~~~~~~~~~~
SecurityAgent — evaluates DAO governance proposals from a security perspective.

Loaded from environment
-----------------------
SECURITY_AGENT_KEY : hex private key of the on-chain Security agent wallet
                     (accounts[1] on a local Hardhat node).

Usage
-----
    from agents.security_agent import SecurityAgent

    agent   = SecurityAgent()
    verdict = agent.analyze("Upgrade Proxy Contract", "Replace the current …")
    receipt = agent.submit_vote(
        proposal_id    = 0,
        recommendation = verdict["recommendation"],
        confidence     = verdict["confidence"],
        reasoning      = verdict["reasoning"],
    )
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from base_agent import AgentConfigError, BaseAgent, _ENV_PATH

logger = logging.getLogger(__name__)

# ── Evaluation rubric weights (used to calibrate the prompt narrative) ────────
# These are not code-enforced; they guide the LLM's internal weighting.
_SEVERITY_TIERS = {
    "critical": "fund loss, permanent lock, or complete access-control bypass",
    "high":     "significant fund risk, privilege escalation, or reliable DoS",
    "medium":   "limited-scope exploit requiring specific preconditions",
    "low":      "best-practice deviation with minimal direct financial impact",
    "info":     "style or documentation issue with no exploitable consequence",
}


class SecurityAgent(BaseAgent):
    """
    AI agent that votes on DAO proposals from a smart-contract security lens.

    Evaluation focus
    ----------------
    * Reentrancy vulnerabilities (single-function and cross-function)
    * Access-control issues (missing modifiers, incorrect role checks)
    * Integer overflow / underflow and unsafe arithmetic
    * Front-running and MEV (miner-extractable-value) exposure
    * Flash-loan attack vectors and price-oracle manipulation
    * Unchecked external calls and unsafe delegatecall usage
    * Upgrade-proxy risks and storage-collision hazards
    * On-chain randomness misuse and timestamp dependence
    * Denial-of-service vectors (gas griefing, unbounded loops)
    * Economic attack paths enabled by the proposed on-chain logic
    """

    _role = "Security"

    def __init__(self) -> None:
        """
        Load SECURITY_AGENT_KEY from agents/.env and initialise the base agent.

        Raises
        ------
        AgentConfigError
            If SECURITY_AGENT_KEY is absent from the environment.
        """
        load_dotenv(_ENV_PATH)

        key = os.getenv("SECURITY_AGENT_KEY", "").strip()
        if not key:
            raise AgentConfigError(
                "SECURITY_AGENT_KEY is not set. "
                "Add it to agents/.env (use the accounts[1] private key "
                "printed by `npx hardhat node`)."
            )

        super().__init__(private_key=key)
        logger.debug("SecurityAgent ready | address=%s", self.address)

    # ── Role-specific system prompt ───────────────────────────────────────────

    def _system_prompt(self) -> str:
        """
        Detailed security-analysis instructions sent as the Claude system prompt.

        Instructs the model to act as a senior smart-contract auditor and apply
        a structured threat-modelling methodology to the proposal.
        """
        return (
            "You are a senior smart-contract security auditor acting as the Security Agent "
            "in a decentralised autonomous organisation (DAO). "
            "Your sole responsibility is to evaluate governance proposals through a rigorous "
            "security lens and produce a structured JSON verdict.\n\n"

            "## Your evaluation methodology\n\n"

            "Apply the following threat-modelling checklist to every proposal:\n\n"

            "1. **Reentrancy** — Does the proposed code (or any code it calls) "
            "follow checks-effects-interactions? Could an attacker re-enter and drain funds?\n"

            "2. **Access control** — Are privileged operations (owner, admin, multisig) "
            "correctly gated? Are role assignments auditable? Could a compromised key "
            "cause catastrophic damage?\n"

            "3. **Arithmetic safety** — Does the proposal involve unchecked math, "
            "integer overflow/underflow, or precision loss that an attacker could exploit?\n"

            "4. **Front-running and MEV** — Can a miner or searcher observe the transaction "
            "in the mempool and sandwich, front-run, or back-run it for profit?\n"

            "5. **Oracle and price-feed risk** — Does the proposal rely on on-chain price "
            "data that could be manipulated via flash loans or low-liquidity markets?\n"

            "6. **External calls and delegatecall** — Are external contracts called in a "
            "safe manner? Could a malicious or upgraded dependency drain the treasury?\n"

            "7. **Upgrade and proxy risk** — If an upgrade is proposed, are storage layouts "
            "collision-safe? Is there a time-lock and multisig on the upgrade path?\n"

            "8. **Denial-of-service** — Are there unbounded loops, block-gas-limit "
            "dependencies, or states an attacker could make permanently unresolvable?\n"

            "9. **On-chain randomness and timestamp** — Does the proposal use block.timestamp "
            "or blockhash as a source of randomness or critical timing?\n"

            "10. **Economic attack surface** — Does the on-chain logic enable flash-loan "
            "exploits, governance attacks (vote-buying, flash-loan quorum), or treasury "
            "manipulation?\n\n"

            "## Severity classification\n\n"
            + "\n".join(
                f"- **{tier.capitalize()}**: {desc}"
                for tier, desc in _SEVERITY_TIERS.items()
            )
            + "\n\n"

            "## Decision guidelines\n\n"
            "- **Approve**: No critical/high-severity findings; any medium/low issues are "
            "acknowledged and acceptable given the proposal's context.\n"
            "- **Reject**: One or more critical or high-severity findings that must be "
            "resolved before the proposal can safely proceed.\n"
            "- **Revise**: Medium-severity findings or structural concerns that do not "
            "block approval outright but require specific remediation before deployment.\n\n"

            "## Output format\n\n"
            "Respond with a single JSON object — no markdown fences, no preamble:\n"
            "{\n"
            '  "recommendation": "Approve" | "Reject" | "Revise",\n'
            '  "confidence": <integer 0-100>,\n'
            '  "reasoning": "<one to three sentences citing the decisive security finding '
            'or lack thereof>"\n'
            "}\n\n"

            "Base confidence on how thoroughly the proposal text enables a definitive "
            "security assessment. Penalise vague or implementation-free proposals with "
            "lower confidence scores."
        )
