"""Pull three validated and promoted images; never deploy mixed releases."""
import fcntl
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path('/opt/tabula')

def release_order(release):
    match = re.fullmatch(r'[0-9a-f]{40}-([0-9]+)-([0-9]+)', release)
    return (int(match[1]), int(match[2])) if match else None

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def main():
    if not (ROOT / '.env').exists() or (ROOT / 'hold').exists():
        print('Not configured or manually held')
        return
    with open('/run/lock/tabula-deploy.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Deployment already running')
            return
        if (ROOT / 'update-failed').exists():
            raise SystemExit('Previous update failed; manual inspection required')
        current_path = ROOT / 'release'
        current = current_path.read_text().strip() if current_path.exists() else None
        current_order = release_order(current) if current is not None else None
        if current is not None and current_order is None:
            raise SystemExit('Invalid current release marker; manual inspection required')
        pair = {}
        releases = set()
        for component in ['web', 'module', 'manager']:
            repository = 'ghcr.io/djdevpro/tabula-' + component
            subprocess.run(['docker', 'pull', repository + ':latest'], check=True)
            image = json.loads(run('docker', 'image', 'inspect', repository + ':latest'))[0]
            release = (image['Config'].get('Labels') or {}).get('io.tabula.release', '')
            image_order = release_order(release)
            if image_order is None:
                print('Waiting for a release with validated pairing metadata')
                return
            if component == 'web' and current_order is not None and image_order < current_order:
                print('Older promoted release ignored; automatic downgrades are refused')
                return
            releases.add(release)
            pair[component] = next(d for d in image['RepoDigests'] if d.startswith(repository + '@sha256:'))
        if len(releases) != 1:
            print('Promotion in progress: mixed releases refused')
            return
        release = next(iter(releases))
        candidate_order = release_order(release)
        if current_order is not None:
            if candidate_order < current_order:
                print('Older promoted release ignored; automatic downgrades are refused')
                return
            if candidate_order == current_order and release != current:
                raise SystemExit('Conflicting release metadata; manual inspection required')
        config = ROOT / '.env'
        original = config.read_text()
        values = dict(line.split('=', 1) for line in original.splitlines() if '=' in line and not line.startswith('#'))
        if all(values.get('TABULA_' + c.upper() + '_IMAGE') == pair[c] for c in pair):
            print('Already current')
            return
        for component, image in pair.items():
            values['TABULA_' + component.upper() + '_IMAGE'] = image
        previous = ROOT / '.env.previous'
        previous.write_text(original)
        previous.chmod(0o600)
        candidate = ROOT / '.env.next'
        candidate.write_text(''.join(f'{k}={v}\n' for k, v in values.items()))
        candidate.chmod(0o600)
        candidate.replace(config)
        try:
            subprocess.run(['bash', str(Path(__file__).with_name('deploy.sh'))],
                env={**os.environ, 'TABULA_LOCK_HELD': '1'}, check=True)
        except subprocess.CalledProcessError:
            (ROOT / 'update-failed').write_text(release + '\n')
            raise
        print('Promoted release deployed successfully')

if __name__ == '__main__':
    main()
