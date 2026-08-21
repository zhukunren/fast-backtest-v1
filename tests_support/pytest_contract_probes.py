from __future__ import annotations


TEMP_PROBE = """\
import json
import os
from pathlib import Path


def test_report_pytest_temp_paths(request, tmp_path):
    payload = {
        "basetemp": str(Path(request.config.option.basetemp).resolve()),
        "tmp_path": str(Path(tmp_path).resolve()),
    }
    Path(os.environ["WP02_TEMP_PROBE_OUT"]).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
"""

CONCURRENT_TEMP_PROBE = """\
import json
import os
from pathlib import Path
import time


def test_wait_for_peer_sessions(request, tmp_path):
    payload = {
        "basetemp": str(Path(request.config.option.basetemp).resolve()),
        "tmp_path": str(Path(tmp_path).resolve()),
    }
    Path(os.environ["WP02_TEMP_READY_OUT"]).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    release = Path(os.environ["WP02_TEMP_RELEASE"])
    deadline = time.monotonic() + 30
    while not release.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for release file: {release}")
        time.sleep(0.02)
    Path(os.environ["WP02_TEMP_PROBE_OUT"]).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
"""

FAILED_CLOSE_PROBE = """\
import json
import os
from pathlib import Path

from tests_support.pytest_temp import ManagedPytestRun


def test_force_managed_run_close_failure(request, tmp_path):
    payload = {
        "basetemp": str(Path(request.config.option.basetemp).resolve()),
        "tmp_path": str(Path(tmp_path).resolve()),
    }
    Path(os.environ["WP02_TEMP_PROBE_OUT"]).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    original_close = ManagedPytestRun.close

    def close_with_denied_removal(self, *, remove_tree=None, warning_sink=None):
        def deny_removal(path):
            raise PermissionError(f"forced teardown denial: {path}")

        return original_close(
            self,
            remove_tree=deny_removal,
            warning_sink=warning_sink,
        )

    ManagedPytestRun.close = close_with_denied_removal
"""
