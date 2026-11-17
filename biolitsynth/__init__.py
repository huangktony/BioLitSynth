"""BioLitSynth: multi-agent biomedical literature synthesis. See docs/IMPLEMENTATION_PLAN.md."""

__version__ = "0.0.1"

import os as _os

# macOS: the torch and faiss-cpu wheels each bundle their own libomp, and loading both in one process aborts with
# "OMP: Error #15". Import order does not avoid it (tried both). This is the one place we allow the duplicate;
# it only matters when the dense index and the cross-encoder run in the same process (`eval compare`).
_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
