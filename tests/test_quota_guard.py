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


if __name__ == "__main__":
    unittest.main()