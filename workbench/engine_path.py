"""Put the existing research package on the import path without rewriting it."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "quant_signal_lab_online"
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))
