"""Run the existing suites relevant to the handoff in an isolated Codex home."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

BUNDLE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BUNDLE / "source/work/qingxu"), str(BUNDLE / "tests/work/qingxu")]
SUITES = (
    "test_context_usage",
    "test_dashboard",
    "test_relay",
    "test_app.ConfigTests",
    "test_app.AppTests.test_three_workspaces",
    "test_app.AppTests.test_usage_tree_uses_all_catalog_chats_and_restores_selection",
    "test_app.AppTests.test_taskbar_flashes_once_per_alert_episode",
    "test_app.AppTests.test_reminder_threshold_persists_and_pause_disables_flash",
    "test_app.AppTests.test_content_sources_shares_use_bytes_and_clear_on_switch",
    "test_app.AppTests.test_read_config_populates_current_values_without_enabling_changes",
    "test_app.AppTests.test_recommended_preview_and_apply_to_copy",
    "test_app.AppTests.test_official_mode_apply_to_copy_and_restore",
    "test_app.AppTests.test_write_child_defaults_to_copy_and_show_readback",
    "test_app.AppTests.test_decline_close_keeps_window",
    "test_app.AppTests.test_decline_switch_keeps_exact_chat_with_shared_project",
)

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="qingxu-handoff-tests-") as directory:
        os.environ["QINGXU_CODEX_HOME"] = directory
        suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        raise SystemExit(not result.wasSuccessful())
