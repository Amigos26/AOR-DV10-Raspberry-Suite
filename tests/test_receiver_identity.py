import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aor_dv10.protocol.codec import CommandChannel
from aor_dv10.transport.serial_transport import SerialTransport
from aor_dv10.web import receiver_context as context


class Transport:
    def __init__(self, rows):
        self.rows = list(rows)
        self.writes = []
    def read_line(self, timeout):
        return self.rows.pop(0) if self.rows else None
    def write_line(self, data):
        self.writes.append(data)


def packet(slot='0102', lm='0901', rf='0438.67500', tag='DEMO ONE'):
    return f'RX MS{slot} RF{rf} ST012.50 MD000 LM{lm} TT{tag}'


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'channels.csv'
        self.path.write_text('DEPMICRO-BACKUP,MEM BANK,AR-DV10,P,\n'
                             'MB,01,0,Okreg 1\nMB,04,0,Okreg 4 / local\n'
                             'MC,0102,0,0438.67500,012.50,000.00,0,000,DEMO ONE\n'
                             'MC,0408,0,0438.67500,012.50,000.00,0,000,DEMO TWO\n')
        self.patcher = patch.object(context.viewer_upload, 'CHANNEL_PATH', self.path)
        self.patcher.start()
        context._signature = None

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def decode(self, text):
        return context.decode_snapshot((text, time.monotonic(), time.time()))

    def test_same_frequency_selects_exact_bank_and_slot(self):
        a = self.decode(packet())
        b = self.decode(packet('0408', tag='DEMO TWO'))
        self.assertEqual(a['channel_name'], 'Okreg 1 → DEMO ONE')
        self.assertEqual(b['channel_name'], 'Okreg 4 / local → DEMO TWO')
        self.assertEqual(b['memory_channel'], 8)
        self.assertEqual(a['frequency_hz'], b['frequency_hz'])

    def test_live_tag_beats_stale_csv_and_other_frequency_matches(self):
        self.assertEqual(self.decode(packet(tag='NEW NAME'))['memory_name'], 'NEW NAME')
        self.assertIsNone(self.decode(packet(rf='0439.00000', tag=''))['memory_name'])

    def test_vfo_does_not_guess_a_bank(self):
        hit = self.decode(packet().replace('MS0102', 'VFA').replace(' TTDEMO ONE', ''))
        self.assertIsNone(hit['memory_bank'])
        self.assertIsNone(hit['channel_name'])

    def test_telemetry_is_filtered_without_consuming_command_reply(self):
        t = Transport([('10' + packet()).encode(), b'10RF0438.67500', b'10LM0901', b'20RF0145.50000'])
        chan = CommandChannel(t)
        self.assertEqual(chan.read('RF').value, '0145.50000')
        self.assertEqual(t.writes, [b'RF\r'])
        self.assertEqual(chan.receiver_snapshot[0], packet())

    def test_pending_multi_line_reply_skips_rt(self):
        chan = CommandChannel(Transport([('10' + packet()).encode(), b'21MA0102data']))
        self.assertEqual(chan.read_pending().value, 'MA0102data')

    def test_idle_reader_keeps_command_continuation(self):
        t = Transport([('10' + packet()).encode(), b'21MA0102data'])
        t._serial = SimpleNamespace(in_waiting=100)
        chan = CommandChannel(t)
        chan.collect_receiver_reports()
        self.assertEqual(chan.receiver_snapshot[0], packet())
        self.assertEqual(chan.read_pending().value, 'MA0102data')
        self.assertFalse(chan._response_queue)

    def test_last_received_retains_slot_while_scanning_same_frequency(self):
        chan = CommandChannel(Transport([('10' + packet()).encode(),
                                        ('10' + packet('0408', '1200', tag='DEMO TWO')).encode(), b'20']))
        chan.write('RT', '05')
        result = context.enrich_status({'connected': True, 'frequency_hz': 999}, SimpleNamespace(_chan=chan))
        self.assertEqual(result['memory_bank'], 4)
        self.assertFalse(result['squelch_open'])
        self.assertEqual(result['last_received']['memory_bank'], 1)
        self.assertEqual(result['last_received']['memory_name'], 'DEMO ONE')

    def test_stale_snapshot_does_not_supply_wrong_name(self):
        chan = CommandChannel(Transport([]))
        chan.receiver_snapshot = (packet(), time.monotonic()-10, time.time()-10)
        result = context.enrich_status({'connected': True, 'frequency_hz': 999, 'squelch_open': True}, SimpleNamespace(_chan=chan))
        self.assertIsNone(result['channel_name'])
        self.assertFalse(result['squelch_open'])

    def test_fragment_survives_timeout(self):
        serial = SerialTransport(port='fake')
        class FakeSerial:
            is_open = True
            chunks = [b'1', b'0', b'R', b'X', b'\r']
            def read(self, n):
                time.sleep(.003)
                return self.chunks.pop(0) if self.chunks else b''
        serial._serial = FakeSerial()
        self.assertIsNone(serial.read_line(.005))
        self.assertEqual(serial.read_line(.1), b'10RX')
        self.assertEqual(serial._line_buffer, bytearray())


if __name__ == '__main__':
    unittest.main()
