"""Run baseline and new tests together on isolated data; requires Tk display."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest

BUNDLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BUNDLE / "work/qingxu"))
SUITES = (
    "test_core", "test_context_usage", "test_dashboard", "test_relay", "test_sync",
    "test_app", "test_handoff", "test_monitor", "test_preferences", "test_ui_preferences",
    "test_ui_handoff", "test_ui_monitor", "test_workbench_gui", "test_workbench_logic",
    "test_ui_brand_motion", "test_ui_polish", "test_path_support",
    "test_billing", "test_billing_index", "test_billing_zstd", "test_billing_periods", "test_ui_billing", "test_usage_stats_ui",
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, help="write the test receipt to this file")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="qingxu-upgrade-tests-") as folder:
        os.environ["QINGXU_CODEX_HOME"] = folder
        suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    evidence = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
                "python": sys.version, "tests": result.testsRun, "failures": len(result.failures),
                "errors": len(result.errors), "skipped": len(result.skipped), "success": result.wasSuccessful(),
                "failure_details": [(str(test), message) for test, message in result.failures + result.errors],
                "synthetic_data_only": True,
                "not_verified": ["Windows taskbar and packaging", "real Codex new-chat continuation",
                                 "Codex runtime adopts edited settings", "real knowledge-base writes", "collector lifecycle",
                                 "real billing reconciliation", "unsupported or legacy request log coverage"]}
    if args.result is not None:
        args.result.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(not result.wasSuccessful())
