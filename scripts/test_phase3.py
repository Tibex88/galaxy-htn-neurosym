"""
Phase 3 integration test — ChatHTN online learning loop.

Exercises the four modules:
  - MethodProposer  (offline fallback path)
  - MethodVerifier  (tool-existence + DAG well-formedness)
  - MethodPersistence (append to method_sets.metta)
  - OnlineLearner   (orchestrator)
  - FeedbackLearner (STV revision from outcomes)

Uses offline proposer so the test runs without a network. The LLM path
exercises identically once GEMINI_API_KEY is set.
"""

import sys
import shutil
from pathlib import Path

sys.path.insert(0, ".")

from src import config
from src.pln.reasoner import PLNReasoner
from src.htn.planner import HTNPlanner
from src.htn.method_proposer import MethodProposer
from src.htn.method_verifier import MethodVerifier
from src.htn.method_persistence import MethodPersistence
from src.htn.online_learner import OnlineLearner
from src.htn.feedback import FeedbackLearner


DOMAIN_DIR = Path(config.METTA_DOMAIN_DIR)
METHOD_SETS = DOMAIN_DIR / "method_sets.metta"
TOOL_ATOMS = DOMAIN_DIR / "tool_atoms.metta"


def _snapshot():
    backups = {}
    for p in (METHOD_SETS, TOOL_ATOMS):
        if p.exists():
            backups[p] = p.read_text()
    return backups


def _restore(backups):
    for p, content in backups.items():
        p.write_text(content)


def main():
    backups = _snapshot()
    try:
        reasoner = PLNReasoner().load()
        planner = HTNPlanner(reasoner)

        print("\n=== Proposer — offline fallback ===")
        proposer = MethodProposer(reasoner, prefer_llm=False)
        prop = proposer.propose("RNA_Analysis")
        print(prop.render())
        assert prop.tools, "proposer should synthesize a tool list from examples"

        print("\n=== Verifier — accepts in-catalog proposal ===")
        verifier = MethodVerifier(reasoner)
        v = verifier.verify(prop)
        print(v.render())
        assert v.ok, "RNA_Analysis splice should verify"

        print("\n=== Verifier — rejects hallucinated tool ===")
        from src.htn.method_proposer import ProposedMethod

        fake = ProposedMethod(
            task="RNA_Analysis",
            method_name="fake_method",
            tools=["FastQC", "THIS_TOOL_DOES_NOT_EXIST_ZZZ"],
            rationale="intentionally invalid",
            source="test",
        )
        v2 = verifier.verify(fake)
        print(v2.render())
        assert not v2.ok, "verifier must reject unknown tool"
        assert v2.unknown_tools, "verifier should flag which tools are unknown"

        print("\n=== OnlineLearner — end-to-end on RNA_Analysis ===")
        learner = OnlineLearner(
            reasoner,
            planner,
            score_threshold=0.99,  # force gap detection
        )
        report = learner.plan_with_learning("RNA_Analysis")
        print(report.render())
        assert report.gap_detected, "threshold=0.99 should force a gap"
        assert report.rounds >= 1

        print("\n=== Persistence — method_sets.metta grew ===")
        new_text = METHOD_SETS.read_text()
        assert (
            "online-learned method" in new_text
        ), "method_sets.metta should have the online-learned marker"

        print("\n=== FeedbackLearner — success bumps STV ===")
        fb = FeedbackLearner(reasoner)
        if prop.tools:
            tool = prop.tools[0]
            old = reasoner.get_tool_stv(tool)
            res = fb.record(tool, "success")
            print(res.render())
            assert (
                res.new_stv[1] >= old[1] - 0.001
            ), "success should not reduce confidence"

        print("\n=== PASS ===")
    finally:
        _restore(backups)
        print("(domain files restored)")


if __name__ == "__main__":
    main()
