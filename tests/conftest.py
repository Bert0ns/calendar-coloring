import sys
from pathlib import Path

import pytest

# Add src to sys.path for test discovery
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Run every test in a temp dir so strategies never touch the repo's
    real exam_states.json / course_colors.json state files."""
    monkeypatch.chdir(tmp_path)
