"""Structural detectors: high recall, deliberately low precision.

They emit candidates, not findings. See docs/ARCHITECTURE.md section 4.
"""

from materia.detect.detectors import (
    DETECTOR_FAMILIES,
    DETECTORS,
    Candidate,
    detect,
)
from materia.detect.peers import PeerCell, PeerGroup, Workbook, load

__all__ = [
    "Candidate",
    "DETECTORS",
    "DETECTOR_FAMILIES",
    "PeerCell",
    "PeerGroup",
    "Workbook",
    "detect",
    "load",
]
