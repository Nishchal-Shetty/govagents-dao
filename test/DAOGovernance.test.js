/**
 * DAOGovernance – comprehensive test suite
 *
 * Sections
 * ────────────────────────────────────────────────────────────────────────────
 * 1. Deployment & initial state
 * 2. Agent registration  (registerAgent)
 * 3. Agent replacement   (replaceAgent)
 * 4. Proposal submission (submitProposal)
 * 5. Voting              (submitVote) – individual agents + error paths
 * 6. Auto-finalisation   – majority outcomes, all tiebreak branches
 * 7. Multi-proposal isolation
 * 8. Getters             – happy-path and revert cases
 * ────────────────────────────────────────────────────────────────────────────
 */

const { expect }   = require("chai");
const { ethers }   = require("hardhat");
const { anyValue } = require("@nomicfoundation/hardhat-chai-matchers/withArgs");

// ── Enum mirrors (must match Solidity declaration order) ────────────────────
const Status = Object.freeze({ Pending: 0n, Decided: 1n });

const Role = Object.freeze({ Security: 0n, Economic: 1n, Governance: 2n });

const Rec = Object.freeze({ Approve: 0n, Reject: 1n, Revise: 2n });

// ── Shared fixture ──────────────────────────────────────────────────────────
// Re-deployed fresh for every test via beforeEach at the suite level.

async function deployFresh() {
  const [owner, agentSec, agentEco, agentGov, stranger, proposer, extra] =
    await ethers.getSigners();

  const Factory = await ethers.getContractFactory("DAOGovernance");
  const dao     = await Factory.deploy();

  return { dao, owner, agentSec, agentEco, agentGov, stranger, proposer, extra };
}

// Helper: register all 3 agents in the canonical order Security→Economic→Governance
async function registerAll({ dao, agentSec, agentEco, agentGov }) {
  await dao.registerAgent(agentSec.address);
  await dao.registerAgent(agentEco.address);
  await dao.registerAgent(agentGov.address);
}

// Helper: submit one proposal and return its id (BigInt)
async function submitOne(dao, signer, title = "Proposal Alpha", desc = "Full description") {
  const tx      = await dao.connect(signer).submitProposal(title, desc);
  const receipt = await tx.wait();
  // ProposalSubmitted event arg[0] is the id
  const event = receipt.logs
    .map((l) => { try { return dao.interface.parseLog(l); } catch { return null; } })
    .find((e) => e?.name === "ProposalSubmitted");
  return event.args[0]; // BigInt proposalId
}

// Helper: have all three agents vote on a proposal
async function voteAll(dao, agents, proposalId, recs, confidences, reasonings) {
  const roles = [Role.Security, Role.Economic, Role.Governance];
  for (let i = 0; i < 3; i++) {
    await dao.connect(agents[i]).submitVote(
      proposalId,
      roles[i],
      recs[i],
      confidences[i],
      reasonings?.[i] ?? `Reasoning ${i}`,
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Suite
// ═══════════════════════════════════════════════════════════════════════════

describe("DAOGovernance", function () {
  let ctx; // populated by top-level beforeEach

  beforeEach(async () => {
    ctx = await deployFresh();
  });

  // =========================================================================
  // 1. Deployment & initial state
  // =========================================================================

  describe("1 · Deployment & initial state", () => {
    it("sets the deployer as owner", async () => {
      const { dao, owner } = ctx;
      expect(await dao.owner()).to.equal(owner.address);
    });

    it("starts with zero registered agents", async () => {
      const { dao } = ctx;
      expect(await dao.registeredAgentCount()).to.equal(0);
    });

    it("starts with zero proposals", async () => {
      const { dao } = ctx;
      expect(await dao.proposalCount()).to.equal(0);
    });

    it("getAgents returns three zero-address slots before any registration", async () => {
      const { dao } = ctx;
      const slots = await dao.getAgents();
      for (const slot of slots) {
        expect(slot).to.equal(ethers.ZeroAddress);
      }
    });

    it("MAX_AGENTS constant is 3", async () => {
      const { dao } = ctx;
      expect(await dao.MAX_AGENTS()).to.equal(3);
    });
  });

  // =========================================================================
  // 2. Agent registration
  // =========================================================================

  describe("2 · registerAgent", () => {
    it("owner registers 3 agents; count and slots update correctly", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;

      await dao.registerAgent(agentSec.address);
      expect(await dao.registeredAgentCount()).to.equal(1);

      await dao.registerAgent(agentEco.address);
      expect(await dao.registeredAgentCount()).to.equal(2);

      await dao.registerAgent(agentGov.address);
      expect(await dao.registeredAgentCount()).to.equal(3);

      const slots = await dao.getAgents();
      expect(slots[0]).to.equal(agentSec.address);
      expect(slots[1]).to.equal(agentEco.address);
      expect(slots[2]).to.equal(agentGov.address);
    });

    it("non-owner cannot register an agent", async () => {
      const { dao, agentSec, stranger } = ctx;
      await expect(
        dao.connect(stranger).registerAgent(agentSec.address)
      ).to.be.revertedWith("DAOGovernance: caller is not the owner");
    });

    it("reverts when all 3 slots are already filled", async () => {
      const { dao, agentSec, agentEco, agentGov, stranger } = ctx;
      await registerAll(ctx);
      await expect(
        dao.registerAgent(stranger.address)
      ).to.be.revertedWith("DAOGovernance: agent slots full");
      // count must not have changed
      expect(await dao.registeredAgentCount()).to.equal(3);
    });

    it("reverts when registering the same address twice", async () => {
      const { dao, agentSec } = ctx;
      await dao.registerAgent(agentSec.address);
      await expect(
        dao.registerAgent(agentSec.address)
      ).to.be.revertedWith("DAOGovernance: agent already registered");
    });

    it("reverts for zero address", async () => {
      const { dao } = ctx;
      await expect(
        dao.registerAgent(ethers.ZeroAddress)
      ).to.be.revertedWith("DAOGovernance: zero address");
    });
  });

  // =========================================================================
  // 3. Agent replacement
  // =========================================================================

  describe("3 · replaceAgent", () => {
    beforeEach(() => registerAll(ctx));

    it("owner can replace slot 0", async () => {
      const { dao, stranger } = ctx;
      await dao.replaceAgent(0, stranger.address);
      const slots = await dao.getAgents();
      expect(slots[0]).to.equal(stranger.address);
      expect(await dao.registeredAgentCount()).to.equal(3); // count unchanged
    });

    it("owner can replace slot 1", async () => {
      const { dao, stranger } = ctx;
      await dao.replaceAgent(1, stranger.address);
      const slots = await dao.getAgents();
      expect(slots[1]).to.equal(stranger.address);
    });

    it("owner can replace slot 2", async () => {
      const { dao, stranger } = ctx;
      await dao.replaceAgent(2, stranger.address);
      const slots = await dao.getAgents();
      expect(slots[2]).to.equal(stranger.address);
    });

    it("old address can no longer vote after being replaced", async () => {
      const { dao, agentSec, stranger, proposer } = ctx;
      await dao.replaceAgent(0, stranger.address);
      const pid = await submitOne(dao, proposer);
      await expect(
        dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 80, "")
      ).to.be.revertedWith("DAOGovernance: caller is not a registered agent");
    });

    it("new address can vote after replacing", async () => {
      const { dao, agentEco, agentGov, stranger, proposer } = ctx;
      await dao.replaceAgent(0, stranger.address);
      const pid = await submitOne(dao, proposer);
      await expect(
        dao.connect(stranger).submitVote(pid, Role.Security, Rec.Approve, 80, "")
      ).to.emit(dao, "AgentVoted");
    });

    it("non-owner cannot replace an agent", async () => {
      const { dao, stranger, extra } = ctx;
      await expect(
        dao.connect(stranger).replaceAgent(0, extra.address)
      ).to.be.revertedWith("DAOGovernance: caller is not the owner");
    });

    it("reverts for index beyond registeredCount", async () => {
      // fresh contract, 0 registered — any index is invalid
      const freshCtx = await deployFresh();
      await expect(
        freshCtx.dao.replaceAgent(0, freshCtx.agentSec.address)
      ).to.be.revertedWith("DAOGovernance: invalid index");
    });

    it("reverts when new address is already registered", async () => {
      const { dao, agentEco } = ctx;
      await expect(
        dao.replaceAgent(0, agentEco.address) // agentEco is in slot 1
      ).to.be.revertedWith("DAOGovernance: agent already registered");
    });

    it("reverts for zero address replacement", async () => {
      const { dao } = ctx;
      await expect(
        dao.replaceAgent(0, ethers.ZeroAddress)
      ).to.be.revertedWith("DAOGovernance: zero address");
    });
  });

  // =========================================================================
  // 4. Proposal submission
  // =========================================================================

  describe("4 · submitProposal", () => {
    it("stores every field correctly", async () => {
      const { dao, proposer } = ctx;
      const blockBefore = await ethers.provider.getBlock("latest");

      const tx      = await dao.connect(proposer).submitProposal("My Title", "My Description");
      const receipt = await tx.wait();
      const block   = await ethers.provider.getBlock(receipt.blockNumber);

      const p = await dao.getProposal(0);
      expect(p.id).to.equal(0n);
      expect(p.title).to.equal("My Title");
      expect(p.description).to.equal("My Description");
      expect(p.submitter).to.equal(proposer.address);
      expect(p.status).to.equal(Status.Pending);
      expect(p.timestamp).to.equal(BigInt(block.timestamp));
      expect(p.hasDecision).to.be.false;
    });

    it("emits ProposalSubmitted with correct indexed args and title", async () => {
      const { dao, proposer } = ctx;
      await expect(
        dao.connect(proposer).submitProposal("Emit Test", "Description")
      )
        .to.emit(dao, "ProposalSubmitted")
        .withArgs(0n, proposer.address, "Emit Test", anyValue);
    });

    it("assigns sequential IDs: 0, 1, 2", async () => {
      const { dao, proposer } = ctx;
      const id0 = await submitOne(dao, proposer, "P0", "d");
      const id1 = await submitOne(dao, proposer, "P1", "d");
      const id2 = await submitOne(dao, proposer, "P2", "d");
      expect(id0).to.equal(0n);
      expect(id1).to.equal(1n);
      expect(id2).to.equal(2n);
    });

    it("proposalCount increments with each submission", async () => {
      const { dao, proposer } = ctx;
      expect(await dao.proposalCount()).to.equal(0);
      await submitOne(dao, proposer);
      expect(await dao.proposalCount()).to.equal(1);
      await submitOne(dao, proposer, "P2", "d");
      expect(await dao.proposalCount()).to.equal(2);
    });

    it("any address (not just owner or agents) can submit a proposal", async () => {
      const { dao, stranger } = ctx;
      await expect(
        dao.connect(stranger).submitProposal("Open Proposal", "Anyone can submit")
      ).to.emit(dao, "ProposalSubmitted");
    });

    it("reverts for empty title", async () => {
      const { dao, proposer } = ctx;
      await expect(
        dao.connect(proposer).submitProposal("", "Description")
      ).to.be.revertedWith("DAOGovernance: empty title");
    });

    it("reverts for empty description", async () => {
      const { dao, proposer } = ctx;
      await expect(
        dao.connect(proposer).submitProposal("Title", "")
      ).to.be.revertedWith("DAOGovernance: empty description");
    });

    it("getProposal reverts for a non-existent id", async () => {
      const { dao } = ctx;
      await expect(dao.getProposal(0)).to.be.revertedWith(
        "DAOGovernance: proposal does not exist"
      );
    });
  });

  // =========================================================================
  // 5. Voting — individual agents & error paths
  // =========================================================================

  describe("5 · submitVote", () => {
    let pid;

    beforeEach(async () => {
      await registerAll(ctx);
      pid = await submitOne(ctx.dao, ctx.proposer);
    });

    // ── Happy-path: each agent's vote stored correctly ──────────────────────

    it("Security agent (slot 0) vote is stored with all fields", async () => {
      const { dao, agentSec } = ctx;
      const tx = await dao
        .connect(agentSec)
        .submitVote(pid, Role.Security, Rec.Approve, 92, "No critical vulnerabilities found");
      await tx.wait();

      const v = await dao.getVote(pid, agentSec.address);
      expect(v.role).to.equal(Role.Security);
      expect(v.recommendation).to.equal(Rec.Approve);
      expect(v.confidence).to.equal(92);
      expect(v.reasoning).to.equal("No critical vulnerabilities found");
    });

    it("Economic agent (slot 1) vote is stored with all fields", async () => {
      const { dao, agentEco } = ctx;
      await dao
        .connect(agentEco)
        .submitVote(pid, Role.Economic, Rec.Revise, 78, "Returns are insufficient");

      const v = await dao.getVote(pid, agentEco.address);
      expect(v.role).to.equal(Role.Economic);
      expect(v.recommendation).to.equal(Rec.Revise);
      expect(v.confidence).to.equal(78);
      expect(v.reasoning).to.equal("Returns are insufficient");
    });

    it("Governance agent (slot 2) vote is stored with all fields", async () => {
      const { dao, agentGov } = ctx;
      await dao
        .connect(agentGov)
        .submitVote(pid, Role.Governance, Rec.Reject, 55, "Quorum rules violated");

      const v = await dao.getVote(pid, agentGov.address);
      expect(v.role).to.equal(Role.Governance);
      expect(v.recommendation).to.equal(Rec.Reject);
      expect(v.confidence).to.equal(55);
      expect(v.reasoning).to.equal("Quorum rules violated");
    });

    it("emits AgentVoted with correct arguments", async () => {
      const { dao, agentSec } = ctx;
      await expect(
        dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 85, "Looks safe")
      )
        .to.emit(dao, "AgentVoted")
        .withArgs(pid, agentSec.address, Role.Security, Rec.Approve, 85n);
    });

    it("voteCount increments after each agent votes: 0 → 1 → 2", async () => {
      const { dao, agentSec, agentEco } = ctx;
      expect(await dao.getVoteCount(pid)).to.equal(0);

      await dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 80, "");
      expect(await dao.getVoteCount(pid)).to.equal(1);

      await dao.connect(agentEco).submitVote(pid, Role.Economic, Rec.Approve, 70, "");
      expect(await dao.getVoteCount(pid)).to.equal(2);
    });

    it("accepts confidence = 0 (minimum boundary)", async () => {
      const { dao, agentSec } = ctx;
      await expect(
        dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 0, "Very uncertain")
      ).to.emit(dao, "AgentVoted");
      const v = await dao.getVote(pid, agentSec.address);
      expect(v.confidence).to.equal(0);
    });

    it("accepts confidence = 100 (maximum boundary)", async () => {
      const { dao, agentSec } = ctx;
      await expect(
        dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 100, "Fully certain")
      ).to.emit(dao, "AgentVoted");
      const v = await dao.getVote(pid, agentSec.address);
      expect(v.confidence).to.equal(100);
    });

    it("accepts an empty reasoning string", async () => {
      const { dao, agentSec } = ctx;
      await dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 50, "");
      const v = await dao.getVote(pid, agentSec.address);
      expect(v.reasoning).to.equal("");
    });

    // ── Error paths ─────────────────────────────────────────────────────────

    it("reverts when caller is not a registered agent", async () => {
      const { dao, stranger } = ctx;
      await expect(
        dao.connect(stranger).submitVote(pid, Role.Security, Rec.Approve, 50, "")
      ).to.be.revertedWith("DAOGovernance: caller is not a registered agent");
    });

    it("reverts when the owner (non-agent) tries to vote", async () => {
      const { dao, owner } = ctx;
      await expect(
        dao.connect(owner).submitVote(pid, Role.Governance, Rec.Approve, 90, "")
      ).to.be.revertedWith("DAOGovernance: caller is not a registered agent");
    });

    it("reverts for confidence = 101 (one above maximum)", async () => {
      const { dao, agentSec } = ctx;
      await expect(
        dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 101, "")
      ).to.be.revertedWith("DAOGovernance: confidence exceeds 100");
    });

    it("reverts on a second vote from the same agent (duplicate vote)", async () => {
      const { dao, agentSec } = ctx;
      await dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 80, "First");
      await expect(
        dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Reject, 60, "Second")
      ).to.be.revertedWith("DAOGovernance: agent already voted on this proposal");
    });

    it("reverts when proposal id does not exist", async () => {
      const { dao, agentSec } = ctx;
      await expect(
        dao.connect(agentSec).submitVote(99n, Role.Security, Rec.Approve, 80, "")
      ).to.be.revertedWith("DAOGovernance: proposal does not exist");
    });

    it("reverts when proposal is already decided", async () => {
      const { dao, agentSec, agentEco, agentGov, stranger } = ctx;

      // Decide the proposal
      await voteAll(
        dao,
        [agentSec, agentEco, agentGov],
        pid,
        [Rec.Approve, Rec.Approve, Rec.Approve],
        [80, 80, 80],
      );

      // Rotate agent and try to vote again
      await dao.replaceAgent(0, stranger.address);
      await expect(
        dao.connect(stranger).submitVote(pid, Role.Security, Rec.Reject, 90, "")
      ).to.be.revertedWith("DAOGovernance: proposal already decided");
    });
  });

  // =========================================================================
  // 6. Auto-finalisation & majority / tiebreak logic
  // =========================================================================

  describe("6 · Auto-finalisation", () => {
    let pid;

    beforeEach(async () => {
      await registerAll(ctx);
      pid = await submitOne(ctx.dao, ctx.proposer);
    });

    // ── Partial-vote state ──────────────────────────────────────────────────

    it("proposal stays Pending after 1 vote", async () => {
      const { dao, agentSec } = ctx;
      await dao.connect(agentSec).submitVote(pid, Role.Security, Rec.Approve, 90, "");
      const p = await dao.getProposal(pid);
      expect(p.status).to.equal(Status.Pending);
      expect(p.hasDecision).to.be.false;
    });

    it("proposal stays Pending after 2 votes", async () => {
      const { dao, agentSec, agentEco } = ctx;
      await dao.connect(agentSec).submitVote(pid, Role.Security,  Rec.Approve, 90, "");
      await dao.connect(agentEco).submitVote(pid, Role.Economic,  Rec.Approve, 80, "");
      const p = await dao.getProposal(pid);
      expect(p.status).to.equal(Status.Pending);
      expect(p.hasDecision).to.be.false;
    });

    // ── Finalisation trigger ────────────────────────────────────────────────

    it("proposal becomes Decided after all 3 votes", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await dao.connect(agentSec).submitVote(pid, Role.Security,    Rec.Approve, 90, "");
      await dao.connect(agentEco).submitVote(pid, Role.Economic,    Rec.Approve, 80, "");
      await dao.connect(agentGov).submitVote(pid, Role.Governance,  Rec.Reject,  60, "");

      const p = await dao.getProposal(pid);
      expect(p.status).to.equal(Status.Decided);
      expect(p.hasDecision).to.be.true;
    });

    it("3rd vote emits ProposalDecided(id, recommendation, 3)", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await dao.connect(agentSec).submitVote(pid, Role.Security,  Rec.Approve, 90, "");
      await dao.connect(agentEco).submitVote(pid, Role.Economic,  Rec.Approve, 80, "");
      await expect(
        dao.connect(agentGov).submitVote(pid, Role.Governance, Rec.Reject, 60, "")
      )
        .to.emit(dao, "ProposalDecided")
        .withArgs(pid, Rec.Approve, 3n);
    });

    it("voteCount is 3 after finalisation", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(
        dao,
        [agentSec, agentEco, agentGov],
        pid,
        [Rec.Approve, Rec.Reject, Rec.Revise],
        [80, 70, 60],
      );
      expect(await dao.getVoteCount(pid)).to.equal(3);
    });

    // ── 3-0 sweeps ──────────────────────────────────────────────────────────

    it("3-0 Approve  →  APPROVE", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Approve, Rec.Approve], [70, 80, 90]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Approve);
    });

    it("3-0 Reject   →  REJECT", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Reject, Rec.Reject, Rec.Reject], [70, 80, 90]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Reject);
    });

    it("3-0 Revise   →  REVISE", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Revise, Rec.Revise, Rec.Revise], [70, 80, 90]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Revise);
    });

    // ── All six 2-1 majority combinations ───────────────────────────────────

    it("2-1: Approve×2, Reject×1   →  APPROVE", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Approve, Rec.Reject], [60, 70, 100]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Approve);
    });

    it("2-1: Approve×2, Revise×1   →  APPROVE", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Approve, Rec.Revise], [60, 70, 100]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Approve);
    });

    it("2-1: Reject×2,  Approve×1  →  REJECT", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Reject, Rec.Reject, Rec.Approve], [80, 90, 100]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Reject);
    });

    it("2-1: Reject×2,  Revise×1   →  REJECT", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Reject, Rec.Reject, Rec.Revise], [80, 90, 100]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Reject);
    });

    it("2-1: Revise×2,  Approve×1  →  REVISE", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Revise, Rec.Revise, Rec.Approve], [80, 90, 100]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Revise);
    });

    it("2-1: Revise×2,  Reject×1   →  REVISE", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Revise, Rec.Revise, Rec.Reject], [80, 90, 100]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Revise);
    });

    // ── 1-1-1 tiebreaks ─────────────────────────────────────────────────────

    it("1-1-1 tie: Approve=50, Reject=90, Revise=60  →  REJECT (highest confidence)", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Reject,  Rec.Revise], [50, 90, 60]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Reject);
    });

    it("1-1-1 tie: Approve=60, Reject=50, Revise=80  →  REVISE (highest confidence)", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Reject,  Rec.Revise], [60, 50, 80]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Revise);
    });

    it("1-1-1 tie: Approve=100, Reject=50, Revise=60  →  APPROVE (highest confidence)", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Reject,  Rec.Revise], [100, 50, 60]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Approve);
    });

    it("1-1-1 tie: all equal confidence  →  APPROVE (priority: Approve > Revise > Reject)", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Reject, Rec.Revise], [70, 70, 70]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Approve);
    });

    it("1-1-1 tie: Approve lowest confidence, Reject=Revise highest  →  APPROVE (priority rule sees all 3 buckets)", async () => {
      // In a true 1-1-1 split the final priority loop iterates tiedBuckets = [Approve,Reject,Revise].
      // When Reject and Revise are tied for the highest confidence sum, stillTied is set and the
      // priority rule fires — but all three buckets are still in tiedBuckets, so Approve wins
      // regardless of its confidence score.  This documents the contract's conservative bias.
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Reject, Rec.Revise], [10, 70, 70]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Approve);
    });
  });

  // =========================================================================
  // 7. Multi-proposal isolation
  // =========================================================================

  describe("7 · Multi-proposal isolation", () => {
    beforeEach(() => registerAll(ctx));

    it("votes on proposal 0 do not affect proposal 1", async () => {
      const { dao, agentSec, agentEco, agentGov, proposer } = ctx;

      const pid0 = await submitOne(dao, proposer, "Proposal 0", "desc 0");
      const pid1 = await submitOne(dao, proposer, "Proposal 1", "desc 1");

      // Decide proposal 0
      await voteAll(dao, [agentSec, agentEco, agentGov], pid0,
        [Rec.Approve, Rec.Approve, Rec.Approve], [80, 80, 80]);

      // Proposal 1 must still be Pending
      const p1 = await dao.getProposal(pid1);
      expect(p1.status).to.equal(Status.Pending);
      expect(p1.hasDecision).to.be.false;
      expect(await dao.getVoteCount(pid1)).to.equal(0);
    });

    it("agents can vote independently on two separate proposals", async () => {
      const { dao, agentSec, agentEco, agentGov, proposer } = ctx;

      const pid0 = await submitOne(dao, proposer, "P0", "d");
      const pid1 = await submitOne(dao, proposer, "P1", "d");

      await voteAll(dao, [agentSec, agentEco, agentGov], pid0,
        [Rec.Approve, Rec.Approve, Rec.Reject], [90, 80, 50]);

      await voteAll(dao, [agentSec, agentEco, agentGov], pid1,
        [Rec.Reject, Rec.Reject, Rec.Revise], [90, 80, 50]);

      expect(await dao.getFinalRecommendation(pid0)).to.equal(Rec.Approve);
      expect(await dao.getFinalRecommendation(pid1)).to.equal(Rec.Reject);
    });

    it("proposal IDs are unique and sequential across multiple proposals", async () => {
      const { dao, proposer } = ctx;
      const ids = [];
      for (let i = 0; i < 5; i++) {
        ids.push(await submitOne(dao, proposer, `P${i}`, "d"));
      }
      for (let i = 0; i < 5; i++) {
        expect(ids[i]).to.equal(BigInt(i));
      }
      expect(await dao.proposalCount()).to.equal(5);
    });
  });

  // =========================================================================
  // 8. Getters — happy path and revert cases
  // =========================================================================

  describe("8 · Getters", () => {
    let pid;

    beforeEach(async () => {
      await registerAll(ctx);
      pid = await submitOne(ctx.dao, ctx.proposer, "Getter Test", "For getter tests");
    });

    // ── getProposal ─────────────────────────────────────────────────────────

    it("getProposal returns the full Proposal struct", async () => {
      const { dao, proposer } = ctx;
      const p = await dao.getProposal(pid);
      expect(p.id).to.equal(pid);
      expect(p.title).to.equal("Getter Test");
      expect(p.description).to.equal("For getter tests");
      expect(p.submitter).to.equal(proposer.address);
      expect(p.status).to.equal(Status.Pending);
      expect(p.hasDecision).to.be.false;
    });

    it("getProposal reverts for non-existent proposal id", async () => {
      const { dao } = ctx;
      await expect(dao.getProposal(999n)).to.be.revertedWith(
        "DAOGovernance: proposal does not exist"
      );
    });

    // ── getVote ─────────────────────────────────────────────────────────────

    it("getVote returns the correct Vote for each agent after voting", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;

      await dao.connect(agentSec).submitVote(pid, Role.Security,    Rec.Approve, 95, "sec reasoning");
      await dao.connect(agentEco).submitVote(pid, Role.Economic,    Rec.Revise,  72, "eco reasoning");
      await dao.connect(agentGov).submitVote(pid, Role.Governance,  Rec.Reject,  40, "gov reasoning");

      const vSec = await dao.getVote(pid, agentSec.address);
      expect(vSec.role).to.equal(Role.Security);
      expect(vSec.recommendation).to.equal(Rec.Approve);
      expect(vSec.confidence).to.equal(95);
      expect(vSec.reasoning).to.equal("sec reasoning");

      const vEco = await dao.getVote(pid, agentEco.address);
      expect(vEco.role).to.equal(Role.Economic);
      expect(vEco.recommendation).to.equal(Rec.Revise);
      expect(vEco.confidence).to.equal(72);
      expect(vEco.reasoning).to.equal("eco reasoning");

      const vGov = await dao.getVote(pid, agentGov.address);
      expect(vGov.role).to.equal(Role.Governance);
      expect(vGov.recommendation).to.equal(Rec.Reject);
      expect(vGov.confidence).to.equal(40);
      expect(vGov.reasoning).to.equal("gov reasoning");
    });

    it("getVote reverts when agent has not yet voted", async () => {
      const { dao, agentSec } = ctx;
      await expect(dao.getVote(pid, agentSec.address)).to.be.revertedWith(
        "DAOGovernance: agent has not voted on this proposal"
      );
    });

    it("getVote reverts for a non-agent address", async () => {
      const { dao, stranger } = ctx;
      await expect(dao.getVote(pid, stranger.address)).to.be.revertedWith(
        "DAOGovernance: agent has not voted on this proposal"
      );
    });

    // ── getAllVotes ──────────────────────────────────────────────────────────

    it("getAllVotes reverts before proposal is decided", async () => {
      const { dao } = ctx;
      await expect(dao.getAllVotes(pid)).to.be.revertedWith(
        "DAOGovernance: proposal not yet decided"
      );
    });

    it("getAllVotes returns agents and votes in agent-slot order after decision", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(
        dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Reject, Rec.Revise], [88, 66, 44],
        ["s-reason", "e-reason", "g-reason"],
      );

      const [addrs, agentVotes] = await dao.getAllVotes(pid);

      expect(addrs[0]).to.equal(agentSec.address);
      expect(addrs[1]).to.equal(agentEco.address);
      expect(addrs[2]).to.equal(agentGov.address);

      expect(agentVotes[0].role).to.equal(Role.Security);
      expect(agentVotes[0].recommendation).to.equal(Rec.Approve);
      expect(agentVotes[0].confidence).to.equal(88);
      expect(agentVotes[0].reasoning).to.equal("s-reason");

      expect(agentVotes[1].role).to.equal(Role.Economic);
      expect(agentVotes[1].recommendation).to.equal(Rec.Reject);
      expect(agentVotes[1].confidence).to.equal(66);
      expect(agentVotes[1].reasoning).to.equal("e-reason");

      expect(agentVotes[2].role).to.equal(Role.Governance);
      expect(agentVotes[2].recommendation).to.equal(Rec.Revise);
      expect(agentVotes[2].confidence).to.equal(44);
      expect(agentVotes[2].reasoning).to.equal("g-reason");
    });

    // ── getFinalRecommendation ───────────────────────────────────────────────

    it("getFinalRecommendation reverts before proposal is decided", async () => {
      const { dao } = ctx;
      await expect(dao.getFinalRecommendation(pid)).to.be.revertedWith(
        "DAOGovernance: proposal not yet decided"
      );
    });

    it("getFinalRecommendation returns the correct enum after decision", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Revise, Rec.Revise, Rec.Approve], [90, 80, 70]);
      expect(await dao.getFinalRecommendation(pid)).to.equal(Rec.Revise);
    });

    // ── getVoteCount ─────────────────────────────────────────────────────────

    it("getVoteCount is 0 before any votes", async () => {
      const { dao } = ctx;
      expect(await dao.getVoteCount(pid)).to.equal(0);
    });

    it("getVoteCount is 3 after all agents vote", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      await voteAll(dao, [agentSec, agentEco, agentGov], pid,
        [Rec.Approve, Rec.Approve, Rec.Approve], [80, 80, 80]);
      expect(await dao.getVoteCount(pid)).to.equal(3);
    });

    it("getVoteCount reverts for non-existent proposal", async () => {
      const { dao } = ctx;
      await expect(dao.getVoteCount(999n)).to.be.revertedWith(
        "DAOGovernance: proposal does not exist"
      );
    });

    // ── getAgents / registeredAgentCount ─────────────────────────────────────

    it("getAgents reflects state after full registration", async () => {
      const { dao, agentSec, agentEco, agentGov } = ctx;
      const slots = await dao.getAgents();
      expect(slots[0]).to.equal(agentSec.address);
      expect(slots[1]).to.equal(agentEco.address);
      expect(slots[2]).to.equal(agentGov.address);
    });

    it("registeredAgentCount is 3 after full registration", async () => {
      const { dao } = ctx;
      expect(await dao.registeredAgentCount()).to.equal(3);
    });
  });
});
