"""
agents/economic_agent.py
~~~~~~~~~~~~~~~~~~~~~~~~
EconomicAgent — evaluates DAO governance proposals from an economic perspective.

Loaded from environment
-----------------------
ECONOMIC_AGENT_KEY : hex private key of the on-chain Economic agent wallet
                     (accounts[2] on a local Hardhat node).

Usage
-----
    from agents.economic_agent import EconomicAgent

    agent   = EconomicAgent()
    verdict = agent.analyze("Treasury Grant Q3", "Allocate 50 000 USDC …")
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

# ── Treasury health thresholds used to calibrate the prompt narrative ─────────
# Indicative ratios only — the LLM uses these as reference benchmarks.
_TREASURY_BENCHMARKS = {
    "safe_spend_ratio":  "≤ 10 % of treasury reserves in a single proposal",
    "runway_minimum":    "≥ 12 months of operational runway must remain post-spend",
    "concentration_cap": "≤ 30 % of treasury held in any single non-stablecoin asset",
    "roi_threshold":     "positive expected value within 18 months for yield proposals",
}


class EconomicAgent(BaseAgent):
    """
    AI agent that votes on DAO proposals from a tokenomics and treasury lens.

    Evaluation focus
    ----------------
    * Treasury impact — absolute size, percentage of reserves, and runway effect
    * Token inflation and dilution — new mints, vesting schedules, supply expansion
    * Incentive alignment — whether rewards direct behaviour toward protocol goals
    * Liquidity and market impact — effect on token price, slippage, and depth
    * Revenue and cost model — does the proposal generate, sustain, or erode income
    * Economic sustainability — can the model survive bear-market conditions
    * Counterparty and credit risk — vendor reliability, smart-contract dependencies
    * Concentration risk — does the proposal consolidate economic power
    * Token velocity — does it encourage holding or accelerate sell pressure
    """

    _role = "Economic"

    def __init__(self) -> None:
        """
        Load ECONOMIC_AGENT_KEY from agents/.env and initialise the base agent.

        Raises
        ------
        AgentConfigError
            If ECONOMIC_AGENT_KEY is absent from the environment.
        """
        load_dotenv(_ENV_PATH)

        key = os.getenv("ECONOMIC_AGENT_KEY", "").strip()
        if not key:
            raise AgentConfigError(
                "ECONOMIC_AGENT_KEY is not set. "
                "Add it to agents/.env (use the accounts[2] private key "
                "printed by `npx hardhat node`)."
            )

        super().__init__(private_key=key)
        logger.debug("EconomicAgent ready | address=%s", self.address)

    # ── Role-specific system prompt ───────────────────────────────────────────

    def _system_prompt(self) -> str:
        """
        Detailed economic-analysis instructions sent as the Claude system prompt.

        Instructs the model to act as a DeFi economist / treasury analyst and
        apply a structured financial-impact methodology to each proposal.
        """
        return (
            "You are a DeFi economist and treasury analyst acting as the Economic Agent "
            "in a decentralised autonomous organisation (DAO). "
            "Your sole responsibility is to evaluate governance proposals through a rigorous "
            "economic lens and produce a structured JSON verdict.\n\n"

            "## Your evaluation methodology\n\n"

            "Apply the following financial-impact checklist to every proposal:\n\n"

            "1. **Treasury impact** — What is the absolute spend or commitment, and what "
            "percentage of current reserves does it represent? Does the remaining treasury "
            "provide at least 12 months of operational runway?\n"

            "2. **Token supply and inflation** — Does the proposal mint new tokens, "
            "unlock previously vested supply, or alter emission schedules? Model the "
            "dilutive effect on existing holders.\n"

            "3. **Incentive alignment** — Do proposed rewards, fees, or mechanisms direct "
            "participant behaviour toward long-term protocol health, or do they create "
            "perverse incentives (e.g., farming-and-dumping, short-term mercenary capital)?\n"

            "4. **Liquidity and market impact** — Will the proposal significantly change "
            "on-chain liquidity depth, cause large slippage events, or create arbitrage "
            "that harms the protocol?\n"

            "5. **Revenue and cost model** — Does the proposal generate net positive "
            "value for the treasury over its intended period? Identify the key revenue "
            "assumptions and stress-test them against a bear-market scenario.\n"

            "6. **Economic sustainability** — Can the proposed model survive a 70 % "
            "drawdown in token price and a 50 % drop in protocol revenue? Is it "
            "dependent on continuous token issuance for solvency?\n"

            "7. **Counterparty and credit risk** — Is the DAO taking on credit exposure "
            "to a vendor, protocol, or asset that carries meaningful default risk?\n"

            "8. **Concentration risk** — Does the proposal centralise economic control, "
            "direct a disproportionate share of rewards to a small group, or make the "
            "treasury dangerously reliant on a single asset or strategy?\n"

            "9. **Token velocity** — Does the proposal encourage token locking and "
            "long-term alignment, or does it increase circulation and sell pressure?\n"

            "10. **Opportunity cost** — What is the best alternative use of the same "
            "capital, and does this proposal beat it on risk-adjusted terms?\n\n"

            "## Treasury health benchmarks (reference only)\n\n"
            + "\n".join(
                f"- **{k.replace('_', ' ').capitalize()}**: {v}"
                for k, v in _TREASURY_BENCHMARKS.items()
            )
            + "\n\n"

            "## Decision guidelines\n\n"
            "- **Approve**: Positive or neutral treasury impact; incentives align with "
            "long-term protocol health; no material sustainability or concentration risk.\n"
            "- **Reject**: Unacceptable treasury drain (> 20 % reserves), clearly "
            "misaligned incentives, or a model that is insolvent without continuous "
            "inflationary support.\n"
            "- **Revise**: Economically sound direction but requires better spend controls, "
            "milestone-based disbursement, adjusted emission rates, or additional risk "
            "mitigations before approval.\n\n"

            "## Output format\n\n"
            "Respond with a single JSON object — no markdown fences, no preamble:\n"
            "{\n"
            '  "recommendation": "Approve" | "Reject" | "Revise",\n'
            '  "confidence": <integer 0-100>,\n'
            '  "reasoning": "<one to three sentences citing the decisive economic '
            'finding or metric>"\n'
            "}\n\n"

            "Base confidence on how much quantitative detail the proposal provides. "
            "Penalise proposals with missing budget figures, undefined timelines, or "
            "no stated success metrics with lower confidence scores."
        )
