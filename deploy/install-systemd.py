#!/usr/bin/env python3
"""Install services for this checkout. Run explicitly with sudo."""
import argparse
import os
import pathlib
import pwd
import shutil
import subprocess
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument('--user', required=True)
parser.add_argument('--serial-port')
parser.add_argument('--check', action='store_true', help='Validate unit files without installing or restarting services')
args = parser.parse_args()
if os.geteuid() != 0:
    parser.error('Run with sudo')
account = pwd.getpwnam(args.user)
project = pathlib.Path(__file__).resolve().parents[1]
python = project / '.venv/bin/python'
control = project / '.venv/bin/dv10-web'
if not python.exists() or not control.exists():
    parser.error('Create .venv and install -e .[web] first')
def quote(value):
    return '"' + str(value).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'
working_directory = str(project).replace('%', '%%')
if any(character in working_directory for character in '\n\r\0'):
    parser.error('Project path contains an invalid control character')
common = f'User={args.user}\nGroup={account.pw_gid}\nWorkingDirectory={working_directory}\nEnvironmentFile=-/etc/aor-dv10.env\nRestart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nUMask=0077\n'
serial = ' --serial-port ' + quote(args.serial_port) if args.serial_port else ''
units = {
    'aor-dv10.service': '[Unit]\nDescription=AOR AR-DV10 control and AAC audio\nWants=network-online.target\nAfter=network-online.target\n\n[Service]\n' + common + f'ExecStart={quote(control)} --host 0.0.0.0 --port 8080{serial}\n\n[Install]\nWantedBy=multi-user.target\n',
    'aor-viewer.service': '[Unit]\nDescription=AOR AR-DV10 read-only viewer\nWants=network-online.target aor-dv10.service\nAfter=network-online.target aor-dv10.service\n\n[Service]\n' + common + f'ExecStart={quote(python)} {quote(project / "raspberry-viewer/server.py")}\nEnvironment=PORT=80\nEnvironment=AOR_VIEWER_HOST=0.0.0.0\nAmbientCapabilities=CAP_NET_BIND_SERVICE\nCapabilityBoundingSet=CAP_NET_BIND_SERVICE\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=read-only\n\n[Install]\nWantedBy=multi-user.target\n',
}
with tempfile.TemporaryDirectory() as directory:
    for name, content in units.items():
        pathlib.Path(directory, name).write_text(content)
    subprocess.run(['systemd-analyze', 'verify', *[str(pathlib.Path(directory, name)) for name in units]], check=True)
    if args.check:
        print('Service unit validation passed; no changes made')
        raise SystemExit(0)
    backup = pathlib.Path('/var/backups') / ('aor-dv10-units-%d' % time.time())
    backup.mkdir(mode=0o700)
    for name, content in units.items():
        target = pathlib.Path('/etc/systemd/system') / name
        if target.exists():
            shutil.copy2(target, backup / name)
        target.write_text(content)
        target.chmod(0o644)
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', 'aor-dv10.service', 'aor-viewer.service'], check=True)
subprocess.run(['systemctl', 'restart', 'aor-dv10.service', 'aor-viewer.service'], check=True)
print('Services installed. Previous units:', backup)
