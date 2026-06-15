"""
XHS Pipeline Engine — MaaFramework-inspired declarative automation pipeline.

Architecture:
    YAML Pipeline Definition → Loader → DAG of PipelineNodes → Executor
    
    Executor loop (per MaaFramework PipelineTask.cpp):
        Screencap → Recognize candidates → Execute action → Route to next/on_error
    
    Key features:
        - JumpBack interrupt stack (popup handling with auto-return)
        - Anchor dynamic variables (cross-node state passing)
        - Middleware injection (Watchdog, LoopDetector, Logging)
        - Visual assertions (replace time.sleep with recognition-based waits)
        - Probability gating (farming randomization)
        - Batch OCR optimization
"""

from .models import (
    RecognitionType,
    ActionType,
    RecognitionSpec,
    ActionSpec,
    PipelineNode,
    HitResult,
    RecognitionResult,
    AnchorStore,
)

__all__ = [
    "RecognitionType",
    "ActionType",
    "RecognitionSpec",
    "ActionSpec",
    "PipelineNode",
    "HitResult",
    "RecognitionResult",
    "AnchorStore",
]
