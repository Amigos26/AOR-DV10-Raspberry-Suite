#!/usr/bin/env python3
"""Install the control/viewer services and optional weekly memory synchronization."""
import argparse
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tempfile
import time


def quote(value):
    text = str(value)
    if any(character in text for character in '\n\r\0'):
        raise ValueError('Invalid control character in unit argument')
    return '"' + text.replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'


def build_units(project, user, group, serial_port=None, weekly=True,
                calendar='Sun *-*-* 04:00:00 Europe/Warsaw'):
    directory = str(project).replace('%', '%%')
    if any(character in directory + calendar for character in '\n\r\0'):
        raise ValueError('Invalid control character in configuration')
    python = project / '.venv/bin/python'
    common = f'User={user}\nGroup={group}\nWorkingDirectory={directory}\nEnvironmentFile=-/etc/aor-dv10.env\nNoNewPrivileges=true\nUMask=0077\n'
    restart = 'Restart=on-failure\nRestartSec=5\n'
    serial = ' --serial-port ' + quote(serial_port) if serial_port else ''
    units = {
        'aor-dv10.service': '[Unit]\nDescription=AOR receiver control and AAC audio\nWants=network-online.target\nAfter=network-online.target\n\n[Service]\n' + common + restart + f'ExecStart={quote(project / ".venv/bin/dv10-web")} --host 0.0.0.0 --port 8080{serial}\n\n[Install]\nWantedBy=multi-user.target\n',
        'aor-viewer.service': '[Unit]\nDescription=AOR read-only listening page\nWants=network-online.target aor-dv10.service\nAfter=network-online.target aor-dv10.service\n\n[Service]\n' + common + restart + f'ExecStart={quote(python)} {quote(project / "raspberry-viewer/server.py")}\nEnvironment=PORT=80\nEnvironment=AOR_VIEWER_HOST=0.0.0.0\nAmbientCapabilities=CAP_NET_BIND_SERVICE\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=read-only\n\n[Install]\nWantedBy=multi-user.target\n',
    }
    if weekly:
        units['aor-radio-channels-sync.service'] = '[Unit]\nDescription=Read all AOR memory banks into the channel-name CSV\nWants=network-online.target aor-dv10.service\nAfter=network-online.target aor-dv10.service\n\n[Service]\nType=oneshot\n' + common + f'ExecStart={quote(python)} {quote(project / "raspberry-viewer/sync_radio_channels.py")}\nTimeoutStartSec=300\nNice=10\n'
        units['aor-radio-channels-sync.timer'] = '[Unit]\nDescription=Weekly AOR channel-name synchronization\n\n[Timer]\nOnCalendar=' + calendar + '\nPersistent=true\nUnit=aor-radio-channels-sync.service\n\n[Install]\nWantedBy=timers.target\n'
    return units


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--user', required=True)
    parser.add_argument('--serial-port')
    parser.add_argument('--check', action='store_true', help='Validate units without installing or restarting services')
    parser.add_argument('--no-weekly-sync', action='store_true', help='Keep a manually managed channel-name CSV')
    parser.add_argument('--sync-calendar', default='Sun *-*-* 04:00:00 Europe/Warsaw')
    args = parser.parse_args()
    if os.geteuid() != 0 and not args.check:
        parser.error('Run with sudo, or use --check for validation only')
    account = pwd.getpwnam(args.user)
    project = Path(__file__).resolve().parents[1]
    if not (project / '.venv/bin/python').exists() or not (project / '.venv/bin/dv10-web').exists():
        parser.error('Create .venv and install -e .[web] first')
    units = build_units(project, args.user, account.pw_gid, args.serial_port,
                        weekly=not args.no_weekly_sync, calendar=args.sync_calendar)
    with tempfile.TemporaryDirectory() as directory:
        for name, content in units.items():
            Path(directory, name).write_text(content)
        subprocess.run(['systemd-analyze', 'verify', *[str(Path(directory, name)) for name in units]], check=True)
        if args.check:
            print('Service unit validation passed; no changes made')
            return
        backup = Path('/var/backups') / ('aor-dv10-units-%d' % time.time())
        backup.mkdir(mode=0o700)
        for name, content in units.items():
            target = Path('/etc/systemd/system') / name
            if target.exists():
                shutil.copy2(target, backup / name)
            target.write_text(content)
            target.chmod(0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'aor-dv10.service', 'aor-viewer.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'aor-dv10.service', 'aor-viewer.service'], check=True)
    if args.no_weekly_sync:
        if Path('/etc/systemd/system/aor-radio-channels-sync.timer').exists():
            subprocess.run(['systemctl', 'disable', '--now', 'aor-radio-channels-sync.timer'], check=True)
    else:
        subprocess.run(['systemctl', 'enable', '--now', 'aor-radio-channels-sync.timer'], check=True)
        subprocess.run(['systemctl', 'start', '--no-block', 'aor-radio-channels-sync.service'], check=True)
    print('Services installed. Previous unit files:', backup)


if __name__ == '__main__':
    main()
