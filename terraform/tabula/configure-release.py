"""Reconcile the repository grace period using the operator's existing gh authentication."""
import os
import subprocess

repo = os.environ['TABULA_UPDATE_REPOSITORY']
hours = int(os.environ['TABULA_UPDATE_GRACE_HOURS'])
if not 4 <= hours <= 168:
    raise SystemExit('Grace period must be between four hours and seven days')
subprocess.run(['gh', 'variable', 'set', 'TABULA_UPDATE_GRACE_HOURS',
                '--repo', repo, '--body', str(hours)], check=True)
actual = subprocess.check_output(['gh', 'variable', 'get', 'TABULA_UPDATE_GRACE_HOURS',
                                  '--repo', repo, '--json', 'value', '--jq', '.value'], text=True).strip()
if actual != str(hours):
    raise SystemExit('Repository grace period verification failed')
print(f'Repository promotion grace period verified: {hours} hours')
