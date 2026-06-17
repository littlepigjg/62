import os
import sys
import re
import time
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import ServerConfig
from app.core.script_executor import (
    TempPathSelector,
    CleanupManager,
    ScriptExecutor,
    ExecutionPlan,
    CLEANUP_GLOB_PATTERNS,
    DEFAULT_CANDIDATE_DIRS,
)


TEST_SERVER = ServerConfig(
    id="test-srv-01",
    name="测试服务器",
    host="10.0.0.99",
    port=22,
    username="deploy",
    password="",
    private_key="",
    tags=["test"],
)

TEST_SERVER_ROOT = ServerConfig(
    id="test-srv-root",
    name="Root服务器",
    host="10.0.0.100",
    port=22,
    username="root",
    password="",
    private_key="",
    tags=[],
)


class TestTempPathSelector(unittest.TestCase):
    def setUp(self):
        self.selector = TempPathSelector()

    def test_expand_home_deploy_user(self):
        p = self.selector._expand(TEST_SERVER, "$HOME/.cache/ssh_exec")
        self.assertEqual(p, "/home/deploy/.cache/ssh_exec")

    def test_expand_home_root_user(self):
        p = self.selector._expand(TEST_SERVER_ROOT, "$HOME/.cache/ssh_exec")
        self.assertEqual(p, "/root/.cache/ssh_exec")

    def test_expand_pwd(self):
        p = self.selector._expand(TEST_SERVER, "$PWD/.tmp")
        self.assertEqual(p, "/home/deploy/.tmp")

    def test_invalidate_server(self):
        self.selector._dir_cache["test-srv-01"] = {"ts": time.time(), "dir": "/tmp"}
        self.assertIn("test-srv-01", self.selector._dir_cache)
        self.selector.invalidate_server("test-srv-01")
        self.assertNotIn("test-srv-01", self.selector._dir_cache)

    def test_cache_short_ttl(self):
        self.assertEqual(self.selector._cache_ttl, 300, "TTL should be shortened to 5 minutes")

    @patch("app.core.script_executor.ssh_pool")
    def test_select_success_first_candidate(self, mock_ssh_pool):
        mock_ssh_pool.execute_command.return_value = (0, "STAGE_OK dir=/home/deploy/.cache/ssh_exec avail=512000\n", "")
        chosen, notes = self.selector.select(TEST_SERVER, 1024)
        self.assertEqual(chosen, "/home/deploy/.cache/ssh_exec")
        self.assertTrue(any("dir chosen" in n for n in notes))

    @patch("app.core.script_executor.ssh_pool")
    def test_select_cache_hit(self, mock_ssh_pool):
        self.selector._dir_cache["test-srv-01"] = {
            "ts": time.time(),
            "dir": "/cached/dir",
            "notes": ["cached from before"],
        }
        chosen, notes = self.selector.select(TEST_SERVER, 1024)
        self.assertEqual(chosen, "/cached/dir")
        self.assertTrue(any("cached dir" in n for n in notes))
        mock_ssh_pool.execute_command.assert_not_called()

    @patch("app.core.script_executor.ssh_pool")
    def test_select_cache_expired_forces_recheck(self, mock_ssh_pool):
        mock_ssh_pool.execute_command.return_value = (0, "STAGE_OK dir=/tmp avail=100000\n", "")
        self.selector._dir_cache["test-srv-01"] = {
            "ts": time.time() - 99999,
            "dir": "/old/cache",
            "notes": [],
        }
        chosen, notes = self.selector.select(TEST_SERVER, 1024)
        self.assertNotEqual(chosen, "/old/cache")
        mock_ssh_pool.execute_command.assert_called()

    @patch("app.core.script_executor.ssh_pool")
    def test_select_force_refresh_ignores_cache(self, mock_ssh_pool):
        mock_ssh_pool.execute_command.return_value = (0, "STAGE_OK dir=/tmp avail=100\n", "")
        self.selector._dir_cache["test-srv-01"] = {
            "ts": time.time(),
            "dir": "/cached/dir",
            "notes": [],
        }
        chosen, notes = self.selector.select(TEST_SERVER, 1024, force_refresh=True)
        self.assertNotEqual(chosen, "/cached/dir")
        mock_ssh_pool.execute_command.assert_called()

    @patch("app.core.script_executor.ssh_pool")
    def test_select_all_dirs_fail_returns_none(self, mock_ssh_pool):
        mock_ssh_pool.execute_command.return_value = (3, "STAGE3_FAIL not writable\n", "")
        chosen, notes = self.selector.select(TEST_SERVER, 1024, force_refresh=True)
        self.assertIsNone(chosen)
        self.assertEqual(len(notes), len(DEFAULT_CANDIDATE_DIRS))
        for n in notes:
            self.assertIn("skip", n)


class TestCleanupManager(unittest.TestCase):
    def setUp(self):
        self.cm = CleanupManager()

    def tearDown(self):
        self.cm.stop()

    def test_schedule_cleanup_no_duplicates(self):
        self.cm.schedule_cleanup("srv1", "/tmp/x.sh")
        self.cm.schedule_cleanup("srv1", "/tmp/x.sh")
        self.cm.schedule_cleanup("srv1", "/tmp/x.sh")
        self.assertEqual(len(self.cm._pending), 1)

    def test_schedule_cleanup_empty_path_ignored(self):
        self.cm.schedule_cleanup("srv1", None)
        self.cm.schedule_cleanup("srv1", "")
        self.assertEqual(len(self.cm._pending), 0)

    def test_max_attempts_is_twelve(self):
        rec = list(self.cm._pending) if False else None
        import dataclasses
        cr = CleanupManager.__dict__.get("__init__")
        from app.core.script_executor import CleanupRecord
        dummy = CleanupRecord(server_id="x", remote_path="y", created_at=time.time())
        self.assertEqual(dummy.max_attempts, 12, "should retry up to 12 times")

    @patch("app.core.script_executor.ssh_pool")
    def test_try_remove_multiple_methods(self, mock_ssh_pool):
        mock_ssh_pool.execute_command.return_value = (0, "", "")
        ok = self.cm._try_remove(TEST_SERVER, "/tmp/x.sh_task-123")
        self.assertTrue(ok)
        cmd_sent = mock_ssh_pool.execute_command.call_args[0][1]
        self.assertIn("rm -f", cmd_sent)
        self.assertIn("unlink", cmd_sent)
        self.assertIn("chattr -i", cmd_sent)
        self.assertIn("find", cmd_sent)

    @patch("app.core.script_executor.ssh_pool")
    def test_try_remove_returns_false_when_file_still_exists(self, mock_ssh_pool):
        mock_ssh_pool.execute_command.return_value = (1, "", "")
        ok = self.cm._try_remove(TEST_SERVER, "/tmp/leftover.sh")
        self.assertFalse(ok)


class TestShellSafety(unittest.TestCase):
    def setUp(self):
        self.exe = ScriptExecutor.__new__(ScriptExecutor)
        self.exe._pipe_size_threshold = 64 * 1024
        self.exe._heredoc_size_threshold = 512 * 1024
        self.exe._chunk_size = 48 * 1024

    def test_shell_quote_empty(self):
        self.assertEqual(self.exe._shell_quote(""), "''")

    def test_shell_quote_no_special(self):
        self.assertEqual(self.exe._shell_quote("hello"), "'hello'")

    def test_shell_quote_with_single_quotes(self):
        quoted = self.exe._shell_quote("it's a test")
        self.assertIn("'\\''", quoted)
        self.assertTrue(quoted.startswith("'"))
        self.assertTrue(quoted.endswith("'"))

    def test_shell_quote_injection_attempt(self):
        evil = "$(rm -rf /); `cat /etc/passwd`"
        quoted = self.exe._shell_quote(evil)
        self.assertTrue(quoted.startswith("'") and quoted.endswith("'"))
        self.assertEqual(len(re.findall(r"'", quoted)), 2 + 2 * evil.count("'"),
                         "single quote wrapping with proper escapes")
        reconstructed = quoted[1:-1].replace("'\\''", "'")
        self.assertEqual(reconstructed, evil)

    def test_marker_generation_unique_and_safe(self):
        content = "some script\nwith lines\n__SH_END_123__FAKE__\nend"
        markers = set()
        for _ in range(100):
            m = self.exe._gen_safe_marker(content)
            self.assertNotIn(m, content)
            markers.add(m)
        self.assertGreater(len(markers), 90, "markers should be unique")

    def test_marker_generation_rejects_existing(self):
        fake_marker = "__SH_END_1781675001710_FIXED__"
        content = f"line1\n{fake_marker}\nline2"
        ts = 1781675001.710
        with patch("app.core.script_executor.secrets.token_hex") as mock_rand, \
             patch("app.core.script_executor.time.time", return_value=ts):
            mock_rand.side_effect = ["FIXED", "REALUNIQ1", "REALUNIQ2"]
            result = self.exe._gen_safe_marker(content)
            self.assertIn("REALUNIQ1", result)
            self.assertNotIn("FIXED", result)

    def test_heredoc_rejects_null_bytes(self):
        bad = "echo hello\x00world"
        cmd, err = self.exe._build_heredoc_command("bash", bad, [], None)
        self.assertEqual(err, "contains null byte")
        self.assertEqual(cmd, "")

    def test_heredoc_sizes_limits(self):
        self.assertEqual(self.exe._heredoc_size_threshold, 512 * 1024)

    def test_extract_exit_code_from_stdout(self):
        out = "line1\nline2\n__RCMARK__42__\n"
        code, clean_out, clean_err = self.exe._extract_exit_code(out, "")
        self.assertEqual(code, 42)
        self.assertNotIn("__RCMARK__", clean_out)

    def test_extract_exit_code_negative(self):
        out = "__RCMARK__-1__"
        code, _, _ = self.exe._extract_exit_code(out, "")
        self.assertEqual(code, -1)

    def test_extract_exit_code_missing_returns_zero(self):
        code, _, _ = self.exe._extract_exit_code("just output", "")
        self.assertEqual(code, 0)

    def test_extract_exit_code_in_stderr(self):
        code, _, _ = self.exe._extract_exit_code("", "error\n__RCMARK__2__")
        self.assertEqual(code, 2)


class TestFallbackChain(unittest.TestCase):
    def setUp(self):
        self.exe = ScriptExecutor.__new__(ScriptExecutor)
        self.exe.path_selector = TempPathSelector()
        self.exe.cleanup = MagicMock(spec=CleanupManager)
        self.exe.cleanup.bulk_cleanup_leftovers = MagicMock(return_value=3)
        self.exe._pipe_size_threshold = 100
        self.exe._heredoc_size_threshold = 500
        self.exe._chunk_size = 48 * 1024

    def test_small_script_uses_pipe_mode(self):
        small = "#!/bin/bash\necho hi"
        plan = self.exe.plan_execution(TEST_SERVER, small, "hi.sh", "bash", [], "task-001")
        self.assertEqual(plan.mode, "pipe")
        self.assertTrue(any("pipe mode" in n for n in plan.notes))
        self.assertIsNone(plan.remote_path)

    def test_medium_script_uses_heredoc_mode(self):
        medium = "echo " + "x" * 200 + "\ndone\n"
        with patch.object(self.exe, "_encode_script", return_value="A" * 130000):
            plan = self.exe.plan_execution(TEST_SERVER, medium, "m.sh", "bash", [], "task-002")
        self.assertEqual(plan.mode, "heredoc")

    @patch("app.core.script_executor.ssh_pool")
    def test_uses_chunked_when_file_dir_available(self, mock_pool):
        mock_pool.execute_command.return_value = (0, "STAGE_OK dir=/tmp avail=999999\n", "")
        large_script = "echo " + "A" * 600_000 + "\n"
        self.exe._encode_script = MagicMock(return_value="A" * 130_000)
        self.exe._build_heredoc_command = MagicMock(return_value=("", "too large"))
        plan = self.exe.plan_execution(TEST_SERVER, large_script, "lg.sh", "bash", [], "task-003")
        self.assertEqual(plan.mode, "chunked_file")
        self.assertIsNotNone(plan.remote_path)
        self.assertIn("chunked", plan.notes[-1])

    @patch("app.core.script_executor.ssh_pool")
    def test_all_dirs_fail_uses_ultimate_fallback(self, mock_pool):
        mock_pool.execute_command.side_effect = [
            (4, "STAGE4_FAIL\n", "") for _ in range(len(DEFAULT_CANDIDATE_DIRS) * 2)
        ]
        large = "echo " + "X" * 700_000
        self.exe._encode_script = MagicMock(return_value="B" * 130_000)
        self.exe._build_heredoc_command = MagicMock(return_value=("", "too large"))
        plan = self.exe.plan_execution(TEST_SERVER, large, "fall.sh", "bash", [], "task-004")
        self.assertEqual(plan.mode, "ultimate_fallback")
        self.assertGreater(len(plan.extra_cleanups), 0)
        self.assertIn("ALL FILE MODES FAILED", plan.notes[-1])


class TestSchedulerIntegration(unittest.TestCase):
    def test_scheduler_finally_calls_cleanup_for_any_outcome(self):
        from app.core.scheduler import CommandScheduler
        sched = CommandScheduler()
        self.assertTrue(hasattr(sched, "_run_script_task"))
        core_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "core"))
        src = open(os.path.join(core_dir, "scheduler.py"), encoding="utf-8").read()
        self.assertIn("finally:", src)
        self.assertIn("schedule_cleanup", src)
        self.assertIn(".ssh_exec_final_", src)


class TestPlanPreflightCleanup(unittest.TestCase):
    def test_plan_calls_bulk_cleanup_before_execution(self):
        mock_cleanup = MagicMock()
        mock_cleanup.bulk_cleanup_leftovers = MagicMock(return_value=5)
        sel = TempPathSelector()
        exe = ScriptExecutor.__new__(ScriptExecutor)
        exe.cleanup = mock_cleanup
        exe.path_selector = sel
        exe._pipe_size_threshold = 100000
        exe._heredoc_size_threshold = 500_000
        exe._chunk_size = 48 * 1024

        plan = exe.plan_execution(TEST_SERVER, "echo ok", "x.sh", "bash", [], "task-005")
        mock_cleanup.bulk_cleanup_leftovers.assert_called_once_with(TEST_SERVER)
        self.assertIn("pre-flight", plan.notes[0])


class TestChunkedUpload(unittest.TestCase):
    def setUp(self):
        self.exe = ScriptExecutor.__new__(ScriptExecutor)
        self.exe._chunk_size = 100
        self.exe.path_selector = TempPathSelector()
        self.exe.cleanup = MagicMock()

    def test_chunking_splits_correctly(self):
        content = b"A" * 550
        cmd, path = self.exe._build_chunked_upload_command(
            "/tmp", content, "bash", [], "task-999", None
        )
        expected_chunks = 6
        base64_count = cmd.count("base64")
        self.assertGreaterEqual(base64_count, expected_chunks * 2)
        self.assertIn("/tmp/.chunked_", path)
        self.assertIn("exit 11", cmd)
        self.assertIn("exit 12", cmd)
        self.assertIn("exit 13", cmd)
        self.assertIn("chattr -i", cmd)
        self.assertIn("unlink", cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
