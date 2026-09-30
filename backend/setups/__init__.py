"""Objective, versioned setup discovery. See docs/SETUP_LIBRARY.md.

Recognition is separate from permission to trade: detections are research /
watch output until their detector version is validated (see evidence.py)."""

from . import detectors  # noqa: F401  (registers detectors)
from .bars import Bars
from .engine import scan_bars
from .model import pending_definitions, registered, spec_for

__all__ = ["Bars", "scan_bars", "pending_definitions", "registered", "spec_for"]
