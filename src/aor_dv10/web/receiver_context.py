"""Coherent receiver identity from unsolicited RT reports; never infer a bank by RF."""
import csv
import io
import re
import threading
import time
from decimal import Decimal

from . import viewer_upload

_signature = None
_slots = {}
_banks = {}
_RX = re.compile(r'^RX\s+(MS\d{4}|MR\d{4}|VF[A-Z]|SR\d{2}|VS[A-Z])\s+RF(\d+\.\d+)\s+ST\S+\s+MD(\S+)\s+LM(\d{4})(?:\s+TT(.*))?$')


def ensure_receiver_stream(device):
    chan = getattr(device, '_chan', None)
    if chan is None or not device.connected:
        return
    if not getattr(chan, '_receiver_reader_started', False):
        with chan.transaction():
            if not getattr(chan, '_receiver_reader_started', False):
                chan._receiver_reader_started = True
                def collect():
                    try:
                        while device.connected:
                            try:
                                chan.collect_receiver_reports()
                            except Exception:
                                pass
                            time.sleep(.1)
                    finally:
                        chan._receiver_reader_started = False
                threading.Thread(target=collect, daemon=True, name='receiver-reports').start()
    # Retry after reconnection or after another client disables RT; no RX query.
    now = time.monotonic()
    if now < getattr(chan, '_receiver_stream_check', 0):
        return
    with chan.transaction():
        chan.write('RE', '1')
        chan.write('RT', '05')
        chan._receiver_stream_check = now + 30


def _catalog():
    global _signature, _slots, _banks
    with viewer_upload._write_lock:
        try:
            path = viewer_upload.CHANNEL_PATH
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if signature == _signature:
                return _banks, _slots
            if stat.st_size > 1024 * 1024:
                raise ValueError('Channel file exceeds 1 MB')
            rows = list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
            banks, slots = {}, {}
            if rows and rows[0][:2] == ['DEPMICRO-BACKUP', 'MEM BANK']:
                for row in rows[1:]:
                    if row and row[0] == 'MB' and len(row) == 4:
                        banks[int(row[1])] = row[3].strip()
                    elif row and row[0] == 'MC' and len(row) == 9 and row[3].strip():
                        slots[(int(row[1][:2]), int(row[1][2:]))] = (
                            int(Decimal(row[3]) * 1000000), row[8].strip())
            _banks, _slots, _signature = banks, slots, signature
        except (OSError, UnicodeError, ValueError, ArithmeticError, csv.Error):
            # An unreadable/replaced catalog must not preserve a stale slot name.
            _banks, _slots, _signature = {}, {}, None
        return _banks, _slots


def decode_snapshot(record):
    if record is None:
        return None
    raw, captured_monotonic, captured_wall = record
    match = _RX.fullmatch(raw)
    if not match:
        return None
    position, rf, mode, lm, tag = match.groups()
    memory = position[:2] in ('MS', 'MR')
    bank = int(position[2:4]) if memory else None
    channel = int(position[4:6]) if memory else None
    frequency = int(Decimal(rf) * 1000000)
    banks, slots = _catalog()
    slot = slots.get((bank, channel))
    name = (tag or '').strip()
    # The live tag is authoritative. CSV fills missing tags only for the same slot/RF.
    if not name and slot and slot[0] == frequency:
        name = slot[1]
    bank_name = banks.get(bank) or (f'{bank:02d}' if memory else '')
    label = ' → '.join(part for part in (bank_name, name) if part) or None
    return {
        'frequency_hz': frequency, 'memory_bank': bank, 'memory_channel': channel,
        'bank_name': bank_name or None, 'memory_name': name or None,
        'channel_name': label, 'receiver_position': position,
        'identity_source': 'receiver_report', 'received_at': captured_wall,
        'snapshot_age': max(0, time.monotonic() - captured_monotonic),
        'squelch_state': int(lm[-1]), 'squelch_open': int(lm[-1]) in (1, 2),
        'smeter': lm, 'smeter_dbm': -int(lm[:3]), 'mode': mode,
    }


def enrich_status(payload, device):
    chan = getattr(device, '_chan', None)
    payload.update(channel_name=None, bank_name=None, memory_name=None,
                   memory_bank=None, memory_channel=None, identity_source=None,
                   last_received=None)
    if chan is None or not payload.get('connected'):
        return payload
    with chan.transaction():
        current = decode_snapshot(getattr(chan, 'receiver_snapshot', None))
        held = decode_snapshot(getattr(chan, 'receiver_last_received', None))
    if current and current['snapshot_age'] < 3:
        payload.update(current)
    else:
        # Without a coherent report, even an open LM read must not capture a guessed name.
        payload['squelch_open'] = False
    if held:
        payload['last_received'] = held
    return payload
