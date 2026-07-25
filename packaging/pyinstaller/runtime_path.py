from __future__ import annotations

import os
import sys

bundle_dir = getattr(sys, "_MEIPASS", None)
if bundle_dir is not None:
    os.environ["PATH"] = os.pathsep.join((bundle_dir, os.environ.get("PATH", "")))
