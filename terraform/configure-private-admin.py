"""Apply the Dokploy HTTP access policy over verified OpenSSH/NetBird from Terraform."""
import os
import subprocess

REMOTE = r'''
import ipaddress
import json
import os
from pathlib import Path
import subprocess

interfaces = json.loads(subprocess.check_output(['ip', '-json', 'address', 'show', 'wt0'], text=True))
networks = []
for address in interfaces[0]['addr_info']:
    if address['scope'] != 'global':
        continue
    network = ipaddress.ip_network(f"{address['local']}/{address['prefixlen']}", strict=False)
    if network.version == 4 and network.subnet_of(ipaddress.ip_network('100.64.0.0/10')):
        networks.append(str(network))
    elif network.version == 6 and network.subnet_of(ipaddress.ip_network('fc00::/7')):
        networks.append(str(network))
if not networks:
    raise SystemExit('No private NetBird network found; refusing an empty or public allowlist')
directory = Path('/etc/dokploy/traefik/dynamic')
router = directory / 'dokploy.yml'
original = router.read_text()
anchor = '      service: dokploy-service-app\n'
guard = '      middlewares:\n        - dokploy-netbird-only@file\n'
if 'dokploy-netbird-only@file' not in original:
    if original.count(anchor) != 1 or 'middlewares:' in original:
        raise SystemExit('Unexpected Dokploy route structure; refusing to overwrite its configuration')
    updated = original.replace(anchor, anchor + guard)
else:
    if anchor + guard not in original:
        raise SystemExit('Unexpected existing Dokploy access policy')
    updated = original
backup = Path('/opt/backups/private-admin')
backup.mkdir(parents=True, exist_ok=True, mode=0o700)
if not (backup / 'dokploy.before-netbird.yml').exists():
    (backup / 'dokploy.before-netbird.yml').write_text(original)
    (backup / 'dokploy.before-netbird.yml').chmod(0o600)
def replace(path, value):
    if path.exists() and path.read_text() == value:
        return
    temporary = path.with_suffix('.pending')
    temporary.write_text(value)
    temporary.chmod(0o644)
    temporary.replace(path)
policy = {'http': {'middlewares': {'dokploy-netbird-only': {'ipAllowList': {'sourceRange': networks}}}}}
replace(directory / 'private-admin.yml', json.dumps(policy, indent=2) + '\n')
replace(router, updated)
print('Dokploy HTTP route restricted to private NetBird source networks; other routes unchanged')
'''

subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ConnectTimeout=10', '-l', os.environ['ADMIN_SSH_USER'],
                os.environ['ADMIN_SSH_HOST'], 'sudo -n python3 -'], input=REMOTE, text=True, check=True)
