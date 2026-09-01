"""Context package: split of the legacy context_message.ContextManager.
"""

from context.token_estimator import TokenEstimator

try:
    from context.serialization import serialize_conversation, truncate_for_summary
except ImportError:
    serialize_conversation = None  # type: ignore[assignment]
    truncate_for_summary = None  # type: ignore[assignment]

try:
    from context.transform import default_transform_context
except ImportError:
    default_transform_context = None

__all__ = [
    "TokenEstimator",
    "default_transform_context",
    "serialize_conversation",
    "truncate_for_summary",
]