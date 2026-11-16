import asyncio
import copy
import json
import re
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request
from aor_dv10.device_types import MemoryBankInfo, MemoryChannelInfo
from aor_dv10.memory import MemoryBank, MemoryChannel, write_backup_csv, parse_backup_csv
from aor_dv10.protocol.parsing import parse_composite_fields
from aor_dv10.web import memory_editor as editor


class Channel:
    timeout=1.5
    @contextmanager
    def transaction(self):
        with threading.RLock(): yield


class Device:
    def __init__(self):
        self._chan=Channel();self.calls=[];self.pending={};self.pending_names={};self.callback=None
        self.rows={b:[MemoryChannelInfo(bank=b,channel=i,registered=False) for i in range(50)] for b in range(40)}
        self.names={b:'' for b in range(40)};self.protected=False
        self.names[0]='Anchor';self.names[11]='Target'
        for b,i,freq,name in [(0,0,145500000,'UNCHANGED'),(11,1,146500000,'BEFORE')]:
            self.rows[b][i]=MemoryChannelInfo(bank=b,channel=i,registered=True,frequency_hz=freq,step_hz=12500,step_adjust_hz=0,mode='0F0',tag=name)
    def read_memory_bank(self,b):return copy.deepcopy(self.rows[b])
    def get_memory_bank_info(self,b):return MemoryBankInfo(bank=b,tag=self.names[b],protect=self.protected if b==11 else False)
    def enter_vfo_mode(self,vfo):self.calls.append(('VF',vfo))
    def _commit_other(self,b):
        for key,c in list(self.pending.items()):
            if key[0]!=b:self.rows[key[0]][key[1]]=c;del self.pending[key]
        for bank,name in list(self.pending_names.items()):
            if bank!=b:self.names[bank]=name;del self.pending_names[bank]
    def raw(self,code,value):
        self.calls.append((code,value));b=int(value[:2]);self._commit_other(b)
        if code=='MX':
            i=int(value[2:4]);fields=parse_composite_fields(value[5:],tag_field='TT')
            self.pending[b,i]=MemoryChannelInfo(bank=b,channel=i,registered=True,frequency_hz=round(float(fields['RF'])*1e6),step_hz=round(float(fields['ST'])*1000),step_adjust_hz=round(float(fields['SH'])*1000),mode=fields['MD'],tag=fields['TT'].strip(),pass_channel=fields['MP']=='1',write_protect=fields['PT']=='1')
        elif code=='MW':self.pending_names[b]=parse_composite_fields(value[3:],tag_field='TT')['TT'].strip()
        else:raise AssertionError(code)
        if self.callback:self.callback(code,b)
    def delete_memory_channel(self,b,i):
        self.calls.append(('MQ',b,i));self._commit_other(b)
        self.pending[b,i]=MemoryChannelInfo(bank=b,channel=i,registered=False)


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.csv=self.base/'channels.csv'
        self.device=Device();self.job={'id':'a'*32,'bank':11,'state':'queued','completed':0,'total':1,'backup_id':None,'_cancel':threading.Event()}
        self.patches=[patch.object(editor,'BACKUPS',self.base/'backups'),patch.object(editor.upload,'CHANNEL_PATH',self.csv),patch.object(editor.time,'sleep'),patch.object(editor,'_device',lambda:self.device)]
        for p in self.patches:p.start()
        self.csv.write_text(write_backup_csv([MemoryBank(index=0,title='Anchor',protect=False),MemoryBank(index=11,title='Target',protect=False)],[MemoryChannel(bank=0,channel=0,frequency_hz=145500000,step_hz=12500,mode='0F0',name='UNCHANGED'),MemoryChannel(bank=11,channel=1,frequency_hz=146500000,step_hz=12500,mode='0F0',name='BEFORE')]))
        self.original=editor._read(self.device,11)
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def plan(self,whole=False):
        channels=copy.deepcopy(self.original['channels'] if whole else [self.original['channels'][1]])
        if whole:
            channels[1].update(name='',frequency_hz=None,skip=False)
            channels[2].update(name='NEW',frequency_hz=147500000)
        else:channels[0]['name']='AFTER'
        return editor._validate({'revision':self.original['revision'],'channel':None if whole else 1,'name':'New bank' if whole else 'Target','channels':channels},11)
    def test_single_write_has_durable_bank_backup_and_preserves_other_slots(self):
        def before_write(code,b):
            self.assertTrue((editor.BACKUPS/self.job['id']/'before.json').exists())
        self.device.callback=before_write
        anchor=editor._read(self.device,0)
        editor._execute(self.job,self.plan())
        self.assertEqual(self.job['state'],'done');self.assertEqual(self.job['result']['channels'][1]['name'],'AFTER')
        self.assertEqual(editor._read(self.device,0),anchor)
        self.assertEqual(self.device._chan.timeout,1.5)
        saved=json.loads((editor.BACKUPS/self.job['id']/'before.json').read_text())
        self.assertEqual(len(saved['memory']['channels']),50);self.assertEqual(saved['memory']['channels'][1]['name'],'BEFORE')
        _,channels=parse_backup_csv(self.csv.read_text(encoding='utf-8-sig'))
        self.assertEqual(next(c for c in channels if c.bank==0).name,'UNCHANGED')
        self.assertEqual(next(c for c in channels if c.bank==11 and c.channel==1).name,'AFTER')
    def test_whole_bank_write_clears_explicit_empty_slots_and_verifies_all_50(self):
        editor._execute(self.job,self.plan(True))
        self.assertEqual(self.job['completed'],50);self.assertEqual(self.job['state'],'done')
        result=self.job['result'];self.assertIsNone(result['channels'][1]['frequency_hz']);self.assertEqual(result['channels'][2]['name'],'NEW');self.assertEqual(result['name'],'New bank')
    def test_backup_failure_prevents_memory_writes(self):
        original=editor._json
        def fail(path,value):
            if path.name=='before.json':raise OSError('disk full')
            return original(path,value)
        with patch.object(editor,'_json',side_effect=fail):
            with self.assertRaises(OSError):editor._execute(self.job,self.plan())
        self.assertFalse([c for c in self.device.calls if c[0]!='VF'])
    def test_stale_revision_blocks_writes(self):
        plan=self.plan();self.device.rows[11][1].tag='EXTERNAL'
        with self.assertRaisesRegex(RuntimeError,'zmieniła'):editor._execute(self.job,plan)
        self.assertFalse([c for c in self.device.calls if c[0]!='VF'])
    def test_inconsistent_reads_block_backup_and_memory_writes(self):
        original=self.device.read_memory_bank
        count=[0]
        def unstable(bank):
            rows=original(bank);count[0]+=1;rows[1].tag='CHANGE%d'%count[0];return rows
        with patch.object(self.device,'read_memory_bank',side_effect=unstable):
            with self.assertRaisesRegex(RuntimeError,'niespójne'):editor._execute(self.job,self.plan())
        self.assertFalse([c for c in self.device.calls if c[0]!='VF'])
        self.assertFalse((editor.BACKUPS/self.job['id']/'before.json').exists())
    def test_read_requires_explicit_authorized_action(self):
        def request(marker):
            headers=[(b'host',b'localhost')]
            if marker:headers.append((b'x-memory-editor',b'1'))
            return Request({'type':'http','method':'POST','path':'/read','scheme':'http','server':('localhost',80),'query_string':b'','headers':headers})
        async def run():
            with patch.object(editor,'_command_lock',asyncio.Lock()):
                with self.assertRaises(HTTPException):await editor.read_bank(11,request(False))
                self.assertEqual(self.device.calls,[])
                result=await editor.read_bank(11,request(True))
                self.assertEqual(len(result['channels']),50)
                self.assertEqual(self.device.calls,[('VF','A')])
        asyncio.run(run())
    def test_protection_blocks_writes(self):
        self.device.rows[11][1].write_protect=True;self.original=editor._read(self.device,11)
        with self.assertRaisesRegex(RuntimeError,'chroniony'):editor._execute(self.job,self.plan())
        self.assertFalse([c for c in self.device.calls if c[0]!='VF'])
    def test_cancel_before_writing_keeps_complete_backup(self):
        self.job['_cancel'].set();editor._execute(self.job,self.plan())
        self.assertEqual(self.job['state'],'cancelled');self.assertFalse([c for c in self.device.calls if c[0]!='VF'])
        self.assertTrue((editor.BACKUPS/self.job['id']/'before.json').exists())
    def test_cancel_between_channels_preserves_unwritten_slots(self):
        plan=self.plan(True)
        def cancel(code,b):
            if code=='MX' and b==11:self.job['_cancel'].set()
        self.device.callback=cancel;editor._execute(self.job,plan)
        self.assertEqual(self.job['state'],'cancelled');self.assertEqual(self.job['result']['channels'][2]['name'],'NEW')
        self.assertEqual(self.job['result']['name'],'Target')
    def test_invalid_payloads_and_cross_origin_requests(self):
        for mutate in [lambda p:p['channels'][0].update(frequency_hz=True),lambda p:p['channels'][0].update(name='ŁÓDŹ'),lambda p:p['channels'][0].update(step_hz=42),lambda p:p.update(channels=[]),lambda p:p.update(channel=50)]:
            plan=self.plan();mutate(plan)
            with self.assertRaises(HTTPException):editor._validate(plan,11)
        request=Request({'type':'http','method':'POST','path':'/','scheme':'http','server':('localhost',80),'query_string':b'','headers':[(b'host',b'localhost'),(b'x-memory-editor',b'1'),(b'origin',b'http://elsewhere')]})
        with self.assertRaises(HTTPException) as exc:editor._authorize(request)
        self.assertEqual(exc.exception.status_code,403)


if __name__=='__main__':unittest.main()


