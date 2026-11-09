"""Headless checks for the real configuration preview, using isolated TOML files."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import core
from app import INHERIT
from sync import sha
from workbench import Workbench, final_models


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FinalModelTests(unittest.TestCase):
    def test_main_change_validates_existing_efforts_against_new_inherited_model(self):
        parsed = core.parse(b'model="old"\nmodel_reasoning_effort="high"\n'
                            b'[agents]\ndefault_subagent_reasoning_effort="low"\n')
        self.assertEqual(final_models(parsed, {core.MODEL: "new"}),
                         ("new", "high", "new", "low"))

    def test_explicit_child_model_is_preserved_when_main_changes(self):
        parsed = core.parse(b'model="old"\n[agents]\ndefault_subagent_model="child"\n')
        self.assertEqual(final_models(parsed, {core.MODEL: "new"}),
                         ("new", None, "child", None))

    def test_removing_child_override_uses_prospective_main_model(self):
        parsed = core.parse(b'model="old"\n[agents]\ndefault_subagent_model="child"\n')
        result = final_models(parsed, {core.MODEL: "new", core.AGENT_MODEL: None})
        self.assertEqual(result, ("new", None, "new", None))

    def test_removing_main_and_child_overrides_preserves_unknown_default(self):
        parsed = core.parse(b'model="old"\n[agents]\ndefault_subagent_model="child"\n')
        self.assertEqual(final_models(parsed, {core.MODEL: None, core.AGENT_MODEL: None}),
                         (None, None, None, None))


class ConfigurationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="qingxu-preview-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "config.toml"
        self.original = (b'model="old"\nmodel_reasoning_effort="high"\n'
                         b'tool_output_token_limit=8000\n[agents]\n'
                         b'default_subagent_reasoning_effort="high"\n')
        self.path.write_bytes(self.original)
        self.app = object.__new__(Workbench)
        self.app.original_config = self.original
        self.app.original_config_path = self.path
        self.app.config_path = Value(str(self.path))
        self.app.use_file_catalog = True
        self.app.config_items = {}
        parsed = core.parse(self.original)
        for key in core.ALLOWED:
            actual = core.config_value(parsed, key)
            self.app.config_items[key] = Value(False), Value(
                INHERIT if actual is None else str(actual))
        self.app.model_reference = Mock(side_effect=lambda model: {
            "old": {"efforts": ("low", "high")},
            "new": {"efforts": ("low",)},
            "empty": {"efforts": ()},
        }.get(model, {}))
        self.app.config_apply = Mock()
        self.app.config_status = Mock()
        self.app.show_config_result = Mock()
        self.app.config_preview = None

    def choose(self, key, value):
        checked, entered = self.app.config_items[key]
        checked.set(True)
        entered.set(INHERIT if value is None else str(value))

    def test_main_model_change_rejects_unchanged_unsupported_main_effort(self):
        self.choose(core.MODEL, "new")
        with self.assertRaisesRegex(ValueError, "new.*high"):
            self.app.preview_config()
        self.assertIsNone(self.app.config_preview)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_main_model_change_checks_unchanged_inherited_child_effort(self):
        self.choose(core.MODEL, "new")
        self.choose("model_reasoning_effort", "low")
        with self.assertRaisesRegex(ValueError, "new.*high"):
            self.app.preview_config()
        self.assertIsNone(self.app.config_preview)

    def test_valid_combination_previews_without_writing_and_retains_unchecked_value(self):
        self.choose(core.MODEL, "new")
        self.choose("model_reasoning_effort", "low")
        self.choose(core.AGENT_EFFORT, "low")
        # An edited field without its checkbox must not enter the write plan.
        self.app.config_items["tool_output_token_limit"][1].set("999")
        self.app.preview_config()
        path, original, selection, changes, digest = self.app.config_preview
        candidate = core.propose(original, changes)
        self.assertEqual(path, self.path)
        self.assertEqual(digest, sha(candidate))
        self.assertEqual(core.parse(candidate)["tool_output_token_limit"], 8000)
        self.assertNotIn("tool_output_token_limit", selection)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.app.config_apply.configure.assert_called_with(state="normal")

    def test_external_edit_invalidates_previous_preview_without_overwriting_it(self):
        self.choose("tool_output_token_limit", 9000)
        self.app.preview_config()
        external = self.original + b"# changed elsewhere\n"
        self.path.write_bytes(external)
        with self.assertRaisesRegex(ValueError, "其他程序"):
            self.app.preview_config()
        self.assertIsNone(self.app.config_preview)
        self.app.config_apply.configure.assert_called_with(state="disabled")
        self.assertEqual(self.path.read_bytes(), external)

    def test_known_empty_effort_support_rejects_explicit_effort(self):
        self.choose(core.MODEL, "empty")
        with self.assertRaisesRegex(ValueError, "empty.*high"):
            self.app.preview_config()
        self.assertIsNone(self.app.config_preview)

    def test_unknown_model_support_does_not_invent_a_rejection(self):
        self.choose(core.MODEL, "provider/unknown")
        self.app.preview_config()
        self.assertEqual(self.app.config_preview[3], {core.MODEL: "provider/unknown"})

    def test_noop_does_not_enable_apply_or_create_a_backup(self):
        self.choose(core.MODEL, "old")
        self.app.preview_config()
        self.assertIsNone(self.app.config_preview)
        self.app.config_apply.configure.assert_called_with(state="disabled")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
