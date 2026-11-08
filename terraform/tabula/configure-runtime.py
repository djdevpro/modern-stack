"""Select an immutable Tabula release without changing credentials or restarting services."""
import json
import os
import re
import sys
from pathlib import Path


def configure(config_path: Path, directory: Path = Path('/opt/tabula')) -> None:
    release = json.loads(config_path.read_text(encoding='utf-8'))
    database = release['database_name']
    if not re.fullmatch(r'tabula(?:-[a-z0-9]+)*', database):
        raise ValueError('Invalid catalogue database name')
    entries = {'STDB_DATABASE': database}
    for component in ('web', 'module', 'manager'):
        image = release[component + '_image']
        if not re.fullmatch(r'ghcr\.io/[a-z0-9_.-]+/tabula-' + component + r'@sha256:[0-9a-f]{64}', image):
            raise ValueError('Release images must use immutable component digests')
        entries['TABULA_' + component.upper() + '_IMAGE'] = image
    config = directory / '.env'
    if not config.is_file():
        raise ValueError('Initialize the private Tabula identity configuration first')
    content = config.read_text(encoding='utf-8')
    lines = [line for line in content.splitlines() if line.partition('=')[0] not in entries]
    lines.extend(key + '=' + value for key, value in entries.items())
    candidate = '\n'.join(lines) + '\n'
    if candidate != content:
        previous = directory / '.env.before-release'
        fd = os.open(previous, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
        previous.chmod(0o600)
        temporary = directory / '.env.runtime-next'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(candidate)
        temporary.replace(config)
    config.chmod(0o600)
    print('Immutable native release selected; deployment is handled by the stack agent.')


if __name__ == '__main__':
    configure(Path(sys.argv[1]))
