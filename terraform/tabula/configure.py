"""Reconcile the dedicated Tabula realm. Run as root through Terraform over SSH.

No secret is logged or placed in Terraform state. Existing user passwords are preserved.
"""
import fcntl
import json
import os
import pwd
from pathlib import Path
import secrets
import smtplib
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from mail import prepare_smtp


ROOT = Path('/opt/tabula')


def read_env(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if '=' in line and not line.lstrip().startswith('#'))


def private_write(path, content):
    temporary = path.with_suffix(path.suffix + '.next')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
    temporary.chmod(0o600)
    temporary.replace(path)


class Keycloak:
    def __init__(self, base, settings):
        self.base = base
        body = urllib.parse.urlencode({
            'client_id': 'admin-cli', 'grant_type': 'password',
            'username': settings.get('KEYCLOAK_ADMIN', 'admin'),
            'password': settings['KEYCLOAK_ADMIN_PASSWORD'].strip().strip('"').strip("'"),
        }).encode()
        with urllib.request.urlopen(base + '/realms/master/protocol/openid-connect/token', body, timeout=20) as response:
            self.token = json.load(response)['access_token']

    def request(self, path, value=None, method=None):
        request = urllib.request.Request(self.base + '/admin/' + path,
            data=json.dumps(value).encode() if value is not None else None,
            method=method, headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            # Error response bodies may contain sensitive request data.
            raise RuntimeError(f'Keycloak {method or "GET"} {path.split("?")[0]}: HTTP {error.code}') from None


def update_access_file(config):
    """Maintain only our marked section; preserve other applications' credentials."""
    account = pwd.getpwnam(os.environ['SUDO_USER'])
    path = Path(account.pw_dir) / 'acces.txt'
    if path.is_symlink():
        raise RuntimeError('Refusing a symlink for acces.txt')
    bootstrap = json.loads((ROOT / 'bootstrap.json').read_text())
    if bootstrap['username'] != config['admin_email']:
        raise RuntimeError('Bootstrap account differs from the configured administrator')
    start, end = '# BEGIN TABULA (Terraform)', '# END TABULA (Terraform)'
    section = '\n'.join([start, 'Tabula : ' + config['frontend_url'],
        'Identifiant : ' + bootstrap['username'],
        'Mot de passe initial : ' + bootstrap['password'],
        'Si vous avez change ce mot de passe, utilisez votre nouveau mot de passe.', end])
    original = path.read_text() if path.exists() else ''
    if start in original or end in original:
        if original.count(start) != 1 or original.count(end) != 1 or original.index(start) > original.index(end):
            raise RuntimeError('Malformed Tabula section in acces.txt; refusing to alter other credentials')
        updated = original[:original.index(start)] + section + original[original.index(end) + len(end):]
    else:
        updated = original + ('\n\n' if original else '') + section + '\n'
    if updated != original:
        private_write(path, updated)
    os.chown(path, account.pw_uid, account.pw_gid)
    path.chmod(0o600)
    print('Tabula initial access synchronized in the SSH administrator acces.txt; other entries preserved')


def reconcile(config):
    if not (ROOT / '.env').exists():
        raise RuntimeError('Deploy the validated Tabula images and private GHCR access before applying identity configuration')
    smtp_password = prepare_smtp(config, ROOT, private_write)
    # Authenticate without sending an unsolicited email; fail before changing Keycloak.
    context = ssl.create_default_context()
    if config['smtp_port'] == 465:
        smtp = smtplib.SMTP_SSL(config['smtp_host'], 465, timeout=20, context=context)
    else:
        smtp = smtplib.SMTP(config['smtp_host'], 587, timeout=20)
        smtp.starttls(context=context)
    with smtp:
        smtp.login(config['smtp_user'], smtp_password)
    print('SMTP TLS and authentication verified (no email sent)')

    kc = Keycloak(config['keycloak_url'], read_env(Path('/opt/stack/.env')))
    realm = 'realms/tabula'
    settings = {
        'realm': 'tabula', 'displayName': 'Tabula', 'enabled': True,
        'sslRequired': 'all', 'registrationAllowed': False,
        'loginWithEmailAllowed': True, 'duplicateEmailsAllowed': False,
        'resetPasswordAllowed': True, 'verifyEmail': True,
        'bruteForceProtected': True,
        'internationalizationEnabled': True, 'supportedLocales': ['en', 'fr', 'de', 'es', 'it', 'pt', 'ar'],
        'defaultLocale': 'fr',
        'smtpServer': {
            'host': config['smtp_host'], 'port': str(config['smtp_port']),
            'from': config['smtp_from'], 'fromDisplayName': config['smtp_name'],
            'auth': 'true', 'user': config['smtp_user'], 'password': smtp_password,
            'ssl': str(config['smtp_port'] == 465).lower(),
            'starttls': str(config['smtp_port'] == 587).lower(),
        },
    }
    existing = next((r for r in kc.request('realms') if r['realm'] == 'tabula'), None)
    kc.request(realm if existing else 'realms', settings, 'PUT' if existing else 'POST')
    origin = config['frontend_url']
    client_settings = {
        'clientId': 'tabula', 'name': 'Tabula', 'enabled': True,
        'protocol': 'openid-connect', 'publicClient': True,
        'standardFlowEnabled': True, 'implicitFlowEnabled': False,
        'directAccessGrantsEnabled': False, 'serviceAccountsEnabled': False,
        'fullScopeAllowed': False, 'rootUrl': origin, 'baseUrl': origin,
        'redirectUris': [origin + '/auth/callback'], 'webOrigins': [origin],
        'attributes': {'pkce.code.challenge.method': 'S256',
                       'post.logout.redirect.uris': origin + '/login'},
        'defaultClientScopes': ['profile', 'email'],
    }
    clients = kc.request(realm + '/clients?clientId=tabula')
    if clients:
        client_id = clients[0]['id']
        kc.request(realm + '/clients/' + client_id, client_settings, 'PUT')
    else:
        kc.request(realm + '/clients', client_settings, 'POST')
        client_id = kc.request(realm + '/clients?clientId=tabula')[0]['id']
    client_path = realm + '/clients/' + client_id
    mappers = [
        {'name': 'tabula-audience', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-audience-mapper',
         'config': {'included.client.audience': 'tabula', 'access.token.claim': 'true', 'id.token.claim': 'false'}},
        {'name': 'tabula-roles', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-usermodel-client-role-mapper',
         'config': {'usermodel.clientRoleMapping.clientId': 'tabula', 'claim.name': 'resource_access.tabula.roles',
                    'jsonType.label': 'String', 'multivalued': 'true', 'access.token.claim': 'true', 'id.token.claim': 'false'}},
    ]
    existing_mappers = {m['name']: m for m in kc.request(client_path + '/protocol-mappers/models')}
    for mapper in mappers:
        old = existing_mappers.get(mapper['name'])
        path = client_path + '/protocol-mappers/models'
        if old:
            mapper['id'] = old['id']
            path += '/' + old['id']
        kc.request(path, mapper, 'PUT' if old else 'POST')
    roles = {r['name']: r for r in kc.request(client_path + '/roles')}
    for name in ['super-admin', 'admin', 'user']:
        if name not in roles:
            kc.request(client_path + '/roles', {'name': name}, 'POST')
    roles = {r['name']: r for r in kc.request(client_path + '/roles')}
    kc.request(client_path + '/scope-mappings/clients/' + client_id, list(roles.values()), 'POST')

    query = realm + '/users?username=' + urllib.parse.quote(config['admin_email'], safe='') + '&exact=true'
    users = kc.request(query)
    if not users:
        bootstrap_path = ROOT / 'bootstrap.json'
        if bootstrap_path.exists():
            bootstrap = json.loads(bootstrap_path.read_text())
            if bootstrap['username'] != config['admin_email']:
                raise RuntimeError('Bootstrap account differs; explicit migration required')
        else:
            bootstrap = {'username': config['admin_email'], 'password': secrets.token_urlsafe(30)}
            private_write(bootstrap_path, json.dumps(bootstrap) + '\n')
        kc.request(realm + '/users', {
            'username': config['admin_email'], 'email': config['admin_email'], 'enabled': True,
            'emailVerified': False, 'requiredActions': ['VERIFY_EMAIL'],
            'credentials': [{'type': 'password', 'value': bootstrap['password'], 'temporary': False}],
        }, 'POST')
        users = kc.request(query)
    if len(users) != 1:
        raise RuntimeError('Expected exactly one bootstrap administrator')
    kc.request(realm + '/users/' + users[0]['id'] + '/role-mappings/clients/' + client_id,
               [roles['super-admin']], 'POST')
    if not any(c['type'] == 'password' for c in kc.request(realm + '/users/' + users[0]['id'] + '/credentials')):
        raise RuntimeError('Existing account has no password; refusing an implicit password reset')
    verified = kc.request(realm)
    assert verified['smtpServer']['host'] == config['smtp_host']
    assert verified['resetPasswordAllowed'] and verified['verifyEmail']
    verified_client = kc.request(client_path)
    assert verified_client['attributes']['pkce.code.challenge.method'] == 'S256'
    assert verified_client['redirectUris'] == [origin + '/auth/callback']
    print('Dedicated realm, SMTP, PKCE client, roles and administrator reconciled; existing password preserved')

    env_path = ROOT / '.env'
    previous = env_path.read_text()
    env = read_env(env_path)
    updates = {'OIDC_ISSUER': config['keycloak_url'] + '/realms/tabula',
               'TABULA_HOST': urllib.parse.urlsplit(origin).hostname,
               'TABULA_DB_HOST': urllib.parse.urlsplit(config['backend_url']).hostname}
    if any(env.get(k) != v for k, v in updates.items()):
        private_write(ROOT / '.env.before-identity', previous)
        private_write(ROOT / 'identity-deploy-pending', '1\n')
        env.update(updates)
        private_write(env_path, ''.join(f'{k}={v}\n' for k, v in env.items()))
    if (ROOT / 'identity-deploy-pending').exists():
        subprocess.run(['bash', '/opt/stack/tabula/deploy.sh'], check=True,
                       env={**os.environ, 'TABULA_LOCK_HELD': '1', 'COMPOSE_ANSI': 'never', 'COMPOSE_PROGRESS': 'plain'})
        (ROOT / 'identity-deploy-pending').unlink()
    print('Tabula runtime configuration matches its dedicated realm')
    update_access_file(config)


if __name__ == '__main__':
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    with open('/run/lock/tabula-deploy.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reconcile(json.loads(Path(sys.argv[1]).read_text()))
