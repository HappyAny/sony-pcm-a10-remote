import io
import json
import threading
import unittest

from pcma10_remote.ipc import BridgeHost
from pcma10_remote.simulator import SimulatedTransport


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.host = BridgeHost(self.output, demo=True)
        self.addCleanup(self.host.close)

    def request(self, method, **params):
        self.host.handle({"id": 27, "method": method, "params": params})
        messages = [json.loads(x) for x in self.output.getvalue().splitlines()]
        return next(m for m in reversed(messages) if m["type"] == "response")

    def test_native_ui_workflow_uses_existing_session(self):
        self.assertEqual(self.request("devices")["result"][0]["address"], "demo")
        self.assertTrue(self.request("connect")["result"]["ready"])
        for name, expected in [("record", 3), ("mark", 3), ("pause", 2), ("record", 3), ("stop", 1)]:
            result = self.request("action", name=name)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["result"]["remote_status"], expected)
        self.assertEqual(self.host.session.transport.marks, 1)

    def test_closing_ui_disconnects_without_stopping_recording(self):
        self.request("connect")
        self.request("action", name="record")
        peer = self.host.session.transport
        reply = self.request("shutdown")
        self.assertEqual(reply["result"]["last_state"]["remote_status"], 3)
        self.assertFalse(reply["result"]["last_state"]["ready"])
        self.assertTrue(peer.closed.is_set())
        self.assertEqual(peer.status, 3)
        self.assertNotIn(b"\x10\x01", peer.actions)

    def test_shutdown_during_connect_cannot_leave_a_late_connection(self):
        started, release = threading.Event(), threading.Event()
        peers = []
        def delayed_factory(address):
            started.set()
            release.wait(2)
            peer = SimulatedTransport()
            peers.append(peer)
            return peer
        self.host.transport_factory = delayed_factory
        thread = threading.Thread(target=self.host.handle, args=({"id": 90, "method": "connect"},))
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            self.assertTrue(self.request("shutdown")["ok"])
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(self.host.session)
        self.assertTrue(peers[0].closed.is_set())
        self.assertFalse(peers[0].host_frames)

    def test_shutdown_interrupts_an_unconfirmed_action_and_keeps_recording(self):
        self.request("connect")
        peer = self.host.session.transport
        sent = threading.Event()
        original = peer._command
        def observe(payload):
            original(payload)
            if payload == b"\x10\x03":
                sent.set()
        peer._command = observe
        peer.drop_action_ack = True
        thread = threading.Thread(target=self.host.handle, args=({"id": 91, "method": "action", "params": {"name": "record"}},))
        thread.start()
        self.assertTrue(sent.wait(1))
        self.assertTrue(self.request("shutdown")["ok"])
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(peer.closed.is_set())
        self.assertEqual(peer.status, 3)
        self.assertNotIn(b"\x10\x01", peer.actions)

    def test_disconnect_releases_the_existing_transport_before_reconnect(self):
        self.request("connect")
        previous = self.host.session.transport
        self.assertTrue(self.request("disconnect")["ok"])
        self.assertTrue(previous.closed.is_set())
        self.assertIsNone(self.host.session)
        self.assertTrue(self.request("connect")["result"]["ready"])
        self.assertIsNot(self.host.session.transport, previous)

    def test_transient_connection_failure_is_retried_before_recorder_commands(self):
        calls = []
        def factory(address):
            calls.append(address)
            if len(calls) == 1:
                raise OSError(10060, "temporary RFCOMM timeout")
            return SimulatedTransport()
        self.host.transport_factory = factory
        response = self.request("connect", address="paired-device")
        self.assertTrue(response["ok"])
        self.assertEqual(calls, ["paired-device", "paired-device"])
        self.assertFalse(self.host.session.transport.actions)

    def test_shutdown_cancels_connection_retry_delay(self):
        failed = threading.Event()
        calls = []
        def factory(address):
            calls.append(address)
            failed.set()
            raise OSError(10060, "temporary RFCOMM timeout")
        self.host.transport_factory = factory
        thread = threading.Thread(target=self.host.handle, args=({"id": 92, "method": "connect"},))
        thread.start()
        self.assertTrue(failed.wait(1))
        self.request("shutdown")
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(calls), 1)

    def test_action_without_connection_is_a_structured_error(self):
        response = self.request("action", name="record")
        self.assertFalse(response["ok"])
        self.assertEqual(response["id"], 27)
        self.assertIn("连接", response["error"]["message"])

    def test_raw_frames_are_not_published_in_normal_ui_events(self):
        self.request("connect")
        messages = [json.loads(x) for x in self.output.getvalue().splitlines()]
        events = [m["data"]["event"] for m in messages if m["type"] == "event"]
        self.assertIn("connected", events)
        self.assertIn("state", events)
        self.assertNotIn("rx", events)
        self.assertNotIn("tx", events)

    def test_settings_and_typed_marks_cross_the_native_bridge(self):
        self.request("connect")
        self.assertEqual(len(self.request("settings")["result"]["settings"]), 16)
        changed = self.request("settings-set", key="pre-record", value=1)
        self.assertTrue(changed["ok"])
        self.assertEqual(changed["result"]["state"]["settings"]["pre-record"], 1)
        self.request("action", name="pause")
        marked = self.request("action", name="mark", mark_type=4, mark_name="新话题")
        self.assertEqual(marked["result"]["last_mark"]["name"], "新话题")
        self.assertEqual(marked["result"]["last_mark"]["type"], 4)

    def test_rejected_setting_is_a_structured_error_at_native_boundary(self):
        self.request("connect")
        self.host.session.transport.reject_settings[0x0509] = 0xA1
        changed = self.request("settings-set", key="pre-record", value=1)
        self.assertFalse(changed["ok"])
        self.assertIn("0xA1", changed["error"]["message"])
        self.assertTrue(self.host.session.ready)
