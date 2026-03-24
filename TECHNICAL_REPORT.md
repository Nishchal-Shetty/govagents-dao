# Technical Report: govagents-dao — AI-Augmented DAO Governance

## 1. System Architecture and Design Decisions

The system couples a Solidity smart contract with three Python-based AI agents to produce a fully on-chain governance verdict for natural-language proposals. The architecture is deliberately layered: `DAOGovernance.sol` owns all authoritative state — proposals, votes, and final outcomes — while the off-chain Python layer handles LLM inference. This separation maps cleanly to the trust model: anything requiring trustless auditability lives on-chain; anything benefiting from rich language understanding lives off-chain.

The central design choice was role segregation rather than a single general-purpose reviewer. One agent evaluates smart-contract risk, one evaluates economic sustainability, and one evaluates governance process — mirroring multi-stakeholder review panels in institutional governance and guarding against any single model's blind spots dominating the outcome. The agents run concurrently via `concurrent.futures.ThreadPoolExecutor`, avoiding serialisation bottlenecks while keeping nonce management trivial, since each agent controls a separate wallet with an independent transaction sequence.

## 2. Smart Contract Design and Aggregation Logic

`DAOGovernance.sol` (Solidity 0.8.20) stores proposals as fully on-chain structs and restricts `submitVote()` to three owner-registered agent addresses. Each vote records an `AgentRole` enum (Security / Economic / Governance), a `Recommendation` enum (Approve / Reject / Revise), a uint8 confidence score (0–100), and a verbatim reasoning string. After the third vote, `submitVote()` calls `_finalise()`, which implements a three-tier aggregation:

1. **Clear majority** — the recommendation with the most votes wins outright.
2. **Confidence tiebreak** — among tied recommendations, the one with the highest summed confidence score wins.
3. **Priority tiebreak** — if confidence sums are also equal, a conservative ordering of Approve > Revise > Reject applies, biasing toward less drastic outcomes.

Events (`ProposalSubmitted`, `AgentVoted`, `ProposalDecided`) are emitted as indexed logs, making all state changes auditable without trusting any front-end.

## 3. Agent Prompting and Output Structure

Each agent overrides `_system_prompt()` to receive a role-specific instruction set. The Security agent's prompt includes a 10-point threat-modelling checklist (reentrancy, access control, arithmetic safety, front-running/MEV, oracle manipulation, external calls, upgrade/proxy risks, DoS, on-chain randomness, economic attack surface), a severity taxonomy, and explicit guidelines mapping severity levels to recommendations. All agents are instructed to return a single JSON object with three fields: `recommendation`, `confidence`, and `reasoning`.

The `_parse_claude_response()` method in `BaseAgent` strips residual markdown fences, validates the parsed JSON for field presence and type correctness, and raises `AnalysisError` on any malformed output before it can reach the transaction layer. The model used is `claude-sonnet-4-20250514`, called via the Anthropic Python SDK with separate `system` and `user` parameters.

## 4. Off-Chain / On-Chain Interaction

`base_agent.py` uses web3.py (v7) and loads the contract address and ABI from `contract_info.json`, which is written by `deploy.js` at deployment time. Transaction gas is set dynamically using `fn.estimate_gas() × 1.2` — a fix necessitated by long proposal descriptions pushing `submitProposal` beyond 1.3 M gas, far above any static limit. The on-chain proposal ID is extracted from the `ProposalSubmitted` event in the transaction receipt, avoiding reliance on a state-changing function's return value. Futures are collected via `as_completed()` and re-sorted to canonical order before display, so network arrival order does not affect the printed summary.

## 5. Security Considerations and Risks of Agent-Based Governance

Several risks are specific to the agent-based model:

**Prompt injection.** A malicious proposer could embed adversarial instructions inside the proposal text. The prototype has no sanitisation beyond the model's own instruction-following fidelity.

**Model homogeneity.** All three agents share the same underlying model; divergent opinions arise only from differing system prompts. A systematic model bias or failure mode affects all three agents simultaneously.

**Non-determinism.** LLM outputs are stochastic; running the same proposal twice may produce different verdicts, which creates accountability problems absent from deterministic on-chain logic.

**Key management.** The prototype uses Hardhat's publicly known test keys. In production, each agent wallet would require HSM custody or threshold signing, since two compromised keys are sufficient to control any outcome.

## 6. Evaluation of the Three Sample Proposals

**Proposal #004 — 15% Treasury Allocation to Liquidity Mining.** Security recommended **Revise** (75): without a staking-contract specification, reentrancy and access-control vectors cannot be assessed. Economic recommended **Approve** (85): 85% of treasury reserves are retained, rewards are USDC-denominated (no token dilution), and a month-3 revenue checkpoint provides accountability. Governance recommended **Revise** (75): the 15% quorum threshold is disproportionately low for a proposal allocating 15% of treasury funds, and the 96-hour voting window is insufficient for deliberation at this scale. **Final outcome: REVISE** — a sound result; the economic case is solid, but two independent agents correctly identified structural weaknesses in the proposal's own governance parameters.

**Proposal #005 — Quadratic Voting Upgrade (UUPS Proxy).** Not executed in the live demo, but the proposal's explicit 7-day timelock, 4-of-7 multisig, independent audit requirement, and anti-flash-loan epoch protection directly address the top items in the security checklist; Security would likely Approve with high confidence. The Governance agent would likely flag the centralisation risk of the upgrade multisig, while the Economic agent would scrutinise the migration path for vote-weight distortions.

**Proposal #006 — Emergency Pause Multisig.** The PAUSER_ROLE is tightly scoped to four contracts with no fund-movement capability and a 72-hour auto-expiry; Security would likely Approve. The Governance agent might Revise on the grounds that the DAO's inability to pause its own voting contract creates a recursive attack surface, and that the 6-month community-representative term provides weak accountability against a 3-of-5 threshold.

## 7. Limitations and Production Requirements

The prototype demonstrates the pattern but departs from production-grade in several ways. The contract hard-codes exactly three agent slots; a production system requires a configurable committee size with quorum expressed as a fraction of registered agents. There is no token-holder override: the AI verdict is final, whereas a production deployment should allow GOV holders to challenge outcomes within a time window to preserve human sovereignty. Storing full reasoning strings on-chain is gas-intensive (~1.3 M gas for a 1.6 kB proposal); production deployments should store content hashes on-chain and full text on IPFS. Proposals with fewer than three votes remain pending indefinitely, requiring a deadline-and-lapse mechanism. Finally, delegating binding governance votes to an AI system raises unresolved questions around fiduciary liability; a responsible deployment would likely restrict agents to advisory output with mandatory human ratification for proposals involving material fund transfers.
