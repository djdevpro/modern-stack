"""Install the Tabula update timer from Terraform, without deploying a release early."""
import json
from pathlib import Path
import subprocess
import sys

policy = json.loads(Path(sys.argv[1]).read_text())
interval = policy['check_interval_minutes']
if not isinstance(interval, int) or not 5 <= interval <= 60:
    raise SystemExit('Update check interval must be between 5 and 60 minutes')
if not Path('/opt/stack/tabula/update.py').is_file():
    raise SystemExit('Deploy the reviewed Tabula stack files before enabling automatic updates')

service = '''[Unit]
Description=Deploy validated Tabula releases after their notification grace period
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/stack/tabula/update.py
TimeoutStartSec=15min
UMask=0077
'''
timer = f'''[Unit]
Description=Check promoted Tabula releases every {interval} minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec={interval}min
RandomizedDelaySec=1min
Persistent=true

[Install]
WantedBy=timers.target
'''
changed = False
for name, content in [('tabula-update.service', service), ('tabula-update.timer', timer)]:
    path = Path('/etc/systemd/system') / name
    if not path.exists() or path.read_text() != content:
        candidate = path.with_suffix('.next')
        candidate.write_text(content)
        candidate.chmod(0o644)
        candidate.replace(path)
        changed = True
subprocess.run(['systemd-analyze', 'verify', '/etc/systemd/system/tabula-update.service',
                '/etc/systemd/system/tabula-update.timer'], check=True)
subprocess.run(['systemctl', 'daemon-reload'], check=True)
if policy['enabled']:
    subprocess.run(['systemctl', 'enable', '--now', 'tabula-update.timer'], check=True)
    if changed:
        subprocess.run(['systemctl', 'restart', 'tabula-update.timer'], check=True)
    subprocess.run(['systemctl', 'is-active', '--quiet', 'tabula-update.timer'], check=True)
else:
    subprocess.run(['systemctl', 'disable', '--now', 'tabula-update.timer'], check=True)
print(f"Automatic update timer reconciled: enabled={policy['enabled']}, interval={interval} minutes")
