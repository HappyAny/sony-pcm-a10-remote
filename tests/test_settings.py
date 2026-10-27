import unittest
from pathlib import Path

from pcma10_remote.protocol import DATA, SHOT, Frame, ProtocolError, encode, parse_mark, parse_filename, parse_capabilities
from pcma10_remote.session import RecorderSession, SessionError
from pcma10_remote.settings import BY_KEY
from pcma10_remote.simulator import SimulatedTransport, a10_capabilities


class ExtendedControlTests(unittest.TestCase):
    def test_demo_capabilities_match_the_independent_hardware_capture(self):
        fixture = Path(__file__).with_name('fixtures') / 'a10-capabilities.hex'
        captured = parse_capabilities(bytes.fromhex(fixture.read_text(encoding='ascii')))
        self.assertEqual(a10_capabilities(), captured)

    def connect(self, **kwargs):
        peer = SimulatedTransport(**kwargs)
        events = []
        session = RecorderSession(peer, events.append, ack_timeout=0.3)
        self.addCleanup(session.close)
        session.connect()
        return session, peer, events

    def test_real_a10_capability_formats_offer_only_supported_controls(self):
        session, peer, _ = self.connect()
        result = session.refresh_settings()
        rows = {row['key']: row for row in result['settings']}
        self.assertEqual(len(rows), 16)  # 15 settings plus save-scene.
        self.assertEqual({x['value'] for x in rows['rec-mode']['choices']}, {2, 13, 14, 15, 3, 4, 5, 6, 7})
        self.assertEqual(rows['limiter']['choices'], [{'value': 1, 'label': '开启'}, {'value': 2, 'label': '关闭'}])
        self.assertEqual([x['value'] for x in rows['auto-mark-interval']['choices']], [300, 600, 900, 1800])
        self.assertNotIn('auto-mark-legacy', rows)
        self.assertNotIn('peak-hold', rows)
        self.assertTrue(result['state']['features']['rehearsal'])
        self.assertFalse(result['state']['features']['remote_peak_reset'])
        self.assertEqual({x['item'] for x in result['additional_capabilities']}, {'0102', '0103', '0204'})

    def test_all_supported_settings_write_and_read_back(self):
        session, peer, _ = self.connect()
        for row in session.refresh_settings()['settings']:
            if row['kind'] == 'action':
                continue
            with self.subTest(key=row['key']):
                value = row['choices'][-1]['value']
                result = session.set_setting(row['key'], value)
                self.assertEqual(result['state']['settings'][row['key']], value)
                item = int(row['item'], 16)
                self.assertIn(b'\x15' + item.to_bytes(2, 'big') + value.to_bytes(2, 'big'), peer.actions)

    def test_unsupported_values_and_unknown_items_never_write(self):
        session, peer, _ = self.connect()
        for key, value in [('rec-mode', 8), ('audio-input', 2), ('limiter', 0), ('pre-record', True), ('unknown', 1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                session.set_setting(key, value)
        self.assertFalse(peer.actions)

    def test_rejected_setting_is_not_overridden_by_successful_readback(self):
        session, peer, events = self.connect(reject_settings={0x0509: 0xA1})
        with self.assertRaisesRegex(SessionError, '0xA1'):
            session.set_setting('pre-record', 1)
        session.refresh_settings()
        self.assertEqual(session.snapshot()['settings']['pre-record'], 2)
        self.assertFalse(any(e['event'] == 'setting_confirmed' for e in events))

    def test_success_reply_without_actual_value_change_is_not_success(self):
        session, _, events = self.connect(ignore_settings={0x0509})
        with self.assertRaisesRegex(SessionError, '读回'):
            session.set_setting('pre-record', 1)
        self.assertFalse(any(e['event'] == 'setting_confirmed' for e in events))

    def test_interval_enables_auto_mark_in_documented_order(self):
        session, peer, _ = self.connect()
        session.set_setting('auto-mark-interval', 900)
        self.assertEqual(peer.actions[:2], [bytes.fromhex('15 05 07 00 01'), bytes.fromhex('15 05 06 03 84')])
        self.assertEqual(session.snapshot()['settings']['auto-mark'], 1)

    def test_interval_failure_keeps_visible_partial_result_without_retry(self):
        session, peer, _ = self.connect(reject_settings={0x0506: 0xA1})
        with self.assertRaises(SessionError):
            session.set_setting('auto-mark-interval', 900)
        self.assertEqual(session.snapshot()['settings']['auto-mark'], 1)
        self.assertEqual(peer.actions.count(bytes.fromhex('15 05 06 03 84')), 1)

    def test_rehearsal_has_confirmed_state_and_blocks_settings(self):
        session, peer, _ = self.connect()
        self.assertEqual(session.action('rehearsal-start')['rehearsal_status'], 2)
        self.assertEqual(session.snapshot()['remote_status'], 2)
        for operation in [lambda: session.set_setting('pre-record', 1), lambda: session.action('gain-up')]:
            with self.assertRaises(SessionError):
                operation()
        self.assertEqual(session.action('rehearsal-stop')['rehearsal_status'], 4)
        session.action('record')
        with self.assertRaises(SessionError):
            session.action('rehearsal-start')
        self.assertNotIn(b'\x10\x01', peer.actions)

    def test_manual_input_is_an_explicit_operation(self):
        session, peer, _ = self.connect()
        session.set_setting('internal-mic', 1)
        before = peer.actions.copy()
        with self.assertRaisesRegex(SessionError, '手动'):
            session.action('rehearsal-start')
        self.assertEqual(peer.actions, before)
        session.action('manual-input')
        self.assertEqual(session.snapshot()['settings']['internal-mic'], 8)
        self.assertEqual(session.action('rehearsal-start')['rehearsal_status'], 2)

    def test_four_mark_types_and_utf8_names_return_device_receipts(self):
        session, peer, _ = self.connect()
        session.action('pause')
        for mark_type in range(1, 5):
            name = f'测试 {mark_type}'
            mark = session.action('mark', mark_type=mark_type, mark_name=name)['last_mark']
            self.assertEqual((mark['type'], mark['name'], mark['number']), (mark_type, name, mark_type))
        self.assertEqual(session.action('last-mark')['last_mark']['type'], 4)
        self.assertEqual(peer.marks, 4)

    def test_save_scene_and_apply_scene_use_distinct_commands(self):
        session, peer, _ = self.connect()
        session.set_setting('rec-mode', 13)
        session.set_setting('save-scene', 2)
        self.assertEqual(peer.presets[2][0x0101], 13)
        session.set_setting('rec-mode', 6)
        result = session.set_setting('scene', 10)
        self.assertEqual(result['state']['settings']['rec-mode'], 13)
        self.assertIn(bytes.fromhex('15 06 02 00 02'), peer.actions)

    def test_filename_and_device_notifications(self):
        session, _, events = self.connect()
        session.action('pause')
        self.assertEqual(session.refresh()['filename'], 'DEMO_001.WAV')
        session._apply(bytes.fromhex('41 01 00 03 11 60'))
        notices = [e for e in events if e['event'] == 'recorder_notice']
        self.assertEqual(notices[-1]['message_id'], 201056)

    def test_extended_receipt_parsers_reject_truncated_data(self):
        for payload in [bytes.fromhex('12 00 01 06 03'), bytes.fromhex('12 00 01 06 03 00 05 00 00 00 01 01 41')]:
            with self.assertRaises(ProtocolError):
                parse_mark(payload)
        with self.assertRaises(ProtocolError):
            parse_filename(b'\x34\x00\x01\x07\x08\x08abc')
        self.assertIsNone(parse_mark(b'\x14\x00\x00'))

    def test_setting_a0_result_is_accepted_only_after_actual_readback(self):
        session, peer, _ = self.connect()
        original = peer._command
        def coupled_setting(payload):
            if payload == bytes.fromhex('15 01 01 00 0d'):
                peer.values[0x0101] = 13
                peer._publish(bytes.fromhex('16 a0 01 01 01 00 0d'))
            else:
                original(payload)
        peer._command = coupled_setting
        result = session.set_setting('rec-mode', 13)
        self.assertEqual(result['state']['settings']['rec-mode'], 13)

    def test_missing_rehearsal_and_peak_capabilities_never_send_controls(self):
        session, peer, _ = self.connect(capabilities={})
        for action in ['rehearsal-start', 'peak-reset']:
            with self.assertRaises(SessionError):
                session.action(action)
        self.assertFalse(peer.actions)

    def test_stereo_device_peak_data_and_pre_record_count_are_retained(self):
        session, _, _ = self.connect()
        session._apply(bytes.fromhex('40 01 01 4c 32 50 19 00 00 00 00 00 01 4a 19 4d 32 00 01'))
        state = session.snapshot()
        self.assertEqual((state['left_db'], state['right_db']), (-12.5, -16.25))
        self.assertEqual((state['peak_left_db'], state['peak_right_db']), (-10.25, -13.5))
        self.assertTrue(state['peak_right_over'])
        session._apply(bytes.fromhex('25 00 01 07 0c 00 04'))
        self.assertEqual(session.snapshot()['pre_record_seconds'], 4)

    def test_malformed_peak_packet_does_not_partially_update_state(self):
        session, _, _ = self.connect()
        before = session.snapshot()
        with self.assertRaises(ProtocolError):
            session._apply(bytes.fromhex('40 01 01 4c 32 50 19 00 00 00 00 00 01 4a 19 4d 32 00 02'))
        after = session.snapshot()
        for key in ['left_db', 'right_db', 'peak_left_db', 'peak_right_db', 'peak_left_over', 'peak_right_over']:
            self.assertEqual(after[key], before[key])
