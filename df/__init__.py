"""
Workflow-diff utilities — compare a generated Galaxy workflow against
the KB methods it could have been derived from.

This package is intentionally isolated from src/ so adding it does not
risk breaking the planner / compiler / reasoner. Import from here only;
nothing in this package writes to src/, metta/, or generated/.
"""
