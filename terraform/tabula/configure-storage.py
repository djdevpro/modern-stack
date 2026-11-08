"""Merge S3 settings into the private Tabula host environment without deploying."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

KEYS = (
    'S3_ENDPOINT', 'S3_REGION', 'S3_BUCKET', 'S3_ACCESS_KEY_ID',
    'S3_SECRET_ACCESS_KEY', 'S3_FORCE_PATH_STYLE',
)


def validate(settings: dict[str, str]) -> None:
    if set(settings) != set(KEYS):
        raise ValueError('Expected exactly the six S3 configuration keys')
    if any(not isinstance(value, str) or not value or any(ord(c) < 32 for c in value)
           for value in settings.values()):
        raise ValueError('S3 settings must be nonempty single-line strings')
    endpoint = urlsplit(settings['S3_ENDPOINT'])
    if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.path or endpoint.query or endpoint.fragment):
        raise ValueError('S3 endpoint must be an HTTPS origin')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', settings['S3_BUCKET']):
        raise ValueError('S3 bucket must be a DNS-compatible name')
    if settings['S3_FORCE_PATH_STYLE'] not in ('true', 'false'):
        raise ValueError('S3_FORCE_PATH_STYLE must be true or false')


def merge_environment(original: str, settings: dict[str, str]) -> str:
    validate(settings)
    remaining = []
    for line in original.splitlines(keepends=True):
        match = re.match(r'\s*(?:export\s+)?([A-Z0-9_]+)\s*=', line)
        if not match or match[1] not in KEYS:
            remaining.append(line)
    content = ''.join(remaining)
    if content and not content.endswith('\n'):
        content += '\n'
    # Compose single-quoted values are literal, including dollar signs in keys.
    for key in KEYS:
        value = settings[key].replace("'", "\\'")
        content += f"{key}='{value}'\n"
    return content


def install(settings: dict[str, str], target: Path) -> None:
    validate(settings)
    if target.is_symlink():
        raise ValueError('Refusing to replace a symbolic-link environment file')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    original = target.read_text(encoding='utf-8') if target.exists() else ''
    content = merge_environment(original, settings)
    if content == original:
        target.chmod(0o600)
        return
    fd, temporary = tempfile.mkstemp(prefix='.storage-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    source = Path(sys.argv[1])
    source.chmod(0o600)
    try:
        install(json.loads(source.read_text(encoding='utf-8')), Path('/opt/tabula/.env'))
    finally:
        source.unlink(missing_ok=True)
    print('Private Tabula S3 configuration reconciled; release publication applies it')


if __name__ == '__main__':
    main()
