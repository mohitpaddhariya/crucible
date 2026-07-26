"""judge — scores conversations that already happened.

Reads  runs/<run_id>/conversations/*.json
Writes runs/<run_id>/scorecards/*.json

This stage NEVER opens a socket to the target and never runs a conversation. That is the
whole point of the file boundary (docs/INTERFACES.md §1 rule 5): re-judging a run costs
nothing but judge tokens, so the rubric can be tuned twenty times against identical input.
"""

from judge.judge import JudgeError, audit_evidence, judge_conversation, judge_run
from judge.rubric import DIMENSIONS, band_for, weighted_score

__all__ = [
    "judge_conversation", "judge_run", "audit_evidence", "JudgeError",
    "DIMENSIONS", "band_for", "weighted_score",
]
