"""Names from an original AR-DV10 Connect export or a simple UTF-8 CSV."""
import csv
import io
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path


def add_name(grouped, raw_frequency, raw_name, row_number):
    try:
        hz = Decimal(raw_frequency.strip().replace(',', '.')) * 1000000
        if not hz.is_finite() or hz <= 0 or hz != hz.to_integral_value():
            raise ValueError('Invalid frequency')
        name = raw_name.strip()
        if name:
            grouped.setdefault(int(hz), set()).add(name)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Invalid frequency in row %d' % row_number) from exc


def parse_connect(lines):
    reader = csv.reader(io.StringIO('\n'.join(lines)), strict=True)
    header = next(reader)
    if len(header) < 3 or header[:2] != ['DEPMICRO-BACKUP', 'MEM BANK']:
        raise ValueError('Expected AR-DV10 Connect memory-bank export')
    grouped = {}
    for row_number, row in enumerate(reader, 2):
        if row[0] == 'MB':
            if len(row) != 4:
                raise ValueError('Invalid bank record in row %d' % row_number)
            continue
        if row[0] != 'MC' or len(row) != 9:
            raise ValueError('Invalid channel record in row %d' % row_number)
        if not row[3].strip():
            continue  # Empty receiver memory slot.
        add_name(grouped, row[3], row[8], row_number)
    return {hz: ' / '.join(sorted(values)) for hz, values in grouped.items()}


def parse_names(text):
    lines = [line for line in text.lstrip('\ufeff').splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not lines:
        return {}
    if lines[0].startswith('DEPMICRO-BACKUP,'):
        return parse_connect(lines)
    delimiter = ';' if ';' in lines[0] else ','
    reader = csv.DictReader(io.StringIO('\n'.join(lines)), delimiter=delimiter, strict=True)
    if reader.fieldnames is None or set(reader.fieldnames) != {'frequency_mhz', 'name'}:
        raise ValueError('Expected header: frequency_mhz;name')
    grouped = {}
    for row_number, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError('Invalid columns in row %d' % row_number)
        add_name(grouped, row['frequency_mhz'], row['name'], row_number)
    return {hz: ' / '.join(sorted(values)) for hz, values in grouped.items()}


class ChannelNames:
    def __init__(self, path):
        self.path = Path(path)
        self.signature = None
        self.names = {}

    def load(self):
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if signature == self.signature:
                return self.names
            self.signature = signature
            if stat.st_size > 1024 * 1024:
                raise ValueError('Channel file exceeds 1 MB')
            self.names = parse_names(self.path.read_text(encoding='utf-8-sig'))
        except FileNotFoundError:
            self.names = {}
            self.signature = None
        except (OSError, UnicodeError, ValueError, csv.Error) as exc:
            logging.warning('Cannot read channel names from %s: %s; keeping previous names', self.path, exc)
        return self.names
