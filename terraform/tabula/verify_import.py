#!/usr/bin/env python3
"""Exercise installation and recovery against a local, disposable API double."""

from __future__ import annotations

import copy
import base64
import hashlib
import io
import json
import os
import tempfile
import urllib.error
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import import_template as importer
import bootstrap_model as bootstrapper


class FakeApi:
    def __init__(self, fail_after: int | None = None, lose_base_response: bool = False):
        self.root = "https://tabula.test/v1/database/tabula/route/api/v3"
        self.workspace = importer.ident("wsp", "workspace")
        self.bases: list[dict] = []
        self.tables: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.counter = 0
        self.mutations = 0
        self.fail_after = fail_after
        self.lose_base_response = lose_base_response

    def new_id(self, prefix: str) -> str:
        self.counter += 1
        return importer.ident(prefix, str(self.counter))

    def write(self) -> None:
        self.mutations += 1
        if self.fail_after == self.mutations:
            self.fail_after = None
            raise importer.ImportError_("Simulated interruption")

    def field(self, table_id: str, spec: dict) -> dict:
        options = copy.deepcopy(spec.get("options", {}))
        if "choices" in options:
            options["choices"] = [{"id": self.new_id("cho"), "title": c["title"], "color": "gray"} for c in options["choices"]]
        if spec["type"] == "Formula":
            options["result_type"] = "number"
        item = {"id": self.new_id("fld"), "table_id": table_id, "title": spec["title"], "type": spec["type"], "options": options}
        self.tables[table_id]["fields"].append(item)
        return item

    def request(self, method: str, path: str, params: dict | None = None, body: dict | None = None, missing_ok: bool = False) -> dict | None:
        params, body = params or {}, body or {}
        if method != "GET":
            self.write()
        if path == "/meta/workspaces":
            assert params["workspaceId"] == self.workspace
            result = {"id": self.workspace}
        elif path == "/meta/bases":
            if method == "POST":
                result = {"id": self.new_id("bas"), "workspace_id": self.workspace, "title": body["title"]}
                self.bases.append(result)
                if self.lose_base_response:
                    self.lose_base_response = False
                    raise importer.ImportError_("Simulated lost response")
            elif "baseId" in params:
                result = next(b for b in self.bases if b["id"] == params["baseId"])
            else:
                result = {"list": self.bases}
        elif path == "/meta/tables":
            if method == "POST":
                table_id = self.new_id("tbl")
                table = {"id": table_id, "title": body["title"], "base_id": params["baseId"], "fields": [], "views": []}
                self.tables[table_id] = table
                for spec in body["fields"]:
                    self.field(table_id, spec)
                table["views"].append({"id": self.new_id("viw"), "title": "Grid view", "type": "grid", "config": {}})
                result = table
            else:
                result = {"list": [t for t in self.tables.values() if t["base_id"] == params["baseId"]]}
        elif path == "/meta/fields":
            if method == "GET":
                result = {"list": self.tables[params["tableId"]]["fields"]}
            elif method == "PATCH":
                result = next(f for t in self.tables.values() for f in t["fields"] if f["id"] == params["fieldId"])
                result.update(body)
            else:
                table_id = params["tableId"]
                result = self.field(table_id, body)
                if body["type"] == "Links":
                    target = self.tables[body["options"]["related_table_id"]]
                    inverse = self.field(target["id"], {"title": self.tables[table_id]["title"], "type": "Links", "options": {"related_table_id": table_id, "inverse_field_id": result["id"], "allow_multiple": True}})
                    result["options"]["inverse_field_id"] = inverse["id"]
        elif path == "/meta/views":
            if method == "GET":
                result = {"list": self.tables[params["tableId"]]["views"]}
            elif method == "PATCH":
                result = next(v for t in self.tables.values() for v in t["views"] if v["id"] == params["viewId"])
                result.update(body)
            else:
                result = {"id": self.new_id("viw"), **copy.deepcopy(body)}
                self.tables[params["tableId"]]["views"].append(result)
        elif path == "/data/records":
            if method == "GET":
                result = self.records.get(params["recordId"])
                if result is None:
                    assert missing_ok
                    return None
            else:
                result = {"id": body["id"], "fields": body["fields"]}
                self.records[body["id"]] = result
        else:
            raise AssertionError((method, path))
        return copy.deepcopy(result)


def rejected(call, text: str) -> None:
    try:
        call()
    except importer.ImportError_ as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError(f"Expected rejection: {text}")


def run() -> None:
    model_path = Path(__file__).parent / "templates" / "prospection.json"
    raw = model_path.read_bytes()
    model = json.loads(raw)
    importer.validate(model)
    digest = hashlib.sha256(raw).hexdigest()
    scratch = Path(__file__).parent / ".terraform"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tabula-import-", dir=scratch) as directory:
        receipt = Path(directory) / "receipt.json"
        api = FakeApi(fail_after=8)
        rejected(lambda: importer.install(api, api.workspace, model, digest, receipt), "interruption")
        importer.install(api, api.workspace, model, digest, receipt)
        assert len(api.bases) == 1 and len(api.tables) == 6 and len(api.records) == 5
        assert sum(len(t["fields"]) for t in api.tables.values()) == 77
        assert sum(len(t["views"]) for t in api.tables.values()) == 12
        written = api.mutations
        table = next(t for t in api.tables.values() if t["title"] == "Prospects")
        table["views"][0]["title"] = "Modified by owner"
        importer.install(api, api.workspace, model, digest, receipt)
        assert api.mutations == written and table["views"][0]["title"] == "Modified by owner"
        rejected(lambda: importer.install(api, api.workspace, model, "different", receipt), "model changed")

        collision = FakeApi()
        collision.bases.append({"id": collision.new_id("bas"), "workspace_id": collision.workspace, "title": model["title"]})
        rejected(lambda: importer.install(collision, collision.workspace, model, digest, Path(directory) / "collision.json"), "already exists")
        assert collision.mutations == 0

        lost = FakeApi(lose_base_response=True)
        lost_receipt = Path(directory) / "lost.json"
        rejected(lambda: importer.install(lost, lost.workspace, model, digest, lost_receipt), "lost response")
        rejected(lambda: importer.install(lost, lost.workspace, model, digest, lost_receipt), "outcome is unknown")
        try:
            os.environ["TABULA_RECOVER_BASE_ID"] = lost.bases[0]["id"]
            importer.install(lost, lost.workspace, model, digest, lost_receipt)
        finally:
            os.environ.pop("TABULA_RECOVER_BASE_ID", None)
        assert len(lost.bases) == 1 and len(lost.tables) == 6

        export = os.environ.get("TABULA_VERIFY_EXPORT")
        if export:
            Path(export).write_text(json.dumps({"tables": list(lost.tables.values())}, ensure_ascii=False), encoding="utf-8")
    print("Import checks passed: resume, duplicate refusal, recovery, no rewrite after completion.")
    verify_native_transport()
    verify_bootstrap(model_path)


class Response(io.BytesIO):
    def __init__(self, status: int, result: dict):
        super().__init__(json.dumps(result).encode())
        self.status = self.code = status


class Opener:
    def __init__(self, responses: list[tuple[int, dict]]):
        self.responses = responses
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout > 0
        status, result = self.responses.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "Fixture", {}, Response(status, result))
        return Response(status, result)


def verify_native_transport() -> None:
    root = "https://database.tabula.test/prefix/v1/database/tabula-v2/route/api/v3"
    token = "tb_" + "A" * 43
    base_id = importer.ident("bas", "native")
    details = {"baseId": base_id, "database": "tabula-native-" + base_id}
    job = {"id": "intent-fixture_1", "status": "queued", "pollPath": "/meta/schema-jobs?intentId=intent-fixture_1"}
    routed = root.replace("/tabula-v2/", "/" + details["database"] + "/")
    api = importer.Api(root, token)
    api.opener = Opener([(409, {"error": "ERR_NATIVE_ROUTE_REQUIRED", "details": details}), (202, {"job": job}),
                         (202, {"job": {**job, "status": "prepared"}}), (200, {"id": "result"})])
    with patch.object(importer.time, "sleep"):
        assert api.request("POST", "/meta/fields", {"tableId": "fixture"}, {"title": "Fixture"}) == {"id": "result"}
    requests = api.opener.requests
    assert len(requests) == 4 and api.root == root
    assert requests[0].full_url == root + "/meta/fields?tableId=fixture"
    assert requests[1].full_url == routed + "/meta/fields?tableId=fixture"
    assert requests[0].get_header("Idempotency-key") == requests[1].get_header("Idempotency-key")
    assert requests[0].get_header("Idempotency-key")
    assert requests[0].data == requests[1].data
    assert all(request.get_header("Authorization") == "Bearer " + token for request in requests)
    assert all(request.method == "GET" and request.full_url == routed + job["pollPath"] and request.data is None
               and request.get_header("Idempotency-key") is None for request in requests[2:])

    for unsafe in [{**details, "database": "https://other.tabula.test"}, {**details, "baseId": "../other"},
                   {**details, "database": "tabula-native-other"}]:
        api = importer.Api(root, token)
        api.opener = Opener([(409, {"error": "ERR_NATIVE_ROUTE_REQUIRED", "details": unsafe})])
        rejected(lambda: api.request("POST", "/meta/fields", body={}), "HTTP 409")
        assert len(api.opener.requests) == 1
    api = importer.Api(root, token)
    api.opener = Opener([(409, {"error": "ERR_NATIVE_ROUTE_REQUIRED", "details": details})] * 2)
    rejected(lambda: api.request("POST", "/meta/fields", body={}), "HTTP 409")
    assert len(api.opener.requests) == 2

    for unsafe_path in ["https://other.tabula.test/job", "//other.tabula.test/job", job["pollPath"] + "&other=1", "/meta/schema-jobs?intentId=other"]:
        api = importer.Api(root, token)
        api.opener = Opener([(202, {"job": {**job, "pollPath": unsafe_path}})])
        rejected(lambda: api.request("POST", "/meta/fields", body={}), "pollPath")
        assert len(api.opener.requests) == 1
    api = importer.Api(root, token)
    api.opener = Opener([(202, {"job": job}), (202, {"job": {**job, "id": "other", "pollPath": "/meta/schema-jobs?intentId=other"}})])
    with patch.object(importer.time, "sleep"):
        rejected(lambda: api.request("POST", "/meta/fields", body={}), "changed")

    api = importer.Api(root, token, request_timeout=1)
    api.opener = Opener([(202, {"job": job})])
    with patch.object(importer.time, "monotonic", side_effect=[0, 2]):
        try:
            api.request("POST", "/meta/bases", body={})
        except importer.OperationPending as error:
            assert error.job == job and error.root == root and error.idempotency_key
        else:
            raise AssertionError("Missing pending-operation timeout")
    assert len(api.opener.requests) == 1
    api = importer.Api(root, token)
    api.opener = Opener([(500, {"error": "server secret should not be printed"})])
    rejected(lambda: api.request("POST", "/meta/bases", body={}), "HTTP 500 (error)")
    assert len(api.opener.requests) == 1
    api.opener = Opener([(404, {"error": "ERR_NOT_FOUND"})])
    assert api.request("GET", "/data/records", missing_ok=True) is None
    print("Native transport checks passed: one safe route retry, stable idempotency, bounded same-origin polling, malformed/changed jobs rejected.")


def verify_bootstrap(model_path: Path) -> None:
    config = {"backend_url": "https://database.tabula.test", "frontend_url": "https://app.tabula.test",
              "keycloak_url": "https://identity.tabula.test", "realm": "tabula", "admin_email": "alice@tabula.test"}
    assert bootstrapper.database_name(config) == "tabula" and bootstrapper.manager_url(config) is None
    native = {**config, "database_name": "tabula-v2", "native_manager_url": "/native"}
    assert bootstrapper.manager_url(native) == "https://app.tabula.test/native"
    for invalid in ["https://other.tabula.test/native", "//other.tabula.test/native", "https://app.tabula.test/native?secret=x", "https://app.tabula.test/elsewhere"]:
        rejected(lambda: bootstrapper.manager_url({**native, "native_manager_url": invalid}), "manager")
    rejected(lambda: bootstrapper.database_name({**config, "database_name": "../other"}), "database_name")
    transport = Opener([(200, {})])
    with patch.object(bootstrapper.urllib.request, "build_opener", return_value=transport):
        bootstrapper.manager_request("https://app.tabula.test/native", "fixture-jwt", "deleteApiToken", {"tokenId": "fixture-token-id"})
    request = transport.requests[0]
    assert request.full_url == "https://app.tabula.test/native/catalogue/deleteApiToken"
    assert request.get_header("Authorization") == "Bearer fixture-jwt"
    assert json.loads(request.data) == {"args": {"tokenId": "fixture-token-id"}}
    with tempfile.TemporaryDirectory(prefix="bootstrap-fixture-", dir=Path(__file__).parent / ".terraform") as temporary:
        for settings in [config, native]:
            created = []
            actual_api = importer.Api
            def make_api(root, token):
                result = actual_api(root, token)
                created.append(result)
                return result
            with ExitStack() as stack:
                stack.enter_context(patch.object(bootstrapper, "oidc_login", return_value=("fixture-jwt", "fixture-refresh")))
                refresh = stack.enter_context(patch.object(bootstrapper, "refresh_login", return_value=("refreshed-jwt", "rotated-refresh")))
                stack.enter_context(patch.object(importer, "Api", side_effect=make_api))
                stack.enter_context(patch.object(bootstrapper, "choose_workspace", return_value=importer.ident("wsp", "fixture")))
                stack.enter_context(patch.object(importer, "install"))
                manager = stack.enter_context(patch.object(bootstrapper, "manager_request"))
                reducer = stack.enter_context(patch.object(bootstrapper, "reducer"))
                logout = stack.enter_context(patch.object(bootstrapper, "form_request", return_value=(204, {}, b"")))
                stack.enter_context(patch.object(bootstrapper.time, "time", return_value=1000))
                stack.enter_context(redirect_stdout(io.StringIO()))
                bootstrapper.run(settings, model_path, Path(temporary) / "receipt.json", {})
                refresh.assert_called_once_with(settings, "fixture-refresh", "fixture-jwt")
                assert logout.call_args.args[2]["refresh_token"] == "rotated-refresh"
                if settings is native:
                    reducer.assert_not_called()
                    create, remove = manager.call_args_list
                    assert create.args[:3] == ("https://app.tabula.test/native", "fixture-jwt", "createApiToken")
                    assert create.args[3]["expiresAtMicros"] == "4600000000"
                    assert created[0].root.endswith("/v1/database/tabula-v2/route/api/v3")
                    assert created[0].token not in json.dumps(create.args[3])
                    assert remove.args[:3] == ("https://app.tabula.test/native", "refreshed-jwt", "deleteApiToken")
                    assert remove.args[3]["tokenId"] == create.args[3]["id"]
                else:
                    manager.assert_not_called()
                    create, remove = reducer.call_args_list
                    assert create.args[2] == "create_api_token" and create.args[4] == "tabula"
                    assert create.args[3]["expiresAt"]["some"]["__timestamp_micros_since_unix_epoch__"] == 2200000000
                    assert remove.args[1] == "refreshed-jwt" and remove.args[2] == "delete_api_token"

        with ExitStack() as stack:
            stack.enter_context(patch.object(bootstrapper, "oidc_login", return_value=("still-valid-jwt", "refresh")))
            stack.enter_context(patch.object(bootstrapper, "refresh_login", side_effect=importer.ImportError_("Issuer unavailable")))
            stack.enter_context(patch.object(bootstrapper, "choose_workspace", return_value=importer.ident("wsp", "fixture")))
            stack.enter_context(patch.object(importer, "install", side_effect=importer.ImportError_("Import interrupted")))
            manager = stack.enter_context(patch.object(bootstrapper, "manager_request"))
            stack.enter_context(patch.object(bootstrapper, "form_request", return_value=(204, {}, b"")))
            stack.enter_context(redirect_stdout(io.StringIO()))
            rejected(lambda: bootstrapper.run(native, model_path, Path(temporary) / "failure.json", {}), "Import interrupted")
            assert manager.call_args.args[1:3] == ("still-valid-jwt", "deleteApiToken")

    def jwt(subject: str) -> str:
        claims = {"iss": config["keycloak_url"] + "/realms/tabula", "aud": "tabula", "sub": subject}
        return "fixture." + base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode() + ".signature"
    for subject in ["alice", "other"]:
        with patch.object(bootstrapper, "form_request", return_value=(200, {}, json.dumps({"access_token": jwt(subject), "refresh_token": "rotated"}).encode())):
            if subject == "alice":
                assert bootstrapper.refresh_login(config, "refresh", jwt("alice")) == (jwt("alice"), "rotated")
            else:
                rejected(lambda: bootstrapper.refresh_login(config, "refresh", jwt("alice")), "another identity")
    print("Bootstrap checks passed: legacy compatibility, native database/proxy, temporary token coordination, refreshed cleanup identity.")


if __name__ == "__main__":
    run()
