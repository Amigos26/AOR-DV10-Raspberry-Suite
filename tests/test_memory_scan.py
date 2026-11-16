import asyncio
import contextlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request
from aor_dv10.web import memory_scan as scan


class FakeDevice:
    def __init__(self):
        self.commands = []
        self._chan = SimpleNamespace(transaction=contextlib.nullcontext, timeout=1.5)
        self.receiver = 'RX VFA RF0145.50000'
    def read_memory_bank(self, bank):
        self.commands.append(('read', bank))
        return [SimpleNamespace(registered=True, frequency_hz=145500000, pass_channel=False, channel=3)]
    def tune_memory_channel(self, bank, channel):
        self.commands.append(('MR', bank, channel))
    def set_bank_link(self, banks):
        self.commands.append(('BK', banks))
    def raw(self, code, value):
        self.commands.append((code, value))
        self.receiver = 'RX MS%s03 RF0145.50000' % value
    def enter_vfo_mode(self, vfo):
        self.commands.append(('VF', vfo))
        self.receiver = 'RX VFA RF0145.50000'
    def get_receiver_status(self):
        return self.receiver


class MemoryScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'channels.csv'
        self.path.write_text('DEPMICRO-BACKUP,MEM BANK,AR-DV10,P,test\nMB,11,0,AIR\nMB,12,0,RAIL\nMC,1103,0,0145.50000,012.50,000.00,0F0,0,Test\nMC,1200,0,0150.22500,005.00,000.00,0F0,0,Test2\nMC,1201,0,0150.20000,005.00,000.00,0F0,1,Skip\n')
        self.context = patch.object(scan, 'CHANNEL_PATH', self.path)
        self.context.start()
    def tearDown(self):
        self.context.stop()
        self.temporary.cleanup()
    def test_catalog_names_and_eligible_counts(self):
        rows = scan.bank_catalog()['banks']
        self.assertEqual(len(rows), 40)
        self.assertEqual(rows[11]['name'], 'AIR')
        self.assertEqual(rows[12]['channels'], 2)
        self.assertEqual(rows[12]['scannable'], 1)
        self.assertEqual(rows[0]['scannable'], 0)
    def test_native_multi_bank_start(self):
        device = FakeDevice()
        result = scan.start_scan(device, [12, 11, 11])
        self.assertEqual(device.commands, [('MR', 11, 3), ('BK', [11, 12]), ('MS', '11')])
        self.assertTrue(result['running'])
        self.assertEqual(result['banks'], [11, 12])
        self.assertTrue(scan.scan_status(device)['running'])
    def test_single_bank_clears_previous_links(self):
        device = FakeDevice()
        scan.start_scan(device, [11])
        self.assertIn(('BK', None), device.commands)
    def test_stop_enters_vfo_a(self):
        device = FakeDevice()
        result = scan.stop_scan(device)
        self.assertEqual(device.commands, [('VF', 'A')])
        self.assertFalse(result['running'])
    def test_invalid_banks_do_not_send_commands(self):
        for value in ([], [True], ['11'], [-1], [40], None, {'bank':11}):
            with self.assertRaises(HTTPException):
                scan.validate_banks(value)
        device = FakeDevice()
        with self.assertRaises(HTTPException):
            scan.start_scan(device, [0])
        self.assertFalse(device.commands)
    def test_missing_csv_is_reported(self):
        self.path.unlink()
        with self.assertRaises(HTTPException) as caught:
            scan.bank_catalog()
        self.assertEqual(caught.exception.status_code, 409)


    def request(self, body=b'{"banks":[11]}', origin='http://testserver', marker='1'):
        async def receive():
            return {'type':'http.request','body':body,'more_body':False}
        headers=[(b'host',b'testserver')]
        if origin is not None: headers.append((b'origin',origin.encode()))
        if marker is not None: headers.append((b'x-memory-scan',marker.encode()))
        return Request({'type':'http','method':'POST','path':'/api/memory/scan/start','scheme':'http','server':('testserver',80),'headers':headers,'query_string':b''},receive)

    def test_http_origin_header_and_payload_validation(self):
        for request,code in [(self.request(marker=None),403),(self.request(origin='http://elsewhere'),403),(self.request(body=b'{"banks":[true]}'),400),(self.request(body=b'x'*1025),413)]:
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(scan.api_start(request))
            self.assertEqual(caught.exception.status_code,code)

    def test_http_start_stop_preserve_serial_timeout(self):
        device=FakeDevice()
        previous=(scan._get_device,scan._command_lock)
        try:
            async def run():
                scan.configure(lambda:device,asyncio.Lock())
                result=await scan.api_start(self.request())
                self.assertTrue(result['running'])
                self.assertEqual(device._chan.timeout,1.5)
                result=await scan.api_stop(self.request())
                self.assertFalse(result['running'])
            asyncio.run(run())
            self.assertIn(('MS','11'),device.commands)
            self.assertEqual(device.commands[-1],('VF','A'))
        finally:scan.configure(*previous)

    def test_device_error_preserves_serial_timeout(self):
        from aor_dv10.protocol.codec import DV10Error
        device=FakeDevice()
        previous=(scan._get_device,scan._command_lock)
        def fail(device): raise DV10Error('test rejection')
        async def run():
            scan.configure(lambda:device,asyncio.Lock())
            with self.assertRaises(HTTPException) as caught:
                await scan.run_command(fail)
            self.assertEqual(caught.exception.status_code,502)
        try:
            asyncio.run(run())
            self.assertEqual(device._chan.timeout,1.5)
        finally:scan.configure(*previous)


if __name__ == '__main__':
    unittest.main()

