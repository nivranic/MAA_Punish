"""The Android agent must discover Oblivion without desktop-only OpenCV."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_combat_entry_discovers_oblivion_without_cv2(tmp_path):
    script = textwrap.dedent("""
        import importlib.abc
        import sys

        class NoOpenCv(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'cv2' or fullname.startswith('cv2.'):
                    raise ModuleNotFoundError("No module named 'cv2'", name='cv2')

        sys.meta_path.insert(0, NoOpenCv())
        from action.combat.entry.runner import CombatRunner
        from action.combat.core.role_factory import ROLE_CLASS_MAP, ROLE_IMPORT_ERRORS
        assert not ROLE_IMPORT_ERRORS, ROLE_IMPORT_ERRORS
        assert ROLE_CLASS_MAP['Oblivion'].__module__ == 'action.combat.roles.oblivion'
        assert 'cv2' not in sys.modules
    """)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "-c", script], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
