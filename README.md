# govagents-dao

A DAO governance system where three specialized AI agents — Security, Economic, and Governance — autonomously analyze on-chain proposals and cast cryptographically signed votes. Each agent calls Claude via the Anthropic API, applies a role-specific evaluation rubric, and submits a typed vote (Approve / Reject / Revise) plus a confidence score directly to a Solidity smart contract. The contract automatically tallies the majority once all three votes arrive and marks the proposal as Decided — no human intermediary required.

The system is designed to be fully auditable: every vote, confidence score, and reasoning string is stored on-chain and emitted as an indexed event. Tiebreaks (three different verdicts) are resolved first by summed confidence scores and finally by a conservative priority of Approve > Revise > Reject. Proposals and results are also persisted locally as JSON in `results/`, and a formatted terminal summary is printed after every run. The architecture is intentionally simple — three wallets, one contract, one runner script — making it straightforward to extend with additional agent roles, token-weighted quorum logic, or a front-end dashboard.

---

## System Architecture

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                        runner.py                                │
  │                                                                 │
  │  1. Parse proposal (--file or --title/--description)            │
  │  2. submitProposal()  ──────────────────────────────────────┐   │
  │  3. Spawn 3 parallel worker threads                         │   │
  └──────────────┬──────────────────────────────────────────────┼───┘
                 │                                              │
     ┌───────────┼───────────────────────────────┐             │
     │           │           │                   │             ▼
     ▼           ▼           ▼             ┌─────────────────────────┐
┌─────────┐ ┌─────────┐ ┌──────────┐      │   DAOGovernance.sol     │
│Security │ │Economic │ │Governance│      │   (Hardhat / localhost) │
│ Agent   │ │ Agent   │ │  Agent   │      │                         │
└────┬────┘ └────┬────┘ └────┬─────┘      │  submitProposal()       │
     │           │           │            │  submitVote()           │
     │  analyze()│           │            │  _finalise()            │
     ▼           ▼           ▼            │  getFinalRecommendation()│
┌─────────────────────────────────┐       └──────────┬──────────────┘
│        Anthropic API            │                  │
│    claude-sonnet-4-20250514     │                  │  ProposalDecided
│                                 │                  │  event emitted
│  System prompt (role-specific)  │                  │  after 3rd vote
│  + proposal text                │                  │
│                                 │                  ▼
│  → JSON: recommendation,        │       ┌──────────────────────────┐
│          confidence, reasoning  │       │   Final Recommendation   │
└──────────────┬──────────────────┘       │   APPROVE / REJECT /     │
               │                          │   REVISE  (on-chain)     │
               │  submit_vote()           └──────────────────────────┘
               └──────────────────────────────────────────────────┐
                                                                   │
                                          signed tx (web3.py)      │
                                          ◄──────────────────────┘
                                                                   │
                         ┌─────────────────────────────────────────┘
                         ▼
              ┌──────────────────────┐      ┌────────────────────────┐
              │  Terminal Summary    │      │  results/              │
              │  (ANSI box-drawing)  │      │  proposal_<id>_        │
              │                      │      │  results.json          │
              └──────────────────────┘      └────────────────────────┘
```

**Vote aggregation (on-chain, `_finalise`):**

```
  3 votes cast  →  count votes per bucket (Approve / Reject / Revise)
                        │
               clear majority?  ──yes──►  winner
                        │ no
               highest summed confidence?  ──yes──►  winner
                        │ no
               priority: Approve > Revise > Reject
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Node.js | v18+ |
| npm | v9+ |
| Python | 3.10+ |
| Git | any |

You also need an **Anthropic API key** — get one at <https://console.anthropic.com/>.

---

## Installation

### 1. Clone and install JavaScript dependencies

```bash
git clone <repo-url>
cd govagents-dao
npm install
```

### 2. Compile the contracts

```bash
npm run compile
```

### 3. Create the Python virtual environment

```bash
cd agents
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd ..
```

### 4. Configure environment variables

```bash
cp agents/.env.example agents/.env
```

Open `agents/.env` and set your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

The RPC URL, proposer key, and agent keys are pre-filled with Hardhat's
well-known local test values and do not need to change for a local demo.

---

## Running the Demo

### Step 1 — Start the local Hardhat node

In a separate terminal:

```bash
npm run node
```

Leave it running. You should see:

```
Started HTTP and WebSocket JSON-RPC server at http://127.0.0.1:8545/
```

### Step 2 — Deploy the contract

```bash
npm run deploy:local
```

This deploys `DAOGovernance.sol`, registers three agent wallets, and writes
`agents/contract_info.json` with the contract address and ABI.

### Step 3 — Run the agent pipeline

```bash
cd agents
source venv/bin/activate

# Using a sample proposal file (filename only — searched in sample-proposals/)
python3 runner.py --file proposal_treasury.txt

# Or inline title + description
python3 runner.py --title "My Proposal" --description "Full description here..."

# Disable ANSI colour (e.g. piping to a file)
python3 runner.py --file proposal_treasury.txt --no-color
```

**Sample output:**

```
Submitting proposal to DAOGovernance…
  ✔ Proposal #0 confirmed (block 6)
Initialising agents…
  ✔ 3 agents ready — running analysis in parallel…

╔════════════════════════════════════════════════════════════════╗
║    DAOGovernance — Proposal Analysis                           ║
╚════════════════════════════════════════════════════════════════╝

  Proposal #0  ·  Liquidity Mining Program: 15% Treasury Allocation over 6 Months

══════════════════════════════════════════════════════════════════
  Agent Verdicts
══════════════════════════════════════════════════════════════════

  ■ SECURITY      REVISE                          confidence: 75/100
    The proposal lacks critical implementation details for the
    MasterChef staking contract...

  ■ ECONOMIC      APPROVE                         confidence: 85/100
    The 15% treasury allocation leaves 357,000 USDC (85% of
    reserves) providing strong operational runway...

  ■ GOVERNANCE    REVISE                          confidence: 75/100
    The proposal demonstrates good mission alignment but the
    15% quorum threshold is dangerously low...

══════════════════════════════════════════════════════════════════
  ✦  Final On-Chain Recommendation:  REVISE
══════════════════════════════════════════════════════════════════

  Results saved →  results/proposal_0_results.json
```

### Available sample proposals

| File | Proposal |
|------|----------|
| `proposal_treasury.txt` | #004 — 15% treasury allocation to Uniswap v3 liquidity mining |
| `proposal_upgrade.txt` | #005 — Upgrade to quadratic voting via UUPS proxy |
| `proposal_access.txt` | #006 — Grant emergency pause authority to 3-of-5 multisig |

---

## Agent Roles and Decision Criteria

### Security Agent
Evaluates the proposal for smart-contract risk. Scrutinises:

1. **Reentrancy** — external calls, CEI pattern compliance
2. **Access control** — role boundaries, privilege escalation paths
3. **Arithmetic safety** — overflow/underflow, precision loss
4. **Front-running / MEV** — sandwich attacks, ordering dependencies
5. **Oracle / price-feed** — TWAP staleness, manipulation vectors
6. **External calls / delegatecall** — untrusted contract interactions
7. **Upgrade / proxy risk** — storage collisions, initializer hygiene
8. **DoS** — gas griefing, unbounded loops
9. **Randomness / timestamp** — block-level manipulation
10. **Economic attack surface** — flash-loan governance, vote manipulation

Outputs a high confidence score (≥ 80) only when the proposal includes an
explicit audit requirement, clear access-control boundaries, and no
unspecified external contract dependencies.

### Economic Agent
Evaluates the financial sustainability and incentive design. Scrutinises:

1. **Treasury impact** — runway remaining after allocation
2. **Token supply / inflation** — new minting, dilution effects
3. **Incentive alignment** — participant vs. protocol interests
4. **Liquidity and market impact** — slippage, pool depth
5. **Revenue / cost model** — ROI projections, payback period
6. **Sustainability** — dependency on external conditions
7. **Counterparty / credit risk** — third-party protocol exposure
8. **Concentration risk** — single-point-of-failure treasury exposure
9. **Token velocity** — demand destruction, sell pressure
10. **Opportunity cost** — alternative uses of the same capital

Outputs a high confidence score when the allocation is bounded, rewards are
non-dilutive, there are clear accountability metrics, and risk controls
(multisig, oracle) are in place.

### Governance Agent
Evaluates alignment with protocol principles and decision-making health.
Scrutinises:

1. **Mission alignment** — consistency with protocol purpose
2. **Voting fairness / quorum** — threshold adequacy for proposal stakes
3. **Decentralisation** — concentration of new powers granted
4. **Precedent risk** — doors opened for future misuse
5. **Process integrity** — timelock, audit, and review requirements
6. **Reversibility** — rollback mechanisms and safeguards
7. **Scope / proportionality** — powers granted vs. need stated
8. **Accountability** — reporting obligations, role forfeiture conditions
9. **Constitutional compliance** — adherence to existing governance rules
10. **Participation** — accessibility to minority token holders

Outputs a high confidence score when quorum and majority thresholds match
the severity of the action, powers granted are tightly scoped, and
accountability mechanisms are explicit.

---

## Known Limitations

- **Fixed agent count** — the contract hard-codes exactly 3 agents; changing
  the count requires a contract redeployment and corresponding Python changes.
- **No token-weighted quorum** — the on-chain tally treats all three agent
  votes equally; there is no mechanism for token-holder override.
- **Sequential nonce management** — votes submitted in the same block by
  different agents work correctly (separate wallets), but any agent that
  submits two transactions quickly may hit nonce race conditions without
  additional retry logic.
- **Plain-text reasoning on-chain** — long reasoning strings increase gas
  costs significantly (`submitProposal` for a ~1.6 kB description costs
  ~1.35 M gas). Consider IPFS / calldata hashing for production.
- **No proposal expiry** — pending proposals remain open indefinitely if
  fewer than three agents vote.
- **Local network only** — keys and addresses in `.env.example` are Hardhat
  test accounts and must be replaced before any public-network deployment.
- **Single Anthropic model** — all three agents use the same underlying
  model; divergent opinions arise solely from differing system prompts, not
  architectural diversity.

## Future Improvements

- **Token-holder veto** — allow GOV token holders to override the agent
  recommendation within a time window after `ProposalDecided` is emitted.
- **Confidence-weighted tally** — weight each vote by confidence score rather
  than treating all votes as equal.
- **IPFS reasoning storage** — store reasoning off-chain and post only the
  content hash on-chain to reduce gas costs.
- **Proposal expiry and quorum fallback** — auto-reject proposals that do not
  receive all three votes within a deadline.
- **Agent key rotation UI** — front-end for the owner to call `replaceAgent()`
  without interacting with the contract directly.
- **Multi-network support** — parameterise `runner.py` for Sepolia / mainnet
  with appropriate key management.
- **Streaming output** — print each agent's verdict as it arrives rather than
  waiting for all three to complete.
- **Retrieval-augmented context** — give agents access to past proposals and
  their outcomes to improve reasoning consistency.

---

## Related Work

The space of AI-augmented DAO governance has become active. The most directly relevant work:

- **DAO-AI** (Capponi et al., October 2025) — tested an autonomous AI voter against 3,000+ real proposals from Compound, Uniswap, and Aave. Found that AI produces interpretable, auditable voting signals at scale and that model outputs correlate meaningfully with eventual human outcomes.
- **QOC DAO** (Jansen and Verdot, November 2025) — proposes a stepwise framework for integrating LLM-based agents into DAO evaluation pipelines, with explicit safeguards to detect prompt manipulation and voting collusion across agents.
- **DAO-Agent** (Xia et al., December 2025) — addresses the multi-agent coordination and verification problem using zero-knowledge proofs over LLM inference and Shapley-value-based contribution measurement to attribute decision influence across agents.
- **StableLab AI Delegate Report** (May 2025) — practitioner survey of current AI delegate tooling deployed across major DAOs; covers integration patterns, trust assumptions, and observed failure modes.
- **NEAR Foundation AI Digital Twins** — live program building AI agents trained on individual users' past voting history and stated preferences, effectively implementing the personal agent model described in the Trust and Bias section below.

This project's contribution relative to that literature is a specific architecture: fixed specialized roles, majority-vote aggregation with a confidence-score tiebreak, and full reasoning strings stored on-chain as indexed events. The claim isn't novelty over all agent-based governance approaches — it's that this particular combination of design choices (on-chain auditability + role specialization + cryptographic agent identity) is worth studying as a concrete prototype.

---

## Trust and Bias

The most important unresolved design question: whoever deploys and registers the three agents controls what they evaluate and how. A token holder interacting with the DAO has no way to verify that those agents weren't pre-tuned to favor a particular outcome. Everything downstream of `registerAgent()` is trust-the-operator.

There are two meaningful directions to address this.

**Personal agent model.** Instead of a shared panel of three fixed agents, each token holder runs their own agent configured with their own preferences and delegates their vote to it. This sidesteps the centralized-operator problem entirely: I trust my agent because I configured it. It maps directly onto how liquid delegation works in existing DAOs, with an AI substituting for a human delegate. Concretely, the contract would need:
- A `delegateAgent(address agent)` function per token holder
- Quorum tracking across registered delegates rather than a hardcoded `MAX_AGENTS = 3`
- Per-holder configuration storage (or an off-chain config referenced by content hash)

This is the more tractable near-term path. The fixed-panel design in the current prototype is a simplification, not a deliberate architectural choice.

**ZK verification of agent outputs.** A zero-knowledge proof could attest that a given model, given a specific input, produced a specific output — without revealing the full reasoning or model weights. This eliminates the trust-in-operator problem at the cost of significant computational overhead. Xia et al.'s "DAO-Agent" (December 2025) explores something adjacent using ZK proofs and Shapley-based contribution measurement. This is likely out of scope as a prototype feature but is the right long-term direction for any production deployment where agent behavior needs to be verifiable without trusting the operator.

For now, the trust assumption is documented here so it's visible rather than hidden.

---

## File Structure

```
govagents-dao/
│
├── contracts/
│   ├── DAOGovernance.sol        # Core governance contract (3-agent, auto-finalise)
│   └── AgentDAO.sol             # Earlier prototype (weight × confidence aggregation)
│
├── scripts/
│   └── deploy.js                # Deploys DAOGovernance, registers 3 agents,
│                                #   writes agents/contract_info.json
│
├── test/
│   ├── DAOGovernance.test.js    # 77 tests — all paths, tiebreaks, edge cases
│   └── AgentDAO.test.js         # 14 tests for the prototype contract
│
├── agents/
│   ├── base_agent.py            # BaseAgent: web3 + Anthropic wiring, analyze(), submit_vote()
│   ├── security_agent.py        # SecurityAgent — smart-contract risk evaluation
│   ├── economic_agent.py        # EconomicAgent — treasury & tokenomics evaluation
│   ├── governance_agent.py      # GovernanceAgent — process & alignment evaluation
│   ├── runner.py                # End-to-end CLI: submit proposal → parallel agents → summary
│   ├── requirements.txt         # anthropic, web3, python-dotenv
│   ├── .env.example             # Environment variable template
│   └── contract_info.json       # Written by deploy.js; read by agents at runtime
│
├── sample-proposals/
│   ├── proposal_treasury.txt    # #004 — Liquidity mining program (15% treasury)
│   ├── proposal_upgrade.txt     # #005 — Quadratic voting upgrade (UUPS proxy)
│   └── proposal_access.txt      # #006 — Emergency pause multisig (PAUSER_ROLE)
│
├── results/                     # Auto-created; one JSON file per proposal run
│
├── hardhat.config.js            # Solidity 0.8.24, localhost + hardhat networks
├── package.json                 # Hardhat 2.x, ethers v6, hardhat-toolbox
└── .gitignore
```

---

## Running the Test Suite

```bash
npm test
```

Expected output: **91 tests passing** across `DAOGovernance.test.js` (77) and
`AgentDAO.test.js` (14), covering deployment, agent registration, proposal
submission, all voting paths, auto-finalisation, tiebreak logic, and getters.
