"""Shared fixtures for vision-engine rate limiting tests.

Key design: ratelimit.py uses ~/.local/share/vision-engine/ratelimit/ for data
files. Tests must not touch real user state. We monkeypatch ratelimit._BASE_DIR
to redirect to tmp_path, then pre-create the {rpm,rpd,cooldown} subdirs so
tests can write into them directly.
"""
import pytest


@pytest.fixture(autouse=True)
def ratelimit_tmp_dir(monkeypatch, tmp_path):
    """Redirect all ratelimit file I/O to tmp_path for test isolation."""
    import ratelimit
    monkeypatch.setattr(ratelimit, "_BASE_DIR", str(tmp_path))
    # Pre-create subdirs so test code can write files directly without race
    for prefix in ("rpm", "rpd", "cooldown"):
        (tmp_path / prefix).mkdir(exist_ok=True)
    yield tmp_path
