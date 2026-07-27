"""Unit tests for ratelimit.py: general quota framework (requests/tokens × any window)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import ratelimit


# Helper to build quota config
def requests_quota(window_seconds: int, limit: int) -> dict:
    return {"metric": "requests", "window_seconds": window_seconds, "limit": limit}


def tokens_quota(window_seconds: int, limit: int) -> dict:
    return {"metric": "tokens", "window_seconds": window_seconds, "limit": limit}


# ═══════════════════════════════════════════════════════════
# Requests metric
# ═══════════════════════════════════════════════════════════

class TestRequestsMetric:
    def test_requests_under_limit_allows(self, tmp_path):
        """0 prior calls, limit=25 → returns False (allowed). check_quota is dry-run, doesn't write."""
        now = 1000.0
        result = ratelimit.check_quota("m1", requests_quota(60, 25), _now=now)
        assert result is False
        # check_quota is dry-run — file should NOT be created
        path = Path(f"{tmp_path}/requests-60s-m1.json")
        assert not path.is_file()

    def test_record_usage_writes_request(self, tmp_path):
        """record_usage(actual=1) appends timestamp to file."""
        now = 1000.0
        q = requests_quota(60, 25)
        ratelimit.record_usage("m1", q, 1, _now=now)
        ratelimit.record_usage("m1", q, 1, _now=now + 1)
        data = json.loads(Path(f"{tmp_path}/requests-60s-m1.json").read_text())
        assert data["timestamps"] == [now, now + 1]

    def test_requests_at_limit_blocks(self, tmp_path):
        """After 25 record_usage, 26th check_quota returns True (exceeded)."""
        now = 1000.0
        q = requests_quota(60, 25)
        # Saturate to exactly 25
        for i in range(25):
            ratelimit.record_usage("m1", q, 1, _now=now - (25 - i))
        # 26th check should be blocked
        result = ratelimit.check_quota("m1", q, _now=now)
        assert result is True
        # File should still have 25 entries (check didn't add a 26th)
        data = json.loads(Path(f"{tmp_path}/requests-60s-m1.json").read_text())
        assert len(data["timestamps"]) == 25

    def test_requests_window_prunes_old(self, tmp_path):
        """Timestamps outside window are pruned before counting."""
        now = 1000.0
        q = requests_quota(60, 5)
        # Write 5 old timestamps (100s ago, outside 60s window)
        path = Path(f"{tmp_path}/requests-60s-m1.json")
        path.write_text(json.dumps({"timestamps": [now - 100 - i for i in range(5)]}))
        # Check at now → old ones pruned → 0 in window → allowed
        result = ratelimit.check_quota("m1", q, _now=now)
        assert result is False
        # File on disk still has the old data (check_quota is dry-run)
        # Pruning happens at read time; not written back unless record_usage is called

    def test_requests_different_windows_isolated(self, tmp_path):
        """requests-60 and requests-86400 are independent files."""
        now = 1000.0
        q60 = requests_quota(60, 10)
        q_day = requests_quota(86400, 5)
        # Saturate 60s window
        for i in range(10):
            ratelimit.record_usage("m1", q60, 1, _now=now - (10 - i))
        # 86400s window is empty → allowed
        result = ratelimit.check_quota("m1", q_day, _now=now)
        assert result is False


# ═══════════════════════════════════════════════════════════
# Tokens metric
# ═══════════════════════════════════════════════════════════

class TestTokensMetric:
    def test_tokens_under_limit_allows(self, tmp_path):
        """Cumulative tokens < limit → allowed. check_quota is dry-run."""
        now = 1000.0
        result = ratelimit.check_quota("m1", tokens_quota(60, 2_000_000), _now=now)
        assert result is False
        # check_quota is dry-run — file should NOT be created
        path = Path(f"{tmp_path}/tokens-60s-m1.json")
        assert not path.is_file()

    def test_tokens_projected_does_not_write(self, tmp_path):
        """check_quota with projected value is a dry-run — doesn't write to file."""
        now = 1000.0
        q = tokens_quota(60, 1000)
        # Simulate prior usage of 900 tokens
        ratelimit.record_usage("m1", q, 900, _now=now - 1)
        # Check with projected=200 → 900+200=1100 > 1000 → blocked, but not written
        result = ratelimit.check_quota("m1", q, projected=200, _now=now)
        assert result is True
        # File should still have only the recorded 900
        data = json.loads(Path(f"{tmp_path}/tokens-60s-m1.json").read_text())
        assert data == {"entries": [[now - 1, 900]]}

    def test_tokens_record_usage_writes(self, tmp_path):
        """record_usage appends [now, actual] to file."""
        now = 1000.0
        q = tokens_quota(60, 1000)
        ratelimit.record_usage("m1", q, 500, _now=now)
        ratelimit.record_usage("m1", q, 300, _now=now + 1)
        data = json.loads(Path(f"{tmp_path}/tokens-60s-m1.json").read_text())
        assert data == {"entries": [[now, 500], [now + 1, 300]]}

    def test_tokens_sum_in_window(self, tmp_path):
        """check_quota sums all entries within window."""
        now = 1000.0
        q = tokens_quota(60, 1000)
        # Record 400, 300, 200 = 900 in window
        ratelimit.record_usage("m1", q, 400, _now=now - 10)
        ratelimit.record_usage("m1", q, 300, _now=now - 5)
        ratelimit.record_usage("m1", q, 200, _now=now - 1)
        # Check without projected → 900 < 1000, allowed
        result = ratelimit.check_quota("m1", q, _now=now)
        assert result is False
        # Check with projected=200 → 1100 > 1000, blocked
        result = ratelimit.check_quota("m1", q, projected=200, _now=now)
        assert result is True

    def test_tokens_window_prunes_old(self, tmp_path):
        """Old entries (outside window) are pruned before summing."""
        now = 1000.0
        q = tokens_quota(60, 1000)
        # Record 800 at t=now-100 (outside 60s window)
        ratelimit.record_usage("m1", q, 800, _now=now - 100)
        # Record 100 at t=now-1 (inside window)
        ratelimit.record_usage("m1", q, 100, _now=now - 1)
        # Check: only 100 in window → allowed
        result = ratelimit.check_quota("m1", q, _now=now)
        assert result is False

    def test_tokens_different_windows_isolated(self, tmp_path):
        """tokens-60 and tokens-3600 are independent files."""
        now = 1000.0
        q60 = tokens_quota(60, 1000)
        q3600 = tokens_quota(3600, 5000)
        ratelimit.record_usage("m1", q60, 800, _now=now)
        # 3600s window: only 800 used → well under 5000
        result = ratelimit.check_quota("m1", q3600, _now=now)
        assert result is False


# ═══════════════════════════════════════════════════════════
# Cross-metric isolation
# ═══════════════════════════════════════════════════════════

class TestMetricIsolation:
    def test_requests_and_tokens_use_separate_files(self, tmp_path):
        """Same model, requests-60 and tokens-60 are different files."""
        now = 1000.0
        ratelimit.record_usage("m1", requests_quota(60, 10), 1, _now=now)
        ratelimit.record_usage("m1", tokens_quota(60, 1000), 500, _now=now)
        req_path = Path(f"{tmp_path}/requests-60s-m1.json")
        tok_path = Path(f"{tmp_path}/tokens-60s-m1.json")
        assert req_path.is_file()
        assert tok_path.is_file()
        req_data = json.loads(req_path.read_text())
        tok_data = json.loads(tok_path.read_text())
        assert req_data["timestamps"] == [now]
        assert tok_data["entries"] == [[now, 500]]


# ═══════════════════════════════════════════════════════════
# File corruption / missing
# ═══════════════════════════════════════════════════════════

class TestRobustness:
    def test_corrupt_file_resets_for_requests(self, tmp_path):
        """Corrupt JSON → fail-open, treated as empty. record_usage overwrites with valid data."""
        path = Path(f"{tmp_path}/requests-60s-m1.json")
        path.write_text("NOT JSON!!!")
        # check_quota is dry-run, won't write
        result = ratelimit.check_quota("m1", requests_quota(60, 10), _now=1000.0)
        assert result is False
        # File still corrupt on disk
        assert path.read_text() == "NOT JSON!!!"
        # record_usage overwrites with valid state
        ratelimit.record_usage("m1", requests_quota(60, 10), 1, _now=1000.0)
        data = json.loads(path.read_text())
        assert "timestamps" in data

    def test_corrupt_file_resets_for_tokens(self, tmp_path):
        path = Path(f"{tmp_path}/tokens-60s-m1.json")
        path.write_text("GARBAGE")
        result = ratelimit.check_quota("m1", tokens_quota(60, 1000), _now=1000.0)
        assert result is False

    def test_missing_file_is_empty(self, tmp_path):
        """No file → treated as 0 in window."""
        result = ratelimit.check_quota("m1", requests_quota(60, 10), _now=1000.0)
        assert result is False

    def test_malformed_data_resets(self, tmp_path):
        """If data is dict but missing expected keys, fail-open at read time."""
        path = Path(f"{tmp_path}/requests-60s-m1.json")
        path.write_text(json.dumps({"wrong_key": []}))
        result = ratelimit.check_quota("m1", requests_quota(60, 10), _now=1000.0)
        assert result is False
        # record_usage overwrites with valid data
        ratelimit.record_usage("m1", requests_quota(60, 10), 1, _now=1000.0)
        data = json.loads(path.read_text())
        assert "timestamps" in data

    def test_tokens_entries_with_mixed_types_cleaned(self, tmp_path):
        """Entries containing [ts, str] or other bad types are filtered out."""
        now = 1000.0
        path = Path(f"{tmp_path}/tokens-60s-m1.json")
        # Mixed valid and invalid entries
        path.write_text(json.dumps({"entries": [
            [now - 10, 100],       # valid
            [now - 5, "abc"],      # bad: string instead of int
            [now - 3],              # bad: only 1 element
            "not_a_list",           # bad: not a list
            [now - 1, 50],          # valid
        ]}))
        q = tokens_quota(60, 1000)
        # Only 2 valid entries: 100 + 50 = 150 in window
        ratelimit.record_usage("m1", q, 25, _now=now)
        # Should have 3 entries now: the 2 valid survived + 1 new
        data = json.loads(path.read_text())
        assert len(data["entries"]) == 3

    def test_tokens_all_entries_invalid_returns_empty(self, tmp_path):
        """If ALL entries are invalid, state is reset to empty (fail-open)."""
        path = Path(f"{tmp_path}/tokens-60s-m1.json")
        path.write_text(json.dumps({"entries": ["bad", "also_bad", 123]}))
        # check_quota treats it as empty
        result = ratelimit.check_quota("m1", tokens_quota(60, 1000), _now=1000.0)
        assert result is False
        # record_usage starts fresh
        ratelimit.record_usage("m1", tokens_quota(60, 1000), 100, _now=1000.0)
        data = json.loads(path.read_text())
        assert data == {"entries": [[1000.0, 100]]}

    def test_requests_timestamps_with_non_numbers_reset(self, tmp_path):
        """Timestamps array with strings or None resets (fail-open)."""
        path = Path(f"{tmp_path}/requests-60s-m1.json")
        path.write_text(json.dumps({"timestamps": [1000.0, "bad", None, 1001.0]}))
        result = ratelimit.check_quota("m1", requests_quota(60, 10), _now=1002.0)
        assert result is False  # treated as empty


# ═══════════════════════════════════════════════════════════
# Cooldown (独立维度，保留旧 API)
# ═══════════════════════════════════════════════════════════

class TestCooldown:
    def test_set_cooldown_writes_timestamp(self, tmp_path):
        now = 1000.0
        ratelimit.set_cooldown("m1", 60, _now=now)
        path = Path(f"{tmp_path}/cooldown-m1.json")
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["cooldown_until"] == now + 60

    def test_is_cooled_down_true_within_period(self, tmp_path):
        now = 1000.0
        ratelimit.set_cooldown("m1", 60, _now=now)
        assert ratelimit.is_cooled_down("m1", _now=now + 30) is True

    def test_is_cooled_down_false_after_expiry(self, tmp_path):
        now = 1000.0
        ratelimit.set_cooldown("m1", 60, _now=now)
        assert ratelimit.is_cooled_down("m1", _now=now + 61) is False

    def test_cooldown_independent_of_quotas(self, tmp_path):
        """Cooldown file is separate from quota files."""
        now = 1000.0
        ratelimit.set_cooldown("m1", 60, _now=now)
        ratelimit.record_usage("m1", requests_quota(60, 10), 1, _now=now)
        # 2 files: cooldown + requests, each in own namespace
        assert Path(f"{tmp_path}/cooldown-m1.json").is_file()
        assert Path(f"{tmp_path}/requests-60s-m1.json").is_file()


# ═══════════════════════════════════════════════════════════
# Unknown metric
# ═══════════════════════════════════════════════════════════

class TestUnknownMetric:
    def test_unknown_metric_raises(self):
        """Unknown metric value should raise (caller bug, not silent fail)."""
        with pytest.raises(ValueError, match="metric"):
            ratelimit.check_quota("m1", {"metric": "cost", "window_seconds": 60, "limit": 100}, _now=1000.0)
