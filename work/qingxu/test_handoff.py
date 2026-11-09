"""Behavioral tests for portable handoffs, independent of Tk and Codex."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import handoff
from test_path_support import directory_alias, file_symlink
from handoff import (HandoffError, HandoffStore, TemplateStore, build_restore_prompt,
                     build_save_prompt, default_templates, summary_skeleton)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "交接 含空格"
        self.record = {"path": str(self.base / "项目 A"), "chat_id": "chat-a",
                       "label": "任务一", "app_project_id": "project-a",
                       "app_project_name": "示例工程", "project_id": None}
        self.store = HandoffStore(self.root)
        self.summary = (
            "## 项目概览\n目标：让普通用户查看会话并安全续接。\n"
            "## 当前任务与验收\n完成保存功能，重启后可读。\n"
            "## 已完成与证据\nhandoff.py 已编写，检查通过。\n"
            "## 未完成与下一步\n下一步：连接界面，Windows 未验证。\n"
            "## 关键决策与约束\n不自动发送聊天，知识库可选。\n"
            "## 工作区与验证\n无 Git；未提交记录不适用，GUI 未运行。\n"
            "## 按需资料索引\nRUN.md：启动方式；需要打包时读取。"
        )

    def test_no_creation_until_save_and_roundtrip_unicode(self):
        self.assertFalse(self.root.exists())
        saved = self.store.save(self.record, self.summary)
        self.assertEqual(saved["status"], "SAVED_VERIFIED")
        self.assertEqual(saved["summary"], self.summary)
        self.assertEqual(saved["metadata"]["cwd"], self.record["path"])
        self.assertEqual(saved["metadata"]["thread_id"], "chat-a")
        self.assertEqual(saved["warnings"], [])
        self.assertEqual(HandoffStore(self.root).latest(self.record)["sha256"], saved["sha256"])

    def test_long_multiline_catalog_labels_do_not_block_handoff(self):
        record = self.record | {
            "label": "长聊天\r\n第二行 " + "中" * 1000,
            "app_project_name": "项目\n第二行",
        }
        saved = self.store.save(record, self.summary)
        self.assertEqual(saved["summary"], self.summary)
        self.assertEqual(saved["metadata"]["thread_id"], record["chat_id"])
        self.assertEqual(saved["metadata"]["cwd"], record["path"])
        self.assertTrue(saved["metadata"]["title"].startswith("长聊天 第二行"))
        self.assertLessEqual(len(saved["metadata"]["title"].encode("utf-8")), 2048)
        self.assertEqual(saved["metadata"]["project_name"], "项目 第二行")
        self.assertEqual(self.store.latest(record)["sha256"], saved["sha256"])
        self.assertIn("来源会话：长聊天 第二行", build_save_prompt(record))

    def test_versions_preserve_old_content_and_latest(self):
        first = self.store.save(self.record, self.summary)
        second = self.store.save(self.record, self.summary + "\n第二次增加进展。")
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(self.store.load(first["path"])["summary"], self.summary)
        self.assertEqual(self.store.latest(self.record)["path"], second["path"])

    def test_destination_collision_never_overwrites(self):
        target = self.store.new_path(self.record)
        target.parent.mkdir(parents=True)
        target.write_text("用户已有文件", encoding="utf-8")
        with mock.patch.object(self.store, "new_path", return_value=target):
            with self.assertRaisesRegex(HandoffError, "未覆盖"):
                self.store.save(self.record, self.summary)
        self.assertEqual(target.read_text(encoding="utf-8"), "用户已有文件")
        self.assertEqual(list(target.parent.glob(".qingxu-handoff-*")), [])

    def test_blank_oversized_or_nontext_refused_without_directory_creation(self):
        for value in ("   ", "中" * 6000, None, "invalid\x00text"):
            with self.subTest(type=type(value)):
                with self.assertRaises(HandoffError):
                    self.store.save(self.record, value)
        self.assertFalse(self.root.exists())

    def test_write_error_does_not_return_saved_state_or_leave_temp(self):
        with mock.patch("handoff.os.fsync", side_effect=OSError("磁盘故障")):
            with self.assertRaises(OSError):
                self.store.save(self.record, self.summary)
        self.assertEqual(list(self.root.rglob("*.md")), [])
        self.assertEqual(list(self.root.rglob(".qingxu-handoff-*")), [])

    def test_wrong_worktree_and_shared_root_other_project_rejected(self):
        saved = self.store.save(self.record, self.summary)
        for other in ({**self.record, "path": str(self.base / "项目 A-worktree")},
                      {**self.record, "app_project_id": "different-project"}):
            with self.subTest(other=other):
                with self.assertRaisesRegex(HandoffError, "项目"):
                    self.store.load(saved["path"], other)
                with self.assertRaises(HandoffError):
                    build_restore_prompt(other, saved=saved, mode="file")

    def test_latest_never_automatically_borrows_another_threads_summary(self):
        self.store.save(self.record, self.summary)
        other = {**self.record, "chat_id": "chat-b", "label": "任务二"}
        self.assertIsNone(self.store.latest(other))
        saved_b = self.store.save(other, self.summary + "\n这是任务二。")
        self.assertNotEqual(self.store.latest(self.record)["path"], saved_b["path"])
        self.assertEqual(self.store.latest(other)["path"], saved_b["path"])

    def test_missing_or_modified_file_blocks_file_restore(self):
        saved = self.store.save(self.record, self.summary)
        p = Path(saved["path"])
        original = p.read_bytes()
        p.write_bytes(original.replace("普通用户".encode(), "别的用户".encode()))
        with self.assertRaisesRegex(HandoffError, "校验失败"):
            build_restore_prompt(self.record, saved=saved)
        p.unlink()
        with self.assertRaises(FileNotFoundError):
            build_restore_prompt(self.record, saved=saved)

    def test_file_mode_does_not_trust_fabricated_saved_status(self):
        path = self.base / "raw.md"
        path.write_text(self.summary, encoding="utf-8")
        with self.assertRaisesRegex(HandoffError, "尚未"):
            build_restore_prompt(self.record, saved={"status": "SAVED_VERIFIED", "path": str(path)})
        with self.assertRaisesRegex(HandoffError, "先保存"):
            build_restore_prompt(self.record, summary=self.summary, mode="file")

    def test_inline_mode_is_self_contained_and_honest_about_unsaved_provenance(self):
        prompt = build_restore_prompt(self.record, summary=self.summary, mode="inline")
        self.assertIn(self.summary, prompt)
        self.assertIn("未验证本地保存", prompt)
        self.assertIn(self.record["path"], prompt)
        self.assertIn("项目目的", prompt)
        self.assertNotIn("交接文件：", prompt)

    def test_file_mode_uses_reference_and_requires_current_hash(self):
        saved = self.store.save(self.record, self.summary)
        prompt = build_restore_prompt(self.record, saved=saved, mode="file",
                                      next_goal="接入 UI")
        self.assertIn(saved["path"], prompt)
        self.assertIn(saved["sha256"], prompt)
        self.assertIn("接入 UI", prompt)
        self.assertNotIn(self.summary, prompt)
        with self.assertRaisesRegex(HandoffError, "改变"):
            build_restore_prompt(self.record, saved={**saved, "sha256": "false"})

    def test_bound_exchange_import_and_paste_keep_project_identity(self):
        prompt = build_save_prompt(self.record)
        marker = next(line for line in prompt.splitlines() if line.startswith(handoff.SOURCE_HEADER))
        raw = marker + "\n\n" + self.summary
        p = self.base / "旧聊返回.md"
        p.write_text(raw, encoding="utf-8")
        saved = self.store.import_file(self.record, p)
        self.assertEqual(saved["summary"], self.summary)
        self.assertEqual(saved["imported_from"], str(p))
        self.assertEqual(p.read_text(encoding="utf-8"), raw)
        self.assertEqual(self.store.save(self.record, raw)["summary"], self.summary)
        wrong = {**self.record, "path": str(self.base / "其他")}
        with self.assertRaises(HandoffError):
            self.store.import_file(wrong, p)

    def test_plain_markdown_import_needs_explicit_project_binding(self):
        p = self.base / "普通摘要.md"
        p.write_text(self.summary, encoding="utf-8")
        with self.assertRaisesRegex(HandoffError, "明确确认"):
            self.store.import_file(self.record, p)
        result = self.store.import_file(self.record, p, allow_unbound=True)
        self.assertTrue(any("未自动验证" in item for item in result["warnings"]))
        self.assertNotEqual(result["path"], str(p))

    def test_oversized_input_and_invalid_utf8_refused(self):
        p = self.base / "bad.md"
        for raw in (b"x" * (handoff.MAX_FILE_BYTES + 1), b"\xff\xfe"):
            p.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.store.import_file(self.record, p, allow_unbound=True)

    def test_windows_case_normalization_and_no_relative_project(self):
        record = {**self.record, "path": r"C:\Projects\My App"}
        saved = self.store.save(record, self.summary)
        self.store.load(saved["path"], {**record, "path": "c:/projects/my app"})
        with self.assertRaises(HandoffError):
            build_save_prompt({**self.record, "path": "relative/project"})
        with self.assertRaises(HandoffError):
            build_save_prompt({**self.record, "path": r"C:\projects\..\other"})

    def test_bad_metadata_and_hash_cannot_be_accepted(self):
        saved = self.store.save(self.record, self.summary)
        p = Path(saved["path"])
        raw = p.read_text(encoding="utf-8")
        for changed in (raw.replace('"schema_version":1', '"schema_version":9'),
                        raw.replace('"project_key":"', '"project_key":"bad'),
                        raw.replace(handoff.HEADER, handoff.HEADER + "{")):
            p.write_text(changed, encoding="utf-8")
            with self.assertRaises(HandoffError):
                self.store.load(p, self.record)
        p.write_text(raw, encoding="utf-8")
        self.store.load(p, self.record)

    def test_crlf_and_bom_are_normalized_without_losing_digest(self):
        saved = self.store.save(self.record, self.summary.replace("\n", "\r\n"))
        self.assertEqual(saved["summary"], self.summary)
        p = self.base / "windows.md"
        p.write_bytes(b"\xef\xbb\xbf" + Path(saved["path"]).read_bytes().replace(b"\n", b"\r\n"))
        result = self.store.import_file(self.record, p)
        self.assertEqual(result["summary"], self.summary)

    def test_directory_alias_destination_rejected(self):
        target = self.base / "real"
        target.mkdir()
        shortcut = self.base / "alias"
        directory_alias(shortcut, target)
        with self.assertRaises(ValueError):
            HandoffStore(shortcut)

    def test_symlink_source_rejected(self):
        p = self.base / "real.md"
        p.write_text(self.summary, encoding="utf-8")
        link = self.base / "alias.md"
        file_symlink(self, link, p)
        with self.assertRaises(ValueError):
            self.store.import_file(self.record, link, allow_unbound=True)

    def test_all_knowledge_policies_are_distinct_and_do_not_write_library(self):
        directory = self.base / "knowledge"
        none = build_save_prompt(self.record, knowledge_policy="none",
                                 knowledge_dir=str(directory))
        ask = build_save_prompt(self.record, knowledge_policy="ask",
                                knowledge_dir=str(directory))
        authorized = build_save_prompt(self.record, knowledge_policy="authorized",
                                       knowledge_dir=str(directory))
        self.assertIn("不更新长期", none)
        self.assertIn("询问我是否更新", ask)
        self.assertIn("已有明确授权", authorized)
        self.assertFalse(directory.exists())
        with self.assertRaises(HandoffError):
            build_save_prompt(self.record, knowledge_policy="always")

    def test_custom_guidance_cannot_remove_identity_and_exchange_format(self):
        prompt = build_save_prompt(self.record, template="使用简洁分点写法。")
        self.assertIn("使用简洁分点写法", prompt)
        self.assertIn(self.record["path"], prompt)
        self.assertIn(handoff.SOURCE_HEADER, prompt)
        self.assertIn("## 项目概览", prompt)
        self.assertIn("## 未完成与下一步", prompt)
        prompt = build_restore_prompt(self.record, summary=self.summary, mode="inline",
                                      template="继续之前先检查配置。")
        self.assertIn("继续之前先检查配置", prompt)
        self.assertIn(self.record["path"], prompt)
        self.assertIn(self.summary, prompt)

    def test_template_versions_survive_restart_and_restore_default(self):
        store = TemplateStore(self.base / "templates")
        self.assertEqual(store.load(), default_templates())
        changed = default_templates() | {"save": "简短总结，并核对证据。"}
        first = store.save(changed)
        self.assertEqual(TemplateStore(store.root).load(), changed)
        second = store.save(default_templates())
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(store.load(), default_templates())
        self.assertTrue(Path(first["path"]).exists())

    def test_invalid_template_or_truncated_latest_is_not_silently_accepted(self):
        store = TemplateStore(self.base / "templates")
        for data in ({}, {"save": "x", "restore": ""}, {"save": "x" * 9000, "restore": "x"}):
            with self.assertRaises(HandoffError):
                store.save(data)
        receipt = store.save(default_templates())
        Path(receipt["path"]).write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            store.load()

    def test_invalid_history_is_visible_without_false_restore(self):
        saved = self.store.save(self.record, self.summary)
        Path(saved["path"]).write_text("broken file", encoding="utf-8")
        result = self.store.list(self.record)
        self.assertEqual(result[0]["status"], "INVALID")
        self.assertIsNone(self.store.latest(self.record))

    def test_reimport_preserves_original_chat_and_original_snapshot_age(self):
        with mock.patch("handoff._now", return_value="2025-01-01T00:00:00Z"):
            original = self.store.save(self.record, self.summary)
        other = {**self.record, "chat_id": "chat-b", "label": "另一条聊天"}
        imported = self.store.import_file(other, original["path"])
        restarted = HandoffStore(self.root).latest(other)
        self.assertEqual(restarted["metadata"]["source_identity"]["thread_id"], "chat-a")
        self.assertTrue(any("来源会话不同" in warning for warning in restarted["warnings"]))
        self.assertTrue(any("超过一天" in warning for warning in restarted["warnings"]))
        prompt = build_restore_prompt(other, saved=imported)
        self.assertIn("任务一（chat-a）", prompt)
        self.assertNotIn("来源聊天：另一条聊天", prompt)

    def test_malformed_timestamp_and_project_key_fail_without_expected_record(self):
        saved = self.store.save(self.record, self.summary)
        p = Path(saved["path"])
        original = p.read_text(encoding="utf-8")
        for changed in (original.replace(saved["metadata"]["generated_at"], "not-a-date"),
                        original.replace('"project_key":"', '"project_key":"bad')):
            p.write_text(changed, encoding="utf-8")
            with self.assertRaises(HandoffError):
                self.store.load(p)

    def test_prompt_destination_and_private_state_are_only_advisory_text(self):
        target = self.store.new_path(self.record)
        prompt = build_save_prompt(self.record, target, knowledge_policy="ask")
        self.assertIn(str(target), prompt)
        self.assertIn("若路径已存在", prompt)
        self.assertIn("若不能写文件", prompt)
        self.assertFalse(self.root.exists())
        self.assertIn("项目概览", summary_skeleton())


if __name__ == "__main__":
    unittest.main()

