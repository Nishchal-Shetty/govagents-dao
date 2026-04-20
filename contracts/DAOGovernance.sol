// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title  DAOGovernance
 * @notice DAO contract where exactly three registered AI agents (one per role)
 *         each submit a typed vote on a proposal. Once all three have voted,
 *         the contract automatically tallies the majority and marks the
 *         proposal as Decided.
 *
 * Roles         : Security | Economic | Governance
 * Recommendations: Approve  | Reject   | Revise
 * Tiebreak (1-1-1): highest sum of confidence scores wins;
 *                   if still equal, Approve > Revise > Reject (conservative bias).
 */
contract DAOGovernance {

    // =========================================================================
    // Enumerations
    // =========================================================================

    enum Status         { Pending, Decided }
    enum AgentRole      { Security, Economic, Governance }
    enum Recommendation { Approve, Reject, Revise }

    // =========================================================================
    // Structs
    // =========================================================================

    struct Proposal {
        uint256        id;
        string         title;
        string         description;
        address        submitter;
        Status         status;
        uint256        timestamp;
        // Populated once all three agents have voted
        Recommendation finalRecommendation;
        bool           hasDecision;        // guards reads of finalRecommendation
    }

    struct Vote {
        AgentRole      role;
        Recommendation recommendation;
        uint8          confidence;   // 0 – 100
        string         reasoning;
    }

    // =========================================================================
    // State
    // =========================================================================

    address public owner;

    // Registered agents – maximum of MAX_AGENTS slots (indexed 0-2)
    uint8 public constant MAX_AGENTS = 3;
    address[MAX_AGENTS] private _agentSlots;
    uint8   private _registeredCount;

    // proposals[id]
    mapping(uint256 => Proposal)                     private _proposals;
    uint256 private _nextProposalId;

    // _votes[proposalId][agentAddress]
    mapping(uint256 => mapping(address => Vote))     private _votes;

    // _voted[proposalId][agentAddress] — prevents double voting
    mapping(uint256 => mapping(address => bool))     private _voted;

    // _voteCount[proposalId] — how many agents have voted so far
    mapping(uint256 => uint8)                        private _voteCount;

    // =========================================================================
    // Events
    // =========================================================================

    event ProposalSubmitted(
        uint256 indexed proposalId,
        address indexed submitter,
        string          title,
        uint256         timestamp
    );

    event AgentVoted(
        uint256        indexed proposalId,
        address        indexed agent,
        AgentRole             role,
        Recommendation        recommendation,
        uint8                 confidence
    );

    event ProposalDecided(
        uint256        indexed proposalId,
        Recommendation        finalRecommendation,
        uint8                 voteCount   // always 3 at decision time
    );

    // =========================================================================
    // Modifiers
    // =========================================================================

    modifier onlyOwner() {
        require(msg.sender == owner, "DAOGovernance: caller is not the owner");
        _;
    }

    modifier onlyRegisteredAgent() {
        require(_isRegisteredAgent(msg.sender), "DAOGovernance: caller is not a registered agent");
        _;
    }

    modifier proposalExists(uint256 proposalId) {
        require(proposalId < _nextProposalId, "DAOGovernance: proposal does not exist");
        _;
    }

    // =========================================================================
    // Constructor
    // =========================================================================

    constructor() {
        owner = msg.sender;
    }

    // =========================================================================
    // Owner — agent management
    // =========================================================================

    /**
     * @notice Register an agent address. Maximum of {MAX_AGENTS} agents allowed.
     * @param  agent  Wallet address the agent will sign transactions from.
     */
    function registerAgent(address agent) external onlyOwner {
        require(agent != address(0),          "DAOGovernance: zero address");
        require(_registeredCount < MAX_AGENTS, "DAOGovernance: agent slots full");
        require(!_isRegisteredAgent(agent),    "DAOGovernance: agent already registered");

        _agentSlots[_registeredCount] = agent;
        _registeredCount++;
    }

    /**
     * @notice Replace a registered agent (e.g. key rotation).
     * @param  index    Slot index 0-2 to replace.
     * @param  newAgent Replacement address.
     */
    function replaceAgent(uint8 index, address newAgent) external onlyOwner {
        require(index < _registeredCount,       "DAOGovernance: invalid index");
        require(newAgent != address(0),          "DAOGovernance: zero address");
        require(!_isRegisteredAgent(newAgent),   "DAOGovernance: agent already registered");

        _agentSlots[index] = newAgent;
    }

    // =========================================================================
    // Proposals
    // =========================================================================

    /**
     * @notice Submit a new governance proposal. Open to any address.
     * @param  title        Short title of the proposal.
     * @param  description  Full description text.
     * @return proposalId   ID of the newly created proposal.
     */
    function submitProposal(
        string calldata title,
        string calldata description
    ) external returns (uint256 proposalId) {
        require(bytes(title).length       > 0, "DAOGovernance: empty title");
        require(bytes(description).length > 0, "DAOGovernance: empty description");

        proposalId = _nextProposalId++;

        _proposals[proposalId] = Proposal({
            id:                 proposalId,
            title:              title,
            description:        description,
            submitter:          msg.sender,
            status:             Status.Pending,
            timestamp:          block.timestamp,
            finalRecommendation: Recommendation.Approve, // placeholder
            hasDecision:        false
        });

        emit ProposalSubmitted(proposalId, msg.sender, title, block.timestamp);
    }

    /**
     * @notice Total number of proposals submitted.
     */
    function proposalCount() external view returns (uint256) {
        return _nextProposalId;
    }

    // =========================================================================
    // Voting
    // =========================================================================

    /**
     * @notice Registered agent submits a vote on a proposal.
     *         Automatically finalises the proposal once all three agents have voted.
     *
     * @param proposalId     Target proposal.
     * @param role           The agent's governance role.
     * @param recommendation The agent's recommendation.
     * @param confidence     Self-reported confidence score, 0–100.
     * @param reasoning      Plain-text reasoning stored on-chain.
     */
    function submitVote(
        uint256        proposalId,
        AgentRole      role,
        Recommendation recommendation,
        uint8          confidence,
        string calldata reasoning
    )
        external
        onlyRegisteredAgent
        proposalExists(proposalId)
    {
        require(
            _proposals[proposalId].status == Status.Pending,
            "DAOGovernance: proposal already decided"
        );
        require(confidence <= 100, "DAOGovernance: confidence exceeds 100");
        require(
            !_voted[proposalId][msg.sender],
            "DAOGovernance: agent already voted on this proposal"
        );

        // Record vote
        _votes[proposalId][msg.sender] = Vote({
            role:           role,
            recommendation: recommendation,
            confidence:     confidence,
            reasoning:      reasoning
        });
        _voted[proposalId][msg.sender] = true;
        _voteCount[proposalId]++;

        emit AgentVoted(proposalId, msg.sender, role, recommendation, confidence);

        // Auto-finalise once all registered agents have voted
        if (_voteCount[proposalId] == _registeredCount && _registeredCount == MAX_AGENTS) {
            _finalise(proposalId);
        }
    }

    // =========================================================================
    // Internal — vote aggregation
    // =========================================================================

    /**
     * @dev  Counts votes and confidence scores per Recommendation bucket,
     *       determines the majority winner, and updates the proposal state.
     *
     *       Tiebreak order (most to least preferred when scores are equal):
     *         Approve (0) > Revise (2) > Reject (1)
     *       This conservative bias avoids rejecting a proposal on a pure tie.
     */
    function _finalise(uint256 proposalId) private {
        uint8[3]   memory counts;     // [Approve, Reject, Revise]
        uint16[3]  memory confidence; // sum of confidence per bucket (max 3*100=300, fits uint16)

        for (uint8 i = 0; i < MAX_AGENTS; i++) {
            address agent = _agentSlots[i];
            Vote storage v = _votes[proposalId][agent];
            uint8 rec = uint8(v.recommendation);
            counts[rec]++;
            confidence[rec] += v.confidence;
        }

        Recommendation winner = _pickWinner(counts, confidence);

        _proposals[proposalId].finalRecommendation = winner;
        _proposals[proposalId].hasDecision         = true;
        _proposals[proposalId].status              = Status.Decided;

        emit ProposalDecided(proposalId, winner, MAX_AGENTS);
    }

    /**
     * @dev  Pure majority tally with confidence-score tiebreak.
     *       counts[0] = Approve, counts[1] = Reject, counts[2] = Revise
     */
    function _pickWinner(
        uint8[3]  memory counts,
        uint16[3] memory confidence
    ) private pure returns (Recommendation) {
        // Find the highest vote count
        uint8 maxCount = counts[0];
        if (counts[1] > maxCount) maxCount = counts[1];
        if (counts[2] > maxCount) maxCount = counts[2];

        // Collect all buckets that share the top count
        uint8  tiedCount = 0;
        uint8[3] memory tiedBuckets;
        for (uint8 i = 0; i < 3; i++) {
            if (counts[i] == maxCount) {
                tiedBuckets[tiedCount++] = i;
            }
        }

        // Clear winner — no tie
        if (tiedCount == 1) {
            return Recommendation(tiedBuckets[0]);
        }

        // Tiebreak 1: highest summed confidence among tied buckets
        uint16 bestConf  = 0;
        uint8  bestBucket = tiedBuckets[0];
        for (uint8 i = 0; i < tiedCount; i++) {
            uint8 b = tiedBuckets[i];
            if (confidence[b] > bestConf) {
                bestConf   = confidence[b];
                bestBucket = b;
            }
        }

        // Tiebreak 2: if confidence is also equal, apply priority Approve > Revise > Reject
        // (already biased by bucket index ordering below — we re-check explicitly)
        bool stillTied = false;
        for (uint8 i = 0; i < tiedCount; i++) {
            uint8 b = tiedBuckets[i];
            if (b != bestBucket && confidence[b] == bestConf) {
                stillTied = true;
                break;
            }
        }

        if (!stillTied) {
            return Recommendation(bestBucket);
        }

        // Final tiebreak priority: Approve (0) > Revise (2) > Reject (1)
        for (uint8 i = 0; i < tiedCount; i++) {
            if (tiedBuckets[i] == uint8(Recommendation.Approve)) return Recommendation.Approve;
        }
        for (uint8 i = 0; i < tiedCount; i++) {
            if (tiedBuckets[i] == uint8(Recommendation.Revise))  return Recommendation.Revise;
        }
        return Recommendation.Reject;
    }

    // =========================================================================
    // Getters
    // =========================================================================

    /**
     * @notice Return the full Proposal struct for a given id.
     */
    function getProposal(uint256 proposalId)
        external
        view
        proposalExists(proposalId)
        returns (Proposal memory)
    {
        return _proposals[proposalId];
    }

    /**
     * @notice Return the Vote submitted by `agent` on `proposalId`.
     * @dev    Reverts if the agent has not yet voted.
     */
    function getVote(uint256 proposalId, address agent)
        external
        view
        proposalExists(proposalId)
        returns (Vote memory)
    {
        require(
            _voted[proposalId][agent],
            "DAOGovernance: agent has not voted on this proposal"
        );
        return _votes[proposalId][agent];
    }

    /**
     * @notice Return all three votes for a decided proposal in agent-slot order.
     * @dev    Reverts if the proposal has not been decided yet.
     */
    function getAllVotes(uint256 proposalId)
        external
        view
        proposalExists(proposalId)
        returns (address[3] memory agents, Vote[3] memory agentVotes)
    {
        require(
            _proposals[proposalId].hasDecision,
            "DAOGovernance: proposal not yet decided"
        );
        agents = _agentSlots;
        for (uint8 i = 0; i < MAX_AGENTS; i++) {
            agentVotes[i] = _votes[proposalId][_agentSlots[i]];
        }
    }

    /**
     * @notice Return the final recommendation for a decided proposal.
     * @dev    Reverts if the proposal has not been decided yet.
     */
    function getFinalRecommendation(uint256 proposalId)
        external
        view
        proposalExists(proposalId)
        returns (Recommendation)
    {
        require(
            _proposals[proposalId].hasDecision,
            "DAOGovernance: proposal not yet decided"
        );
        return _proposals[proposalId].finalRecommendation;
    }

    /**
     * @notice Return the number of votes cast on a proposal so far.
     */
    function getVoteCount(uint256 proposalId)
        external
        view
        proposalExists(proposalId)
        returns (uint8)
    {
        return _voteCount[proposalId];
    }

    /**
     * @notice Return the list of registered agent addresses (up to MAX_AGENTS).
     */
    function getAgents() external view returns (address[3] memory) {
        return _agentSlots;
    }

    /**
     * @notice Return the number of currently registered agents.
     */
    function registeredAgentCount() external view returns (uint8) {
        return _registeredCount;
    }

    // =========================================================================
    // Internal helpers
    // =========================================================================

    function _isRegisteredAgent(address addr) private view returns (bool) {
        for (uint8 i = 0; i < _registeredCount; i++) {
            if (_agentSlots[i] == addr) return true;
        }
        return false;
    }
}

// =============================================================================
// DESIGN NOTES — Personal Agent Model (not implemented, future direction)
// =============================================================================
//
// Current problem: owner registers all 3 agents. Token holders have no way to
// verify those agents weren't tuned to favour a particular outcome. The fixed
// panel is a trust-the-operator assumption.
//
// The fix is to let each token holder delegate their vote to an agent they
// control, then tally across delegates rather than across 3 hardcoded slots.
//
// Rough interface sketch:
//
//   mapping(address => address) public delegatedAgent;
//   // holder => agent address they've pointed their vote at
//
//   mapping(address => uint256) public delegatedWeight;
//   // agent => total token weight delegated to it (summed across holders)
//
//   uint256 public quorumThreshold;
//   // min total token weight that must vote before _finalise() can run
//   // replaces the hardcoded _registeredCount == MAX_AGENTS check
//
//   function delegateAgent(address agent) external {
//       require(agent != address(0), "zero address");
//       address prev = delegatedAgent[msg.sender];
//       if (prev != address(0)) {
//           delegatedWeight[prev] -= _tokenBalance(msg.sender);
//       }
//       delegatedAgent[msg.sender] = agent;
//       delegatedWeight[agent] += _tokenBalance(msg.sender);
//       emit AgentDelegated(msg.sender, agent);
//   }
//
//   function revokeDelegation() external {
//       address agent = delegatedAgent[msg.sender];
//       require(agent != address(0), "no delegation");
//       delegatedWeight[agent] -= _tokenBalance(msg.sender);
//       delegatedAgent[msg.sender] = address(0);
//       emit DelegationRevoked(msg.sender, agent);
//   }
//
// submitVote() would stay mostly the same — any address whose delegatedWeight
// is > 0 is a valid voter. _finalise() triggers when sum of voting agents'
// delegatedWeight >= quorumThreshold instead of when voteCount == 3.
//
// Aggregation changes: instead of 1 vote per agent slot, each agent's vote is
// weighted by delegatedWeight[agent]. The confidence-score tiebreak still
// applies within each recommendation bucket.
//
// What this buys: each holder configures their own agent (off-chain). The
// on-chain contract only sees wallet addresses and token weights — it doesn't
// care what model or prompt the agent uses. No operator can unilaterally
// control the outcome without controlling a quorum of token weight.
//
// Blockers before implementing:
//   - Need a governance token contract with balanceOf() to read weights from
//   - Snapshot vs. live balance (live balance is gameable; snapshot is safer)
//   - What happens when a holder's balance changes after they delegated?
//     delegatedWeight becomes stale — need a rebalance hook or periodic sync
//   - Quorum threshold needs governance to set/change it (circular dependency
//     if the same contract governs itself)
// =============================================================================
