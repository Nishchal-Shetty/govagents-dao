"""
agents/governance_agent.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
GovernanceAgent — evaluates DAO governance proposals from a governance perspective.

Loaded from environment
-----------------------
GOVERNANCE_AGENT_KEY : hex private key of the on-chain Governance agent wallet
                       (accounts[3] on a local Hardhat node).

Usage
-----
    from agents.governance_agent import GovernanceAgent

    agent   = GovernanceAgent()
    verdict = agent.analyze("Agent Weight Update", "Rebalance voting weights …")
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

# ── Governance principles used to calibrate the prompt narrative ──────────────
# These mirror common DAO constitutional values and are referenced in the prompt.
_GOVERNANCE_PRINCIPLES = {
    "decentralisation":  "no single actor should gain disproportionate control",
    "transparency":      "all significant decisions must be publicly auditable",
    "participation":     "quorum thresholds must prevent minority capture",
    "reversibility":     "irreversible actions require higher consensus thresholds",
    "proportionality":   "the power granted must match the stated need",
    "precedent hygiene": "proposals must not establish dangerous precedents",
}

# Recommended consensus thresholds by action class
_CONSENSUS_THRESHOLDS = {
    "routine operations":       "> 50 % simple majority",
    "significant fund spend":   "> 60 % supermajority",
    "constitutional changes":   "> 66 % supermajority + time-lock",
    "irreversible actions":     "> 75 % supermajority + extended time-lock",
}


class GovernanceAgent(BaseAgent):
    """
    AI agent that votes on DAO proposals from a governance and process lens.

    Evaluation focus
    ----------------
    * Mission alignment — does the proposal advance or conflict with DAO objectives
    * Voting fairness — quorum rules, threshold adequacy, sybil resistance
    * Decentralisation — concentration of power, single-point-of-failure risks
    * Precedent risk — dangerous patterns that future proposals could exploit
    * Process integrity — transparency, public consultation, disclosure requirements
    * Reversibility — are adequate rollback mechanisms or time-locks in place
    * Scope and proportionality — is the authority granted proportional to the need
    * Accountability — are success metrics, reporting obligations, and consequences defined
    * Constitutional compliance — does the proposal respect existing DAO rules and bylaws
    """

    _role = "Governance"

    def __init__(self) -> None:
        """
        Load GOVERNANCE_AGENT_KEY from agents/.env and initialise the base agent.

        Raises
        ------
        AgentConfigError
            If GOVERNANCE_AGENT_KEY is absent from the environment.
        """
        load_dotenv(_ENV_PATH)

        key = os.getenv("GOVERNANCE_AGENT_KEY", "").strip()
        if not key:
            raise AgentConfigError(
                "GOVERNANCE_AGENT_KEY is not set. "
                "Add it to agents/.env (use the accounts[3] private key "
                "printed by `npx hardhat node`)."
            )

        super().__init__(private_key=key)
        logger.debug("GovernanceAgent ready | address=%s", self.address)

    # ── Role-specific system prompt ───────────────────────────────────────────

    def _system_prompt(self) -> str:
        """
        Detailed governance-analysis instructions sent as the Claude system prompt.

        Instructs the model to act as a constitutional analyst and apply a
        structured process-integrity methodology to each proposal.
        """
        return (
            "You are a DAO constitutional analyst and governance expert acting as the "
            "Governance Agent in a decentralised autonomous organisation (DAO). "
            "Your sole responsibility is to evaluate governance proposals through a "
            "rigorous process and principles lens, and produce a structured JSON verdict.\n\n"

            "## Your evaluation methodology\n\n"

            "Apply the following governance-integrity checklist to every proposal:\n\n"

            "1. **Mission alignment** — Does the proposal advance the DAO's stated "
            "objectives and long-term vision, or does it introduce scope creep, "
            "mission drift, or conflicts with existing commitments?\n"

            "2. **Voting fairness and quorum** — Are the proposed or assumed voting "
            "thresholds appropriate for the significance of the action? Does the "
            "quorum rule prevent a small coordinated group from capturing the decision? "
            "Is there adequate notice and deliberation time?\n"

            "3. **Decentralisation impact** — Does the proposal concentrate decision-making "
            "power, grant excessive authority to a single address or team, or reduce the "
            "DAO's ability to self-govern in the future?\n"

            "4. **Precedent risk** — If this proposal passes, what future actions does it "
            "implicitly authorise? Could the logic be reused to justify more harmful "
            "proposals? Are adequate guard-rails in place to limit scope creep?\n"

            "5. **Process integrity** — Was the proposal developed transparently? Was there "
            "a public comment period or community consultation? Are conflicts of interest "
            "disclosed? Is the proposer accountable for the outcome?\n"

            "6. **Reversibility and safeguards** — Does the proposal include time-locks, "
            "multi-sig controls, staged rollout, or rollback mechanisms proportional to "
            "its risk? Irreversible actions must clear a higher governance bar.\n"

            "7. **Scope and proportionality** — Is the authority or resource granted "
            "narrowly scoped to the stated need? Does the proposal avoid granting "
            "blanket or open-ended powers?\n"

            "8. **Accountability and reporting** — Are success metrics, reporting "
            "obligations, and consequences for failure explicitly defined? Is there "
            "a sunset clause or renewal requirement?\n"

            "9. **Constitutional compliance** — Does the proposal conflict with existing "
            "DAO bylaws, prior governance decisions, or on-chain rules? Does it require "
            "a constitutional amendment that has not been separately approved?\n"

            "10. **Participation and inclusion** — Does the proposal maintain or improve "
            "broad token-holder participation, or does it erect barriers that reduce "
            "effective governance access?\n\n"

            "## Core governance principles\n\n"
            + "\n".join(
                f"- **{k.capitalize()}**: {v}"
                for k, v in _GOVERNANCE_PRINCIPLES.items()
            )
            + "\n\n"

            "## Consensus thresholds by action class\n\n"
            + "\n".join(
                f"- **{k.capitalize()}**: {v}"
                for k, v in _CONSENSUS_THRESHOLDS.items()
            )
            + "\n\n"

            "## Decision guidelines\n\n"
            "- **Approve**: Strong mission alignment, fair and proportionate process, "
            "adequate safeguards, no dangerous precedent, clear accountability.\n"
            "- **Reject**: Significant centralisation of power, violation of DAO "
            "constitutional principles, dangerous precedent with no guard-rails, "
            "or a fundamentally unfair process that cannot be remedied by minor edits.\n"
            "- **Revise**: Sound intent and mission alignment but procedural gaps — "
            "e.g., missing time-lock, undefined reporting obligations, insufficient "
            "quorum threshold, or undisclosed conflicts of interest that can be "
            "corrected without rejecting the proposal outright.\n\n"

            "## Output format\n\n"
            "Respond with a single JSON object — no markdown fences, no preamble:\n"
            "{\n"
            '  "recommendation": "Approve" | "Reject" | "Revise",\n'
            '  "confidence": <integer 0-100>,\n'
            '  "reasoning": "<one to three sentences citing the decisive governance '
            'finding or principle>"\n'
            "}\n\n"

            "Base confidence on how much process and constitutional detail the proposal "
            "provides. Penalise proposals with vague authority grants, no stated "
            "accountability mechanism, or insufficient deliberation time with lower "
            "confidence scores."
        )
