"""Shared context-management primitives for central and robot-local planners."""

from fireclaw_core.context.evaluation import (
    ContextEvaluationCase,
    ContextEvaluationReport,
    advisory_tuples,
    evaluate_context_case,
)
from fireclaw_core.context.manager import (
    CjkHeuristicTokenCounter,
    ContextBudgetExceeded,
    ContextManagementPolicy,
    HuggingFaceTokenCounter,
    ManagedContextManifest,
    ManagedContextResult,
    ModelAwareContextManager,
    StructuredSemanticCompactor,
)

__all__ = [
    "CjkHeuristicTokenCounter",
    "ContextBudgetExceeded",
    "ContextEvaluationCase",
    "ContextEvaluationReport",
    "ContextManagementPolicy",
    "HuggingFaceTokenCounter",
    "ManagedContextManifest",
    "ManagedContextResult",
    "ModelAwareContextManager",
    "StructuredSemanticCompactor",
    "advisory_tuples",
    "evaluate_context_case",
]
