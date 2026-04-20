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

## 7. Evaluation Against Human-Labeled Ground Truth

To measure agent accuracy, five proposals were evaluated offline using `eval.py` — a stripped-down harness that calls the Anthropic API without submitting any on-chain transactions — and compared against human-assigned ground-truth labels stored in `eval/labels.json`. The human labels were assigned independently before the agents were run.

**Aggregate result: 4 out of 5 proposals matched the human label (80% agreement rate).**

### Per-proposal outcomes

| Proposal | Agent consensus | Human label | Match |
|---|---|---|---|
| proposal_treasury.txt | Revise | Revise | ✓ |
| proposal_upgrade.txt | Revise | Approve | ✗ |
| proposal_access.txt | Approve | Approve | ✓ |
| proposal_bugbounty.txt | Approve | Approve | ✓ |
| proposal_mintingpower.txt | Reject | Reject | ✓ |

### Finding 1 — The single miss: a systematic documentation bias

On `proposal_upgrade.txt` (quadratic voting via UUPS proxy), all three agents returned **Revise**; the human label was **Approve**. The Security agent flagged potential MEV extraction during the 7-day migration window and unchecked mathematical operations; the Economic agent noted the absence of a budgeted audit cost and quantified implementation costs; the Governance agent flagged a 4-of-7 multisig dependency as a centralisation risk. The human evaluator treated the UUPS proxy pattern as standard practice with bounded security surface, and accepted the proposal's existing timelock and audit language as sufficient.

This disagreement is not a straightforward error. It reflects a systematic agent bias: the agents penalise missing or implicit documentation even when the underlying pattern is well-understood. In practice, a well-formed UUPS upgrade with a timelock and audit requirement may carry less actual risk than a poorly specified novel mechanism that documents every parameter. The current prompting framework rewards completeness of specification rather than risk-adjusted confidence in the outcome, which will reliably over-flag mature, standardised proposals that omit details because those details are considered obvious.

### Finding 2 — The clean sweep: unanimous high-confidence rejection

On `proposal_mintingpower.txt` (unrestricted MINT_ROLE to a single EOA), all three agents returned **Reject** at 95% confidence — the highest confidence observed in the evaluation set. The agents independently identified the same core failure: unlimited token minting delegated to a single private key with no timelock, no cap, and no multisig requirement is an unambiguous governance failure that enables complete dilution of token-holder voting power. The unanimous, high-confidence agreement across all three roles on a clear-cut case validates that the specialised prompts do not fragment judgment when the risk is obvious and categorical.

### Finding 3 — Security agent conservatism

On `proposal_access.txt` and `proposal_bugbounty.txt`, the Security agent returned **Revise** (75% confidence) while the Economic and Governance agents returned **Approve** (85% confidence) — and the human label agreed with Approve in both cases. The Security agent's objections were procedural: missing keyholder backup procedures for the multisig, and the risk of duplicate submissions draining the bug bounty budget. These are legitimate considerations, but neither evaluator nor the other two agents treated them as blocking concerns.

This pattern points to a prompt calibration issue rather than a reasoning failure. The Security agent's 10-point threat-modelling checklist creates a strong prior toward flagging any unspecified operational detail as a revision requirement. A more calibrated prompt would distinguish between missing technical specifications (where uncertainty is material) and missing operational procedures that are out of scope for a governance proposal.

### Finding 4 — Confidence is poorly calibrated across proposals

Across all labeled proposals, confidence scores clustered narrowly: 75 or 85 on all proposals where any agent deviated, and 95 only on the unanimous reject. The agents did not use the lower end of the 0–100 scale, and the difference between 75 and 85 does not appear to reflect meaningfully different levels of epistemic certainty. This means confidence scores cannot be used to rank proposals by decision quality or to weight votes in a confidence-weighted tally — a limitation for any future aggregation mechanism that treats confidence as a signal rather than a label.

The root cause is structural: the model produces confidence as a self-report rather than a calibrated probability, and nothing in the evaluation loop provides feedback that would drive scores toward their true distributional range. Addressing this would require either few-shot examples that anchor the confidence scale, or post-hoc calibration using a held-out labeled set.

## 8. Gas Optimization and Off-Chain Reasoning

The prototype stores each agent's full reasoning string as a `string` field in the `AgentVoted` event, written to the EVM's log storage. Measured against the actual reasoning strings produced by the evaluation run (430–490 bytes each), `submitVote()` costs approximately 430–470 K gas for the first two votes and ~515 K gas for the third vote, which additionally executes `_finalise()`. The total per-proposal cost across three votes is approximately 1.41 M gas — the dominant cost in the system, driven by the fact that log data is priced at 8 gas per zero byte and 68 gas per non-zero byte under EIP-2028, with additional overhead from the Solidity ABI encoding of dynamic types. This figure scales roughly linearly with reasoning string length; shorter strings (e.g., the 13-byte fixture strings used in the Hardhat test suite) cost as little as 92 K per vote, while the 430–490 byte strings representative of actual Claude output bring the cost to the measured range above. Three votes per proposal therefore make frequent on-chain use at realistic reasoning lengths impractical at any significant throughput.

Three alternative designs trade auditability for cost at different points on the spectrum. The cheapest approach compatible with permanent auditability is IPFS content hashing: the agent computes a SHA-256 or CID hash of its reasoning off-chain and submits only the 32-byte hash to the contract, which stores it in the event and optionally in contract state. Reasoning is retrievable by anyone who can reach an IPFS node pinning the content; the hash provides a cryptographic guarantee of integrity. This reduces per-vote gas to approximately 80 K — a roughly 5× improvement — at the cost of requiring an operational IPFS pinning infrastructure and accepting that reasoning is not directly queryable from an archive node. A second alternative is calldata storage: emitting the full reasoning string as calldata rather than persisting it as event data. Calldata is included in the transaction and therefore in the block, making it fully recoverable from any full node, but it is not indexed and cannot be retrieved by log filter. Per-vote cost falls to approximately 120 K gas. The third alternative is blob data under EIP-4844 (proto-danksharding): blobs are attached to transactions at a dedicated fee market, cost roughly 1 gas unit per byte at low utilisation, and are pruned from full nodes after approximately 18 days while their KZG commitments remain on-chain as a permanent integrity anchor. This produces the lowest operational cost — in the range of 50 K gas per vote for the commitment alone — but reasoning is only reliably retrievable during the blob retention window, after which a purpose-built archival service is required.

The prototype deliberately chose on-chain string storage. In a demo and academic context the priority is auditability without external dependencies: an evaluator or auditor can inspect the complete governance record — proposals, per-agent reasoning, and outcomes — using only an Ethereum archive node and standard `eth_getLogs` calls, with no reliance on IPFS availability or blob archival services. This makes the system self-contained for evaluation purposes at the cost of gas efficiency. A production deployment would adopt IPFS content hashing as the baseline: the 80 K per-vote cost is acceptable for governance decisions that typically occur infrequently, the integrity guarantee is equivalent to on-chain storage, and the hash stored in contract state can be used by on-chain logic to verify off-chain reasoning without reading the full string. Blob storage would be appropriate for high-frequency or high-volume governance contexts where the 18-day retrieval window is operationally sufficient and a dedicated archival layer can be maintained.

## 9. Limitations and Production Requirements

The prototype demonstrates the pattern but departs from production-grade in several ways. The contract hard-codes exactly three agent slots; a production system requires a configurable committee size with quorum expressed as a fraction of registered agents. There is no token-holder override: the AI verdict is final, whereas a production deployment should allow GOV holders to challenge outcomes within a time window to preserve human sovereignty. Storing full reasoning strings on-chain is gas-intensive (~1.3 M gas for a 1.6 kB proposal); production deployments should store content hashes on-chain and full text on IPFS. Proposals with fewer than three votes remain pending indefinitely, requiring a deadline-and-lapse mechanism. Finally, delegating binding governance votes to an AI system raises unresolved questions around fiduciary liability; a responsible deployment would likely restrict agents to advisory output with mandatory human ratification for proposals involving material fund transfers.

- **No proposal expiry** — pending proposals have no expiry mechanism; a proposal with fewer than 3 agent votes will remain open indefinitely. A production system would add a `deadline` field and an `expire()` function to handle stalled proposals.
