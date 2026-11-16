# AOR DV10 Raspberry Suite

[Polski](README.pl.md) · English

A Raspberry Pi web interface for the AOR AR-DV10 receiver, with a full control panel and a separate read-only listening page. Stream live AAC audio, display channel names from an AR-DV10 Connect CSV export, and keep the last received channel visible while the radio resumes scanning.

## Credits

This project was built using **[roxio/AOR-AR-DV10-Suite](https://github.com/roxio/AOR-AR-DV10-Suite)** as its foundation. Credit for the original receiver-control library and web panel belongs to the upstream author and contributors. This version adds the Raspberry Pi listening page, audio streaming, channel-name integration and browser-based CSV uploads. The repository uses the existing GPL-3.0 license in [LICENSE](LICENSE). The upstream MIT license and copyright notice are retained in [LICENSE.upstream-MIT](LICENSE.upstream-MIT).

Original documentation: [English](docs/UPSTREAM_README.md) · [Polish](docs/UPSTREAM_README.pl.md).

## Features

- **Port 8080:** receiver control, channel names next to the frequency, and CSV uploads from the browser.
- **Port 80:** a read-only listening page that retains the last received frequency and channel name while scanning continues.
- Shared live **AAC-LC audio at 48 kb/s, mono, 24 kHz**, captured from an ALSA audio input through FFmpeg.
- Direct support for original **AR-DV10 Connect / DEPMICRO-BACKUP** exports and simple `frequency_mhz;name` CSV files.
- Automatic CSV reload, upload validation, atomic file replacement and a backup of the previous channel-name file.

## Requirements and installation

Linux/Raspberry Pi OS, Python 3.10 or newer, a receiver connected over USB, and a separate audio input accessible through ALSA. The deployed system was tested on a Raspberry Pi 3 with Python 3.13.

Clone this repository and open its directory, then run:

```bash
sudo apt update
sudo apt install python3-venv ffmpeg
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[web]'
```

Use an editable install (`-e`): the control panel loads the shared channel parser from this checkout's `raspberry-viewer/` directory. The service user must be able to access the serial device and ALSA audio input.

### Run manually

Start the control panel in one terminal. Add `--serial-port /dev/serial/by-id/YOUR_DEVICE` if automatic detection is unsuitable:

```bash
.venv/bin/dv10-web --host 0.0.0.0 --port 8080
```

Start the read-only page in another terminal, using an unprivileged port:

```bash
AOR_VIEWER_HOST=0.0.0.0 PORT=8081 .venv/bin/python raspberry-viewer/server.py
```

Open `http://RASPBERRY_ADDRESS:8080/` and upload a CSV in **Nazwy kanałów w podglądzie** (channel names for the listening page). Open `http://RASPBERRY_ADDRESS:8081/` and click **Słuchaj na żywo** (listen live).

### Start automatically: listening on 80, control on 8080

```bash
sudo .venv/bin/python deploy/install-systemd.py --user pi
```

Optionally append `--serial-port /dev/serial/by-id/YOUR_DEVICE`. The installer writes and starts `aor-dv10.service` and `aor-viewer.service`, preserving copies of existing unit files. The specified user must be able to access the project checkout and its `.venv` environment.

For migration from an older user-level viewer service, disable it first with `systemctl --user disable --now aor-viewer.service`. Ports 80 and 8080 must be available for the new services.

```bash
systemctl status aor-dv10.service aor-viewer.service
journalctl -u aor-dv10.service -u aor-viewer.service -n 50
```

The read-only server blocks control requests. The full control panel on port 8080 remains reachable on the same network and has no authentication. Keep it on a trusted network; use a VPN or an appropriately secured reverse proxy for remote access.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `AOR_VIEWER_DATA_DIR` | `~/.local/share/aor-dv10` | Shared data directory containing `channels.csv` and `backups/`. |
| `AOR_AUDIO_DEVICE` | `plughw:CARD=Device,DEV=0` | ALSA input used by the control service. |
| `AOR_CONTROL_HOST` | `127.0.0.1` | Control-panel host used by the read-only server. |
| `UPSTREAM_PORT` | `8080` | Control-panel port. |
| `AOR_VIEWER_HOST` | `127.0.0.1` | Read-only server bind address. |
| `PORT` | `8081` | Manual listening-page port; the systemd service sets it to 80. |

Both processes must use the same `AOR_VIEWER_DATA_DIR`. Shared systemd configuration can be placed in `/etc/aor-dv10.env`, for example `AOR_AUDIO_DEVICE=plughw:CARD=Device,DEV=0`.

Example files are in `examples/`. Original Connect exports are read without conversion. Multiple names for the same frequency are combined. Uploads are limited to 1 MB and must use UTF-8, optionally with a BOM.

## Last received channel

The listening page records a channel when the receiver reports an open squelch (`squelch_open`). Scanning frequencies do not replace that record. Before the first received signal, it shows a waiting state. Reception history is kept in the viewer service's memory and is cleared when that service restarts. Status is polled approximately once per second while the page is open, so very short transmissions may be missed.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_raspberry_viewer.py' -v
```

The original project's tests require its `.[dev]` dependencies. The added viewer tests use synthetic data and do not control physical radio hardware.

Private channel lists, SSH keys and backups from the deployed Raspberry Pi are not included.
