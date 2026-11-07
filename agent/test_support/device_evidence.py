"""Optional local game evidence. Real screenshots are never repository fixtures."""
from pathlib import Path

import pytest


EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "debug/test-fixtures"


@pytest.fixture(autouse=True)
def require_device_evidence():
    if not EVIDENCE_DIR.is_dir() or not any(EVIDENCE_DIR.glob("*.png")):
        pytest.skip("Real-frame replay requires ignored local evidence in debug/test-fixtures")


def evidence_path(*parts: str) -> Path:
    """Resolve an optional ignored local crop, skipping only its replay."""
    path = EVIDENCE_DIR.joinpath(*parts)
    if not path.is_file():
        pytest.skip("Real-frame replay requires ignored local evidence in debug/test-fixtures")
    return path
