const { expect } = require("chai");
const { ethers } = require("hardhat");
const { time } = require("@nomicfoundation/hardhat-network-helpers");

describe("AgentDAO", function () {
  let dao, owner, agent1, agent2, agent3, proposer;

  const DURATION = 60 * 60; // 1 hour

  beforeEach(async () => {
    [owner, agent1, agent2, agent3, proposer] = await ethers.getSigners();
    const AgentDAO = await ethers.getContractFactory("AgentDAO");
    dao = await AgentDAO.deploy();
  });

  // ── Agent registration ──────────────────────────────────────────

  describe("registerAgent", () => {
    it("registers an agent with correct fields", async () => {
      await dao.registerAgent(agent1.address, "Alpha", 80);
      const a = await dao.agents(agent1.address);
      expect(a.name).to.equal("Alpha");
      expect(a.weight).to.equal(80);
      expect(a.active).to.be.true;
    });

    it("reverts for non-owner", async () => {
      await expect(
        dao.connect(agent1).registerAgent(agent2.address, "Beta", 50)
      ).to.be.revertedWith("AgentDAO: not owner");
    });

    it("reverts for duplicate registration", async () => {
      await dao.registerAgent(agent1.address, "Alpha", 50);
      await expect(
        dao.registerAgent(agent1.address, "Alpha2", 60)
      ).to.be.revertedWith("AgentDAO: agent already registered");
    });

    it("reverts for weight out of range", async () => {
      await expect(
        dao.registerAgent(agent1.address, "Alpha", 0)
      ).to.be.revertedWith("AgentDAO: weight out of range");
      await expect(
        dao.registerAgent(agent1.address, "Alpha", 101)
      ).to.be.revertedWith("AgentDAO: weight out of range");
    });
  });

  // ── Proposals ──────────────────────────────────────────────────

  describe("submitProposal", () => {
    it("creates a proposal and returns its id", async () => {
      await expect(dao.connect(proposer).submitProposal("Fund the treasury", DURATION))
        .to.emit(dao, "ProposalSubmitted")
        .withArgs(0, proposer.address, "Fund the treasury");

      const p = await dao.proposals(0);
      expect(p.description).to.equal("Fund the treasury");
      expect(p.finalized).to.be.false;
    });

    it("increments proposalCount", async () => {
      await dao.submitProposal("P1", DURATION);
      await dao.submitProposal("P2", DURATION);
      expect(await dao.proposalCount()).to.equal(2);
    });
  });

  // ── Voting ────────────────────────────────────────────────────

  describe("submitVote", () => {
    beforeEach(async () => {
      await dao.registerAgent(agent1.address, "Alpha", 80);
      await dao.registerAgent(agent2.address, "Beta", 60);
      await dao.submitProposal("Upgrade protocol", DURATION);
    });

    it("records a vote", async () => {
      await dao.connect(agent1).submitVote(0, 1 /* Yes */, 90, "Looks good");
      const v = await dao.votes(0, agent1.address);
      expect(v.option).to.equal(1);
      expect(v.confidence).to.equal(90);
      expect(v.rationale).to.equal("Looks good");
    });

    it("reverts for non-agent", async () => {
      await expect(
        dao.connect(proposer).submitVote(0, 1, 50, "")
      ).to.be.revertedWith("AgentDAO: not an active agent");
    });

    it("reverts for double-vote", async () => {
      await dao.connect(agent1).submitVote(0, 1, 80, "");
      await expect(
        dao.connect(agent1).submitVote(0, 2, 70, "")
      ).to.be.revertedWith("AgentDAO: already voted");
    });

    it("reverts after deadline", async () => {
      await time.increase(DURATION + 1);
      await expect(
        dao.connect(agent1).submitVote(0, 1, 80, "")
      ).to.be.revertedWith("AgentDAO: voting period closed");
    });
  });

  // ── Finalization & aggregation ────────────────────────────────

  describe("finalizeProposal", () => {
    beforeEach(async () => {
      await dao.registerAgent(agent1.address, "Alpha", 80);
      await dao.registerAgent(agent2.address, "Beta",  60);
      await dao.registerAgent(agent3.address, "Gamma", 40);
      await dao.submitProposal("Major upgrade", DURATION);
    });

    it("produces APPROVE when Yes has highest weighted score", async () => {
      // agent1: Yes  80*90 = 7200
      // agent2: Yes  60*80 = 4800
      // agent3: No   40*100= 4000   → Yes wins
      await dao.connect(agent1).submitVote(0, 1, 90, "");
      await dao.connect(agent2).submitVote(0, 1, 80, "");
      await dao.connect(agent3).submitVote(0, 2, 100, "");

      await time.increase(DURATION + 1);
      await expect(dao.finalizeProposal(0))
        .to.emit(dao, "ProposalFinalized")
        .withArgs(0, "APPROVE");

      const [wYes, wNo, wAbstain, rec] = await dao.getResult(0);
      expect(wYes).to.equal(7200 + 4800);
      expect(wNo).to.equal(4000);
      expect(rec).to.equal("APPROVE");
    });

    it("produces REJECT when No has highest weighted score", async () => {
      // agent1: No   80*100=8000
      // agent2: No   60*90 =5400
      // agent3: Yes  40*100=4000   → No wins
      await dao.connect(agent1).submitVote(0, 2, 100, "");
      await dao.connect(agent2).submitVote(0, 2, 90, "");
      await dao.connect(agent3).submitVote(0, 1, 100, "");

      await time.increase(DURATION + 1);
      await dao.finalizeProposal(0);
      const [, , , rec] = await dao.getResult(0);
      expect(rec).to.equal("REJECT");
    });

    it("reverts finalization before deadline", async () => {
      await expect(dao.finalizeProposal(0)).to.be.revertedWith(
        "AgentDAO: voting still open"
      );
    });

    it("reverts double finalization", async () => {
      await time.increase(DURATION + 1);
      await dao.finalizeProposal(0);
      await expect(dao.finalizeProposal(0)).to.be.revertedWith(
        "AgentDAO: already finalized"
      );
    });
  });
});
