"""
agents/_tally.py
~~~~~~~~~~~~~~~~
Shared vote-tallying logic that mirrors the on-chain ``_finalise`` function
in DAOGovernance.sol.  Imported by both runner.py (dry-run mode) and eval.py.
"""
from __future__ import annotations

from collections import Counter


def tally_consensus(verdicts: list[dict]) -> str | None:
    """Return the consensus recommendation from a list of agent verdict dicts.

    Each dict must contain ``"recommendation"`` (str) and ``"confidence"``
    (int).  Implements the same three-tier tiebreak as the contract:

    1. Clear majority vote count wins.
    2. Among tied buckets, highest summed confidence wins.
    3. If still tied: Approve > Revise > Reject.

    Returns ``None`` if the verdict list is empty.
    """
    recs = [v["recommendation"] for v in verdicts if v.get("recommendation")]
    if not recs:
        return None

    counts: dict[str, int] = Counter(recs)
    conf:   dict[str, int] = {}
    for v in verdicts:
        rec = v.get("recommendation")
        if rec:
            conf[rec] = conf.get(rec, 0) + (v.get("confidence") or 0)

    max_count = max(counts.values())
    leaders   = [r for r, c in counts.items() if c == max_count]

    if len(leaders) == 1:
        return leaders[0]

    best_conf    = max(conf.get(r, 0) for r in leaders)
    conf_leaders = [r for r in leaders if conf.get(r, 0) == best_conf]

    if len(conf_leaders) == 1:
        return conf_leaders[0]

    for priority in ("Approve", "Revise", "Reject"):
        if priority in conf_leaders:
            return priority
    return None
