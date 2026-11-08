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


class LoginRejected(Exception):
    pass


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


def manager_request(url: str, jwt: str, operation: str, args: dict) -> None:
    model_import.require(operation in {"createApiToken", "deleteApiToken"}, "Unsupported bootstrap manager operation")
    request = urllib.request.Request(url + "/catalogue/" + operation, data=json.dumps({"args": args}).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + jwt, "Content-Type": "application/json", "Accept": "application/json"})
    opener = urllib.request.build_opener(model_import.NoRedirect())
    try:
        with opener.open(request, timeout=300) as response:
            model_import.require(response.status == 200, "Unexpected native manager status")
            result = json.load(response)
        model_import.require(isinstance(result, dict) and "error" not in result, "Unexpected native manager response")
    except urllib.error.HTTPError as error:
        raise model_import.ImportError_(f"Tabula token coordination failed (HTTP {error.code})") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise model_import.ImportError_(f"Tabula token coordination unavailable: {type(error).__name__}") from None


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
    ttl = config.get("api_token_ttl_seconds", 3600 if manager else 1200)
    model_import.require(isinstance(ttl, int) and not isinstance(ttl, bool) and 300 <= ttl <= 7200, "api_token_ttl_seconds must be between 300 and 7200")
    login_guard = receipt_path.with_suffix(".login-failed")
    model_import.require(not login_guard.exists(), f"A previous login was refused; inspect the account before removing {login_guard}")
    try:
        jwt, refresh = oidc_login(config, bootstrap)
    except LoginRejected:
        login_guard.parent.mkdir(parents=True, exist_ok=True)
        login_guard.write_text("Login refused; inspect the account before retrying.\n", encoding="utf-8")
        raise
    token_id = model_import.ident("tok", secrets.token_urlsafe(32))
    secret = "tb_" + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    api = model_import.Api(config["backend_url"].rstrip("/") + "/v1/database/" + database + "/route/api/v3", secret)
    created_or_uncertain = False
    try:
        created_or_uncertain = True
        token_args = {"id": token_id, "name": "Terraform model import (temporary)", "prefix": secret[:9], "hash": hashlib.sha256(secret.encode()).hexdigest(), "readOnly": False, "baseIds": []}
        expires_at = int((time.time() + ttl) * 1_000_000)
        if manager:
            manager_request(manager, jwt, "createApiToken", {**token_args, "expiresAtMicros": str(expires_at)})
        else:
            reducer(config["backend_url"], jwt, "create_api_token", {**token_args, "expiresAt": {"some": {"__timestamp_micros_since_unix_epoch__": expires_at}}}, database)
        workspace = choose_workspace(api, config.get("workspace_id"), receipt_path)
        print(f"Selected workspace: {workspace}")
        recovery = config.get("recovery_base_id")
        model_import.require(recovery is None or re.fullmatch(r"bas[0-9a-hjkmnp-tv-z]{14}", recovery), "Invalid recovery base ID")
        os.environ.pop("TABULA_RECOVER_BASE_ID", None)
        if recovery:
            os.environ["TABULA_RECOVER_BASE_ID"] = recovery
        try:
            model_import.install(api, workspace, model, hashlib.sha256(raw).hexdigest(), receipt_path)
        finally:
            os.environ.pop("TABULA_RECOVER_BASE_ID", None)
    finally:
        if created_or_uncertain:
            try:
                if refresh:
                    try:
                        jwt, refresh = refresh_login(config, refresh, jwt)
                    except model_import.ImportError_:
                        pass  # An issuer outage must not prevent revocation with an access token that is still valid.
                if manager:
                    manager_request(manager, jwt, "deleteApiToken", {"tokenId": token_id})
                else:
                    reducer(config["backend_url"], jwt, "delete_api_token", {"tokenId": token_id}, database)
                print("Temporary Tabula API token revoked.")
            except model_import.ImportError_:
                print(f"Temporary token {token_id} could not be revoked; its maximum lifetime is {ttl // 60} minutes.", file=sys.stderr)
        if refresh:
            realm = config.get("realm", "tabula")
            url = config["keycloak_url"].rstrip("/") + "/realms/" + realm + "/protocol/openid-connect/logout"
            try:
                status, _, _ = form_request(urllib.request.build_opener(model_import.NoRedirect()), url, {"client_id": "tabula", "refresh_token": refresh})
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
