// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title AgentDAO
 * @notice DAO governance contract where registered AI agents submit weighted votes
 *         with confidence scores and votes are aggregated into a final recommendation.
 */
contract AgentDAO {
    // ─────────────────────────────────────────────────────────────
    // Types
    // ─────────────────────────────────────────────────────────────

    enum VoteOption { None, Yes, No, Abstain }

    struct Proposal {
        uint256 id;
        string  description;
        address proposer;
        uint256 createdAt;
        uint256 deadline;       // block.timestamp after which no new votes accepted
        bool    finalized;
        // aggregated tallies (updated lazily on finalization)
        uint256 weightedYes;    // sum of (confidence * weight) for Yes votes
        uint256 weightedNo;
        uint256 weightedAbstain;
        string  finalRecommendation; // "APPROVE" | "REJECT" | "ABSTAIN" | "TIE"
    }

    struct Agent {
        address addr;
        string  name;
        uint256 weight;   // relative weight 1–100; default 50
        bool    active;
    }

    struct Vote {
        VoteOption option;
        uint8      confidence; // 1–100
        string     rationale;
        uint256    timestamp;
    }

    // ─────────────────────────────────────────────────────────────
    // State
    // ─────────────────────────────────────────────────────────────

    address public owner;

    uint256 private _nextProposalId;
    mapping(uint256 => Proposal)                        public proposals;
    mapping(uint256 => mapping(address => Vote))        public votes;   // proposalId → agent → vote
    mapping(uint256 => address[])                       private _voters; // proposalId → list of voters

    mapping(address => Agent) public agents;
    address[]                 private _agentList;

    // ─────────────────────────────────────────────────────────────
    // Events
    // ─────────────────────────────────────────────────────────────

    event AgentRegistered(address indexed agent, string name, uint256 weight);
    event AgentDeactivated(address indexed agent);
    event ProposalSubmitted(uint256 indexed proposalId, address indexed proposer, string description);
    event VoteSubmitted(uint256 indexed proposalId, address indexed agent, VoteOption option, uint8 confidence);
    event ProposalFinalized(uint256 indexed proposalId, string recommendation);

    // ─────────────────────────────────────────────────────────────
    // Modifiers
    // ─────────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "AgentDAO: not owner");
        _;
    }

    modifier onlyActiveAgent() {
        require(agents[msg.sender].active, "AgentDAO: not an active agent");
        _;
    }

    modifier proposalExists(uint256 proposalId) {
        require(proposalId < _nextProposalId, "AgentDAO: proposal does not exist");
        _;
    }

    // ─────────────────────────────────────────────────────────────
    // Constructor
    // ─────────────────────────────────────────────────────────────

    constructor() {
        owner = msg.sender;
    }

    // ─────────────────────────────────────────────────────────────
    // Agent management
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Register a new AI agent (owner only).
     * @param agentAddr  Wallet address the agent will sign transactions from.
     * @param name       Human-readable label for the agent.
     * @param weight     Voting weight in the range [1, 100].
     */
    function registerAgent(address agentAddr, string calldata name, uint256 weight) external onlyOwner {
        require(agentAddr != address(0), "AgentDAO: zero address");
        require(!agents[agentAddr].active, "AgentDAO: agent already registered");
        require(weight >= 1 && weight <= 100, "AgentDAO: weight out of range");

        agents[agentAddr] = Agent({
            addr:   agentAddr,
            name:   name,
            weight: weight,
            active: true
        });
        _agentList.push(agentAddr);

        emit AgentRegistered(agentAddr, name, weight);
    }

    /**
     * @notice Deactivate an agent so it can no longer vote (owner only).
     */
    function deactivateAgent(address agentAddr) external onlyOwner {
        require(agents[agentAddr].active, "AgentDAO: agent not active");
        agents[agentAddr].active = false;
        emit AgentDeactivated(agentAddr);
    }

    /**
     * @notice Return the list of all registered agent addresses.
     */
    function getAgents() external view returns (address[] memory) {
        return _agentList;
    }

    // ─────────────────────────────────────────────────────────────
    // Proposals
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Submit a new governance proposal.
     * @param description  Text describing the proposal.
     * @param duration     Voting window in seconds from now.
     */
    function submitProposal(string calldata description, uint256 duration) external returns (uint256) {
        require(bytes(description).length > 0, "AgentDAO: empty description");
        require(duration > 0, "AgentDAO: zero duration");

        uint256 proposalId = _nextProposalId++;

        proposals[proposalId] = Proposal({
            id:                 proposalId,
            description:        description,
            proposer:           msg.sender,
            createdAt:          block.timestamp,
            deadline:           block.timestamp + duration,
            finalized:          false,
            weightedYes:        0,
            weightedNo:         0,
            weightedAbstain:    0,
            finalRecommendation: ""
        });

        emit ProposalSubmitted(proposalId, msg.sender, description);
        return proposalId;
    }

    /**
     * @notice Return the total number of proposals created.
     */
    function proposalCount() external view returns (uint256) {
        return _nextProposalId;
    }

    // ─────────────────────────────────────────────────────────────
    // Voting
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Submit a vote for a proposal.
     * @param proposalId  Target proposal.
     * @param option      Yes (1), No (2), or Abstain (3).
     * @param confidence  Agent's self-reported confidence, 1–100.
     * @param rationale   Optional plain-text reasoning stored on-chain.
     */
    function submitVote(
        uint256    proposalId,
        VoteOption option,
        uint8      confidence,
        string calldata rationale
    )
        external
        onlyActiveAgent
        proposalExists(proposalId)
    {
        Proposal storage p = proposals[proposalId];
        require(!p.finalized, "AgentDAO: proposal already finalized");
        require(block.timestamp <= p.deadline, "AgentDAO: voting period closed");
        require(option != VoteOption.None, "AgentDAO: invalid vote option");
        require(confidence >= 1 && confidence <= 100, "AgentDAO: confidence out of range");
        require(votes[proposalId][msg.sender].option == VoteOption.None, "AgentDAO: already voted");

        votes[proposalId][msg.sender] = Vote({
            option:     option,
            confidence: confidence,
            rationale:  rationale,
            timestamp:  block.timestamp
        });
        _voters[proposalId].push(msg.sender);

        emit VoteSubmitted(proposalId, msg.sender, option, confidence);
    }

    /**
     * @notice Return all votes cast on a proposal.
     */
    function getVoters(uint256 proposalId) external view proposalExists(proposalId) returns (address[] memory) {
        return _voters[proposalId];
    }

    // ─────────────────────────────────────────────────────────────
    // Aggregation & finalization
    // ─────────────────────────────────────────────────────────────

    /**
     * @notice Aggregate all votes and store a final recommendation.
     *         Can be called by anyone once the deadline has passed.
     *         Each vote's contribution = agent.weight * vote.confidence.
     *
     * @param proposalId  Proposal to finalize.
     */
    function finalizeProposal(uint256 proposalId) external proposalExists(proposalId) {
        Proposal storage p = proposals[proposalId];
        require(!p.finalized, "AgentDAO: already finalized");
        require(block.timestamp > p.deadline, "AgentDAO: voting still open");

        uint256 weightedYes;
        uint256 weightedNo;
        uint256 weightedAbstain;

        address[] storage voters = _voters[proposalId];
        for (uint256 i = 0; i < voters.length; i++) {
            address voter = voters[i];
            Vote    storage v = votes[proposalId][voter];
            Agent   storage a = agents[voter];

            uint256 score = a.weight * uint256(v.confidence);

            if (v.option == VoteOption.Yes) {
                weightedYes += score;
            } else if (v.option == VoteOption.No) {
                weightedNo += score;
            } else if (v.option == VoteOption.Abstain) {
                weightedAbstain += score;
            }
        }

        p.weightedYes     = weightedYes;
        p.weightedNo      = weightedNo;
        p.weightedAbstain = weightedAbstain;
        p.finalized       = true;

        string memory rec;
        if (weightedYes > weightedNo && weightedYes > weightedAbstain) {
            rec = "APPROVE";
        } else if (weightedNo > weightedYes && weightedNo > weightedAbstain) {
            rec = "REJECT";
        } else if (weightedAbstain > weightedYes && weightedAbstain > weightedNo) {
            rec = "ABSTAIN";
        } else {
            rec = "TIE";
        }

        p.finalRecommendation = rec;
        emit ProposalFinalized(proposalId, rec);
    }

    /**
     * @notice Convenience view: returns aggregated scores and recommendation for a finalized proposal.
     */
    function getResult(uint256 proposalId)
        external
        view
        proposalExists(proposalId)
        returns (
            uint256 weightedYes,
            uint256 weightedNo,
            uint256 weightedAbstain,
            string memory recommendation
        )
    {
        Proposal storage p = proposals[proposalId];
        require(p.finalized, "AgentDAO: not yet finalized");
        return (p.weightedYes, p.weightedNo, p.weightedAbstain, p.finalRecommendation);
    }
}
