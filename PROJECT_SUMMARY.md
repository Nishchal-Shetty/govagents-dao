# Project Summary — govagents-dao

## What It Is

DAOs (Decentralised Autonomous Organisations) let communities govern shared
resources through on-chain votes, but reviewing proposals is time-consuming and
requires expertise across security, finance, and governance. govagents-dao
deploys three specialised AI agents that read each proposal, independently
analyse it through their own lens, and cast cryptographically signed votes
directly to a smart contract — producing an auditable, automatic recommendation
without any human intermediary.

---

## Deliverables

| Deliverable | What it is | Where to find it |
|---|---|---|
| **Smart contract** | Solidity contract that accepts proposals, records agent votes, and tallies a final recommendation on-chain | `contracts/DAOGovernance.sol` |
| **AI agents** | Three Python agents (Security, Economic, Governance) that call Claude, apply role-specific criteria, and submit signed votes | `agents/security_agent.py`, `economic_agent.py`, `governance_agent.py` |
| **Runner** | Command-line tool to submit a proposal and run all three agents in parallel, with live output and JSON export | `agents/runner.py` |
| **Evaluation harness** | Offline tool that runs proposals through the agents and scores results against human-assigned labels | `agents/eval.py` |
| **Test suite** | 91 automated tests covering every voting path, tiebreak scenario, and contract function | `test/DAOGovernance.test.js` |
| **Sample proposals** | Eight realistic governance proposals used for testing and evaluation | `sample-proposals/` |
| **Documentation** | Full README (setup, demo, design rationale) and Technical Report | `README.md`, `TECHNICAL_REPORT.md` |

---

## Team Contributions

**Nishchal Shetty** built the core system: the smart contract, all three AI
agents, the runner pipeline, the test suite, and the initial sample proposals.
This includes the on-chain voting logic, tiebreak rules, agent prompting
strategy, and the web3 integration that lets Python agents sign and submit
blockchain transactions. He also wrote the Trust and Bias section.

**Shreshth Srivastava** built the evaluation and analysis layer, and hardened
the runner for real use. He added the offline evaluation harness and
human-labeled ground-truth dataset, the `--dry-run` and `--json` CLI flags,
live verdict printing as each agent completes, automatic API retry with
exponential backoff, and the environment-check utility. He also wrote the
Aggregation Mechanism analysis, Related Work survey and 
the personal agent model design sketch in the contract.

---

## Key Results

- **91 / 91 tests passing** across all voting paths, tiebreak scenarios, and
  contract functions.

- **80% accuracy** against human-labeled ground truth (4 of 5 proposals
  matched a human reviewer's recommendation). The one miss — a UUPS proxy
  upgrade — revealed a systematic agent tendency to penalise proposals that
  omit documentation even when the underlying pattern is well-understood.

- **~1.41 M gas per proposal** (measured, not estimated) across three agent
  votes using realistic 430–490 byte reasoning strings. Storing full reasoning
  on-chain costs more than alternatives like IPFS content hashing (~80 K gas
  per vote) but makes the system fully auditable without external dependencies
  — the right tradeoff for a prototype.

- **Agent confidence scores clustered narrowly at 75 and 85** regardless of
  actual uncertainty, and the Security agent showed a consistent conservative
  bias — returning Revise on two proposals where the other agents and human
  evaluator agreed on Approve. Both are documented prompt calibration issues
  rather than reasoning failures.
