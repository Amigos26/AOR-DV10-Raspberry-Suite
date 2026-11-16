#!/usr/bin/env python3
"""Read all receiver memory through the running control panel; never tune/write it."""
import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import time
import urllib.request

from channel_names import parse_names

BASE = Path(os.environ.get('AOR_VIEWER_DATA_DIR', str(Path.home() / '.local/share/aor-dv10'))).expanduser()
CSV_PATH = BASE / 'channels.csv'


def control_url():
    return os.environ.get('AOR_CONTROL_URL', 'http://127.0.0.1:8080').rstrip('/')


def wait_for_receiver(url):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + '/api/status', timeout=3) as response:
                status = json.load(response)
            if status.get('connected'):
                family = status.get('device_family', '')
                return 'AR-' + family if family in ('DV1', 'DV10', 'DV3') else 'AR-DV10'
        except OSError:
            pass
        time.sleep(2)
    raise RuntimeError('Receiver control panel did not become ready; previous CSV retained')


def file_digest():
    return hashlib.sha256(CSV_PATH.read_bytes()).hexdigest() if CSV_PATH.exists() else None


def combine_exports(exports, model='AR-DV10'):
    if len(exports) != 40:
        raise ValueError('Expected all 40 memory banks')
    banks = []
    channels = []
    programmed = 0
    named = 0
    for bank, text in enumerate(exports):
        rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff')), strict=True))
        rows = [row for row in rows if row]
        if not rows or rows[0][:2] != ['DEPMICRO-BACKUP', 'MEM BANK']:
            raise ValueError('Invalid export header in bank %02d' % bank)
        bank_rows = [row for row in rows[1:] if row[0] == 'MB']
        channel_rows = [row for row in rows[1:] if row[0] == 'MC']
        if len(bank_rows) != 1 or len(bank_rows[0]) != 4 or bank_rows[0][1] != '%02d' % bank:
            raise ValueError('Bank identifier mismatch')
        expected = {'%02d%02d' % (bank, channel) for channel in range(50)}
        if len(channel_rows) != 50 or any(len(row) != 9 for row in channel_rows) or {row[1] for row in channel_rows} != expected:
            raise ValueError('Incomplete channel list in bank %02d' % bank)
        if len(rows) != 52:
            raise ValueError('Unexpected extra export records')
        parse_names(text)  # Validate every programmed frequency before writing anything.
        programmed += sum(bool(row[3].strip()) for row in channel_rows)
        named += sum(bool(row[3].strip()) and bool(row[8].strip()) for row in channel_rows)
        banks.extend(bank_rows)
        channels.extend(channel_rows)
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    writer.writerow(['DEPMICRO-BACKUP', 'MEM BANK', model, 'P', datetime.now(timezone.utc).isoformat()])
    writer.writerows(banks)
    writer.writerows(channels)
    combined = output.getvalue()
    names = parse_names(combined)
    if not names:
        raise ValueError('No named frequencies received; retaining the previous CSV')
    return combined.encode('utf-8-sig'), {'banks': 40, 'slots': 2000, 'programmed': programmed, 'named_channels': named, 'named_frequencies': len(names)}


def sync(check_only=False):
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (BASE / '.sync-radio-channels.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        original_digest = file_digest()
        url = control_url()
        model = wait_for_receiver(url)
        exports = []
        for bank in range(40):
            with urllib.request.urlopen(url + '/api/memory/live_export/%d' % bank, timeout=30) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError('Oversized memory export')
                exports.append(raw.decode('utf-8-sig'))
            time.sleep(.2)
        content, counts = combine_exports(exports, model=model)
        if check_only:
            print('CHECK OK:', json.dumps(counts))
            return
        if file_digest() != original_digest:
            raise RuntimeError('CSV was changed during radio read; keeping the newer file')
        request = urllib.request.Request(url + '/api/viewer/channels', data=content,
            headers={'Content-Type': 'text/csv', 'X-Viewer-Upload': '1'})
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        assert result['frequencies'] == counts['named_frequencies']
        counts.update(model=model, last_success=datetime.now(timezone.utc).isoformat(), backup_created=result['backup_created'])
        temporary = BASE / '.radio-channel-sync-state.pending'
        temporary.write_text(json.dumps(counts, ensure_ascii=False, indent=2))
        temporary.replace(BASE / 'radio-channel-sync-state.json')
        print('SYNC OK:', json.dumps(counts))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    sync(args.check_only)
