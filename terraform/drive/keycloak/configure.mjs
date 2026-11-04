#!/usr/bin/env node
// Configure Keycloak pour le mode instance : realm, client « drive » (confidentiel, PKCE S256),
// rôles super-admin, admin, user et mappeur des rôles. Idempotent ; Node 24, sans dépendance.
// Variables : UD_KC_URL, UD_KC_REALM (obligatoire, jamais « master »), UD_KC_ADMIN_USERNAME,
// UD_KC_ADMIN_PASSWORD (environnement seulement), UD_KC_ADMIN_REALM, UD_PUBLIC_URL, UD_OIDC_CLIENT_ID,
// UD_KC_REALM_DISPLAY_NAME. Options : --dry-run, --secret-file <chemin> (0600, jamais affiché).
import { chmodSync, writeFileSync } from 'node:fs';

const ROLES = [
  { name: 'super-admin', description: 'Universal Drive : super admin' },
  { name: 'admin', description: 'Universal Drive : administrateur' },
  { name: 'user', description: 'Universal Drive : utilisateur' },
];
const MAPPER_NAME = 'drive client roles';

function fail(message) {
  console.error(`configure: ${message}`);
  process.exit(1);
}

// ---------- Arguments et variables ----------

const argv = process.argv.slice(2);
let dryRun = false;
let secretFile = null;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '--dry-run') dryRun = true;
  else if (a === '--secret-file') {
    secretFile = argv[++i];
    if (!secretFile || secretFile.startsWith('--')) fail('--secret-file : chemin attendu');
  } else if (a === '--help' || a === '-h') {
    console.log('Usage : UD_KC_REALM=<realm> UD_KC_ADMIN_PASSWORD=… node deploy/keycloak/configure.mjs [--dry-run] [--secret-file chemin]');
    process.exit(0);
  } else if (/password|secret|token/i.test(a)) {
    fail('les identifiants se passent par l\'environnement (UD_KC_ADMIN_PASSWORD), jamais en argument');
  } else fail(`option inconnue ${a}`);
}

const env = process.env;
const KC_URL = (env.UD_KC_URL || fail('UD_KC_URL manquant')).replace(/\/+$/, '');
const REALM = env.UD_KC_REALM || '';
const ADMIN_REALM = env.UD_KC_ADMIN_REALM || 'master';
const ADMIN_USER = env.UD_KC_ADMIN_USERNAME || 'admin';
const ADMIN_PASSWORD = env.UD_KC_ADMIN_PASSWORD || '';
const PUBLIC_URL = (env.UD_PUBLIC_URL || fail('UD_PUBLIC_URL manquant')).replace(/\/+$/, '');
const CLIENT_ID = env.UD_OIDC_CLIENT_ID || 'drive';

if (!REALM) fail('UD_KC_REALM est obligatoire (nom du realm, définitif)');
if (REALM === 'master') fail('refus : le realm master est réservé à l\'administration');
if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,62}$/.test(REALM)) fail(`nom de realm invalide : ${REALM}`);
if (!ADMIN_PASSWORD) fail('UD_KC_ADMIN_PASSWORD est obligatoire (environnement)');
const kcUrl = new URL(KC_URL);
const publicUrl = new URL(PUBLIC_URL);
if (publicUrl.origin !== PUBLIC_URL) fail(`UD_PUBLIC_URL doit être une origine sans chemin (${publicUrl.origin})`);
const LOCAL = new Set(['localhost', '127.0.0.1', '[::1]']);
const isLocal = (u) => LOCAL.has(u.hostname) || u.hostname.endsWith('.localhost') || u.hostname === 'host.docker.internal';
if (kcUrl.protocol !== 'https:' && !isLocal(kcUrl)) fail('UD_KC_URL doit être en HTTPS hors machine locale');
if (publicUrl.protocol !== 'https:' && !isLocal(publicUrl)) fail('UD_PUBLIC_URL doit être en HTTPS hors machine locale');

const REDIRECT = `${PUBLIC_URL}/api/v1/auth/oidc/callback`;
const POST_LOGOUT = `${PUBLIC_URL}/login`;

// ---------- Client de l'API d'administration ----------

let token = null;
let tokenAt = 0;
async function adminToken() {
  // Les jetons du realm master durent 60 s : on en reprend un toutes les 30 s.
  if (token && Date.now() - tokenAt < 30_000) return token;
  let res;
  try {
    res = await fetch(`${KC_URL}/realms/${encodeURIComponent(ADMIN_REALM)}/protocol/openid-connect/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'password', client_id: 'admin-cli', username: ADMIN_USER, password: ADMIN_PASSWORD }).toString(),
    });
  } catch (err) {
    fail(`Keycloak injoignable sur ${kcUrl.host} (${err?.cause?.code ?? err?.message ?? 'erreur'})`);
  }
  if (!res.ok) fail(`jeton d'administration refusé (HTTP ${res.status}) : vérifier UD_KC_ADMIN_USERNAME / UD_KC_ADMIN_PASSWORD / UD_KC_ADMIN_REALM`);
  token = (await res.json()).access_token;
  tokenAt = Date.now();
  return token;
}

/** Appel de l'API d'administration ; renvoie { status, body } ; 404 n'est pas une erreur. */
async function api(method, path, body) {
  const res = await fetch(`${KC_URL}/admin/realms${path}`, {
    method,
    headers: { authorization: `Bearer ${await adminToken()}`, ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok && res.status !== 404) {
    let detail = '';
    try {
      const j = JSON.parse(text);
      detail = j.errorMessage || j.error || '';
    } catch {}
    fail(`${method} ${path} : HTTP ${res.status}${detail ? ` (${detail})` : ''}`);
  }
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {}
  return { status: res.status, body: json, location: res.headers.get('location') };
}

const changes = [];
function plan(what) {
  changes.push(what);
  console.log(`${dryRun ? '[dry-run] ' : ''}${what}`);
}

const R = `/${encodeURIComponent(REALM)}`;

// ---------- 1. Realm ----------

const realmSettings = {
  enabled: true,
  sslRequired: 'external',
  registrationAllowed: false,
  resetPasswordAllowed: false,
  editUsernameAllowed: false,
  duplicateEmailsAllowed: false,
  bruteForceProtected: true,
  permanentLockout: false,
  failureFactor: 10,
  waitIncrementSeconds: 60,
  maxFailureWaitSeconds: 900,
  minimumQuickLoginWaitSeconds: 60,
  quickLoginCheckMilliSeconds: 1000,
  maxDeltaTimeSeconds: 43200,
  internationalizationEnabled: true,
  supportedLocales: ['fr', 'en'],
  defaultLocale: 'fr',
  accessTokenLifespan: 300,
};

const same = (a, b) => JSON.stringify(Array.isArray(a) ? [...a].sort() : a) === JSON.stringify(Array.isArray(b) ? [...b].sort() : b);

let realmExists = false;
{
  const { status, body } = await api('GET', R);
  if (status === 404) {
    plan(`realm ${REALM} : création (inscription désactivée, force brute, fr, jetons d'accès 300 s)`);
    if (!dryRun) await api('POST', '', { realm: REALM, displayName: env.UD_KC_REALM_DISPLAY_NAME || REALM, ...realmSettings });
    realmExists = !dryRun;
  } else {
    realmExists = true;
    const diff = Object.keys(realmSettings).filter((k) => !same(body[k], realmSettings[k]));
    if (diff.length) {
      plan(`realm ${REALM} : mise à jour de ${diff.join(', ')}`);
      if (!dryRun) await api('PUT', R, Object.fromEntries(diff.map((k) => [k, realmSettings[k]])));
    } else console.log(`realm ${REALM} : déjà conforme`);
  }
}

// ---------- 2. Client ----------

const mapper = {
  name: MAPPER_NAME,
  protocol: 'openid-connect',
  protocolMapper: 'oidc-usermodel-client-role-mapper',
  consentRequired: false,
  config: {
    'usermodel.clientRoleMapping.clientId': CLIENT_ID,
    'claim.name': 'resource_access.${client_id}.roles',
    'jsonType.label': 'String',
    multivalued: 'true',
    'id.token.claim': 'true',
    'access.token.claim': 'true',
    'userinfo.token.claim': 'true',
    'introspection.token.claim': 'true',
  },
};

const clientFlags = {
  enabled: true,
  protocol: 'openid-connect',
  publicClient: false,
  bearerOnly: false,
  clientAuthenticatorType: 'client-secret',
  standardFlowEnabled: true,
  implicitFlowEnabled: false,
  directAccessGrantsEnabled: false,
  serviceAccountsEnabled: false,
  frontchannelLogout: false,
  consentRequired: false,
};
const clientAttributes = {
  'pkce.code.challenge.method': 'S256',
  'oauth2.device.authorization.grant.enabled': 'false',
  'oidc.ciba.grant.enabled': 'false',
};

const union = (list, add) => [...new Set([...(list ?? []), ...add])];

let clientUuid = null;
if (realmExists) {
  const { body: found } = await api('GET', `${R}/clients?clientId=${encodeURIComponent(CLIENT_ID)}`);
  const existing = found?.find((c) => c.clientId === CLIENT_ID);
  if (!existing) {
    plan(`client ${CLIENT_ID} : création (confidentiel, standard flow seul, PKCE S256, ${REDIRECT})`);
    if (!dryRun) {
      const created = await api('POST', `${R}/clients`, {
        clientId: CLIENT_ID,
        name: 'Universal Drive',
        rootUrl: '',
        baseUrl: PUBLIC_URL,
        redirectUris: [REDIRECT],
        webOrigins: [PUBLIC_URL],
        ...clientFlags,
        attributes: { ...clientAttributes, 'post.logout.redirect.uris': POST_LOGOUT },
        protocolMappers: [mapper],
      });
      clientUuid = created.location?.split('/').pop() ?? null;
      if (!clientUuid) {
        const { body } = await api('GET', `${R}/clients?clientId=${encodeURIComponent(CLIENT_ID)}`);
        clientUuid = body?.find((c) => c.clientId === CLIENT_ID)?.id ?? null;
      }
      if (!clientUuid) fail(`client ${CLIENT_ID} créé mais introuvable`);
    }
  } else {
    clientUuid = existing.id;
    const { body: full } = await api('GET', `${R}/clients/${clientUuid}`);
    const diff = [];
    for (const [k, v] of Object.entries(clientFlags)) if (full[k] !== v) diff.push(k);
    const redirectUris = union(full.redirectUris, [REDIRECT]);
    const webOrigins = union(full.webOrigins, [PUBLIC_URL]);
    if (!same(redirectUris, full.redirectUris ?? [])) diff.push('redirectUris');
    if (!same(webOrigins, full.webOrigins ?? [])) diff.push('webOrigins');
    const attrs = { ...(full.attributes ?? {}) };
    for (const [k, v] of Object.entries(clientAttributes)) {
      if (attrs[k] !== v) {
        attrs[k] = v;
        diff.push(`attributes.${k}`);
      }
    }
    const logout = (attrs['post.logout.redirect.uris'] ?? '').split('##').filter(Boolean);
    if (!logout.includes(POST_LOGOUT)) {
      attrs['post.logout.redirect.uris'] = [...logout, POST_LOGOUT].join('##');
      diff.push('attributes.post.logout.redirect.uris');
    }
    if (diff.length) {
      plan(`client ${CLIENT_ID} : mise à jour de ${diff.join(', ')}`);
      if (!dryRun) {
        // PUT de la représentation complète : un client partiel remettrait des champs à leur défaut.
        const { protocolMappers: _m, ...rest } = full;
        await api('PUT', `${R}/clients/${clientUuid}`, { ...rest, ...clientFlags, redirectUris, webOrigins, attributes: attrs });
      }
    } else console.log(`client ${CLIENT_ID} : déjà conforme`);

    // Mappeur des rôles du client (le client existait : le mappeur peut manquer ou différer).
    const mappers = full.protocolMappers ?? [];
    const m = mappers.find((x) => x.name === MAPPER_NAME);
    if (!m) {
      plan(`client ${CLIENT_ID} : ajout du mappeur « ${MAPPER_NAME} » (ID token, userinfo, jeton d'accès)`);
      if (!dryRun) await api('POST', `${R}/clients/${clientUuid}/protocol-mappers/models`, mapper);
    } else {
      const cfgDiff = Object.keys(mapper.config).filter((k) => m.config?.[k] !== mapper.config[k]);
      if (m.protocolMapper !== mapper.protocolMapper || cfgDiff.length) {
        plan(`client ${CLIENT_ID} : mise à jour du mappeur « ${MAPPER_NAME} » (${cfgDiff.join(', ') || 'type'})`);
        if (!dryRun) await api('PUT', `${R}/clients/${clientUuid}/protocol-mappers/models/${m.id}`, { ...m, ...mapper, id: m.id, config: { ...m.config, ...mapper.config } });
      } else console.log(`mappeur « ${MAPPER_NAME} » : déjà conforme`);
    }
  }
} else if (dryRun) {
  plan(`client ${CLIENT_ID} : création (confidentiel, standard flow seul, PKCE S256, ${REDIRECT})`);
}

// ---------- 3. Rôles du client ----------

if (clientUuid) {
  for (const role of ROLES) {
    const { status } = await api('GET', `${R}/clients/${clientUuid}/roles/${encodeURIComponent(role.name)}`);
    if (status === 404) {
      plan(`rôle ${CLIENT_ID}/${role.name} : création`);
      if (!dryRun) await api('POST', `${R}/clients/${clientUuid}/roles`, { ...role, composite: false, clientRole: true });
    } else console.log(`rôle ${CLIENT_ID}/${role.name} : présent`);
  }
} else if (dryRun) {
  for (const role of ROLES) plan(`rôle ${CLIENT_ID}/${role.name} : création`);
}

// ---------- 4. Secret du client ----------

if (secretFile) {
  if (dryRun || !clientUuid) {
    console.log(`[dry-run] secret du client : serait écrit dans ${secretFile} (0600)`);
  } else {
    const { body } = await api('GET', `${R}/clients/${clientUuid}/client-secret`);
    const value = body?.value;
    if (!value) fail('secret du client introuvable');
    writeFileSync(secretFile, `${value}\n`, { mode: 0o600 });
    chmodSync(secretFile, 0o600); // fichier déjà présent : writeFileSync ne change pas ses droits
    console.log(`secret du client : écrit dans ${secretFile} (0600, ${value.length} caractères, non affiché)`);
  }
} else {
  console.log('secret du client : non écrit (option --secret-file <chemin>)');
}

console.log(
  changes.length === 0
    ? `configure: rien à faire, ${REALM} est déjà conforme (émetteur ${KC_URL}/realms/${REALM})`
    : `configure: ${changes.length} changement(s) ${dryRun ? 'prévu(s), rien n\'a été écrit' : 'appliqué(s)'} (émetteur ${KC_URL}/realms/${REALM})`,
);
