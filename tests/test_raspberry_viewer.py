import asyncio
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from aor_dv10.web import viewer_upload

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'raspberry-viewer'))
from channel_names import ChannelNames, parse_names

spec = importlib.util.spec_from_file_location('read_only_viewer', ROOT / 'raspberry-viewer/server.py')
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


class ViewerTests(unittest.TestCase):
    def test_original_export_and_reload(self):
        text = '\ufeffDEPMICRO-BACKUP,MEM BANK,AR-DV10,P2504B,example\nMB,00,0,Test\nMC,0000,0,0145.50000,012.50,000.00,0F0,0,Przykład\nMC,0001,,,,,,,\nMC,0002,0,0145.50000,012.50,000.00,0F0,0,Druga nazwa\n'
        self.assertEqual(parse_names(text), {145500000: 'Test → Druga nazwa / Test → Przykład'})
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'channels.csv'
            path.write_text(text, encoding='utf-8')
            loader = ChannelNames(path)
            self.assertIn('Przykład', loader.load()[145500000])
            path.write_text('frequency_mhz;name\n145.500;Nowa nazwa\n', encoding='utf-8')
            self.assertEqual(loader.load()[145500000], 'Nowa nazwa')

    def test_hold_last_reception(self):
        def hit(bank, name):
            return {'frequency_hz': 145500000, 'memory_bank': bank, 'memory_channel': 7,
                    'bank_name': 'Bank %d' % bank, 'memory_name': name,
                    'channel_name': 'Bank %d → %s' % (bank, name),
                    'receiver_position': 'MS%02d07' % bank,
                    'identity_source': 'receiver_report', 'received_at': 1234 + bank}
        first, second = hit(1, 'DEMO ONE'), hit(4, 'DEMO TWO')
        state = dict(first, connected=True, squelch_open=False)
        with patch.object(viewer, 'fetch_json', side_effect=lambda path: dict(state)) as fetch, \
             patch.object(viewer, 'last_received', None), patch.object(viewer, 'cached', None):
            def status():
                viewer.cached_at = 0
                return viewer.status()
            self.assertIsNone(status()['last_received'])
            state.update(squelch_open=True, last_received=first)
            self.assertEqual(status()['last_received'], first)
            # Same RF in another bank while scanning must retain the first reception.
            state.update(second, squelch_open=False, last_received=first)
            self.assertEqual(status()['last_received'], first)
            state.update(squelch_open=True, last_received=second)
            self.assertEqual(status()['last_received'], second)
            self.assertTrue(all(call.args == ('/api/status',) for call in fetch.call_args_list))

    def test_missing_identity_does_not_capture_a_frequency_match(self):
        state = {'connected': True, 'frequency_hz': 145500000, 'squelch_open': True,
                 'channel_name': 'Ambiguous frequency label'}
        with patch.object(viewer, 'fetch_json', return_value=state), \
             patch.object(viewer, 'last_received', None), patch.object(viewer, 'cached', None):
            self.assertIsNone(viewer.status()['last_received'])

    def test_upload_is_validated_and_backed_up(self):
        def request(raw, allowed=True):
            headers = [(b'host', b'localhost:8080'), (b'origin', b'http://localhost:8080')]
            if allowed:
                headers.append((b'x-viewer-upload', b'1'))
            async def receive():
                return {'type': 'http.request', 'body': raw, 'more_body': False}
            return Request({'type': 'http', 'method': 'POST', 'scheme': 'http', 'path': '/api/viewer/channels', 'query_string': b'', 'headers': headers}, receive)
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'data/channels.csv'
            with patch.object(viewer_upload, 'CHANNEL_PATH', path):
                original = 'frequency_mhz;name\n145.500;Pierwsza nazwa\n'.encode()
                result = asyncio.run(viewer_upload.upload_channels(request(original)))
                self.assertEqual(result['frequencies'], 1)
                self.assertFalse(result['backup_created'])
                for raw, expected in ((b'bad', 400), (b'', 400), (b'x' * (1024 * 1024 + 1), 413)):
                    with self.assertRaises(HTTPException) as caught:
                        asyncio.run(viewer_upload.upload_channels(request(raw)))
                    self.assertEqual(caught.exception.status_code, expected)
                    self.assertEqual(path.read_bytes(), original)
                with self.assertRaises(HTTPException) as caught:
                    asyncio.run(viewer_upload.upload_channels(request(original, False)))
                self.assertEqual(caught.exception.status_code, 403)
                replacement = 'frequency_mhz;name\n145.600;Żółć\n'.encode()
                result = asyncio.run(viewer_upload.upload_channels(request(replacement)))
                self.assertTrue(result['backup_created'])
                self.assertEqual(path.read_bytes(), replacement)
                self.assertEqual(next((path.parent / 'backups').glob('*.csv')).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
