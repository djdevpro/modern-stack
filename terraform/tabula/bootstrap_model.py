#!/usr/bin/env python3
"""Provision a model as the configured Tabula owner, using existing server-side bootstrap credentials."""

from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import import_template as model_import


BASE_ID = r"bas[0-9a-hjkmnp-tv-z]{14}"
TOKEN_ID = r"tok[0-9a-hjkmnp-tv-z]{14}"
RETRY_LATER = f"Delete nothing: rerun the same targeted apply later. If it stays blocked, {model_import.LOGS_HINT}."


class LoginRejected(Exception):
    pass


class ManagerRefused(model_import.ImportError_):
    """The native manager answered with an error status. Its short code tells a busy catalogue from a real refusal."""

    def __init__(self, status: int, code: str, subject: str, advice: str = ""):
        super().__init__(f"{subject} failed (HTTP {status}, {code})" + (". " + advice if advice else ""))
        self.status, self.code = status, code


class ManagerUnavailable(model_import.ImportError_):
    """No usable answer came back: whether the request took effect is unknown."""


def origin(url: str) -> tuple[str, str, int | None]:
    parts = urllib.parse.urlsplit(url)
    model_import.require(parts.hostname and not parts.username and not parts.password, "Invalid authentication URL")
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    model_import.require(parts.scheme == "https" or (parts.scheme == "http" and local), "Authentication requires HTTPS except on loopback")
    return parts.scheme, parts.hostname, parts.port


class LoginForm(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_form = False
        self.action = ""
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "kc-form-login":
            self.in_form = True
            self.action = attributes.get("action") or ""
        elif self.in_form and tag == "input" and attributes.get("name"):
            self.inputs[attributes["name"] or ""] = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.in_form:
            self.in_form = False


def form_request(opener: urllib.request.OpenerDirector, url: str, data: dict[str, str] | None = None) -> tuple[int, dict, bytes]:
    body = None if data is None else urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers.items()), error.read(32_768)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise model_import.ImportError_(f"Authentication service unavailable: {type(error).__name__}") from None


def oidc_login(config: dict, bootstrap: dict) -> tuple[str, str]:
    username = bootstrap.get("username")
    model_import.require(isinstance(username, str) and username.casefold() == config["admin_email"].casefold(), "Bootstrap account does not match configured owner")
    model_import.require(isinstance(bootstrap.get("password"), str) and bootstrap["password"], "Bootstrap password unavailable")
    realm = config.get("realm", "tabula")
    model_import.require(re.fullmatch(r"[a-z0-9-]+", realm) is not None, "Invalid realm")
    issuer = config["keycloak_url"].rstrip("/") + "/realms/" + realm
    issuer_origin = origin(issuer)
    callback = config["frontend_url"].rstrip("/") + "/auth/callback"
    origin(callback)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    query = urllib.parse.urlencode({"client_id": "tabula", "redirect_uri": callback, "response_type": "code", "scope": "openid profile email", "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    # Keycloak marks local development cookies Secure; accept them over loopback HTTP only.
    local_http = issuer_origin[0] == "http" and issuer_origin[1] in {"localhost", "127.0.0.1", "::1"}
    policy = http.cookiejar.DefaultCookiePolicy(secure_protocols=("https", "wss", "http") if local_http else ("https", "wss"))
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar(policy)), model_import.NoRedirect())
    status, _, body = form_request(opener, issuer + "/protocol/openid-connect/auth?" + query)
    model_import.require(status == 200, f"Keycloak authorization request failed (HTTP {status})")
    form = LoginForm()
    form.feed(body.decode("utf-8"))
    action = urllib.parse.urljoin(issuer + "/", form.action)
    model_import.require(form.action and origin(action) == issuer_origin and urllib.parse.urlsplit(action).path.startswith("/realms/" + realm + "/"), "Unexpected Keycloak login form destination")
    form.inputs.update({"username": username, "password": bootstrap["password"]})
    status, headers, _ = form_request(opener, action, form.inputs)
    if status in {200, 400, 401, 403}:
        raise LoginRejected("Stored initial credentials were refused or an interactive account action is required")
    model_import.require(status in {302, 303}, f"Keycloak login failed (HTTP {status})")
    location = urllib.parse.urljoin(action, headers.get("Location", ""))
    redirected, expected = urllib.parse.urlsplit(location), urllib.parse.urlsplit(callback)
    model_import.require(origin(location) == origin(callback) and redirected.path == expected.path, "Unexpected Keycloak callback")
    params = urllib.parse.parse_qs(redirected.query)
    model_import.require(params.get("state") == [state] and len(params.get("code", [])) == 1, "Invalid authorization response")
    status, _, body = form_request(opener, issuer + "/protocol/openid-connect/token", {"grant_type": "authorization_code", "client_id": "tabula", "code": params["code"][0], "redirect_uri": callback, "code_verifier": verifier})
    model_import.require(status == 200, f"Keycloak token exchange failed (HTTP {status})")
    result = json.loads(body)
    token, refresh = result.get("access_token"), result.get("refresh_token", "")
    model_import.require(isinstance(token, str) and isinstance(refresh, str), "Invalid Keycloak token response")
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        audience = claims.get("aud", [])
        audience = [audience] if isinstance(audience, str) else audience
        aliases = [claims.get("email", ""), claims.get("preferred_username", "")]
        model_import.require(claims.get("iss") == issuer and "tabula" in audience and bool(claims.get("sub")) and any(isinstance(v, str) and v.casefold() == username.casefold() for v in aliases), "Keycloak returned a token for a different account or audience")
    except (IndexError, ValueError, TypeError):
        raise model_import.ImportError_("Invalid Keycloak access token") from None
    return token, refresh


def database_name(config: dict) -> str:
    database = config.get("database_name", "tabula")
    model_import.require(isinstance(database, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", database), "Invalid database_name")
    return database


def manager_url(config: dict) -> str | None:
    configured = config.get("native_manager_url")
    if configured is None or configured == "":
        return None
    model_import.require(isinstance(configured, str), "Invalid native_manager_url")
    frontend = urllib.parse.urlsplit(config["frontend_url"])
    model_import.require(not frontend.query and not frontend.fragment and frontend.path in {"", "/"}, "Invalid frontend URL for native manager")
    expected = urllib.parse.urlunsplit((frontend.scheme, frontend.netloc, "/native", "", ""))
    candidate = expected if configured in {"/native", "/native/"} else configured.rstrip("/")
    model_import.require(candidate == expected and origin(candidate) == origin(config["frontend_url"]), "Native manager must use the frontend /native proxy")
    return candidate


def reducer(backend: str, jwt: str, name: str, body: dict, database: str = "tabula") -> None:
    model_import.require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", database), "Invalid database_name")
    url = backend.rstrip("/") + "/v1/database/" + database + "/call/" + name
    origin(url)
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Authorization": "Bearer " + jwt, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(model_import.NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            response.read()
    except urllib.error.HTTPError as error:
        raise model_import.ImportError_(f"Tabula reducer {name} failed (HTTP {error.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise model_import.ImportError_(f"Tabula reducer {name} unavailable: {type(error).__name__}") from None


def manager_error_code(raw: bytes) -> str:
    """Keep only the manager's short code: nothing else from an error body is ever logged."""
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return "unknown"
    code = body.get("error") if isinstance(body, dict) and set(body) == {"error"} else None
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "unknown"


def manager_call(url: str, jwt: str, method: str, path: str, body: dict | None, subject: str, timeout: float) -> dict:
    headers = {"Authorization": "Bearer " + jwt, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=headers)
    opener = urllib.request.build_opener(model_import.NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            status, raw = response.status, response.read(65_537)
    except urllib.error.HTTPError as error:
        try:
            code = manager_error_code(error.read(4_097))
        except (OSError, ValueError, AttributeError):
            code = "unknown"
        raise ManagerRefused(error.code, code, subject) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise ManagerUnavailable(f"{subject} unavailable: {type(error).__name__}") from None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        result = None
    if status != 200 or not isinstance(result, dict) or "error" in result:
        raise ManagerUnavailable(f"{subject} returned an unexpected native manager response")
    return result


def manager_request(url: str, jwt: str, operation: str, args: dict) -> None:
    model_import.require(operation in {"createApiToken", "deleteApiToken"}, "Unsupported bootstrap manager operation")
    manager_call(url, jwt, "POST", "/catalogue/" + operation, {"args": args}, "Tabula token coordination", 300)


def base_runtime(url: str, jwt: str, base_id: str) -> str:
    """State of one base as the native manager publishes it to the signed-in account; no API token is involved."""
    model_import.require(re.fullmatch(BASE_ID, base_id) is not None, "Invalid base ID")
    result = manager_call(url, jwt, "GET", "/bases/" + base_id + "/runtime", None, "Tabula base state check", 30)
    if result.get("baseId") != base_id or result.get("status") not in {"ready", "updating", "error", "legacy"}:
        raise ManagerUnavailable("Tabula base state check returned an unexpected native manager response")
    return result["status"]


def refresh_login(config: dict, refresh: str, previous_jwt: str) -> tuple[str, str]:
    realm = config.get("realm", "tabula")
    model_import.require(isinstance(realm, str) and re.fullmatch(r"[a-z0-9-]+", realm), "Invalid realm")
    issuer = config["keycloak_url"].rstrip("/") + "/realms/" + realm
    origin(issuer)
    status, _, body = form_request(urllib.request.build_opener(model_import.NoRedirect()), issuer + "/protocol/openid-connect/token",
                                  {"grant_type": "refresh_token", "client_id": "tabula", "refresh_token": refresh})
    model_import.require(status == 200, f"Keycloak token refresh failed (HTTP {status})")
    try:
        result = json.loads(body)
        token, rotated = result["access_token"], result.get("refresh_token", refresh)
        model_import.require(isinstance(token, str) and isinstance(rotated, str), "Invalid refreshed Keycloak token")
        def claims(value: str) -> dict:
            part = value.split(".")[1]
            return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        current, previous = claims(token), claims(previous_jwt)
        model_import.require(isinstance(current, dict) and isinstance(previous, dict), "Invalid refreshed Keycloak claims")
        audience = current.get("aud", [])
        audience = [audience] if isinstance(audience, str) else audience
        model_import.require(current.get("iss") == issuer and "tabula" in audience and current.get("sub") == previous.get("sub")
                             and bool(current.get("sub")), "Keycloak refreshed another identity or audience")
        return token, rotated
    except (KeyError, IndexError, ValueError, TypeError):
        raise model_import.ImportError_("Invalid refreshed Keycloak token") from None


class Session:
    """The signed-in operator. Access tokens are short-lived, so anything that waits renews through here."""

    def __init__(self, config: dict, jwt: str, refresh: str):
        self.config, self.jwt, self.refresh, self.renewed = config, jwt, refresh, time.monotonic()

    def renew(self, max_age: float = 0) -> str:
        if self.refresh and time.monotonic() - self.renewed >= max_age:
            try:
                self.jwt, self.refresh = refresh_login(self.config, self.refresh, self.jwt)
            except model_import.ImportError_:
                pass  # An issuer outage must not prevent work with an access token that is still valid.
            self.renewed = time.monotonic()
        return self.jwt


def setting(config: dict, key: str, default: int, low: int, high: int) -> int:
    value = default if config.get(key) is None else config[key]
    model_import.require(isinstance(value, int) and not isinstance(value, bool) and low <= value <= high, f"{key} must be between {low} and {high}")
    return value


def read_receipt(path: Path) -> dict:
    if not path.exists():
        return {}
    receipt = json.loads(path.read_text(encoding="utf-8"))
    model_import.require(isinstance(receipt, dict), "Invalid receipt")
    return receipt


def known_bases(receipt: dict, recovery: str | None) -> list[str]:
    noted = receipt.get("pending_job")
    candidates = [receipt.get("base_id"), noted.get("base_id") if isinstance(noted, dict) else None, recovery]
    return list(dict.fromkeys(base for base in candidates if isinstance(base, str) and re.fullmatch(BASE_ID, base)))


def wait_ready(manager: str, session: Session, base_id: str, timeout: int) -> None:
    """Tabula refuses any token change while a base of the account is being published: wait for it instead of failing."""
    deadline, pause, renewed, announced = time.monotonic() + timeout, 1.0, False, False
    while True:
        try:
            status = base_runtime(manager, session.renew(60), base_id)
            renewed = False
        except ManagerRefused as error:
            if error.status in {401, 403} and not renewed:
                session.renew()  # the access token expired during the wait
                renewed = True
                continue
            if error.status < 500:
                raise
            status = "unreachable"
        except ManagerUnavailable:
            status = "unreachable"
        if status in {"ready", "legacy"}:
            return
        model_import.require(status != "error", f"Base {base_id} is in the error state: its last publication failed. "
                                                  f"Delete nothing and {model_import.LOGS_HINT} before retrying.")
        remaining = deadline - time.monotonic()
        model_import.require(remaining > 0, (f"Base {base_id} is still being published after {timeout} seconds; the publication continues on the server. "
                                             if status == "updating" else f"The native manager gave no state for base {base_id} during {timeout} seconds. ") + RETRY_LATER)
        if not announced:
            print(f"Waiting for base {base_id} to be ready (up to {timeout} seconds).")
            announced = True
        time.sleep(min(pause, remaining))
        pause = min(pause * 1.5, 10)


def create_token(config: dict, manager: str | None, database: str, session: Session, token_args: dict, expires_at: int, budget: int) -> None:
    if not manager:
        reducer(config["backend_url"], session.jwt, "create_api_token", {**token_args, "expiresAt": {"some": {"__timestamp_micros_since_unix_epoch__": expires_at}}}, database)
        return
    # Tabula resumes an access job only for a strictly identical repetition, and answers busy to anything else:
    # these arguments, expiry included, are built once and never change between attempts.
    arguments = {**token_args, "expiresAtMicros": str(expires_at)}
    deadline, pause = time.monotonic() + budget, 2.0
    while True:
        try:
            manager_request(manager, session.renew(60), "createApiToken", arguments)
            return
        except ManagerRefused as error:
            if error.code != "busy":
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ManagerRefused(error.status, error.code, "Tabula token coordination",
                                     f"Tabula still answers busy after {budget} seconds: a base of this account is being published, "
                                     f"or another access change is running. {RETRY_LATER}") from None
        time.sleep(min(pause, remaining))
        pause = min(pause * 1.5, 30)


def revoke_token(config: dict, manager: str | None, database: str, session: Session, token_id: str) -> None:
    """Delete one temporary token, or raise while it may still exist."""
    deadline, pause = time.monotonic() + 60, 2.0
    while True:
        try:
            if manager:
                manager_request(manager, session.jwt, "deleteApiToken", {"tokenId": token_id})
            else:
                reducer(config["backend_url"], session.jwt, "delete_api_token", {"tokenId": token_id}, database)
            return
        except ManagerRefused as error:
            if error.code == "token_not_found":
                return  # already revoked, or never created
            remaining = deadline - time.monotonic()
            if error.code != "busy" or remaining <= 0:
                raise
        time.sleep(min(pause, remaining))
        pause = min(pause * 2, 15)


def note_token(receipt_path: Path, token_id: str) -> bool:
    """Leave a token that cannot be revoked now to the next run. Only its identifier is written, never the secret."""
    try:
        receipt = read_receipt(receipt_path)
        if not receipt:
            return False
        noted = receipt.setdefault("unrevoked_tokens", [])
        model_import.require(isinstance(noted, list), "Invalid unrevoked token list in receipt")
        if token_id not in noted:
            noted.append(token_id)
        model_import.save(receipt_path, receipt)
        return True
    except (model_import.ImportError_, OSError, ValueError):
        return False


def revoke_noted(config: dict, manager: str | None, database: str, session: Session, receipt_path: Path, receipt: dict) -> None:
    noted = receipt.get("unrevoked_tokens") or []
    model_import.require(isinstance(noted, list) and all(isinstance(token, str) and re.fullmatch(TOKEN_ID, token) for token in noted),
                         "Invalid unrevoked token list in receipt")
    for token_id in list(noted):
        try:
            revoke_token(config, manager, database, session, token_id)
        except model_import.ImportError_:
            print(f"Temporary token {token_id} from an earlier run could not be revoked yet; it expires on its own.", file=sys.stderr)
            continue
        noted.remove(token_id)
        if not noted:
            del receipt["unrevoked_tokens"]
        model_import.save(receipt_path, receipt)
        print("Temporary Tabula API token from an earlier run revoked.")


def choose_workspace(api: model_import.Api, requested: str | None, receipt_path: Path) -> str:
    if receipt_path.exists():
        saved = json.loads(receipt_path.read_text(encoding="utf-8")).get("workspace_id")
        if saved:
            model_import.require(requested is None or requested == saved, "Installation belongs to another workspace")
            return saved
    if requested:
        api.request("GET", "/meta/workspaces", {"workspaceId": requested})
        return requested
    workspaces = (api.request("GET", "/meta/workspaces") or {}).get("list", [])
    eligible = [w for w in workspaces if w.get("role") in {"owner", "creator"}]
    if eligible:
        eligible.sort(key=lambda w: (w.get("role") != "owner", w.get("title", "").casefold(), w["id"]))
        return eligible[0]["id"]
    created = api.request("POST", "/meta/workspaces", body={"title": "Prospection"}) or {}
    return created["id"]


def run(config: dict, model_file: Path, receipt_path: Path, bootstrap: dict) -> None:
    raw = model_file.read_bytes()
    model = json.loads(raw)
    model_import.validate(model)
    database, manager = database_name(config), manager_url(config)
    job_timeout = model_import.job_timeout(config.get("schema_job_timeout_seconds"))
    ready_timeout = setting(config, "base_ready_timeout_seconds", 900, 30, 3600)
    # A publication still queued fails once its token expires: the token must outlive the longest poll by a margin.
    shortest = job_timeout + model_import.TOKEN_MARGIN if manager else 300
    ttl = setting(config, "api_token_ttl_seconds", min(7200, max(3600, shortest + ready_timeout)) if manager else 1200, shortest, 7200)
    recovery = config.get("recovery_base_id")
    model_import.require(recovery is None or re.fullmatch(BASE_ID, recovery), "Invalid recovery base ID")
    login_guard = receipt_path.with_suffix(".login-failed")
    model_import.require(not login_guard.exists(), f"A previous login was refused; inspect the account before removing {login_guard}")
    lock = receipt_path.with_suffix(receipt_path.suffix + ".lock")
    model_import.require(not lock.exists(), f"Import lock exists: {lock}. If no import is running, remove it before retrying.")
    try:
        session = Session(config, *oidc_login(config, bootstrap))
    except LoginRejected:
        login_guard.parent.mkdir(parents=True, exist_ok=True)
        login_guard.write_text("Login refused; inspect the account before retrying.\n", encoding="utf-8")
        raise
    token_id = model_import.ident("tok", secrets.token_urlsafe(32))
    secret = "tb_" + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    api = model_import.Api(config["backend_url"].rstrip("/") + "/v1/database/" + database + "/route/api/v3", secret, request_timeout=job_timeout)
    api.on_wait = lambda: session.renew(120)
    token_may_exist = publication_pending = False
    try:
        receipt = read_receipt(receipt_path)
        if manager:
            for base_id in known_bases(receipt, recovery):
                wait_ready(manager, session, base_id, ready_timeout)
        revoke_noted(config, manager, database, session, receipt_path, receipt)
        token_args = {"id": token_id, "name": "Terraform model import (temporary)", "prefix": secret[:9], "hash": hashlib.sha256(secret.encode()).hexdigest(), "readOnly": False, "baseIds": []}
        expires_at = int((time.time() + ttl) * 1_000_000)
        api.expires_at = expires_at / 1_000_000 - 30
        try:
            # Time spent on busy answers is taken from the token's life, so it stops while the margin is still intact.
            create_token(config, manager, database, session, token_args, expires_at, min(ready_timeout, ttl - shortest))
            token_may_exist = True
        except ManagerRefused as error:
            token_may_exist = error.status >= 500  # below 500 the manager refused before creating anything
            raise
        except model_import.ImportError_:
            token_may_exist = True  # no answer, or a legacy reducer failure: the outcome is unknown
            raise
        workspace = choose_workspace(api, config.get("workspace_id"), receipt_path)
        print(f"Selected workspace: {workspace}")
        os.environ.pop("TABULA_RECOVER_BASE_ID", None)
        if recovery:
            os.environ["TABULA_RECOVER_BASE_ID"] = recovery
        try:
            model_import.install(api, workspace, model, hashlib.sha256(raw).hexdigest(), receipt_path)
        except model_import.OperationPending:
            publication_pending = True
            raise
        finally:
            os.environ.pop("TABULA_RECOVER_BASE_ID", None)
    finally:
        if token_may_exist and publication_pending:
            # A queued publication is authorized by this token until Tabula prepares it: revoking now would fail it.
            noted = note_token(receipt_path, token_id)
            print(f"Temporary token {token_id} is kept while its publication ends. " + ("The next run revokes it" if noted else "It could not be noted for the next run")
                  + f"; it expires on its own within {ttl // 60} minutes.", file=sys.stderr)
        elif token_may_exist:
            session.renew()
            try:
                revoke_token(config, manager, database, session, token_id)
                print("Temporary Tabula API token revoked.")
            except model_import.ImportError_:
                noted = note_token(receipt_path, token_id)
                print(f"Temporary token {token_id} could not be revoked; its maximum lifetime is {ttl // 60} minutes." + (" The next run retries." if noted else ""), file=sys.stderr)
        if session.refresh:
            realm = config.get("realm", "tabula")
            url = config["keycloak_url"].rstrip("/") + "/realms/" + realm + "/protocol/openid-connect/logout"
            try:
                status, _, _ = form_request(urllib.request.build_opener(model_import.NoRedirect()), url, {"client_id": "tabula", "refresh_token": session.refresh})
            except model_import.ImportError_:
                status = 0
            if status not in {200, 204}:
                print("Temporary Keycloak session could not be closed immediately; it will expire.", file=sys.stderr)


def main() -> None:
    model_import.require(len(sys.argv) == 4, "Usage: bootstrap_model.py CONFIG MODEL RECEIPT")
    os.umask(0o077)
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    bootstrap_path = Path(os.environ.get("TABULA_BOOTSTRAP_FILE", "/opt/tabula/bootstrap.json"))
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    run(config, Path(sys.argv[2]), Path(sys.argv[3]), bootstrap)


if __name__ == "__main__":
    try:
        main()
    except (model_import.ImportError_, LoginRejected, KeyError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Model installation refused: {error}", file=sys.stderr)
        sys.exit(1)
