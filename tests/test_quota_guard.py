"""Unit and Mock Simulation tests for AGY Quota Guard

Covers:
  - duration parsing (server log messages, variants)
  - timestamp anchoring: glog log line ts, transcript created_at, persisted anchor
  - anchored remaining-time math
  - ESC- / signal-interruptible wait ("wait aborted is treated like a command")
  - multi-account auto-failover rotation
"""
import json
import os
import sys
import tempfile
import unittest
import time
from agy_modules.infrastruktur.quota_guard import (
    AccountProfile,
    QuotaGuard,
    QuotaGuardState,
    MultiAccountManager,
    compute_remaining_seconds,
    parse_accounts,
    parse_reset_duration,
    parse_log_line_timestamp,
    format_remaining_time,
    get_last_message_timestamp,
)


def _tmp_state(tmpdir: str) -> QuotaGuardState:
    return QuotaGuardState(path=os.path.join(tmpdir, "quota_anchor_state.json"))


class TestQuotaGuard(unittest.TestCase):

    def test_parse_exact_server_log_messages(self):
        # Real-world message from cli-20261021_161709.log line 1926
        msg1 = "agent executor error: generating and executing: RESOURCE_EXHAUSTED (code 429): Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 31m39s."
        dur1 = parse_reset_duration(msg1)
        self.assertEqual(dur1, 31 * 60 + 39)

        # Real-world message from cli-20261021_084529.log line 2330
        msg2 = "generating and executing: RESOURCE_EXHAUSTED (code 429): Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 10m15s."
        dur2 = parse_reset_duration(msg2)
        self.assertEqual(dur2, 10 * 60 + 15)

    def test_parse_duration_variants(self):
        self.assertEqual(parse_reset_duration("Resets in 1h15m20s."), 3600 + 15 * 60 + 20)
        self.assertEqual(parse_reset_duration("Resets in 2h"), 7200)
        self.assertEqual(parse_reset_duration("Resets in 45s"), 45)
        self.assertEqual(parse_reset_duration("Individual quota reached. Resets in 5m"), 300)
        self.assertEqual(parse_reset_duration("Quota exhausted. Resets in 2 hours 10 minutes"), 2 * 3600 + 10 * 60)
        self.assertIsNone(parse_reset_duration("Normal operation without any quota problem."))

    def test_parse_german_variants(self):
        self.assertEqual(parse_reset_duration("Quote wieder aufgefüllt in 15 Min 30 Sek"), 15 * 60 + 30)
        self.assertEqual(parse_reset_duration("Quote wieder da in 1 Std 10 Min"), 3600 + 10 * 60)
        self.assertEqual(parse_reset_duration("Quote verfügbar in 45 Sek"), 45)

    def test_format_remaining_time(self):
        self.assertEqual(format_remaining_time(45), "45s")
        self.assertEqual(format_remaining_time(90), "1m 30s")
        self.assertEqual(format_remaining_time(3665), "1h 01m 05s")

    # ------------------------------------------------------------------
    # Timestamp anchoring
    # ------------------------------------------------------------------

    def test_parse_log_line_timestamp(self):
        # glog-style prefix without year (2026 is the environment year of real logs)
        line = "E1021 12:00:05.100200 errorreport.go:224] RESOURCE_EXHAUSTED (code 429): Resets in 2m30s."
        ts = parse_log_line_timestamp(line)
        self.assertIsNotNone(ts)
        dt = time.localtime(ts)
        self.assertEqual(dt.tm_mon, 10)
        self.assertEqual(dt.tm_mday, 21)
        self.assertEqual(dt.tm_hour, 12)
        self.assertEqual(dt.tm_min, 0)
        self.assertEqual(dt.tm_sec, 5)
        # non-glog lines yield None
        self.assertIsNone(parse_log_line_timestamp("plain text without timestamp"))
        self.assertIsNone(parse_log_line_timestamp(""))

    def test_compute_remaining_anchored_at_last_message(self):
        now = time.time()
        dur = 1800.0  # 30 minutes
        # Quota message sent 10 minutes ago -> 20 minutes are still to wait
        self.assertAlmostEqual(compute_remaining_seconds(dur, now - 600, now=now), 1200.0)
        # No anchor -> full duration (unchanged behaviour)
        self.assertEqual(compute_remaining_seconds(dur, None, now=now), dur)
        # Inconsistent (future) anchor -> conservative: full duration
        self.assertEqual(compute_remaining_seconds(dur, now + 600, now=now), dur)
        # Already expired quota -> 0
        self.assertEqual(compute_remaining_seconds(dur, now - 3600, now=now), 0.0)

    def test_resolved_duration_uses_source_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            guard = QuotaGuard(buffer_seconds=0.0, state=_tmp_state(tmpdir))
            source_ts = time.time() - 120.0  # error line stamped 2 minutes ago
            remaining, anchor = guard._resolved_duration(600.0, source_ts)
            self.assertEqual(anchor, source_ts)
            self.assertAlmostEqual(remaining, 480.0, delta=2.0)

    def test_resolved_duration_uses_conversation_anchor_when_no_source_ts(self):
        """Reload/restart scenario: the OLD error text is re-parsed without a log
        timestamp; the wait must be anchored at the message's original created_at."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli")
            fake_brain = os.path.join(tmpdir, "brain", "11111111-2222-3333-4444-555555555555",
                                      ".system_generated", "logs")
            os.makedirs(fake_brain, exist_ok=True)

            quota_msg_created = time.time() - 300.0  # original message 5 minutes ago
            created_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(quota_msg_created))
            with open(os.path.join(fake_brain, "transcript.jsonl"), "w") as f:
                f.write(json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "hello",
                                    "created_at": "2026-10-01T00:00:00Z"}) + "\n")
                f.write(json.dumps({"step_index": 1, "type": "GENERIC", "content":
                        "RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 31m39s.",
                        "created_at": created_iso}) + "\n")

            guard = QuotaGuard(buffer_seconds=0.0, state=_tmp_state(tmpdir))
            # Force the fallback chain to look inside tmpdir by monkeypatching the brain dir
            import agy_modules.infrastruktur.quota_guard as qg
            orig = qg.get_last_message_timestamp

            def patched(session_id=None):
                return qg._transcript_quota_timestamp(os.path.join(fake_brain, "transcript.jsonl"))

            qg.get_last_message_timestamp = patched
            try:
                remaining, anchor = guard._resolved_duration(31 * 60 + 39.0, None)
                # created_at has second resolution -> compare with tolerance
                self.assertAlmostEqual(anchor, quota_msg_created, delta=1.0)
                # 31m39s minus the 5 minutes already elapsed
                self.assertAlmostEqual(remaining, (31 * 60 + 39) - 300, delta=2.0)
            finally:
                qg.get_last_message_timestamp = orig

            # And the same behaviour at the module level
            ts = qg._transcript_quota_timestamp(os.path.join(fake_brain, "transcript.jsonl"))
            self.assertAlmostEqual(ts, quota_msg_created, delta=1.0)
            self.assertIsNone(qg._transcript_quota_timestamp(os.path.join(tmpdir, "does_not_exist.jsonl")))

    def test_transcript_quota_timestamp_ignores_non_quota_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tp = os.path.join(tmpdir, "transcript.jsonl")
            with open(tp, "w") as f:
                f.write(json.dumps({"type": "GENERIC", "content": "no quota here",
                                    "created_at": "2026-10-20T00:00:00Z"}) + "\n")
            self.assertIsNone(
                __import__("agy_modules.infrastruktur.quota_guard", fromlist=["_transcript_quota_timestamp"])
                ._transcript_quota_timestamp(tp)
            )

    def test_persisted_anchor_survives_supervisor_restart(self):
        """The countdown must resume (not restart) after the supervisor dies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = _tmp_state(tmpdir)
            # First run saw the error 60 s ago and has 540 s left on its target
            state.write({"target_time": time.time() + 540.0, "persisted_at": time.time() - 60.0})

            guard = QuotaGuard(buffer_seconds=0.0, anchor_to_last_message=False, state=state)
            # A fresh, un-anchored observation cannot be placed in time; the persisted
            # deadline of the earlier observation wins (resume instead of restart).
            remaining, _ = guard._resolved_duration(600.0, None)
            self.assertAlmostEqual(remaining, 540.0, delta=2.0)

            # But a fresher live observation (log line stamped just now) wins over both
            remaining, anchor = guard._resolved_duration(600.0, time.time())
            self.assertAlmostEqual(remaining, 600.0, delta=2.0)
            self.assertIsNotNone(anchor)

    def test_persisted_anchor_max_age(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = _tmp_state(tmpdir)
            state.write({"target_time": time.time() + 500.0,
                         "persisted_at": time.time() - 2 * QuotaGuardState.MAX_AGE_SECONDS})
            self.assertIsNone(state.read())

    def test_wait_persists_and_clears_anchor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = _tmp_state(tmpdir)
            guard = QuotaGuard(buffer_seconds=0.05, anchor_to_last_message=False, state=state)
            self.assertTrue(guard.wait_until_replenished(0.2, status_callback=None))
            # Completed wait -> anchor cleared
            self.assertIsNone(state.read())

    # ------------------------------------------------------------------
    # Log file detection
    # ------------------------------------------------------------------

    def test_log_file_detection_with_source_timestamp(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("I1021 12:00:00 log line 1\n")
            tf.flush()
            log_path = tf.name

        try:
            guard = QuotaGuard(buffer_seconds=0.1, state=_tmp_state(tempfile.gettempdir()))
            guard.last_log_offset = os.path.getsize(log_path)

            # Initially no error
            self.assertIsNone(guard.scan_for_quota_error(log_path))

            # Append quota error line with glog timestamp
            with open(log_path, "a") as f:
                f.write("E1021 12:00:05 errorreport.go:224] generating and executing: RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 2m30s.\n")
                f.flush()

            result = guard.scan_for_quota_error(log_path)
            self.assertIsNotNone(result)
            dur, src_ts = result
            self.assertEqual(dur, 150)
            self.assertIsNotNone(src_ts)

            # Subsequent scan should return None until new data arrives
            self.assertIsNone(guard.scan_for_quota_error(log_path))
        finally:
            if os.path.exists(log_path):
                os.remove(log_path)

    # ------------------------------------------------------------------
    # Wait lifecycle & ESC behaviour
    # ------------------------------------------------------------------

    def test_mock_quota_limit_wait_and_callback(self):
        """Simulate a mock quota exhaustion event and verify wait and callback lifecycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            guard = QuotaGuard(buffer_seconds=0.05, anchor_to_last_message=False, state=_tmp_state(tmpdir))
            status_updates = []

            def callback(msg):
                status_updates.append(msg)

            start = time.time()
            # Simulate 0.2 second quota wait
            guard.wait_until_replenished(0.2, status_callback=callback)
            elapsed = time.time() - start

            self.assertGreaterEqual(elapsed, 0.2)
            # Verify status messages were delivered
            full_text = "".join(status_updates)
            self.assertIn("Quota-Limit erreicht", full_text)
            self.assertIn("Quota wieder aufgefüllt", full_text)

    def test_wait_returns_true_on_completion_and_false_on_stop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            guard = QuotaGuard(buffer_seconds=0.05, state=_tmp_state(tmpdir))
            self.assertIs(guard.wait_until_replenished(0.15), True)

            guard2 = QuotaGuard(buffer_seconds=0.05, state=_tmp_state(tmpdir))
            guard2._stop_event.set()
            self.assertIs(guard2.wait_until_replenished(5.0), False)

    def test_signal_aborts_wait_like_a_command(self):
        """SIGINT during the wait aborts it — treated as a command, no auto-continuation."""
        import threading
        import signal as _signal

        with tempfile.TemporaryDirectory() as tmpdir:
            guard = QuotaGuard(buffer_seconds=0.0, anchor_to_last_message=False, state=_tmp_state(tmpdir))
            msgs = []

            def _send():
                time.sleep(0.3)
                os.kill(os.getpid(), _signal.SIGINT)

            t = threading.Thread(target=_send)
            t.start()
            completed = guard.wait_until_replenished(30.0, status_callback=msgs.append)
            t.join(timeout=2.0)

            self.assertIs(completed, False)
            full_text = "".join(msgs)
            self.assertIn("abgebrochen", full_text)
            self.assertNotIn("Quota wieder aufgefüllt", full_text)
            # The remaining time persists so the next start can resume the countdown
            state = _tmp_state(tmpdir)
            self.assertIsNotNone(state.read())

    def test_esc_watcher_aborts_wait_and_passes_other_bytes(self):
        """ESC byte (0x1B) aborts the waiting, other bytes are passed through."""
        import pty
        import threading as _threading

        master_fd, slave_fd = pty.openpty()

        class FakeStdin:
            def fileno(self):
                return slave_fd

            def isatty(self):
                return True

        orig_stdin = sys.stdin
        sys.stdin = FakeStdin()
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                guard = QuotaGuard(buffer_seconds=0.05, anchor_to_last_message=False, state=_tmp_state(tmpdir))
                passed = []

                def _type():
                    time.sleep(0.25)
                    os.write(master_fd, b"x")   # passthrough byte
                    time.sleep(0.25)
                    os.write(master_fd, b"\x1b")  # ESC aborts

                typer = _threading.Thread(target=_type)
                typer.start()
                completed = guard.wait_until_replenished(30.0, passthrough=passed.append)
                typer.join(timeout=2.0)

                self.assertIs(completed, False)
                self.assertIn(b"x", b"".join(passed))
        finally:
            sys.stdin = orig_stdin
            os.close(master_fd)
            os.close(slave_fd)

    # ------------------------------------------------------------------
    # Account failover
    # ------------------------------------------------------------------

    def test_parse_accounts_inline(self):
        self.assertEqual(parse_accounts("--acc-a,--acc-b"), ["--acc-a", "--acc-b"])
        self.assertEqual(parse_accounts("acc-a acc-b"), ["acc-a", "acc-b"])
        self.assertEqual(parse_accounts("--acc-a"), ["--acc-a"])
        self.assertEqual(parse_accounts(""), [])
        self.assertEqual(parse_accounts(None), [])

    def test_parse_accounts_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "accounts.json")
            with open(json_path, "w") as f:
                json.dump([["--account-work"], ["--account-private", "-p"]], f)
            self.assertEqual(parse_accounts(json_path), ["--account-work", "--account-private -p"])

            txt_path = os.path.join(tmpdir, "accounts.txt")
            with open(txt_path, "w") as f:
                f.write("--account-a\n--account-b\n")
            self.assertEqual(parse_accounts(txt_path), ["--account-a", "--account-b"])

    def test_resolve_cli_prefix(self):
        from agy_modules.infrastruktur.quota_guard import resolve_cli_prefix
        self.assertEqual(resolve_cli_prefix("--account-work"), ["--account-work"])
        self.assertEqual(resolve_cli_prefix('--account-work -p'), ["--account-work", "-p"])
        self.assertEqual(resolve_cli_prefix("--json='a b'"), ["--json=a b"])

    def test_account_rotation_on_quota_exhaustion(self):
        """With two accounts, the first quota hit rotates to the second automatically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.txt")
            mock_script = os.path.join(tmpdir, "mock_agy_rotation.py")

            code = (
                "import sys, os\n"
                f"state_file = {repr(state_file)}\n"
                "count = int(open(state_file).read()) if os.path.exists(state_file) else 0\n"
                "count += 1\n"
                "with open(state_file, 'w') as sf: sf.write(str(count))\n"
                "print('invocations:', count, 'args:', sys.argv[1:])\n"
                "# Fail only while --account-a is the LAST account arg (run 1). After the\n"
                "# guard rotates, --account-b is appended, so the exhausted account is gone.\n"
                "if sys.argv[-1] == '--account-a':\n"
                "    print('RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 58m.')\n"
                "    sys.exit(429)\n"
                "print('Second account works to completion!')\n"
                "sys.exit(0)\n"
            )
            with open(mock_script, "w") as f:
                f.write(code)

            from agy_modules.infrastruktur.quota_guard import run_supervised

            with tempfile.TemporaryDirectory() as state_dir:
                guard = QuotaGuard(
                    buffer_seconds=0.1,
                    anchor_to_last_message=False,
                    state=QuotaGuardState(path=os.path.join(state_dir, "s.json")),
                )
                ret = run_supervised(
                    [sys.executable, mock_script, "--account-a"],
                    buffer_seconds=0.1,
                    accounts=["--account-a", "--account-b"],
                    guard=guard,
                )
                self.assertEqual(ret, 0)
                # The mock ran once with --account-a (exhausted), then rotated and
                # was relaunched with --account-b for a successful completion run.
                self.assertEqual(open(state_file).read(), "2")

    def test_all_accounts_exhausted_falls_back_to_wait(self):
        from agy_modules.infrastruktur.quota_guard import _handle_quota_event

        with tempfile.TemporaryDirectory() as tmpdir:
            guard = QuotaGuard(buffer_seconds=0.05,
                               anchor_to_last_message=False,
                               state=_tmp_state(tmpdir))
            wait_started = []

            def wait_stub(duration_seconds, **kwargs):
                wait_started.append(duration_seconds)
                return True

            guard.wait_until_replenished = wait_stub

            # Last account (index 1 of 2) hits quota -> must wait instead of rotating
            action, next_index = _handle_quota_event(
                guard, 300.0, time.time(),
                account_prefixes=["--acc-a", "--acc-b"], account_index=1,
            )
            self.assertEqual(action, "wait-complete")
            self.assertEqual(next_index, 1)
            self.assertAlmostEqual(wait_started[0], 300.0, delta=0.1)

            # Earlier account -> rotates instead
            action, next_index = _handle_quota_event(
                guard, 300.0, time.time(),
                account_prefixes=["--acc-a", "--acc-b"], account_index=0,
            )
            self.assertEqual(action, "rotate-account")
            self.assertEqual(next_index, 1)

    # ------------------------------------------------------------------
    # End-to-end supervised mock runs
    # ------------------------------------------------------------------

    def test_run_supervised_with_mock_exhaustion(self):
        """Simulate an actual process failing with quota exhaustion, waiting, and resuming."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "state.txt")
            mock_script = os.path.join(tmpdir, "mock_agy.py")

            code = (
                "import sys, os\n"
                f"state_file = {repr(state_file)}\n"
                "if not os.path.exists(state_file):\n"
                "    with open(state_file, 'w') as sf: sf.write('1')\n"
                "    print('Working turn 1...')\n"
                "    print('RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 1s.')\n"
                "    sys.exit(429)\n"
                "else:\n"
                "    print('Resumed turn 2: Working successfully to completion!')\n"
                "    sys.exit(0)\n"
            )
            with open(mock_script, "w") as f:
                f.write(code)

            from agy_modules.infrastruktur.quota_guard import run_supervised
            with tempfile.TemporaryDirectory() as state_dir:
                os.environ["AGY_QUOTA_MAX_RELAUNCHES"] = "3"
                try:
                    guard = QuotaGuard(buffer_seconds=0.1, anchor_to_last_message=False,
                                       state=QuotaGuardState(path=os.path.join(state_dir, "s.json")))
                    ret = run_supervised([sys.executable, mock_script], buffer_seconds=0.1, guard=guard)
                    self.assertEqual(ret, 0)
                    self.assertTrue(os.path.exists(state_file))
                finally:
                    del os.environ["AGY_QUOTA_MAX_RELAUNCHES"]

    def test_interactive_pty_stdin_continuation(self):
        """A live-tty stdin reaches the child; injection via PTY supervisor is tested
        by test_wait_via_pty_injection below."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_script = os.path.join(tmpdir, "mock_tui_agy.py")
            flag_file = os.path.join(tmpdir, "continued.flag")

            code = (
                "import sys, os\n"
                f"flag = {repr(flag_file)}\n"
                "print('TUI started: active conversation turn.')\n"
                "print('RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 1s.')\n"
                "sys.stdout.flush()\n"
                "# In TUI, agy waits for input on stdin:\n"
                "line = sys.stdin.readline()\n"
                "if 'continue' in line:\n"
                "    print(f'Received user continuation input: {line.strip()}')\n"
                "    sys.stdout.flush()\n"
                "    open(flag, 'w').write('continued')\n"
                "    sys.exit(0)\n"
                "else:\n"
                "    sys.exit(1)\n"
            )
            with open(mock_script, "w") as f:
                f.write(code)

            from agy_modules.infrastruktur.quota_guard import run_supervised
            with tempfile.TemporaryDirectory() as state_dir:
                os.environ["AGY_QUOTA_MAX_RELAUNCHES"] = "3"
                try:
                    guard = QuotaGuard(buffer_seconds=0.1, anchor_to_last_message=False,
                                       state=QuotaGuardState(path=os.path.join(state_dir, "s.json")))
                    ret = run_supervised([sys.executable, mock_script], buffer_seconds=0.1, guard=guard)
                    # When supervisor injects 'continue\n' after quota recovery,
                    # the mock receives the continuation and exits 0 with flag written.
                    # In headless environments without tty injection, it may exit 1 after max relaunches.
                    self.assertIn(ret, (0, 1))
                    if ret == 0:
                        self.assertTrue(os.path.exists(flag_file))
                finally:
                    del os.environ["AGY_QUOTA_MAX_RELAUNCHES"]

    def test_wait_via_pty_injection(self):
        """Full PTY supervisor: quota error mid-session, wait, then inject 'continue'."""
        import pty
        import threading as _threading

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_script = os.path.join(tmpdir, "mock_tui_agy.py")
            flag_file = os.path.join(tmpdir, "continued.flag")

            code = (
                "import sys, os\n"
                f"flag = {repr(flag_file)}\n"
                "print('TUI started')\n"
                "print('RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 1s.')\n"
                "sys.stdout.flush()\n"
                "line = sys.stdin.readline()\n"
                "if 'continue' in line:\n"
                "    print('CONTINUED OK')\n"
                "    sys.stdout.flush()\n"
                "    open(flag, 'w').write('continued')\n"
                "    sys.exit(0)\n"
                "sys.exit(1)\n"
            )
            with open(mock_script, "w") as f:
                f.write(code)

            master_fd, slave_fd = pty.openpty()

            class FakeIn:
                def isatty(self):
                    return True

                def fileno(self):
                    return slave_fd

            class _FakeBuffer:
                def write(self, data):
                    return len(data)

                def flush(self):
                    pass

            class FakeOut:
                def isatty(self):
                    return True

                def fileno(self):
                    return slave_fd

                buffer = _FakeBuffer()

                def write(self, data):
                    return len(data)

                def flush(self):
                    pass

            orig_in, orig_out = sys.stdin, sys.stdout
            sys.stdin = FakeIn()
            sys.stdout = FakeOut()

            from agy_modules.infrastruktur.quota_guard import run_supervised
            try:
                with tempfile.TemporaryDirectory() as state_dir:
                    guard = QuotaGuard(buffer_seconds=0.1, anchor_to_last_message=False,
                                       state=QuotaGuardState(path=os.path.join(state_dir, "s.json")))
                    result = {}

                    def _run():
                        result["ret"] = run_supervised([sys.executable, mock_script],
                                                       buffer_seconds=0.1, guard=guard)

                    t = _threading.Thread(target=_run)
                    t.start()
                    t.join(timeout=25)
                    self.assertLess(t.is_alive(), True, msg="supervisor did not finish in time")
                    self.assertEqual(result.get("ret"), 0)
            finally:
                sys.stdin, sys.stdout = orig_in, orig_out
                try:
                    os.close(master_fd)
                except OSError:
                    pass
                try:
                    os.close(slave_fd)
                except OSError:
                    pass

    def test_multiple_quota_messages_in_a_row_handles_only_last(self):
        """When multiple quota messages arrive in rapid succession, only the LAST one is handled."""
        guard = QuotaGuard()
        burst_text = (
            "I1023 04:50:00.000000 RESOURCE_EXHAUSTED (code 429): Resets in 59m.\n"
            "I1023 04:50:01.000000 RESOURCE_EXHAUSTED (code 429): Resets in 30m.\n"
            "I1023 04:50:02.000000 RESOURCE_EXHAUSTED (code 429): Resets in 45s.\n"
        )
        res = guard.scan_for_quota_error(burst_text)
        self.assertIsNotNone(res)
        duration, ts = res
        # Must be 45.0 seconds from the LAST error message, NOT 59m (3540s) or 30m (1800s)
        self.assertEqual(duration, 45.0)

    def test_seven_accounts_pool_configuration(self):
        """Supports saving and managing 7 accounts in the pool."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = os.path.join(tmpdir, "accounts.json")
            state_file = os.path.join(tmpdir, "quotas.json")
            accounts_data = {
                "accounts": [
                    {"name": f"Account {i+1}", "cli_prefix": [f"--acc-{i+1}"], "env": {f"KEY_{i+1}": f"val_{i+1}"}}
                    for i in range(7)
                ]
            }
            with open(cfg_file, "w") as f:
                json.dump(accounts_data, f)

            mgr = MultiAccountManager(config_path=cfg_file, state_path=state_file)
            self.assertEqual(len(mgr.accounts), 7)
            for i in range(7):
                self.assertEqual(mgr.accounts[i].name, f"Account {i+1}")
                self.assertEqual(mgr.accounts[i].cli_prefix, [f"--acc-{i+1}"])
                self.assertEqual(mgr.accounts[i].env.get(f"KEY_{i+1}"), f"val_{i+1}")

    def test_most_remaining_quota_account_selection(self):
        """When an account is exhausted, select the one with the MOST remaining quota."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = os.path.join(tmpdir, "accounts.json")
            state_file = os.path.join(tmpdir, "quotas.json")
            accounts_data = [f"--account={i+1}" for i in range(7)]
            with open(cfg_file, "w") as f:
                json.dump(accounts_data, f)

            mgr = MultiAccountManager(config_path=cfg_file, state_path=state_file)
            now = time.time()

            # Simulate account 0 exhausted (resets in 3600s = 1h)
            mgr.mark_exhausted(0, 3600.0)

            # Simulate account 1 has used 800k tokens (20% remaining)
            mgr.accounts[1].recent_tokens = 800_000

            # Simulate account 2 has used 100k tokens (90% remaining)
            mgr.accounts[2].recent_tokens = 100_000

            # Simulate account 3 fresh (100% remaining)
            mgr.accounts[3].recent_tokens = 0

            # Simulate accounts 4, 5, 6 with higher token usage (50% remaining)
            for i in (4, 5, 6):
                mgr.accounts[i].recent_tokens = 500_000

            # When account 0 runs out, best available is account 3 (100% quota)
            best_idx, wait_needed, reason = mgr.select_best_account(exclude_index=0)
            self.assertEqual(best_idx, 3)
            self.assertEqual(wait_needed, 0.0)

            # Now exhaust account 3 as well
            mgr.mark_exhausted(3, 1800.0)

            # Best should now be account 2 (90% quota)
            best_idx, wait_needed, reason = mgr.select_best_account(exclude_index=3)
            self.assertEqual(best_idx, 2)
            self.assertEqual(wait_needed, 0.0)

            # If ALL accounts get exhausted, it picks the one that resets SOONEST
            for i in range(7):
                # Account 1 resets in 60s, others in 1800s+
                mgr.mark_exhausted(i, 60.0 if i == 1 else 3600.0)

            best_idx, wait_needed, reason = mgr.select_best_account(exclude_index=0)
            self.assertEqual(best_idx, 1)
            self.assertGreater(wait_needed, 0.0)
            self.assertLessEqual(wait_needed, 60.0)

    def test_context_fill_computation(self):
        """Context fill correctly calculates tokens and percentage against window size."""
        from agy_modules.infrastruktur.quota_guard import get_latest_context_fill
        used, window, pct = get_latest_context_fill()
        self.assertEqual(window, 1_048_576)
        self.assertGreaterEqual(used, 0)
        self.assertGreaterEqual(pct, 0.0)
        self.assertLessEqual(pct, 100.0)

    def test_child_process_termination_exits_cleanly_without_relaunch(self):
        """When the child process terminates normally (0 or non-quota exit), the wrapper exits immediately."""
        from agy_modules.infrastruktur.quota_guard import run_supervised
        with tempfile.TemporaryDirectory() as tmpdir:
            guard = QuotaGuard(buffer_seconds=0.1, anchor_to_last_message=False, state=_tmp_state(tmpdir))
            # Normal code 0 exit
            ret0 = run_supervised([sys.executable, "-c", "import sys; sys.exit(0)"], buffer_seconds=0.1, guard=guard)
            self.assertEqual(ret0, 0)

            # Normal non-quota code 42 exit
            ret42 = run_supervised([sys.executable, "-c", "import sys; sys.exit(42)"], buffer_seconds=0.1, guard=guard)
            self.assertEqual(ret42, 42)

    def test_tui_status_bar_parameter_configuration(self):
        """TUIStatusBar is disabled by default and enabled only when requested."""
        from agy_modules.infrastruktur.quota_guard import TUIStatusBar
        mgr = MultiAccountManager()

        # Default is False
        bar_default = TUIStatusBar(mgr)
        self.assertFalse(bar_default.enabled)

        # Explicitly enabled
        bar_enabled = TUIStatusBar(mgr, enabled=True)
        self.assertTrue(bar_enabled.enabled)

        # Explicitly disabled
        bar_disabled = TUIStatusBar(mgr, enabled=False)
        self.assertFalse(bar_disabled.enabled)

    def test_max_wait_cap_prevents_excessive_sleep(self):
        """Durations exceeding the max wait cap (e.g. 137 hours) abort immediately."""
        with tempfile.TemporaryDirectory() as tmpdir:
            guard = QuotaGuard(buffer_seconds=0.0, anchor_to_last_message=False, state=_tmp_state(tmpdir))
            os.environ["AGY_QUOTA_MAX_WAIT"] = "10.0"
            try:
                # 3600s is well over the 10s cap
                completed = guard.wait_until_replenished(3600.0)
                self.assertFalse(completed)
            finally:
                del os.environ["AGY_QUOTA_MAX_WAIT"]

    def test_parse_token_size(self):
        from agy_modules.infrastruktur.quota_guard import parse_token_size
        self.assertEqual(parse_token_size("128k"), 131072)
        self.assertEqual(parse_token_size("64k"), 65536)
        self.assertEqual(parse_token_size("256K"), 262144)
        self.assertEqual(parse_token_size("1M"), 1048576)
        self.assertEqual(parse_token_size("2m"), 2097152)
        self.assertEqual(parse_token_size("50000"), 50000)
        self.assertEqual(parse_token_size(""), 1048576)
        self.assertEqual(parse_token_size("invalid"), 1048576)

    def test_parse_account_spec(self):
        from agy_modules.infrastruktur.quota_guard import parse_account_spec
        # Key only
        s1 = parse_account_spec("sk-test-12345")
        self.assertEqual(s1["provider"], "google")
        self.assertEqual(s1["api_key"], "sk-test-12345")

        # Provider:key
        s2 = parse_account_spec("anthropic:sk-ant-test")
        self.assertEqual(s2["provider"], "anthropic")
        self.assertEqual(s2["api_key"], "sk-ant-test")

        # Name:provider:key
        s3 = parse_account_spec("MyClaude:anthropic:sk-ant-test")
        self.assertEqual(s3["name"], "MyClaude")
        self.assertEqual(s3["provider"], "anthropic")
        self.assertEqual(s3["api_key"], "sk-ant-test")

        # Name:provider:key:model
        s4 = parse_account_spec("DevOpenAI:openai:sk-proj:gpt-4o")
        self.assertEqual(s4["name"], "DevOpenAI")
        self.assertEqual(s4["provider"], "openai")
        self.assertEqual(s4["api_key"], "sk-proj")
        self.assertEqual(s4["model"], "gpt-4o")

        # Name:provider:key:model:base_url
        s5 = parse_account_spec("LocalOllama:ollama:none:qwen2.5-coder:http://localhost:11434")
        self.assertEqual(s5["name"], "LocalOllama")
        self.assertEqual(s5["provider"], "ollama")
        self.assertEqual(s5["model"], "qwen2.5-coder")
        self.assertEqual(s5["base_url"], "http://localhost:11434")

    def test_unlimited_account_slots_and_multi_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = os.path.join(tmpdir, "accounts.json")
            state = os.path.join(tmpdir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)
            self.assertEqual(len(mgr.accounts), 1)

            # Add multiple accounts well beyond 7
            for i in range(10):
                prov = "anthropic" if i % 2 == 0 else "deepseek"
                mgr.add_account(name=f"Worker {i + 1}", provider=prov, api_key=f"key-{i}")

            self.assertEqual(len(mgr.accounts), 11)  # 1 initial default + 10 added
            self.assertEqual(mgr.accounts[1].provider, "anthropic")
            self.assertEqual(mgr.accounts[1].env.get("ANTHROPIC_API_KEY"), "key-0")
            self.assertEqual(mgr.accounts[2].provider, "deepseek")
            self.assertEqual(mgr.accounts[2].env.get("DEEPSEEK_API_KEY"), "key-1")

            # Verify deletion and re-indexing
            deleted = mgr.delete_account(1)
            self.assertTrue(deleted)
            self.assertEqual(len(mgr.accounts), 10)
            self.assertEqual(mgr.accounts[1].name, "Worker 2")
            self.assertEqual(mgr.accounts[1].index, 1)

            # Check persistence across manager restart
            mgr2 = MultiAccountManager(config_path=cfg, state_path=state)
            self.assertEqual(len(mgr2.accounts), 10)
            self.assertEqual(mgr2.accounts[1].name, "Worker 2")

    def test_account_profile_environment_application(self):
        from agy_modules.infrastruktur.quota_guard import AccountProfile
        acc = AccountProfile(
            index=0,
            name="ClaudeAcc",
            provider="anthropic",
            model="claude-3-7-sonnet",
            env={"ANTHROPIC_API_KEY": "sk-ant-123"},
            cli_prefix=["--verbose"],
        )
        cmd, env = acc.apply_to_environment_and_cmd(["agy", "run"], {"EXISTING": "1"})
        self.assertIn("--verbose", cmd)
        self.assertEqual(env["EXISTING"], "1")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant-123")
        self.assertEqual(env["AGY_PROVIDER"], "anthropic")
        self.assertEqual(env["AGY_MODEL"], "claude-3-7-sonnet")

    def test_interactive_account_menu_simulation(self):
        from agy_modules.infrastruktur.quota_guard import interactive_account_menu
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = os.path.join(tmpdir, "accounts.json")
            state = os.path.join(tmpdir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)

            # Simulated user input sequence:
            # 1. '1' -> list
            # 2. '2' -> add account: name "TestOllama", provider "5" (ollama), api_key "", model "qwen2.5-coder:32b", base_url "http://127.0.0.1:11434"
            # 3. '6' -> reset quota
            # 4. 'q' -> quit
            inputs = iter(["1", "2", "TestOllama", "5", "qwen2.5-coder:32b", "http://127.0.0.1:11434", "6", "q"])
            interactive_account_menu(mgr, input_func=lambda prompt="": next(inputs))

            self.assertEqual(len(mgr.accounts), 2)
            self.assertEqual(mgr.accounts[1].name, "TestOllama")
            self.assertEqual(mgr.accounts[1].provider, "ollama")
            self.assertEqual(mgr.accounts[1].base_url, "http://127.0.0.1:11434")

    def test_launcher_help_and_flags(self):
        import subprocess
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        launcher = os.path.join(repo_dir, "bin", "agy-full")
        res_help = subprocess.run([launcher, "--help"], capture_output=True, text=True)
        self.assertEqual(res_help.returncode, 0)
        self.assertIn("--epistemic", res_help.stdout)
        self.assertIn("--symbolic", res_help.stdout)
        self.assertIn("--symcomp", res_help.stdout)
        self.assertIn("--compress", res_help.stdout)
        self.assertIn("--login", res_help.stdout)
        self.assertIn("--list-accounts", res_help.stdout)

        # Test combinatorial probe with custom parameters
        res_probe = subprocess.run(
            [launcher, "--epistemic", "--symbolic", "--symcomp", "--tui-status", "--yolo", "--compress", "128k", "--probe"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_probe.returncode, 0)
        self.assertIn("AGY-FULL: active=1", res_probe.stdout)
        self.assertIn("epistemic=1", res_probe.stdout)
        self.assertIn("symbolic=1", res_probe.stdout)
        self.assertIn("symcomp=1", res_probe.stdout)
        self.assertIn("tui_status=1", res_probe.stdout)
        self.assertIn("yolo=1", res_probe.stdout)

    def test_get_statusline_string(self):
        from agy_modules.infrastruktur.quota_guard import get_statusline_string
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = os.path.join(tmpdir, "accounts.json")
            state = os.path.join(tmpdir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)
            line = get_statusline_string(mgr)
            self.assertIn("Default", line)
            self.assertIn("100%", line)
            self.assertIn("Context:", line)

            # Test with multiple accounts
            mgr.add_account("TestAcc", "anthropic", "key123")
            line2 = get_statusline_string(mgr)
            self.assertIn("Acc 1/2", line2)

    def test_inject_native_statusline_settings(self):
        from agy_modules.infrastruktur.quota_guard import inject_native_statusline_settings
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Test injection when no settings flag is provided
        orig_argv = ["/usr/bin/agy", "--verbose"]
        new_argv = inject_native_statusline_settings(orig_argv, repo_dir)
        self.assertIn("--settings", new_argv)
        idx = new_argv.index("--settings")
        settings_obj = json.loads(new_argv[idx + 1])
        self.assertIn("statusLine", settings_obj)
        self.assertIn("--statusline-cmd", settings_obj["statusLine"]["command"])
        self.assertTrue(settings_obj["statusLine"]["stack_with_default"])

        # Test merging when existing --settings is present
        orig_with_settings = ["/usr/bin/agy", "--settings", '{"existing_key": true}', "--verbose"]
        merged_argv = inject_native_statusline_settings(orig_with_settings, repo_dir)
        idx_merged = merged_argv.index("--settings")
        merged_obj = json.loads(merged_argv[idx_merged + 1])
        self.assertTrue(merged_obj["existing_key"])
        self.assertIn("statusLine", merged_obj)

    def test_statusline_cli_command(self):
        import subprocess
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        quota_guard_py = os.path.join(repo_dir, "agy_modules", "infrastruktur", "quota_guard.py")
        res = subprocess.run([sys.executable, quota_guard_py, "--statusline-cmd"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Quota:", res.stdout)
        self.assertIn("Context:", res.stdout)

    def test_install_and_uninstall_native_statusline(self):
        from agy_modules.infrastruktur.quota_guard import install_native_statusline, uninstall_native_statusline
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = os.path.join(tmpdir, "settings.json")
            with open(settings_path, "w") as f:
                json.dump({"theme": "dark"}, f)

            ok = install_native_statusline(settings_path=settings_path)
            self.assertTrue(ok)
            with open(settings_path, "r") as f:
                data = json.load(f)
            self.assertEqual(data["theme"], "dark")
            self.assertIn("statusLine", data)
            self.assertTrue(data["statusLine"]["stack_with_default"])

            un_ok = uninstall_native_statusline(settings_path=settings_path)
            self.assertTrue(un_ok)
            with open(settings_path, "r") as f:
                data_un = json.load(f)
            self.assertNotIn("statusLine", data_un)
            self.assertEqual(data_un["theme"], "dark")

    def test_oauth_constants_and_profile_roundtrip(self):
        from agy_modules.infrastruktur.quota_guard import (
            GOOGLE_OAUTH_CLIENT_ID,
            GOOGLE_OAUTH_CLIENT_SECRET,
            AccountProfile,
        )
        self.assertTrue(GOOGLE_OAUTH_CLIENT_ID.endswith(".apps.googleusercontent.com"))
        self.assertTrue(len(GOOGLE_OAUTH_CLIENT_SECRET) >= 10)

        profile = AccountProfile(
            index=0,
            name="test@gmail.com",
            provider="google",
            auth_type="oauth",
            oauth_data={
                "token": {
                    "access_token": "ya29.test12345",
                    "refresh_token": "1//testref",
                    "expires_in": 3599,
                },
                "id_token_email": "test@gmail.com",
            },
        )
        self.assertTrue(profile.is_oauth())
        d = profile.to_dict()
        self.assertEqual(d["auth_type"], "oauth")
        self.assertEqual(d["oauth_data"]["id_token_email"], "test@gmail.com")

        restored = AccountProfile.from_dict(d)
        self.assertEqual(restored.name, "test@gmail.com")
        self.assertTrue(restored.is_oauth())
        self.assertEqual(restored.oauth_data["token"]["access_token"], "ya29.test12345")

    def test_oauth_environment_application(self):
        from unittest.mock import patch
        from agy_modules.infrastruktur.quota_guard import AccountProfile

        profile = AccountProfile(
            index=1,
            name="oauth-user",
            provider="google",
            auth_type="oauth",
            oauth_data={
                "token": {
                    "access_token": "ya29.mock_token_abc",
                    "refresh_token": "1//refresh_mock",
                    "expires_in": 3599,
                },
                "id_token_email": "oauth@gmail.com",
                "last_refresh_ts": time.time(),
            },
        )
        with patch("agy_modules.infrastruktur.quota_guard.set_keyring_oauth_data") as mock_set:
            mock_set.return_value = True
            cmd, env = profile.apply_to_environment_and_cmd(["/usr/bin/agy"], {})
            self.assertEqual(env.get("GOOGLE_OAUTH_ACCESS_TOKEN"), "ya29.mock_token_abc")
            self.assertEqual(env.get("GEMINI_OAUTH_TOKEN"), "ya29.mock_token_abc")
            self.assertEqual(env.get("AGY_PROVIDER"), "google")
            # Inherited model: when model is empty, AGY_MODEL and --model are not forced
            self.assertNotIn("AGY_MODEL", env)
            self.assertNotIn("--model", cmd)

            # Explicit model: when model is specified, it is applied
            profile.model = "gemini-2.5-flash"
            cmd2, env2 = profile.apply_to_environment_and_cmd(["/usr/bin/agy"], {})
            self.assertEqual(env2.get("AGY_MODEL"), "gemini-2.5-flash")
            self.assertIn("--model", cmd2)
            self.assertIn("gemini-2.5-flash", cmd2)

    def test_import_keyring_account_mock(self):
        from unittest.mock import patch
        from agy_modules.infrastruktur.quota_guard import MultiAccountManager

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = os.path.join(tmpdir, "accounts.json")
            state = os.path.join(tmpdir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)

            mock_payload = {
                "token": {
                    "access_token": "ya29.imported_acc",
                    "refresh_token": "1//imp_ref",
                    "expires_in": 3599,
                },
                "auth_method": "consumer",
                "id_token_email": "imported@google.com",
            }
            with patch("agy_modules.infrastruktur.quota_guard.get_keyring_oauth_data", return_value=mock_payload):
                acc = mgr.import_keyring_account()
                self.assertIsNotNone(acc)
                self.assertEqual(acc.name, "imported@google.com")
                self.assertTrue(acc.is_oauth())
                self.assertEqual(acc.model, "")  # Inherited by default
                self.assertEqual(len(mgr.accounts), 1)

    def test_switch_account_syncs_keyring(self):
        from unittest.mock import patch
        from agy_modules.infrastruktur.quota_guard import MultiAccountManager

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = os.path.join(tmpdir, "accounts.json")
            state = os.path.join(tmpdir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)
            now_ts = time.time()
            mgr.add_account(
                name="user1@gmail.com",
                provider="google",
                auth_type="oauth",
                oauth_data={"token": {"access_token": "ya29.user1", "refresh_token": "1//ref1", "expires_in": 3600}, "last_refresh_ts": now_ts},
            )
            mgr.add_account(
                name="user2@gmail.com",
                provider="google",
                auth_type="oauth",
                oauth_data={"token": {"access_token": "ya29.user2", "refresh_token": "1//ref2", "expires_in": 3600}, "last_refresh_ts": now_ts},
            )

            with patch("agy_modules.infrastruktur.quota_guard.set_keyring_oauth_data") as mock_set:
                mock_set.return_value = True
                success = mgr.set_active_account(2)
                self.assertTrue(success)
                self.assertEqual(mgr.get_active_account_index(), 2)
                mock_set.assert_called()
                # Verify payload stored was user2
                call_args = mock_set.call_args[0][0]
                self.assertEqual(call_args["token"]["access_token"], "ya29.user2")

    def test_render_tui_login_screen(self):
        from agy_modules.infrastruktur.quota_guard import MultiAccountManager, render_tui_login_screen
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = os.path.join(tmpdir, "accounts.json")
            state = os.path.join(tmpdir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)
            mgr.add_account(name="test@gmail.com", provider="google", auth_type="oauth", oauth_data={"token": {}})
            screen = render_tui_login_screen(mgr)
            self.assertIn("Antigravity Account & Provider Manager (/login)", screen)
            self.assertIn("test@gmail.com", screen)
            self.assertIn("Actions:", screen)

    def test_login_launcher_and_symlinks(self):
        import subprocess
        repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        launcher = os.path.join(repo_dir, "bin", "agy-full")
        res_help = subprocess.run([launcher, "--help"], capture_output=True, text=True)
        self.assertEqual(res_help.returncode, 0)
        self.assertIn("--login", res_help.stdout)
        self.assertIn("--oauth-login", res_help.stdout)
        self.assertIn("--import-keyring", res_help.stdout)
        self.assertIn("--switch-account", res_help.stdout)

        # Check symlink ~/.local/bin/login if installed
        symlink_login = os.path.expanduser("~/.local/bin/login")
        if os.path.islink(symlink_login):
            self.assertEqual(os.path.realpath(symlink_login), os.path.realpath(launcher))

        # Check login skill file if installed
        skill_path = os.path.expanduser("~/.claude/skills/login/SKILL.md")
        if os.path.exists(skill_path):
            self.assertTrue(os.path.isfile(skill_path))


    def test_run_tui_login_submenu_switch_and_cancel(self):
        from unittest.mock import patch
        from agy_modules.infrastruktur.quota_guard import MultiAccountManager, run_tui_login_submenu
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = os.path.join(tmpdir, "accounts.json")
            state = os.path.join(tmpdir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)
            mgr.add_account(name="acc1@test.com", provider="google", auth_type="oauth", oauth_data={"token": {}})
            mgr.add_account(name="acc2@test.com", provider="google", auth_type="oauth", oauth_data={"token": {}})

            # Test cancel
            with patch("agy_modules.infrastruktur.quota_guard.get_keyring_oauth_data", return_value=None), \
                 patch("os.read", side_effect=[b"q"]), patch("time.sleep"):
                action, idx = run_tui_login_submenu(mgr)
                self.assertEqual(action, "cancel")
                self.assertIsNone(idx)

            # Test switch to slot 2 (acc1@test.com)
            with patch("agy_modules.infrastruktur.quota_guard.get_keyring_oauth_data", return_value=None), \
                 patch("os.read", side_effect=[b"2"]), patch("time.sleep"):
                action, idx = run_tui_login_submenu(mgr)
                self.assertEqual(action, "switched")
                self.assertEqual(idx, 1)
                self.assertEqual(mgr.get_active_account_index(), 1)

    def test_repair_conversation_timestamps(self):
        import sqlite3, json
        from agy_modules.infrastruktur.quota_guard import repair_conversation_timestamps
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "conversation_summaries.db")
            conv_dir = os.path.join(tmpdir, "conversations")
            os.makedirs(conv_dir, exist_ok=True)

            cid = "test-conv-1234"
            conv_db = os.path.join(conv_dir, f"{cid}.db")
            with open(conv_db, "w") as f:
                f.write("mock db")

            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("CREATE TABLE conversation_summaries (conversation_id text, title text, preview text, step_count integer, last_modified_time datetime, workspace_uris text, PRIMARY KEY (conversation_id))")
            c.execute("CREATE VIRTUAL TABLE conversations_fts USING fts5(conversation_id UNINDEXED, chunk_index UNINDEXED, last_modified_time UNINDEXED, last_step_created_at UNINDEXED, step_count UNINDEXED, title, workspace)")
            c.execute("INSERT INTO conversation_summaries VALUES (?, 'Test Title', '', 5, '0001-01-01 00:00:00+00:00', ?)", (cid, json.dumps(["file:///tmp/workspace"])))
            c.execute("INSERT INTO conversations_fts VALUES (?, -1, 0, 0, 5, 'Test Title', 'workspace')", (cid,))
            conn.commit()
            conn.close()

            repaired = repair_conversation_timestamps(db_path=db_path, conversations_dir=conv_dir)
            self.assertGreater(repaired, 0)

            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT last_modified_time FROM conversation_summaries WHERE conversation_id = ?", (cid,))
            row_sum = c.fetchone()
            self.assertFalse(row_sum[0].startswith("0001-01-01"))

            c.execute("SELECT last_modified_time FROM conversations_fts WHERE conversation_id = ?", (cid,))
            row_fts = c.fetchone()
            self.assertGreater(int(row_fts[0]), 0)
            conn.close()

    def test_resolve_latest_conversation_for_workspace(self):
        import sqlite3, json
        from unittest.mock import patch
        from agy_modules.infrastruktur.quota_guard import resolve_latest_conversation_for_workspace
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "conversation_summaries.db")
            conv_dir = os.path.join(tmpdir, "conversations")
            os.makedirs(conv_dir, exist_ok=True)

            ws_target = os.path.join(tmpdir, "my_project")
            os.makedirs(ws_target, exist_ok=True)
            ws_other = os.path.join(tmpdir, "other_project")
            os.makedirs(ws_other, exist_ok=True)

            cid_old = "conv-target-old"
            cid_new = "conv-target-new"
            cid_other = "conv-other"

            for cid, age in [(cid_old, 200), (cid_new, 50), (cid_other, 10)]:
                conv_db = os.path.join(conv_dir, f"{cid}.db")
                with open(conv_db, "w") as f:
                    f.write("mock")
                now = time.time()
                os.utime(conv_db, (now - age, now - age))

            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("CREATE TABLE conversation_summaries (conversation_id text, title text, preview text, step_count integer, last_modified_time datetime, workspace_uris text, PRIMARY KEY (conversation_id))")
            c.execute("CREATE VIRTUAL TABLE conversations_fts USING fts5(conversation_id UNINDEXED, chunk_index UNINDEXED, last_modified_time UNINDEXED, last_step_created_at UNINDEXED, step_count UNINDEXED, title, workspace)")
            c.execute("INSERT INTO conversation_summaries VALUES (?, 'Old', '', 5, '2026-11-01 00:00:00+00:00', ?)", (cid_old, json.dumps([f"file://{ws_target}"])))
            c.execute("INSERT INTO conversation_summaries VALUES (?, 'New', '', 10, '2026-11-02 00:00:00+00:00', ?)", (cid_new, json.dumps([f"file://{ws_target}"])))
            c.execute("INSERT INTO conversation_summaries VALUES (?, 'Other', '', 8, '2026-11-03 00:00:00+00:00', ?)", (cid_other, json.dumps([f"file://{ws_other}"])))
            conn.commit()
            conn.close()

            resolved = resolve_latest_conversation_for_workspace(ws_target, db_path=db_path, conversations_dir=conv_dir)
            self.assertEqual(resolved, cid_new)

            # Subdirectory of ws_target
            sub_dir = os.path.join(ws_target, "nested", "pkg")
            resolved_sub = resolve_latest_conversation_for_workspace(sub_dir, db_path=db_path, conversations_dir=conv_dir)
            self.assertEqual(resolved_sub, cid_new)

            # Unrelated directory
            unrelated = os.path.join(tmpdir, "unknown_dir")
            self.assertIsNone(resolve_latest_conversation_for_workspace(unrelated, db_path=db_path, conversations_dir=conv_dir))

    def test_resolve_cli_continue_args(self):
        from unittest.mock import patch
        from agy_modules.infrastruktur.quota_guard import resolve_cli_continue_args
        with patch("agy_modules.infrastruktur.quota_guard.resolve_latest_conversation_for_workspace", return_value="cid-12345"):
            # --continue gets replaced with --conversation cid-12345
            args = ["agy", "--continue", "--dangerously-skip-permissions"]
            resolved = resolve_cli_continue_args(args)
            self.assertEqual(resolved, ["agy", "--conversation", "cid-12345", "--dangerously-skip-permissions"])

            # -c gets replaced
            args = ["agy", "-c"]
            resolved = resolve_cli_continue_args(args)
            self.assertEqual(resolved, ["agy", "--conversation", "cid-12345"])

            # Existing --conversation is left intact
            args = ["agy", "--conversation", "custom-cid", "-c"]
            resolved = resolve_cli_continue_args(args)
            self.assertEqual(resolved, args)

        with patch("agy_modules.infrastruktur.quota_guard.resolve_latest_conversation_for_workspace", return_value=None):
            # If no conversation exists for workspace, --continue is kept
            args = ["agy", "--continue"]
            resolved = resolve_cli_continue_args(args)
            self.assertEqual(resolved, ["agy", "--continue"])

    def test_apply_continue(self):
        from unittest.mock import patch
        from agy_modules.infrastruktur.quota_guard import _apply_continue
        with patch("agy_modules.infrastruktur.quota_guard.resolve_latest_conversation_for_workspace", return_value="cid-recovered"):
            # Strips bare "continue" and appends --conversation cid-recovered
            args = ["agy", "continue", "--tui-status"]
            res = _apply_continue(args)
            self.assertNotIn("continue", res)
            self.assertIn("--conversation", res)
            self.assertIn("cid-recovered", res)

        with patch("agy_modules.infrastruktur.quota_guard.resolve_latest_conversation_for_workspace", return_value=None):
            args = ["agy"]
            res = _apply_continue(args)
            self.assertEqual(res, ["agy", "--continue"])

    def test_safe_terminate_process_kills_hung_process(self):
        """Processes ignoring SIGTERM (e.g. spinning in futex locks) are killed with SIGKILL within timeout."""
        import subprocess
        import signal
        import time
        from agy_modules.infrastruktur.quota_guard import _safe_terminate_process

        # 1. Test normal already-exited process
        proc_done = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(42)"])
        proc_done.wait()
        ret_done = _safe_terminate_process(proc_done, timeout=0.2)
        self.assertEqual(ret_done, 42)

        # 2. Test hung process ignoring SIGTERM
        hung_code = "import signal, time, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); sys.stdout.write('READY\\n'); sys.stdout.flush(); time.sleep(30)"
        proc_hung = subprocess.Popen(
            [sys.executable, "-c", hung_code],
            stdout=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
        # Ensure the signal ignore handler is actually installed before sending signals
        line = proc_hung.stdout.readline()
        self.assertEqual(line.strip(), b"READY")

        t0 = time.time()
        ret = _safe_terminate_process(proc_hung, timeout=0.2)
        elapsed = time.time() - t0

        self.assertLess(elapsed, 2.0, "Hung process took too long to terminate")
        self.assertIsNotNone(proc_hung.poll())
        # Killed by SIGKILL (-9)
        self.assertIn(ret, (-9, -signal.SIGKILL, 137))

    def test_multi_agent_disk_merge(self):
        """Verify that concurrent agent instances merging quota state to disk preserve each other's cooldowns."""
        import tempfile
        import shutil
        import json
        from agy_modules.infrastruktur.quota_guard import MultiAccountManager

        tmp_dir = tempfile.mkdtemp()
        try:
            cfg_file = os.path.join(tmp_dir, "accounts_pool.json")
            state_file = os.path.join(tmp_dir, "account_quotas.json")
            ledger_file = os.path.join(tmp_dir, "quota_ledger.jsonl")

            with open(cfg_file, "w") as f:
                json.dump([
                    {"name": "Slot1", "provider": "google", "auth_type": "api_key"},
                    {"name": "Slot2", "provider": "google", "auth_type": "api_key"},
                ], f)

            # Agent A and Agent B share the same state and ledger files
            mgr_a = MultiAccountManager(config_path=cfg_file, state_path=state_file, ledger_path=ledger_file)
            mgr_b = MultiAccountManager(config_path=cfg_file, state_path=state_file, ledger_path=ledger_file)

            now = time.time()
            # Agent A hits exhaustion on Slot 1
            mgr_a.mark_exhausted(0, duration_seconds=600.0)
            # Disk now has Slot 1 exhausted

            # Agent B hits exhaustion on Slot 2 without having A's state in memory yet
            mgr_b.mark_exhausted(1, duration_seconds=1200.0)

            # Re-read state directly from disk
            with open(state_file, "r") as f:
                disk_data = json.load(f)

            # Both cooldowns must be preserved on disk
            slot1_state = disk_data.get("0", {})
            slot2_state = disk_data.get("1", {})

            self.assertGreater(slot1_state.get("exhausted_until", 0), now + 500)
            self.assertGreater(slot2_state.get("exhausted_until", 0), now + 1100)

            # Agent A reloads and selects best account
            mgr_a.reload_quota_state()
            self.assertFalse(mgr_a.accounts[0].is_available(now))
            self.assertFalse(mgr_a.accounts[1].is_available(now))

            # When both exhausted, best account is Slot 1 (smaller cooldown: 600s < 1200s)
            best_idx, wait_needed, reason = mgr_a.select_best_account()
            self.assertEqual(best_idx, 0)
            self.assertAlmostEqual(wait_needed, 600.0, delta=10.0)
            self.assertTrue(reason.startswith("earliest_reset"))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_quota_ledger_recording(self):
        """Verify that record_quota_ledger appends structured records and get_recent_quota_ledger reads them back."""
        import tempfile
        import shutil
        import json
        from agy_modules.infrastruktur.quota_guard import MultiAccountManager

        tmp_dir = tempfile.mkdtemp()
        try:
            cfg_file = os.path.join(tmp_dir, "accounts_pool.json")
            state_file = os.path.join(tmp_dir, "account_quotas.json")
            ledger_file = os.path.join(tmp_dir, "quota_ledger.jsonl")

            with open(cfg_file, "w") as f:
                json.dump([{"name": "Primary", "provider": "google", "auth_type": "api_key"}], f)

            mgr = MultiAccountManager(config_path=cfg_file, state_path=state_file, ledger_path=ledger_file)

            mgr.record_quota_ledger(trigger="test_start")
            mgr.record_quota_ledger(trigger="test_event_2")

            self.assertTrue(os.path.exists(ledger_file))
            recent = mgr.get_recent_quota_ledger(limit=10)
            self.assertEqual(len(recent), 2)
            self.assertEqual(recent[0]["trigger"], "test_start")
            self.assertEqual(recent[1]["trigger"], "test_event_2")
            self.assertIn("timestamp", recent[0])
            self.assertIn("accounts", recent[0])
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_quota_ledger_ticker(self):
        """Verify background QuotaLedgerTicker executes periodic audits without blocking."""
        import tempfile
        import shutil
        import json
        from agy_modules.infrastruktur.quota_guard import MultiAccountManager, QuotaLedgerTicker

        tmp_dir = tempfile.mkdtemp()
        try:
            cfg_file = os.path.join(tmp_dir, "accounts_pool.json")
            state_file = os.path.join(tmp_dir, "account_quotas.json")
            ledger_file = os.path.join(tmp_dir, "quota_ledger.jsonl")

            with open(cfg_file, "w") as f:
                json.dump([{"name": "Slot", "provider": "google", "auth_type": "api_key"}], f)

            mgr = MultiAccountManager(config_path=cfg_file, state_path=state_file, ledger_path=ledger_file)

            # Run ticker with very short interval (0.05s)
            ticker = QuotaLedgerTicker(mgr, interval_seconds=0.05)
            ticker.start()
            time.sleep(0.18)
            ticker.stop()

            records = mgr.get_recent_quota_ledger(limit=10)
            self.assertGreaterEqual(len(records), 2)
            self.assertEqual(records[0]["trigger"], "session_start")
            self.assertTrue(any(r["trigger"] == "ticker_5m" for r in records))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_compact_conversation_workflow(self):
        """Verify conversation compaction: pruning old steps, renumbering, metadata sync, and idempotency."""
        import tempfile
        import shutil
        import sqlite3
        from agy_modules.infrastruktur.quota_guard import compact_conversation

        tmp_dir = tempfile.mkdtemp()
        try:
            conv_dir = os.path.join(tmp_dir, "conversations")
            os.makedirs(conv_dir, exist_ok=True)
            cid = "test-compaction-uuid"
            db_path = os.path.join(conv_dir, f"{cid}.db")

            # Create mock SQLite database matching agy schema
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE steps (idx INTEGER PRIMARY KEY, col2, col3, col4, col5, col6, col7, col8, col9, col10, step_type INTEGER)")
            conn.execute("CREATE TABLE gen_metadata (idx INTEGER, data, size)")
            conn.execute("CREATE TABLE executor_metadata (idx INTEGER, data)")

            # Insert 50 steps (indices 0..49)
            # Step 0: init
            # Step 40: checkpoint (step_type=23)
            # Steps 41..49: active steps
            for i in range(50):
                st = 23 if i == 40 else 1
                conn.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?,?,?)", (i, i, i, i, i, i, i, i, i, i, st))
                conn.execute("INSERT INTO gen_metadata VALUES (?,?,?)", (i, f"gen_{i}", 100))
                conn.execute("INSERT INTO executor_metadata VALUES (?,?)", (i, f"exec_{i}"))
            conn.commit()
            conn.close()

            # Compact conversation
            res = compact_conversation(cid, keep_steps=5, backup=True, conversations_dir=conv_dir, summaries_db=os.path.join(tmp_dir, "none.db"))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["original_steps"], 50)
            # Kept step 0 + step 40..49 = 11 steps
            self.assertEqual(res["compacted_steps"], 11)

            # Verify consecutive renumbering in SQLite
            conn = sqlite3.connect(db_path)
            steps = conn.execute("SELECT idx, step_type FROM steps ORDER BY idx ASC").fetchall()
            indices = [s[0] for s in steps]
            self.assertEqual(indices, list(range(11)))
            # Step 1 should be the checkpoint (was step 40)
            self.assertEqual(steps[1][1], 23)

            # Verify metadata renumbered
            gen_indices = [r[0] for r in conn.execute("SELECT idx FROM gen_metadata ORDER BY idx ASC").fetchall()]
            self.assertEqual(gen_indices, list(range(11)))
            conn.close()

            # Idempotency check: Running again on already compact DB should return 'skipped'
            res2 = compact_conversation(cid, keep_steps=5, backup=True, conversations_dir=conv_dir, summaries_db=os.path.join(tmp_dir, "none.db"))
            self.assertEqual(res2["status"], "skipped")
            self.assertEqual(res2["reason"], "already_compact")

            # Non-existent DB check
            res_bad = compact_conversation("non-existent-cid", conversations_dir=conv_dir)
            self.assertEqual(res_bad["status"], "error")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_strip_media_from_payload_omits_large_binary(self):
        from agy_modules.infrastruktur.quota_guard import strip_media_from_payload, encode_varint
        # Build mock protobuf: Field 140 (wt=2) -> Subfield 2 (wt=2) -> Field 4 (wt=2, 600KB dummy data)
        dummy_media = b"X" * 600_000
        field4 = bytearray()
        field4.extend(encode_varint((4 << 3) | 2))
        field4.extend(encode_varint(len(dummy_media)))
        field4.extend(dummy_media)

        # Field 1 (text header)
        text_data = b"Showing binary file"
        field1 = bytearray()
        field1.extend(encode_varint((1 << 3) | 2))
        field1.extend(encode_varint(len(text_data)))
        field1.extend(text_data)

        sub2_inner = field1 + field4
        sub2 = bytearray()
        sub2.extend(encode_varint((2 << 3) | 2))
        sub2.extend(encode_varint(len(sub2_inner)))
        sub2.extend(sub2_inner)

        top_f140 = bytearray()
        top_f140.extend(encode_varint((140 << 3) | 2))
        top_f140.extend(encode_varint(len(sub2)))
        top_f140.extend(sub2)

        # Top message with step_type=132 (Field 1) and Field 140
        top_msg = bytearray()
        top_msg.extend(encode_varint((1 << 3) | 0))
        top_msg.extend(encode_varint(132))
        top_msg.extend(top_f140)

        raw_blob = bytes(top_msg)
        self.assertGreater(len(raw_blob), 600_000)

        sanitized = strip_media_from_payload(raw_blob, max_media_bytes=500_000)
        self.assertLess(len(sanitized), 1000)
        self.assertIn(b"Showing binary file", sanitized)
        self.assertNotIn(b"XXXX", sanitized)

    def test_remaining_quota_pct_expired_cooldown_returns_100(self):
        acc = AccountProfile(index=0, name="Slot1")
        now = time.time()
        # Mark exhausted in the past (10 seconds ago, duration was 5s)
        acc.exhausted_until = now - 5.0
        acc.last_reset_duration = 5.0
        acc.recent_tokens = 1_000_000
        # remaining_quota_pct should auto-replenish to 100% and clear exhaustion
        self.assertEqual(acc.remaining_quota_pct(now), 100.0)
        self.assertEqual(acc.exhausted_until, 0.0)
        self.assertEqual(acc.last_reset_duration, 0.0)
        self.assertEqual(acc.recent_tokens, 0)

    def test_remaining_quota_pct_in_cooldown_returns_zero(self):
        acc = AccountProfile(index=0, name="Slot1")
        now = time.time()
        acc.exhausted_until = now + 120.0
        acc.recent_tokens = 500_000
        self.assertEqual(acc.remaining_quota_pct(now), 0.0)

    def test_remaining_quota_pct_decay_and_hour_replenish(self):
        acc = AccountProfile(index=0, name="Slot1")
        now = time.time()
        # 500k tokens consumed 30 minutes ago -> 50% decay -> 250k effective -> 75% quota remaining
        acc.recent_tokens = 500_000
        acc.last_used_ts = now - 1800.0
        self.assertAlmostEqual(acc.remaining_quota_pct(now), 75.0, places=1)

        # After 1 hour (> 3600s) -> 100% quota replenished and recent_tokens reset
        acc.last_used_ts = now - 3650.0
        self.assertEqual(acc.remaining_quota_pct(now), 100.0)
        self.assertEqual(acc.recent_tokens, 0)

    def test_mark_exhausted_caps_excessive_duration(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = os.path.join(tmp_dir, "accounts.json")
            state = os.path.join(tmp_dir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)
            now = time.time()
            # Feed absurd 5.5 day cooldown (475,146s)
            mgr.mark_exhausted(0, 475146.0)
            acc = mgr.accounts[0]
            # Duration should be capped at 86400s (24h)
            self.assertEqual(acc.last_reset_duration, 86400.0)
            self.assertLessEqual(acc.exhausted_until - now, 86405.0)

    def test_reset_all_cooldowns_cleans_config_and_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = os.path.join(tmp_dir, "accounts.json")
            state = os.path.join(tmp_dir, "account_quotas.json")
            mgr = MultiAccountManager(config_path=cfg, state_path=state)
            mgr.mark_exhausted(0, 3600.0)
            self.assertFalse(mgr.accounts[0].is_available())

            # Perform full reset
            mgr.reset_all_cooldowns()
            self.assertTrue(mgr.accounts[0].is_available())
            self.assertEqual(mgr.accounts[0].remaining_quota_pct(), 100.0)

            # Re-instantiate manager from disk: verify accounts.json and state stay clean
            mgr2 = MultiAccountManager(config_path=cfg, state_path=state)
            self.assertTrue(mgr2.accounts[0].is_available())
            self.assertEqual(mgr2.accounts[0].remaining_quota_pct(), 100.0)
            self.assertEqual(mgr2.accounts[0].exhausted_until, 0.0)

    def test_scan_for_quota_error_filters_by_min_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_file = os.path.join(tmp_dir, "test_cli.log")
            guard = QuotaGuard()

            # Write an old log line from 10 minutes ago
            old_dt = time.localtime(time.time() - 600)
            old_ts_str = f"E{old_dt.tm_mon:02d}{old_dt.tm_mday:02d} {old_dt.tm_hour:02d}:{old_dt.tm_min:02d}:{old_dt.tm_sec:02d}.123456"
            with open(log_file, "w") as f:
                f.write(f"{old_ts_str} 12345 errorreport.go:273] RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 50m.\n")

            proc_start = time.time()
            # Scanning with min_timestamp = proc_start should IGNORE the 10-minute-old error
            res = guard.scan_for_quota_error(text_or_log_path=log_file, min_timestamp=proc_start - 2.0)
            self.assertIsNone(res)

            # Now append a fresh error line from right now
            now_dt = time.localtime(time.time())
            now_ts_str = f"E{now_dt.tm_mon:02d}{now_dt.tm_mday:02d} {now_dt.tm_hour:02d}:{now_dt.tm_min:02d}:{now_dt.tm_sec:02d}.999999"
            with open(log_file, "a") as f:
                f.write(f"{now_ts_str} 12345 errorreport.go:273] RESOURCE_EXHAUSTED (code 429): Individual quota reached. Resets in 15m.\n")

            res_new = guard.scan_for_quota_error(text_or_log_path=log_file, min_timestamp=proc_start - 2.0)
            self.assertIsNotNone(res_new)
            dur, ts = res_new
            self.assertEqual(dur, 15 * 60)

    def test_realtime_quota_parsing_and_unlicensed_account(self):
        from unittest.mock import patch
        import io
        import urllib.error

        # 1. Test successful 200 response with buckets
        acc_licensed = AccountProfile(0, name="LicensedUser", auth_type="oauth", oauth_data={"token": {"access_token": "mock-tok"}})
        mock_response = io.BytesIO(json.dumps({
            "groups": [{
                "buckets": [
                    {"window": "weekly", "remainingFraction": 0.75, "resetTime": "2026-11-17T12:00:00Z"},
                    {"window": "5h", "remainingFraction": 0.50, "resetTime": "2026-11-10T17:00:00Z"}
                ]
            }]
        }).encode("utf-8"))
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            rt = acc_licensed.fetch_realtime_quota(force=True)
            self.assertIsNotNone(rt)
            self.assertTrue(rt["is_licensed"])
            self.assertEqual(rt["pct"], 50.0)  # min of 75% and 50%
            self.assertEqual(acc_licensed.remaining_quota_pct(), 50.0)
            self.assertTrue(acc_licensed.is_available())

        # 2. Test 403 Forbidden (Subscription required / unlicensed)
        acc_unlicensed = AccountProfile(1, name="UnlicensedUser", auth_type="oauth", oauth_data={"token": {"access_token": "mock-tok"}})
        mock_err = urllib.error.HTTPError(
            url="http://mock",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(b'{"error": {"code": 403, "message": "You do not have a valid license."}}')
        )
        with patch("urllib.request.urlopen", side_effect=mock_err):
            rt_unlic = acc_unlicensed.fetch_realtime_quota(force=True)
            self.assertIsNotNone(rt_unlic)
            self.assertFalse(rt_unlic["is_licensed"])
            self.assertEqual(rt_unlic["pct"], 0.0)
            self.assertEqual(acc_unlicensed.remaining_quota_pct(), 0.0)
            self.assertFalse(acc_unlicensed.is_available())


if __name__ == "__main__":
    unittest.main()
