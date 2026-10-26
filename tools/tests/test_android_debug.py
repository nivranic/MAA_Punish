"""PC protocol/USB boundary tests; these do not prove Android/device behavior."""

from __future__ import annotations

import json
import io
import hashlib
import shlex
import tempfile
from pathlib import Path
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import android_debug as debug

PACKAGE = "com.example.maa"
DEVICE = "physical-123"
TOKEN = "1" * 32
CHALLENGE = "2" * 32


def hello():
    return {"version": 1, "package": PACKAGE, "app_instance": TOKEN,
            "challenge": CHALLENGE, "max_message_bytes": 16384, "audit_logging_supported": True}


def state():
    return {"version": 1, "package": PACKAGE, "app_instance": TOKEN, "challenge": CHALLENGE,
            "phase": "idle", "session_id": "", "host_id": "", "last_sequence": 0,
            "lease_remaining_ms": 0, "request_id": "", "result": "", "reason": "",
            "screen_saver_status": "inactive", "audit_logging": False}


class AdbProcess:
    def __init__(self):
        self.calls = []
        self.files = {}
        self.detected = DEVICE
        self.disconnect_after_push = False
        self.usb_error = b""
        self.device_state = b"device\n"

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        self.assert_bounded(kwargs)
        if args[1:] == ["-d", "get-serialno"]:
            if self.usb_error:
                return subprocess.CompletedProcess(args, 1, b"", self.usb_error)
            return subprocess.CompletedProcess(args, 0, (self.detected + "\n").encode(), b"")
        self.assert_target(args)
        operation = args[3:]
        output = b""
        if operation == ["get-state"]:
            output = self.device_state
        elif operation[0] == "push":
            self.files[operation[2]] = Path(operation[1]).read_bytes()
            if self.disconnect_after_push:
                self.detected = "other-device"
        elif operation[:2] == ["shell", "mv"]:
            self.files[operation[3]] = self.files.pop(operation[2])
        elif operation[:3] == ["shell", "rm", "-f"]:
            self.files.pop(operation[3], None)
        elif operation[:3] == ["shell", "-T", "head"]:
            output = self.files.get(operation[-1])
            if output is None:
                return subprocess.CompletedProcess(args, 1, b"", b"No such file")
            output = output[:int(operation[4])]
        elif operation[:4] == ["shell", "-T", "sh", "-c"]:
            assert operation[4] == shlex.quote(debug.HEARTBEAT_SCRIPT)
            assert operation[5] == "maa-heartbeat"
            temporary, destination, status = operation[6:]
            self.files[temporary] = kwargs["input"]
            self.files[destination] = self.files.pop(temporary)
            output = self.files[status][:debug.MAX_MESSAGE_BYTES + 1]
        else:
            raise AssertionError(operation)
        return subprocess.CompletedProcess(args, 0, output, b"")

    @staticmethod
    def assert_bounded(kwargs):
        assert 0 < kwargs["timeout"] <= 5
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True

    @staticmethod
    def assert_target(args):
        assert args[1:3] == ["-s", DEVICE]


class FakePhone:
    package = PACKAGE

    def __init__(self):
        self.snapshot = state()
        self.messages = []

    def write(self, message, *, on_publish=None):
        if on_publish is not None:
            on_publish(debug.time.monotonic())
        self.messages.append(message)
        self.snapshot.update(
            request_id=message["request_id"], result="accepted", last_sequence=message["sequence"],
            session_id=message["session_id"], host_id=message["host_id"], phase="active",
            audit_logging=message["audit_logging"],
        )
        if message["operation"] == "close":
            self.snapshot.update(phase="closed", challenge="3" * 32, session_id="", host_id="", audit_logging=False)

    def read(self, name):
        return hello() if name == "hello.json" else dict(self.snapshot)

    def exchange_heartbeat(self, message, deadline, timings, *, on_publish=None):
        self.write(message, on_publish=on_publish)
        return self.read("state.json")

    def read_heartbeat_state(self, deadline, timings):
        return self.read("state.json")


class UsbBoundaryTest(unittest.TestCase):
    def make_adb(self):
        process = AdbProcess()
        return debug.UsbAdb("adb", DEVICE, PACKAGE, process), process

    def test_rejects_wireless_emulator_and_shell_metacharacters(self):
        for serial in ("192.0.2.1:5555", "emulator-5554", "x;reboot", "", "$(anything)"):
            with self.subTest(serial=serial), self.assertRaises(debug.DebugError):
                debug.UsbAdb("adb", serial, PACKAGE)
        for package in ("com.x/../../", "com.x;id", "invalid", "com.x\n"):
            with self.subTest(package=package), self.assertRaises(debug.DebugError):
                debug.UsbAdb("adb", DEVICE, package)

    def test_target_mismatch_refuses_all_writes(self):
        adb, process = self.make_adb()
        process.detected = "different"
        with self.assertRaisesRegex(debug.DebugError, "differs"):
            adb.write({"operation": "open"})
        self.assertEqual([["adb", "-d", "get-serialno"]], process.calls)

    def test_zero_multiple_and_unauthorized_usb_never_reach_mailbox(self):
        for error in (b"no devices found", b"more than one device/emulator", b"device unauthorized"):
            adb, process = self.make_adb()
            process.usb_error = error
            with self.subTest(error=error), self.assertRaises(debug.DebugError):
                adb.write({"operation": "open"})
            self.assertEqual([["adb", "-d", "get-serialno"]], process.calls)
        for device_state in (b"offline\n", b"unauthorized\n", b"unknown\n"):
            adb, process = self.make_adb()
            process.device_state = device_state
            with self.subTest(device_state=device_state), self.assertRaises(debug.DebugError):
                adb.read("state.json")
            self.assertFalse(any("head" in call or "push" in call for call in process.calls))

    def test_transport_loss_during_upload_does_not_promote(self):
        adb, process = self.make_adb()
        process.disconnect_after_push = True
        with self.assertRaises(debug.DebugError):
            adb.write({"operation": "open"})
        self.assertFalse(any("mv" in call for call in process.calls))
        self.assertFalse(any(path.endswith("/request.json") for path in process.files))

    def test_publish_uses_same_directory_temp_and_only_cleans_own_file(self):
        adb, process = self.make_adb()
        adb.write({"operation": "heartbeat", "sequence": 5})
        destination = adb.root + "/request.json"
        self.assertEqual({"operation": "heartbeat", "sequence": 5}, json.loads(process.files[destination]))
        self.assertEqual([destination], list(process.files))
        move = next(call for call in process.calls if "mv" in call)
        self.assertEqual(Path(move[-2]).parent.as_posix(), Path(move[-1]).parent.as_posix())
        self.assertFalse(any("mkdir" in call or "-r" in call for call in process.calls))

    def test_close_has_independent_mailbox_and_does_not_overwrite_heartbeat(self):
        adb, process = self.make_adb()
        adb.write({"operation": "heartbeat"})
        adb.write({"operation": "close"})
        self.assertIn(adb.root + "/request.json", process.files)
        self.assertIn(adb.root + "/close.json", process.files)

    def test_oversized_request_never_uploads_or_deletes_files(self):
        adb, process = self.make_adb()
        with self.assertRaisesRegex(debug.DebugError, "exceeds"):
            adb.write({"operation": "open", "padding": "x" * debug.MAX_MESSAGE_BYTES})
        self.assertFalse(any("push" in call or "shell" in call for call in process.calls))

    def test_missing_mailbox_has_explicit_unavailable_error(self):
        adb, process = self.make_adb()
        with self.assertRaises(debug.MailboxUnavailable):
            adb.read("hello.json")
        self.assertFalse(any("mkdir" in call or "push" in call for call in process.calls))

    def test_reads_are_bound_and_limited_and_never_renew(self):
        adb, process = self.make_adb()
        process.files[adb.root + "/state.json"] = json.dumps(state()).encode()
        self.assertEqual(state(), adb.read("state.json"))
        self.assertEqual(["adb", "-d", "get-serialno"], process.calls[0])
        self.assertEqual("16385", process.calls[-1][-2])
        self.assertFalse(any("push" in call for call in process.calls))
        process.files[adb.root + "/state.json"] = b"x" * 16385
        with self.assertRaisesRegex(debug.DebugError, "exceeds"):
            adb.read("state.json")

    def test_only_fixed_mailbox_files_are_readable(self):
        adb, _ = self.make_adb()
        with self.assertRaises(debug.DebugError):
            adb.read("../pi/config.json")

    def test_subprocess_timeout_is_controlled_failure(self):
        def timed_out(*args, **kwargs):
            raise subprocess.TimeoutExpired(args, 5)
        adb = debug.UsbAdb("adb", DEVICE, PACKAGE, timed_out)
        with self.assertRaisesRegex(debug.DebugError, "TimeoutExpired"):
            adb.verify()

    def test_missing_adb_executable_is_controlled_failure(self):
        def unavailable(*args, **kwargs):
            raise FileNotFoundError("missing adb")
        adb = debug.UsbAdb("missing-adb", DEVICE, PACKAGE, unavailable)
        with self.assertRaisesRegex(debug.DebugError, "FileNotFoundError"):
            adb.verify()

    def test_heartbeat_uses_three_bounded_calls_and_stdin_with_atomic_fixed_paths(self):
        adb, process = self.make_adb()
        process.files[adb.root + "/state.json"] = json.dumps(state()).encode()
        message = {"operation": "heartbeat", "padding": "$(reboot); 'quoted'"}
        timings = []
        self.assertEqual(state(), adb.exchange_heartbeat(message, debug.time.monotonic() + 3, timings))
        self.assertEqual(3, len(process.calls))
        self.assertEqual(["usb_serial", "usb_state", "mailbox_exchange"], [name for name, _ in timings])
        shell = process.calls[-1]
        self.assertEqual(["adb", "-d", "get-serialno"], process.calls[0])
        self.assertEqual(["adb", "-s", DEVICE, "get-state"], process.calls[1])
        self.assertEqual(["adb", "-s", DEVICE, "shell", "-T", "sh", "-c"], shell[:7])
        self.assertNotIn(message["padding"], " ".join(shell))
        self.assertEqual(message, json.loads(process.files[adb.root + "/request.json"]))
        self.assertIn('trap', debug.HEARTBEAT_SCRIPT)
        self.assertIn('mv "$1" "$2"', debug.HEARTBEAT_SCRIPT)
        self.assertIn('mv "$1" "$2" && sleep 0.3 && head', debug.HEARTBEAT_SCRIPT)
        self.assertIn('head -c 16385 "$3"', debug.HEARTBEAT_SCRIPT)
        self.assertNotIn("mkdir", debug.HEARTBEAT_SCRIPT)
        self.assertEqual(adb.root, Path(shell[-3]).parent.as_posix())
        self.assertFalse(any(name.endswith(".tmp") for name in process.files))

    def test_heartbeat_cannot_publish_on_mismatched_offline_or_ambiguous_usb(self):
        for change in ({"detected": "other-device"}, {"device_state": b"offline\n"},
                       {"usb_error": b"more than one device/emulator"}):
            adb, process = self.make_adb()
            for key, value in change.items():
                setattr(process, key, value)
            with self.subTest(change=change), self.assertRaises(debug.DebugError):
                adb.exchange_heartbeat({"operation": "heartbeat"}, debug.time.monotonic() + 3, [])
            self.assertFalse(any("shell" in call for call in process.calls))

    def test_heartbeat_cannot_write_close_reload_or_oversized_requests(self):
        for message in ({"operation": "close"}, {"operation": "apply"},
                        {"operation": "heartbeat", "padding": "x" * debug.MAX_MESSAGE_BYTES}):
            adb, process = self.make_adb()
            with self.subTest(message=message["operation"]), self.assertRaises(debug.DebugError):
                adb.exchange_heartbeat(message, debug.time.monotonic() + 3, [])
            self.assertEqual([], process.calls)

    def test_heartbeat_budget_includes_usb_verification_and_bounds_each_remaining_call(self):
        clock = [0.0]
        limits = []
        process = AdbProcess()
        def delayed_process(*args, **kwargs):
            limits.append(kwargs["timeout"])
            clock[0] += 2
            return process(*args, **kwargs)
        adb = debug.UsbAdb("adb", DEVICE, PACKAGE, delayed_process)
        timings = []
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                self.assertRaisesRegex(debug.DebugError, "operation budget"):
            adb.exchange_heartbeat({"operation": "heartbeat"}, 3, timings)
        self.assertEqual([3, 1], limits)
        self.assertEqual([("usb_serial", 2), ("usb_state", 2)], timings)
        self.assertFalse(any("shell" in call for call in process.calls))

    def test_heartbeat_readback_remains_bounded_and_usb_bound(self):
        adb, process = self.make_adb()
        process.files[adb.root + "/state.json"] = b"x" * (debug.MAX_MESSAGE_BYTES + 1)
        with self.assertRaisesRegex(debug.DebugError, "exceeds"):
            adb.read_heartbeat_state(debug.time.monotonic() + 3, [])
        self.assertEqual(3, len(process.calls))
        self.assertEqual("16385", process.calls[-1][-2])
        self.assertFalse(any("push" in call or "mv" in call for call in process.calls))

    def test_open_publication_start_precedes_promotion_and_excludes_cleanup(self):
        clock = [0.0]
        process = AdbProcess()
        publications = []
        def delayed_process(*args, **kwargs):
            clock[0] += 0.1
            return process(*args, **kwargs)
        adb = debug.UsbAdb("adb", DEVICE, PACKAGE, delayed_process)
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]):
            adb.write({"operation": "open"},
                      on_publish=lambda started: publications.append((started, len(process.calls))))
        self.assertEqual(1, len(publications))
        started, call_index = publications[0]
        self.assertAlmostEqual(0.5, started)
        self.assertEqual(["shell", "mv"], process.calls[call_index][3:5])
        self.assertGreater(clock[0], started)

    def test_overlong_single_usb_call_never_reaches_publication(self):
        clock = [0.0]
        process = AdbProcess()
        publications = []
        def delayed_process(*args, **kwargs):
            # Windows process creation/timeout cleanup may finish after timeout.
            clock[0] += 5.797
            return process(*args, **kwargs)
        adb = debug.UsbAdb("adb", DEVICE, PACKAGE, delayed_process)
        timings = []
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                self.assertRaisesRegex(debug.DebugError, "operation budget"):
            adb.exchange_heartbeat({"operation": "heartbeat"}, 5, timings,
                                   on_publish=publications.append)
        self.assertEqual([["adb", "-d", "get-serialno"]], process.calls)
        self.assertEqual([], publications)
        self.assertEqual([("usb_serial", 5.797)], timings)

    def test_late_mailbox_result_is_rejected_before_decoding_or_reading_again(self):
        clock = [0.0]
        process = AdbProcess()
        adb = debug.UsbAdb("adb", DEVICE, PACKAGE)
        process.files[adb.root + "/state.json"] = b"invalid late JSON"
        def delayed_process(args, **kwargs):
            if "maa-heartbeat" in args:
                clock[0] = 5.1
            return process(args, **kwargs)
        adb.run = delayed_process
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                self.assertRaisesRegex(debug.DebugError, "operation budget"):
            adb.exchange_heartbeat({"operation": "heartbeat"}, 5, [])
        self.assertEqual(3, len(process.calls))
        self.assertEqual(1, sum("maa-heartbeat" in call for call in process.calls))


class ProtocolClientTest(unittest.TestCase):
    def test_audit_logging_requires_explicit_flag_and_is_confirmed_by_phone(self):
        for enabled in (False, True):
            phone = FakePhone()
            session = debug.Session(phone, hello(), audit_logging=enabled)
            self.assertEqual(enabled, session.send("open")["audit_logging"])
            self.assertEqual(enabled, session.send("heartbeat")["audit_logging"])
            debug.close_session(phone)
            self.assertEqual([enabled] * 3, [message["audit_logging"] for message in phone.messages])
            self.assertFalse(phone.snapshot["audit_logging"])

    def test_old_or_unsupported_audit_handshake_is_refused_before_writes(self):
        old = hello()
        old.pop("audit_logging_supported")
        for greeting in (old, {**hello(), "audit_logging_supported": False},
                         {**hello(), "audit_logging_supported": 1}):
            phone = FakePhone()
            with self.subTest(greeting=greeting), self.assertRaises(debug.DebugError):
                debug.Session(phone, greeting, audit_logging=True)
            self.assertEqual([], phone.messages)

    def test_audit_mode_mismatch_in_open_or_heartbeat_is_explicit_failure(self):
        class WrongAuditPhone(FakePhone):
            def write(self, message, **kwargs):
                super().write(message, **kwargs)
                self.snapshot["audit_logging"] = False
        with self.assertRaisesRegex(debug.DebugError, "audit mode"):
            debug.Session(WrongAuditPhone(), hello(), audit_logging=True).send("open")
        phone = FakePhone()
        session = debug.Session(phone, hello(), audit_logging=True)
        session.send("open")
        phone.snapshot["audit_logging"] = False
        phone.exchange_heartbeat = lambda *_, **_kwargs: dict(phone.snapshot)
        with self.assertRaisesRegex(debug.DebugError, "audit mode"):
            session.send("heartbeat")

    def test_session_cli_passes_explicit_audit_choice_defaulting_to_off(self):
        arguments = ["--adb", "adb", "--serial", DEVICE, "--package", PACKAGE, "session"]
        for flags, enabled in (([], False), (["--audit-logging"], True)):
            with patch.object(debug, "UsbAdb") as adb, patch.object(debug, "run_session") as run:
                self.assertEqual(0, debug.main(arguments + flags))
                run.assert_called_once_with(adb.return_value, 60, audit_logging=enabled)

    def test_audit_status_requires_boolean_and_inactive_sessions_are_off(self):
        for invalid in (None, 0, 1, "true", [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(debug.DebugError):
                debug.validate_state({**state(), "audit_logging": invalid}, PACKAGE)
        with self.assertRaises(debug.DebugError):
            debug.validate_state({**state(), "audit_logging": True}, PACKAGE)

    def test_hello_and_status_reject_malformed_or_cross_package_data(self):
        for value in ({}, {**hello(), "version": True}, {**hello(), "package": "com.other.app"},
                      {**hello(), "challenge": "bad"}, {**hello(), "extra": 1}):
            with self.subTest(value=value), self.assertRaises(debug.DebugError):
                debug.validate_hello(value, PACKAGE)
        for value in ({}, {**state(), "last_sequence": True}, {**state(), "phase": "active"},
                      {**state(), "lease_remaining_ms": -1}, {**state(), "host_id": []}):
            with self.subTest(value=value), self.assertRaises(debug.DebugError):
                debug.validate_state(value, PACKAGE)

    def test_strict_bounded_json(self):
        for raw in (b"[]", b"not JSON", b"\xff", b"x" * 16385):
            with self.subTest(raw=raw[:30]), self.assertRaises(debug.DebugError):
                debug.decode_object(raw)

    def test_bounded_json_accepts_exact_limit_and_rejects_one_more_byte(self):
        prefix, suffix = b'{"padding":"', b'"}'
        raw = prefix + b"x" * (debug.MAX_MESSAGE_BYTES - len(prefix) - len(suffix)) + suffix
        self.assertEqual(debug.MAX_MESSAGE_BYTES, len(raw))
        self.assertIn("padding", debug.decode_object(raw))
        with self.assertRaises(debug.DebugError):
            debug.decode_object(raw + b" ")

    def test_corrupt_status_field_types_fail_without_type_errors(self):
        for key in ("phase", "result", "reason", "screen_saver_status", "app_instance", "challenge", "session_id", "host_id", "request_id"):
            for invalid in ([], {}, None, 5, True):
                with self.subTest(key=key, invalid=invalid), self.assertRaises(debug.DebugError):
                    debug.validate_state({**state(), key: invalid}, PACKAGE)
        for key in ("last_sequence", "lease_remaining_ms"):
            for invalid in (None, "1", 1.0, True, -1):
                with self.subTest(key=key, invalid=invalid), self.assertRaises(debug.DebugError):
                    debug.validate_state({**state(), key: invalid}, PACKAGE)

    def test_status_requires_exact_schema_and_correct_identity(self):
        for invalid in (
            {**state(), "version": False}, {**state(), "version": 2},
            {**state(), "package": "com.other.app"}, {**state(), "extra": "unexpected"},
            {**state(), "phase": "active", "session_id": "1" * 32},
            {**state(), "phase": "active", "host_id": "1" * 32},
        ):
            with self.subTest(value=invalid), self.assertRaises(debug.DebugError):
                debug.validate_state(invalid, PACKAGE)
        self.assertEqual(state(), debug.validate_state(state(), PACKAGE))

    def test_envelopes_bind_package_challenge_session_and_increasing_sequence(self):
        phone = FakePhone()
        session = debug.Session(phone, hello())
        session.send("open")
        session.send("heartbeat")
        session.send("close")
        self.assertEqual([1, 2, 0], [message["sequence"] for message in phone.messages])
        self.assertEqual(1, len({message["host_id"] for message in phone.messages}))
        self.assertEqual(1, len({message["session_id"] for message in phone.messages}))
        self.assertEqual(3, len({message["request_id"] for message in phone.messages}))
        for message in phone.messages:
            self.assertEqual(CHALLENGE, message["challenge"])
            self.assertEqual(TOKEN, message["app_instance"])
            self.assertEqual(PACKAGE, message["package"])

    def test_unknown_operation_never_reaches_phone(self):
        phone = FakePhone()
        with self.assertRaises(debug.DebugError):
            debug.Session(phone, hello()).send("shell")
        self.assertEqual([], phone.messages)

    def test_rejected_open_is_reported_instead_of_starting_a_session(self):
        class RejectingPhone(FakePhone):
            def write(self, message, **kwargs):
                super().write(message, **kwargs)
                self.snapshot.update(result="rejected", reason="session_busy")
        phone = RejectingPhone()
        with self.assertRaisesRegex(debug.DebugError, "session_busy"):
            debug.Session(phone, hello()).send("open")

    def test_unacknowledged_request_times_out_without_real_wait(self):
        class SilentPhone(FakePhone):
            def write(self, message, *, on_publish=None):
                if on_publish is not None:
                    on_publish(debug.time.monotonic())
                self.messages.append(message)
        phone = SilentPhone()
        now = [0.0]
        def advance(seconds):
            now[0] += seconds
        with patch.object(debug.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(debug.time, "sleep", side_effect=advance):
            with self.assertRaisesRegex(debug.DebugError, "did not acknowledge open"):
                debug.Session(phone, hello()).send("open", wait_seconds=0.25)
        self.assertEqual(1, len(phone.messages))
        self.assertGreaterEqual(now[0], 0.25)

    def test_retired_close_challenge_proves_completion_after_ack_was_overwritten(self):
        class ConcurrentClosePhone(FakePhone):
            def write(self, message, **kwargs):
                super().write(message, **kwargs)
                if message["operation"] == "close":
                    self.snapshot.update(request_id="f" * 32, result="rejected", reason="stale_challenge")
        phone = ConcurrentClosePhone()
        session = debug.Session(phone, hello())
        session.send("open")
        self.assertEqual("closed", session.send("close")["phase"])

    def test_close_reuses_session_instead_of_opening_one(self):
        phone = FakePhone()
        session = debug.Session(phone, hello())
        session.send("open")
        debug.close_session(phone)
        self.assertEqual(["open", "close"], [message["operation"] for message in phone.messages])
        self.assertEqual(session.session_id, phone.messages[-1]["session_id"])

    def test_close_of_idle_session_has_no_write(self):
        phone = FakePhone()
        debug.close_session(phone)
        self.assertEqual([], phone.messages)

    def test_close_refuses_if_phone_process_changed_between_status_and_hello(self):
        class RestartedPhone(FakePhone):
            def read(self, name):
                if name == "hello.json":
                    return {**hello(), "app_instance": "f" * 32}
                return super().read(name)
        phone = RestartedPhone()
        phone.snapshot.update(phase="active", session_id="a" * 32, host_id="b" * 32)
        with self.assertRaisesRegex(debug.DebugError, "changed while closing"):
            debug.close_session(phone)
        self.assertEqual([], phone.messages)

    def test_ctrl_c_closes_the_opened_session(self):
        phone = FakePhone()
        with patch.object(debug.time, "sleep", side_effect=KeyboardInterrupt), redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                debug.run_session(phone, wait_seconds=1)
        self.assertEqual(["open", "close"], [message["operation"] for message in phone.messages])

    def test_ended_phone_session_does_not_silently_open_another_one(self):
        phone = FakePhone()
        def expire(_seconds):
            phone.snapshot.update(phase="expired", session_id="", host_id="", challenge="f" * 32)
        with patch.object(debug.time, "sleep", side_effect=expire), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(debug.DebugError, "session ended"):
                debug.run_session(phone, wait_seconds=1)
        self.assertEqual(["open", "heartbeat", "close"], [message["operation"] for message in phone.messages])

    def test_heartbeat_rejects_retired_or_foreign_owner_before_waiting_for_ack(self):
        changes = ({"challenge": "f" * 32}, {"app_instance": "f" * 32},
                   {"phase": "expired"}, {"session_id": "f" * 32}, {"host_id": "f" * 32})
        for change in changes:
            class ChangedPhone(FakePhone):
                def exchange_heartbeat(self, message, deadline, timings, **kwargs):
                    super().exchange_heartbeat(message, deadline, timings, **kwargs)
                    return {**self.snapshot, **change, "request_id": "f" * 32}
            phone = ChangedPhone()
            session = debug.Session(phone, hello())
            session.send("open")
            with self.subTest(change=change), patch.object(debug.time, "sleep") as sleep, \
                    self.assertRaisesRegex(debug.DebugError, "session ended"):
                session.send("heartbeat")
            sleep.assert_not_called()

    def test_heartbeat_accepted_ack_requires_exact_sequence(self):
        class BadSequencePhone(FakePhone):
            def exchange_heartbeat(self, message, deadline, timings, **kwargs):
                result = super().exchange_heartbeat(message, deadline, timings, **kwargs)
                return {**result, "last_sequence": message["sequence"] + 1}
        phone = BadSequencePhone()
        session = debug.Session(phone, hello())
        session.send("open")
        with self.assertRaisesRegex(debug.DebugError, "sequence mismatch"):
            session.send("heartbeat")

    def test_heartbeat_ack_after_aggregate_budget_is_not_success(self):
        clock = [0.0]
        class LatePhone(FakePhone):
            def exchange_heartbeat(self, message, deadline, timings, **kwargs):
                result = super().exchange_heartbeat(message, deadline, timings, **kwargs)
                clock[0] = deadline
                return result
        phone = LatePhone()
        session = debug.Session(phone, hello())
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]):
            session.send("open")
            with self.assertRaisesRegex(debug.DebugError, "operation budget"):
                session.send("heartbeat", wait_seconds=3)

    def test_heartbeat_waits_for_matching_ack_after_atomic_publication(self):
        class DelayedAckPhone(FakePhone):
            polls = 0
            def exchange_heartbeat(self, message, deadline, timings, **kwargs):
                previous = dict(self.snapshot)
                super().exchange_heartbeat(message, deadline, timings, **kwargs)
                return previous
            def read_heartbeat_state(self, deadline, timings):
                self.polls += 1
                return super().read_heartbeat_state(deadline, timings)
        phone = DelayedAckPhone()
        session = debug.Session(phone, hello())
        session.send("open")
        with patch.object(debug.time, "sleep"):
            self.assertEqual("accepted", session.send("heartbeat")["result"])
        self.assertEqual(1, phone.polls)

    def test_slow_usb_verification_can_exceed_three_seconds_within_safe_lease(self):
        clock = [0.0]
        process = AdbProcess()
        limits = []
        adb = debug.UsbAdb("adb", DEVICE, PACKAGE)
        def delayed_process(args, **kwargs):
            limits.append(kwargs["timeout"])
            if args[1:] == ["-d", "get-serialno"]:
                clock[0] += 0.75
            elif args[3:] == ["get-state"]:
                clock[0] += 2.625
            else:
                # Includes the fixed 0.3-second phone poll allowance.
                clock[0] += 0.4
                message = json.loads(kwargs["input"])
                accepted = {**state(), **{key: message[key] for key in (
                    "app_instance", "challenge", "session_id", "host_id", "request_id", "audit_logging")},
                    "phase": "active", "result": "accepted", "last_sequence": message["sequence"],
                    "lease_remaining_ms": 8000}
                process.files[adb.root + "/state.json"] = json.dumps(accepted).encode()
            return process(args, **kwargs)
        adb.run = delayed_process
        session = debug.Session(FakePhone(), hello())
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]):
            session.send("open")
            session.adb = adb
            clock[0] = 2
            self.assertEqual("accepted", session.send("heartbeat")["result"])
        self.assertAlmostEqual(5.775, clock[0])
        self.assertAlmostEqual(5.375, session.last_confirmed_publication_started)
        self.assertEqual([5, 4.25, 1.625], limits)
        self.assertEqual(3, len(process.calls))

    def test_late_ack_does_not_move_lease_origin_to_ack_or_state_snapshot(self):
        clock = [0.0]
        deadlines = []
        class DelayedPhone(FakePhone):
            def exchange_heartbeat(self, message, deadline, timings, **kwargs):
                deadlines.append(deadline)
                result = super().exchange_heartbeat(message, deadline, timings, **kwargs)
                clock[0] += 4 if len(deadlines) == 1 else 2
                return {**result, "lease_remaining_ms": 8000}
        phone = DelayedPhone()
        session = debug.Session(phone, hello())
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]):
            session.send("open")
            clock[0] = 2
            session.send("heartbeat")
            self.assertEqual(2, session.last_confirmed_publication_started)
            clock[0] = 8
            with self.assertRaisesRegex(debug.DebugError, "operation budget"):
                session.send("heartbeat")
        self.assertEqual([7, 9.5], deadlines)
        self.assertIsNone(session.last_confirmed_publication_started)

    def test_late_open_ack_cannot_create_a_renewable_session(self):
        clock = [0.0]
        class LateOpenPhone(FakePhone):
            def write(self, message, **kwargs):
                super().write(message, **kwargs)
                clock[0] = 6
            def read(self, name):
                clock[0] = 8
                return super().read(name)
        session = debug.Session(LateOpenPhone(), hello())
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                self.assertRaisesRegex(debug.DebugError, "open acknowledgement arrived after"):
            session.send("open")
        self.assertEqual(0, session.publication_started)
        self.assertIsNone(session.last_confirmed_publication_started)

    def test_late_open_is_not_retried_as_a_missing_coordinator(self):
        for delay_before_read in (False, True):
            clock = [0.0]
            class LateOpenPhone(FakePhone):
                def write(self, message, **kwargs):
                    super().write(message, **kwargs)
                    clock[0] = 8 if delay_before_read else 6
                def read(self, name):
                    if name == "state.json":
                        clock[0] = 8
                    return super().read(name)
            phone = LateOpenPhone()
            with self.subTest(delay_before_read=delay_before_read), \
                    patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                    patch.object(debug.time, "sleep") as sleep, redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(debug.DebugError, "open .*budget"):
                debug.run_session(phone, wait_seconds=60)
            sleep.assert_not_called()
            self.assertEqual(["open"], [message["operation"] for message in phone.messages])

    def test_open_ack_keeps_earlier_publication_and_expired_budget_never_writes(self):
        clock = [0.0]
        class SlowOpenPhone(FakePhone):
            def write(self, message, **kwargs):
                super().write(message, **kwargs)
                clock[0] = 6
        phone = SlowOpenPhone()
        session = debug.Session(phone, hello())
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]):
            session.send("open")
            self.assertEqual(0, session.last_confirmed_publication_started)
            clock[0] = 7.5
            with self.assertRaisesRegex(debug.DebugError, "operation budget"):
                session.send("heartbeat")
        self.assertEqual(["open"], [message["operation"] for message in phone.messages])
        self.assertIsNone(session.last_confirmed_publication_started)

    def test_unknown_publication_cannot_establish_or_renew_session(self):
        class UnknownPublicationPhone(FakePhone):
            def write(self, message, **_kwargs):
                super().write(message)
        phone = UnknownPublicationPhone()
        session = debug.Session(phone, hello())
        with self.assertRaisesRegex(debug.DebugError, "publication start is unknown"):
            session.send("open")
        with self.assertRaisesRegex(debug.DebugError, "publication start is unknown"):
            session.send("heartbeat")
        self.assertEqual(1, len(phone.messages))

        phone = FakePhone()
        session = debug.Session(phone, hello())
        session.send("open")
        def unknown_heartbeat(message, _deadline, _timings, **_kwargs):
            phone.write(message)
            return phone.read("state.json")
        phone.exchange_heartbeat = unknown_heartbeat
        with self.assertRaisesRegex(debug.DebugError, "publication start is unknown"):
            session.send("heartbeat")
        self.assertIsNone(session.last_confirmed_publication_started)

    def test_rejected_publication_cannot_authorize_another_heartbeat(self):
        class RejectingPhone(FakePhone):
            def exchange_heartbeat(self, message, deadline, timings, **kwargs):
                result = super().exchange_heartbeat(message, deadline, timings, **kwargs)
                return {**result, "result": "rejected", "reason": "stale_sequence", "request_id": "f" * 32}
        phone = RejectingPhone()
        session = debug.Session(phone, hello())
        session.send("open")
        with patch.object(debug.time, "sleep") as sleep, \
                self.assertRaisesRegex(debug.DebugError, "stale_sequence"):
            session.send("heartbeat")
        sleep.assert_not_called()
        self.assertIsNone(session.last_confirmed_publication_started)
        with self.assertRaisesRegex(debug.DebugError, "publication start is unknown"):
            session.send("heartbeat")
        session.send("close")
        self.assertEqual(["open", "heartbeat", "close"], [message["operation"] for message in phone.messages])
        self.assertEqual(0, phone.messages[-1]["sequence"])


class HeartbeatScheduleTest(unittest.TestCase):
    def run_timed(self, durations):
        clock = [0.0]
        starts = []
        sleeps = []
        class TimedPhone(FakePhone):
            def exchange_heartbeat(self, message, deadline, timings, **kwargs):
                starts.append(clock[0])
                result = super().exchange_heartbeat(message, deadline, timings, **kwargs)
                elapsed = durations[len(starts) - 1]
                clock[0] += elapsed
                timings.append(("mailbox_exchange", elapsed))
                return result
        phone = TimedPhone()
        def sleep(seconds):
            if len(starts) == len(durations):
                raise KeyboardInterrupt
            sleeps.append(seconds)
            clock[0] += seconds
        output = io.StringIO()
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(debug.time, "sleep", side_effect=sleep), redirect_stdout(output), \
                self.assertRaises(KeyboardInterrupt):
            debug.run_session(phone, wait_seconds=1)
        return starts, sleeps, phone.messages, output.getvalue()

    def test_roundtrip_time_is_included_in_fixed_monotonic_cadence(self):
        starts, sleeps, messages, output = self.run_timed([0.4, 0.4, 0.4])
        self.assertEqual([2.0, 4.0, 6.0], starts)
        for actual, expected in zip(sleeps, [2.0, 1.6, 1.6]):
            self.assertAlmostEqual(expected, actual)
        self.assertEqual(["open", "heartbeat", "heartbeat", "heartbeat", "close"],
                         [message["operation"] for message in messages])
        self.assertIn("elapsed_ms=400", output)
        self.assertIn("since_last_ack_ms=2000", output)
        self.assertIn("adb_stages_ms=[mailbox_exchange:400]", output)
        self.assertNotIn(CHALLENGE, output)

    def test_slow_round_skips_missed_tick_without_burst_or_extra_complete_interval(self):
        starts, sleeps, _messages, _output = self.run_timed([2.6, 0.1, 0.1])
        self.assertEqual([2.0, 6.0, 8.0], starts)
        for actual, expected in zip(sleeps, [2.0, 1.4, 1.9]):
            self.assertAlmostEqual(expected, actual)

    def test_failed_heartbeat_emits_timing_and_exits_without_opening_a_new_session(self):
        clock = [0.0]
        class ExpiringPhone(FakePhone):
            def exchange_heartbeat(self, message, deadline, timings, **kwargs):
                super().exchange_heartbeat(message, deadline, timings, **kwargs)
                clock[0] += 0.3
                timings.append(("mailbox_exchange", 0.3))
                return {**self.snapshot, "challenge": "f" * 32, "phase": "expired",
                        "reason": "heartbeat_timeout", "request_id": "f" * 32}
        phone = ExpiringPhone()
        output = io.StringIO()
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(debug.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                redirect_stdout(output), self.assertRaisesRegex(debug.DebugError, "heartbeat_timeout"):
            debug.run_session(phone, wait_seconds=1)
        self.assertEqual(["open", "heartbeat", "close"], [message["operation"] for message in phone.messages])
        self.assertIn("result=failed", output.getvalue())
        self.assertIn("elapsed_ms=300", output.getvalue())
        self.assertIn("lease_snapshot_ms=unknown", output.getvalue())
        self.assertNotIn("f" * 32, output.getvalue())


class ReloadPhone(FakePhone):
    def __init__(self):
        super().__init__()
        self.snapshot.update(phase="active", session_id="a" * 32, host_id="b" * 32)
        self.baseline = None
        self.manifest = None
        self.reload_status = None
        self.reload_writes = []
        self.staged_paths = []
        self.bad_loaded_evidence = False

    def apk_version(self):
        return 2209

    def upload_stage(self, snapshot, session_id, operation_id):
        self.manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        self.staged_paths = sorted(path.relative_to(snapshot / "stage").as_posix()
                                   for path in (snapshot / "stage").rglob("*") if path.is_file())

    def read(self, name):
        if name == "source-state.json":
            if self.baseline is None:
                raise debug.MailboxUnavailable("no managed baseline")
            return self.baseline
        if name == "reload-state.json":
            return self.reload_status
        return super().read(name)

    def write(self, message, destination=None):
        self.reload_writes.append((destination, message))
        if message["operation"] == "apply":
            self.baseline = {
                "version": 1, "package": PACKAGE, "apk_version_code": 2209,
                "revision": self.manifest["revision"], "files": self.manifest["managed_files"],
            }
            self.reload_status = {
                "version": 1, "session_id": message["session_id"], "operation_id": message["operation_id"],
                "revision": message["revision"], "phase": "loaded", "resource_loaded": True,
                "agent_loaded": not self.bad_loaded_evidence, "native_generation": 2, "agent_generation": 3,
                "controller_reused": True, "display_id": 2,
            }


class SourceSyncTest(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[2] / "build-debug" / "pc-source-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = debug.SourcePolicy()
        self.write("interface.json", b"{}")
        self.mapping_path = "agent/action/basics/InterfaceZone/combat_roi_zone_map.json"
        self.write(self.mapping_path, b"{}")

    def write(self, path, contents):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        return target

    def entry(self, path, contents=b"content"):
        return {"path": path, "size": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}

    def test_only_profile_source_is_collected_and_agent_config_remains_allowed(self):
        wanted = ["agent/action/combat/config/team.py", "resource/base/image/中文.png",
                  "resource/base/pipeline/ROI.jsonc", "options/task.json"]
        excluded = ["agent/tests/test_a.py", "agent/debug/cache.py", "agent/test_support/fake.py",
                    "resource/base/model/ocr/data.json", "resource/base/image/bad.onnx",
                    "resource/pgr_win32/pipeline/a.json", "resource/playcover/pipeline/a.json",
                    "config/user.json", "agent/__pycache__/cache.py", "agent/runtime.so"]
        for path in wanted + excluded:
            self.write(path, b"test")
        self.assertEqual(sorted(["interface.json", self.mapping_path, *wanted]), [entry["path"] for entry in self.policy.collect(self.root)])

    def test_path_traversal_absolute_windows_alias_and_non_bmp_are_rejected(self):
        paths = ["/agent/a.py", "agent/../a.py", "agent//a.py", "agent/./a.py", "agent\\a.py",
                 "agent/C:a.py", "agent/a.py.", "agent/a.py ", "agent/.env.py", "agent/😀.py", "agent/\x00.py"]
        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(self.policy.allowed(path))

    def test_metadata_rejects_case_collisions_sizes_and_hashes(self):
        cases = [[self.entry("agent/A.py"), self.entry("agent/a.py")],
                 [self.entry("config/user.json")], [{**self.entry("agent/a.py"), "size": True}],
                 [{**self.entry("agent/a.py"), "sha256": "bad"}],
                 [{**self.entry("agent/a.py"), "size": self.policy.limits["max_file_bytes"] + 1}]]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(debug.DebugError):
                self.policy.entries(entries)

    def test_revision_is_content_based_includes_dirty_files_and_canonical_utf8(self):
        source = self.write("agent/中文.py", b"first")
        first = self.policy.collect(self.root)
        first_revision = debug.source_revision(first)
        self.assertIn("中文".encode(), debug.canonical_json(first))
        source.write_bytes(b"dirty without commit")
        self.assertNotEqual(first_revision, debug.source_revision(self.policy.collect(self.root)))
        self.assertEqual(first_revision, debug.source_revision(first))

    def test_delta_deletes_only_previously_managed_paths_and_ignores_unchanged_files(self):
        old = self.policy.entries([self.entry("agent/removed.py"), self.entry("tasks/same.json")])
        new = self.policy.entries([self.entry("agent/new.py"), self.entry("tasks/same.json")])
        baseline = {"revision": debug.source_revision(old), "files": old}
        manifest = debug.build_manifest(self.policy, PACKAGE, 2209, "a" * 32, "b" * 32, new, baseline)
        self.assertEqual(["agent/new.py", "agent/removed.py"], [entry["path"] for entry in manifest["files"]])
        self.assertEqual([False, True], [entry["delete"] for entry in manifest["files"]])
        self.assertEqual(old[0]["sha256"], manifest["files"][1]["sha256"])
        self.assertEqual(new, manifest["managed_files"])

    def test_snapshot_rejects_source_changes_before_upload(self):
        files = self.policy.collect(self.root)
        manifest = debug.build_manifest(self.policy, PACKAGE, 2209, "a" * 32, "b" * 32, files, None)
        self.write("interface.json", b"changed after hash")
        snapshot = self.root / "snapshot"
        snapshot.mkdir()
        with self.assertRaisesRegex(debug.DebugError, "changed during snapshot"):
            debug.snapshot_source(self.root, snapshot, manifest)

    def test_invalid_baseline_version_package_hash_or_unmanaged_files_are_refused(self):
        files = self.policy.collect(self.root)
        good = {"version": 1, "package": PACKAGE, "apk_version_code": 2209,
                "revision": debug.source_revision(files), "files": files}
        for bad in ({**good, "apk_version_code": 1}, {**good, "package": "other.pkg"},
                    {**good, "revision": "0" * 64}, {**good, "files": [self.entry("config/user.json")]}):
            with self.subTest(bad=bad), self.assertRaises(debug.DebugError):
                debug.source_baseline(bad, self.policy, PACKAGE, 2209)

    def test_source_policy_is_a_subset_of_the_android_pi_profile(self):
        from ci.check_android_pi_profile import glob_to_regex, parse_profile_lists
        include, exclude = parse_profile_lists((debug.REPO_ROOT / ".github/android/pi-profile.yaml").read_text(encoding="utf-8"))
        for path in self.policy.spec["files"]:
            self.assertTrue(any(glob_to_regex(pattern).fullmatch(path) for pattern in include), path)
            self.assertFalse(any(glob_to_regex(pattern).fullmatch(path) for pattern in exclude), path)
        for root in self.policy.spec["roots"]:
            self.assertIn(root + "/**", include)
        for prefix in self.policy.spec["excluded_prefixes"]:
            self.assertIn(prefix + "**", exclude)

    def test_sync_stages_then_applies_without_writing_session_heartbeat(self):
        self.write("agent/change.py", b"print('new')")
        phone = ReloadPhone()
        with redirect_stdout(io.StringIO()):
            result = debug.sync_source(phone, self.root)
        self.assertEqual("loaded", result["phase"])
        self.assertEqual(sorted(["agent/change.py", "interface.json", self.mapping_path]), phone.staged_paths)
        self.assertEqual(["reload-request.json"], [destination for destination, _ in phone.reload_writes])
        self.assertEqual(["apply"], [message["operation"] for _, message in phone.reload_writes])

    def test_loaded_without_agent_proof_is_failure_and_requests_safe_cancel(self):
        phone = ReloadPhone()
        phone.bad_loaded_evidence = True
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(debug.DebugError, "preparation evidence"):
            debug.sync_source(phone, self.root)
        self.assertEqual(["apply", "cancel"], [message["operation"] for _, message in phone.reload_writes])

    def test_loaded_without_controller_reuse_is_not_a_success(self):
        class RebuiltDisplay(ReloadPhone):
            def write(self, *args):
                super().write(*args)
                self.reload_status["controller_reused"] = False
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(debug.DebugError, "Controller/display"):
            debug.sync_source(RebuiltDisplay(), self.root)

    def test_session_change_after_upload_prevents_apply(self):
        class ChangedPhone(ReloadPhone):
            def upload_stage(self, *args):
                super().upload_stage(*args)
                self.snapshot.update(session_id="c" * 32)
        phone = ChangedPhone()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(debug.DebugError, "changed during upload"):
            debug.sync_source(phone, self.root)
        self.assertEqual([], phone.reload_writes)

    def test_rejected_second_operation_is_read_from_ack_without_overwriting_progress(self):
        class BusyPhone(ReloadPhone):
            def write(self, message, destination=None):
                self.reload_writes.append((destination, message))
                self.ack = {"session_id": message["session_id"], "operation_id": message["operation_id"],
                            "phase": "rejected", "reason": "maintenance_busy"}
                self.reload_status = {"operation_id": "c" * 32, "phase": "loading"}
            def read(self, name):
                return self.ack if name == "reload-ack.json" else super().read(name)
        phone = BusyPhone()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(debug.DebugError, "maintenance_busy"):
            debug.sync_source(phone, self.root)
        self.assertEqual("loading", phone.reload_status["phase"])

    def test_timeout_requests_cancellation_without_touching_heartbeat(self):
        class SlowPhone(ReloadPhone):
            def write(self, message, destination=None):
                super().write(message, destination)
                self.reload_status["phase"] = "loading"
        phone = SlowPhone()
        clock = [0.0]
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(debug.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                redirect_stdout(io.StringIO()), self.assertRaisesRegex(debug.DebugError, "timed out"):
            debug.sync_source(phone, self.root, wait_seconds=0.5)
        self.assertEqual(["apply", "cancel"], [message["operation"] for _, message in phone.reload_writes])
        self.assertTrue(all(destination == "reload-request.json" for destination, _ in phone.reload_writes))

    def test_source_state_readback_mismatch_never_reports_loaded(self):
        class BadReadbackPhone(ReloadPhone):
            def write(self, *args):
                super().write(*args)
                self.baseline["revision"] = "f" * 64
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(debug.DebugError, "baseline revision/hash"):
            debug.sync_source(BadReadbackPhone(), self.root)

    def test_source_symlink_or_junction_is_rejected_before_snapshot(self):
        source = self.root / "interface.json"
        with patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == source), \
                self.assertRaisesRegex(debug.DebugError, "not a regular file"):
            self.policy.collect(self.root)

    def test_upgraded_apk_uses_fresh_baseline_only_after_current_pi_marker(self):
        class UpgradedPhone(ReloadPhone):
            def pi_version(self):
                return 2209
        phone = UpgradedPhone()
        old = [self.entry("agent/old_version.py")]
        phone.baseline = {"version": 1, "package": PACKAGE, "apk_version_code": 2208,
                          "revision": debug.source_revision(old), "files": old}
        with redirect_stdout(io.StringIO()):
            debug.sync_source(phone, self.root)
        self.assertEqual("", phone.manifest["base_revision"])
        self.assertFalse(any(entry["delete"] for entry in phone.manifest["files"]))

    def test_upgraded_apk_with_old_pi_marker_never_stages_or_applies(self):
        class IncompleteUpgrade(ReloadPhone):
            def pi_version(self):
                return 2208
        phone = IncompleteUpgrade()
        phone.baseline = {"version": 1, "package": PACKAGE, "apk_version_code": 2208,
                          "revision": "f" * 64, "files": []}
        with self.assertRaisesRegex(debug.DebugError, "not fully installed"):
            debug.sync_source(phone, self.root)
        self.assertIsNone(phone.manifest)
        self.assertEqual([], phone.reload_writes)


class ReloadStatusContractTest(unittest.TestCase):
    def setUp(self):
        self.policy = debug.SourcePolicy()
        self.manifest = {"revision": debug.source_revision([]), "base_revision": "a" * 64}
        self.request = {"session_id": "b" * 32, "operation_id": "c" * 32}
        self.value = {
            "version": 1, **self.request, "revision": self.manifest["revision"],
            "phase": "loaded", "resource_loaded": True, "agent_loaded": True,
            "native_generation": 2, "agent_generation": 3,
            "controller_reused": True, "display_id": 13,
        }
        self.committed = {
            "version": 1, "package": PACKAGE, "apk_version_code": 2209,
            "revision": self.manifest["revision"], "files": [],
        }
        self.reads = []

    def wait(self):
        case = self

        class StatusPhone:
            package = PACKAGE

            def read(self, name):
                case.reads.append(name)
                if name == "reload-state.json":
                    return case.value.copy()
                if name == "source-state.json":
                    return case.committed.copy()
                if name == "reload-ack.json":
                    return {}
                raise AssertionError(name)

        clock = [0.0]
        with patch.object(debug.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(debug.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                redirect_stdout(io.StringIO()):
            return debug.wait_for_reload(StatusPhone(), self.policy, self.manifest, self.request, 2209, 0.5)

    def test_failed_at_target_revision_reports_original_reason_and_never_succeeds(self):
        self.value.update(phase="failed", reason="candidate_project_invalid")
        with self.assertRaisesRegex(debug.DebugError, "^phone reload failed: candidate_project_invalid$"):
            self.wait()
        self.assertNotIn("source-state.json", self.reads)

    def test_failed_at_submitted_base_revision_reports_original_reason_and_never_succeeds(self):
        for baseline in ("a" * 64, ""):
            with self.subTest(base_revision=baseline):
                self.manifest["base_revision"] = baseline
                self.value.update(phase="failed", revision=baseline,
                                  reason="display_not_previously_prepared", rolled_back=False)
                with self.assertRaisesRegex(debug.DebugError, "^phone reload failed: display_not_previously_prepared$"):
                    self.wait()
        self.assertNotIn("source-state.json", self.reads)

    def test_failed_with_unknown_revision_remains_binding_error(self):
        self.value.update(phase="failed", revision="f" * 64, reason="display_not_previously_prepared")
        with self.assertRaisesRegex(debug.DebugError, "does not match the submitted operation"):
            self.wait()

    def test_failed_base_revision_never_relaxes_version_or_session(self):
        self.value.update(phase="failed", revision=self.manifest["base_revision"],
                          reason="display_not_previously_prepared")
        for field, bad in (("version", True), ("version", 0), ("version", "1"),
                           ("session_id", "d" * 32), ("session_id", None)):
            with self.subTest(field=field, value=bad):
                original = self.value[field]
                self.value[field] = bad
                with self.assertRaisesRegex(debug.DebugError, "does not match the submitted operation"):
                    self.wait()
                self.value[field] = original

    def test_different_operation_is_ignored_and_cannot_supply_failure_reason(self):
        self.value.update(phase="failed", operation_id="d" * 32,
                          revision=self.manifest["base_revision"], reason="display_not_previously_prepared")
        with self.assertRaisesRegex(debug.DebugError, "reload timed out"):
            self.wait()
        self.assertIn("reload-ack.json", self.reads)
        self.assertNotIn("source-state.json", self.reads)

    def test_base_revision_is_not_accepted_for_loaded_or_other_phases(self):
        self.value["revision"] = self.manifest["base_revision"]
        for phase in ("loaded", "loading", "blocked", "cancelled", "rejected", "rolled_back"):
            with self.subTest(phase=phase):
                self.value["phase"] = phase
                with self.assertRaisesRegex(debug.DebugError, "does not match the submitted operation"):
                    self.wait()
        self.assertNotIn("source-state.json", self.reads)

    def test_loaded_still_requires_native_and_controller_evidence(self):
        for field, bad, error in (
            ("resource_loaded", False, "preparation evidence"),
            ("agent_loaded", False, "preparation evidence"),
            ("native_generation", 0, "preparation evidence"),
            ("agent_generation", 0, "preparation evidence"),
            ("controller_reused", False, "Controller/display"),
            ("display_id", -1, "Controller/display"),
        ):
            with self.subTest(field=field):
                original = self.value[field]
                self.value[field] = bad
                with self.assertRaisesRegex(debug.DebugError, error):
                    self.wait()
                self.value[field] = original

    def test_loaded_still_requires_matching_committed_revision(self):
        files = [{"path": "agent/other.py", "size": 1, "sha256": "e" * 64}]
        self.committed.update(files=files, revision=debug.source_revision(files))
        with self.assertRaisesRegex(debug.DebugError, "committed revision differs"):
            self.wait()

    def test_loaded_with_complete_evidence_still_succeeds(self):
        self.assertEqual(self.value, self.wait())
        self.assertIn("source-state.json", self.reads)


class StageTransportTest(unittest.TestCase):
    class Process(AdbProcess):
        def __call__(self, args, **kwargs):
            if args[3:6] == ["shell", "-T", "sh"]:
                self.calls.append(args)
                return subprocess.CompletedProcess(args, 0, b"", b"")
            if len(args) > 5 and args[3] == "push" and Path(args[4]).is_dir():
                self.calls.append(args)
                self.assert_bounded(kwargs)
                for path in Path(args[4]).rglob("*"):
                    if path.is_file():
                        relative = path.relative_to(Path(args[4])).as_posix()
                        self.files[args[5] + "/stage/" + relative] = path.read_bytes()
                if self.disconnect_after_push:
                    self.detected = "other-device"
                return subprocess.CompletedProcess(args, 0, b"", b"")
            if args[3:11] == ["shell", "-T", "cmd", "package", "list", "packages", "--show-versioncode", PACKAGE]:
                self.calls.append(args)
                return subprocess.CompletedProcess(args, 0,
                    f"package:{PACKAGE}.other versionCode:9999\npackage:{PACKAGE} versionCode:2209\n".encode(), b"")
            return super().__call__(args, **kwargs)

        @staticmethod
        def assert_bounded(kwargs):
            assert 0 < kwargs["timeout"] <= 300
            assert kwargs["check"] is False

    def setUp(self):
        parent = debug.REPO_ROOT / "build-debug" / "pc-stage-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.snapshot = Path(self.temp.name)
        (self.snapshot / "stage").mkdir()
        (self.snapshot / "stage" / "interface.json").write_bytes(b"{}")
        (self.snapshot / "manifest.json").write_bytes(b"{\"version\":1}")
        self.process = self.Process()
        self.adb = debug.UsbAdb("adb", DEVICE, PACKAGE, self.process)

    def test_only_derived_stage_is_uploaded_and_manifest_is_promoted_last(self):
        self.adb.upload_stage(self.snapshot, "a" * 32, "b" * 32)
        operation_root = self.adb.root + "/reload/" + "a" * 32 + "/" + "b" * 32
        self.assertIn(operation_root + "/stage/interface.json", self.process.files)
        self.assertEqual(b'{"version":1}', self.process.files[operation_root + "/manifest.json"])
        self.assertEqual("mv", self.process.calls[-1][4])
        self.assertFalse(any("/files/pi/" in str(call) or "-r" in call for call in self.process.calls))
        script = next(call for call in self.process.calls if call[3:6] == ["shell", "-T", "sh"])
        self.assertIn("-L", script[7])

    def test_upload_disconnect_does_not_publish_manifest_or_request_apply(self):
        self.process.disconnect_after_push = True
        with self.assertRaises(debug.DebugError):
            self.adb.upload_stage(self.snapshot, "a" * 32, "b" * 32)
        self.assertFalse(any(path.endswith("manifest.json") for path in self.process.files))
        self.assertFalse(any("mv" in call for call in self.process.calls))

    def test_apk_version_uses_exact_package_instead_of_prefix_match(self):
        self.assertEqual(2209, self.adb.apk_version())

    def test_invalid_stage_ids_are_refused_without_adb(self):
        with self.assertRaises(debug.DebugError):
            self.adb.upload_stage(self.snapshot, "../pi", "b" * 32)
        self.assertEqual([], self.process.calls)


if __name__ == "__main__":
    unittest.main()
