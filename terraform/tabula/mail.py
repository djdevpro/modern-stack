"""Prepare a Stalwart sender and a dedicated SMTP-only application password."""
import json
import subprocess


def jmap(method, arguments):
    result = subprocess.run(['/opt/stack/scripts/sw', method, '-'], input=json.dumps(arguments),
                            text=True, capture_output=True, check=True)
    data = json.loads(result.stdout)
    if data.get('reponse') != method or any(data.get(k) for k in ('notCreated', 'notUpdated', 'notDestroyed')):
        raise RuntimeError(f'Stalwart {method} failed; inspect administration logs')
    return data


def prepare_smtp(config, root, private_write):
    address = config['smtp_user']
    if config['smtp_from'] != address:
        raise RuntimeError('Sender must match the managed SMTP account')
    accounts = jmap('x:Account/get', {'ids': None, 'properties': ['id', 'emailAddress']})['list']
    account = next((a for a in accounts if a['emailAddress'].lower() == address.lower()), None)
    if account is None:
        name, domain = address.rsplit('@', 1)
        domains = jmap('x:Domain/get', {'ids': None, 'properties': ['id', 'name']})['list']
        target = next((d for d in domains if d['name'].lower() == domain.lower()), None)
        if target is None:
            raise RuntimeError('Sender domain must already be provisioned with DNS and TLS in Stalwart')
        created = jmap('x:Account/set', {'create': {'tabula': {
            '@type': 'User', 'name': name, 'domainId': target['id'], 'description': config['smtp_name'],
            'credentials': {}, 'memberGroupIds': {}, 'aliases': {}, 'quotas': {},
            'roles': {'@type': 'User'}, 'permissions': {'@type': 'Inherit'},
            'encryptionAtRest': {'@type': 'Disabled'},
        }}})
        account = created['created']['tabula']
    cache = root / 'smtp.json'
    if cache.exists():
        stored = json.loads(cache.read_text())
        if stored['accountId'] != account['id'] or stored['address'] != address:
            raise RuntimeError('SMTP sender changed; explicit credential migration required')
        current = jmap('x:AppPassword/get', {'accountId': account['id'], 'ids': [stored['id']],
                                            'properties': ['id', 'permissions']})['list']
        if current:
            expected = {'@type': 'Replace', 'permissions': {'authenticate': True, 'emailSend': True}}
            if current[0]['permissions'] != expected:
                jmap('x:AppPassword/set', {'accountId': account['id'],
                    'update': {stored['id']: {'permissions': expected}}})
            return stored['secret']
    created = jmap('x:AppPassword/set', {'accountId': account['id'], 'create': {'tabula': {
        'description': 'Tabula identity emails (Terraform)', 'allowedIps': {},
        'permissions': {'@type': 'Replace', 'permissions': {'authenticate': True, 'emailSend': True}},
    }}})['created']['tabula']
    private_write(cache, json.dumps({'accountId': account['id'], 'address': address,
                                    'id': created['id'], 'secret': created['secret']}) + '\n')
    return created['secret']
