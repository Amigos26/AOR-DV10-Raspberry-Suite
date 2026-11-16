import contextlib
import csv
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'raspberry-viewer'))
import sync_radio_channels as sync
from aor_dv10.protocol.parsing import parse_composite_fields


def exports():
    result = []
    for bank in range(40):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['DEPMICRO-BACKUP', 'MEM BANK', 'AR-DV1', 'P', ''])
        writer.writerow(['MB', '%02d' % bank, '0', 'Bank %d' % bank])
        for channel in range(50):
            row = ['MC', '%02d%02d' % (bank, channel), '', '', '', '', '', '', '']
            if bank == 11 and channel == 0:
                row = ['MC', '1100', '0', '0145.50000', '012.50', '000.00', '0F0', '0', 'Żółć, test']
            writer.writerow(row)
        result.append(output.getvalue())
    return result


class RadioSyncTests(unittest.TestCase):
    def test_complete_export_and_invalid_slots(self):
        banks = exports()
        raw, counts = sync.combine_exports(banks, model='AR-DV1')
        self.assertIn(b'AR-DV1', raw)
        self.assertEqual(counts['slots'], 2000)
        self.assertEqual(sync.parse_names(raw.decode('utf-8-sig')), {145500000: 'Bank 11 → Żółć, test'})
        for bad in (banks[:39], [banks[0]] * 40, banks[:11] + [banks[11].replace('MC,1100', 'MC,1101')] + banks[12:]):
            with self.assertRaises(ValueError):
                sync.combine_exports(bad)

    def test_failed_read_preserves_csv(self):
        banks = exports()
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            path = data / 'channels.csv'
            original = b'frequency_mhz;name\n145.500;Existing\n'
            path.write_bytes(original)
            writes = []
            def open_url(request, **kwargs):
                if isinstance(request, urllib.request.Request):
                    writes.append(request)
                    raise AssertionError('No write allowed after incomplete export')
                if request.endswith('/api/status'):
                    return io.BytesIO(b'{"connected":true,"device_family":"DV1"}')
                bank = int(request.rsplit('/', 1)[-1])
                if bank == 12:
                    raise urllib.error.URLError('Disconnected')
                return io.BytesIO(banks[bank].encode())
            with patch.object(sync, 'BASE', data), patch.object(sync, 'CSV_PATH', path), patch.object(sync.urllib.request, 'urlopen', open_url), patch.object(sync.time, 'sleep'):
                with self.assertRaises(urllib.error.URLError):
                    sync.sync()
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(writes)

    def test_concurrent_manual_upload_is_not_overwritten(self):
        banks = exports()
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            path = data / 'channels.csv'
            path.write_bytes(b'old')
            def open_url(request, **kwargs):
                self.assertIsInstance(request, str)
                if request.endswith('/api/status'):
                    return io.BytesIO(b'{"connected":true,"device_family":"DV1"}')
                bank = int(request.rsplit('/', 1)[-1])
                if bank == 15:
                    path.write_bytes(b'new manual file')
                return io.BytesIO(banks[bank].encode())
            with patch.object(sync, 'BASE', data), patch.object(sync, 'CSV_PATH', path), patch.object(sync.urllib.request, 'urlopen', open_url), patch.object(sync.time, 'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'changed during radio read'):
                    sync.sync()
            self.assertEqual(path.read_bytes(), b'new manual file')

    def test_spaced_dv1_channel_names(self):
        for tag in ('TT STRAZ MIEJSK', 'TTSTRAZ MIEJSK'):
            result = parse_composite_fields('RF0147.87500 ST005.00 ' + tag, tag_field='TT')
            self.assertEqual(result['ST'], '005.00')
            self.assertEqual(result['TT'], 'STRAZ MIEJSK')
        self.assertEqual(parse_composite_fields('PT0 TT', tag_field='TT')['TT'], '')

    def test_unit_schedule_and_opt_out(self):
        spec = importlib.util.spec_from_file_location('installer', ROOT / 'deploy/install-systemd.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        units = installer.build_units(Path('/example/project'), 'pi', 1000)
        self.assertIn('Sun *-*-* 04:00:00 Europe/Warsaw', units['aor-radio-channels-sync.timer'])
        self.assertIn('Persistent=true', units['aor-radio-channels-sync.timer'])
        self.assertNotIn('aor-radio-channels-sync.timer', installer.build_units(Path('/example/project'), 'pi', 1000, weekly=False))


if __name__ == '__main__':
    unittest.main()
