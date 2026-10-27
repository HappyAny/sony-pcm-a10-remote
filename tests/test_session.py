import threading
import time
import unittest

from pcma10_remote.protocol import DATA, SHOT, Frame, ProtocolError, encode
from pcma10_remote.session import CommandUncertainError, RecorderSession, SessionError
from pcma10_remote.simulator import SimulatedTransport, _items


class SessionTests(unittest.TestCase):
    def make_session(self, **kwargs):
        peer = SimulatedTransport(**kwargs)
        session = RecorderSession(peer, ack_timeout=0.3)
        self.addCleanup(session.close)
        session.connect()
        return session, peer

    def test_full_recording_workflow(self):
        session, peer = self.make_session()
        self.assertEqual(session.snapshot()["remote_status"], 1)
        self.assertEqual(session.action("record")["remote_status"], 3)
        self.assertEqual(session.action("gain-up")["gain"], 16)
        self.assertEqual(session.action("gain", value=20)["gain"], 20)
        session.action("mark", mark_name="Test")
        self.assertEqual(peer.marks, 1)
        self.assertEqual(session.action("pause")["remote_status"], 2)
        self.assertEqual(session.action("record")["remote_status"], 3)
        self.assertEqual(session.action("stop")["remote_status"], 1)

    def test_repeat_record_is_idempotent_at_application_level(self):
        session, peer = self.make_session()
        session.action("record")
        session.action("record")
        self.assertEqual(peer.actions.count(b"\x10\x03"), 1)

    def test_single_record_button_starts_pauses_and_resumes_from_fresh_status(self):
        session, peer = self.make_session()
        self.assertEqual(session.action("record-toggle")["remote_status"], 3)
        self.assertEqual(session.action("record-toggle")["remote_status"], 2)
        self.assertEqual(session.action("record-toggle")["remote_status"], 3)
        self.assertEqual(peer.actions, [b"\x10\x03", b"\x10\x02", b"\x10\x03"])
        session.state.remote_status = 1  # The displayed value can be stale.
        self.assertEqual(session.action("record-toggle")["remote_status"], 2)
        self.assertEqual(peer.actions[-1], b"\x10\x02")

    def test_pause_from_stopped_enters_monitor_standby(self):
        session, _ = self.make_session()
        self.assertEqual(session.action("pause")["remote_status"], 2)

    def test_lost_ack_does_not_repeat_action_or_report_success(self):
        session, peer = self.make_session(drop_action_ack=True)
        with self.assertRaises(CommandUncertainError):
            session.action("record")
        self.assertEqual(peer.actions, [b"\x10\x03"])
        self.assertFalse(session.ready)
        self.assertTrue(peer.closed.is_set())

    def test_wrong_model_rejected_before_foreground_or_recording(self):
        peer = SimulatedTransport(model="OTHER")
        session = RecorderSession(peer)
        self.addCleanup(session.close)
        with self.assertRaises(SessionError):
            session.connect()
        self.assertFalse(session.ready)
        self.assertFalse(any(f.kind == DATA for f in peer.host_frames))

    def test_nonzero_status_prevents_action_from_stale_state(self):
        session, peer = self.make_session()
        original = peer._command

        def fail_status(payload):
            if payload == b"\x20":
                peer._publish(_items(0x21, {}, status=0xA0))
            else:
                original(payload)

        peer._command = fail_status
        with self.assertRaises(SessionError):
            session.action("record")
        self.assertEqual(peer.actions, [])

    def test_device_ack_without_state_change_is_not_recording_success(self):
        session, peer = self.make_session()
        original = peer._command

        def ignore_record(payload):
            if payload == b"\x10\x03":
                peer.actions.append(payload)
            else:
                original(payload)

        peer._command = ignore_record
        with self.assertRaisesRegex(SessionError, "未确认"):
            session.action("record")
        self.assertEqual(peer.actions, [b"\x10\x03"])
        self.assertEqual(session.state.remote_status, 1)

    def test_gain_and_marks_require_recording_or_standby(self):
        session, peer = self.make_session()
        for name in ("mark", "gain-up"):
            with self.assertRaises(SessionError):
                session.action(name)
        self.assertFalse(peer.actions)

    def test_real_a10_gain_rejection_is_not_reported_as_success(self):
        session, peer = self.make_session()
        session.action("pause")
        peer.gain = 10
        events = []
        session.on_event = events.append
        original = peer._command

        def reject_gain(payload):
            if payload == b"\x10\x04":
                # Real recorder response captured on 2026-10-26: two suggested
                # items, followed by successful queries of unchanged gain 10.
                peer._publish(bytes.fromhex("16 a1 02 06 04 00 0a 02 01 00 08"))
            else:
                original(payload)

        peer._command = reject_gain
        with self.assertRaisesRegex(SessionError, "0xA1"):
            session.action("gain-up")
        self.assertEqual(session.state.gain, 10)
        self.assertFalse(any(e["event"] == "action_confirmed" for e in events))

    def test_success_result_without_gain_change_is_not_success(self):
        session, peer = self.make_session()
        session.action("pause")
        original = peer._command

        def unchanged_gain(payload):
            if payload == b"\x10\x04":
                peer._publish(_items(0x16, {0x0604: peer.gain}))
            else:
                original(payload)

        peer._command = unchanged_gain
        with self.assertRaisesRegex(SessionError, "未达到请求值"):
            session.action("gain-up")

    def test_gain_limit_does_not_send_another_increment(self):
        session, peer = self.make_session()
        session.action("pause")
        peer.gain = 30
        before = len(peer.actions)
        self.assertEqual(session.action("gain-up")["gain"], 30)
        self.assertEqual(len(peer.actions), before)

    def test_meter_packets_and_stop_clear_live_meter(self):
        session, peer = self.make_session()
        session.action("record")
        deadline = time.monotonic() + 1
        while session.state.left_db is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(session.state.left_db)
        self.assertLess(session.state.left_db, 0)
        session.action("stop")
        self.assertIsNone(session.state.left_db)

    def test_truncated_status_is_never_accepted(self):
        session, peer = self.make_session()
        with self.assertRaises(ProtocolError):
            session._apply(bytes.fromhex("21000207040003"))

    def test_two_operations_are_serialized(self):
        session, peer = self.make_session()
        session.action("record")
        errors = []

        def add_mark():
            try:
                session.action("mark")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=add_mark) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(errors)
        self.assertEqual(peer.marks, 2)

    def test_duplicate_reliable_frame_is_acked_without_redispatch(self):
        session, peer = self.make_session()
        events = []
        session.on_event = events.append
        with peer.lock:
            payload = _items(0x22, {0x0704: 1})
            sequence = peer.sequence
            peer._publish(payload)
            peer._wire(encode(Frame(SHOT, 255, b"\x40\x00")))
            peer._wire(encode(Frame(DATA, sequence, payload)))
        deadline = time.monotonic() + 1
        while not any(e["event"] == "duplicate" for e in events) and time.monotonic() < deadline:
            time.sleep(0.01)
        delivered = [e for e in events if e["event"] == "payload" and e["command"] == "0x22"]
        self.assertEqual(len(delivered), 1)
        self.assertTrue(any(e["event"] == "duplicate" for e in events))
