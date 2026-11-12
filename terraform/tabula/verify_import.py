#!/usr/bin/env python3
"""Exercise installation and recovery against a local, disposable API double."""

from __future__ import annotations

import copy
import base64
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.error
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import import_template as importer
import bootstrap_model as bootstrapper


PENDING_JOB = {"id": "intent-fixture-pending", "status": "queued", "pollPath": "/meta/schema-jobs?intentId=intent-fixture-pending"}


class Clock:
    """Virtual time: a pause only moves the clock, so waits of many minutes are checked instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.pauses: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds

    @contextmanager
    def running(self):
        with patch.object(importer.time, "sleep", self.sleep), patch.object(importer.time, "monotonic", lambda: self.now):
            yield self


class FakeApi:
    def __init__(self, fail_after: int | None = None, lose_base_response: bool = False, pending_after: int | None = None,
                 pending_applied: bool = True, busy_reads: int = 0):
        self.root = "https://tabula.test/v1/database/tabula/route/api/v3"
        self.workspace = importer.ident("wsp", "workspace")
        self.bases: list[dict] = []
        self.tables: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.counter = 0
        self.mutations = 0
        self.fail_after = fail_after
        self.lose_base_response = lose_base_response
        self.request_timeout, self.poll_interval, self.max_poll_interval = 1800, 0.5, 5
        self.pending_after = pending_after  # this mutation outlives the polling timeout
        self.pending_applied = pending_applied  # whether its publication succeeds on the server afterwards
        self.busy_reads = busy_reads  # table reads refused while a publication holds the base

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
        if method == "GET" and path == "/meta/tables" and self.busy_reads:
            self.busy_reads -= 1
            raise importer.ApiError(409, "Tabula API: HTTP 409 (ERR_BUSY) on GET /meta/tables", "ERR_BUSY")
        pending = False
        if method != "GET":
            self.write()
            pending = self.pending_after == self.mutations
            if pending and not self.pending_applied:
                raise importer.OperationPending(PENDING_JOB, self.root, "fixture-key")
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
        if pending:
            raise importer.OperationPending(PENDING_JOB, self.root, "fixture-key")
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
    verify_pending_publication(model, digest, scratch)
    verify_native_transport()
    verify_bootstrap(model_path)
    verify_bootstrap_recovery(model_path, scratch)


def verify_pending_publication(model: dict, digest: str, scratch: Path) -> None:
    second_table = model["tables"][1]["title"]
    with tempfile.TemporaryDirectory(prefix="tabula-pending-", dir=scratch) as directory, redirect_stdout(io.StringIO()):
        # The production failure: base and first table exist, the next schema job outlives the polling timeout.
        receipt = Path(directory) / "pending.json"
        api = FakeApi(pending_after=3)
        try:
            importer.install(api, api.workspace, model, digest, receipt)
        except importer.OperationPending as error:
            assert "Delete nothing" in str(error) and "[native-intents]" in str(error) and "pollPath" not in str(error), str(error)
        else:
            raise AssertionError("Missing pending publication")
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        noted = saved["pending_job"]
        assert {k: noted[k] for k in ("intent_id", "base_id", "step", "title")} == {
            "intent_id": PENDING_JOB["id"], "base_id": api.bases[0]["id"], "step": "table", "title": second_table}
        assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", noted["noted_at"]) and not saved.get("complete")
        assert not receipt.with_suffix(".json.lock").exists()

        # Next run: the base refuses reads while the publication ends, then the table is found by title, not created twice.
        # The note goes as soon as its object is seen, even when the run is interrupted afterwards.
        api.busy_reads, api.fail_after = 2, api.mutations + 2
        with Clock().running() as clock:
            rejected(lambda: importer.install(api, api.workspace, model, digest, receipt), "interruption")
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        assert clock.pauses == [0.5, 0.75] and api.busy_reads == 0 and "pending_job" not in saved and not saved.get("complete")
        importer.install(api, api.workspace, model, digest, receipt)
        assert json.loads(receipt.read_text(encoding="utf-8"))["complete"]
        assert len(api.bases) == 1 and len(api.tables) == 6 and [t["title"] for t in api.tables.values()].count(second_table) == 1

        # The publication failed on the server after the timeout: the object is absent and gets created again.
        failed = FakeApi(pending_after=3, pending_applied=False)
        failed_receipt = Path(directory) / "failed.json"
        rejected(lambda: importer.install(failed, failed.workspace, model, digest, failed_receipt), "still running on the server")
        assert len(failed.tables) == 1 and json.loads(failed_receipt.read_text(encoding="utf-8"))["pending_job"]["title"] == second_table
        importer.install(failed, failed.workspace, model, digest, failed_receipt)
        assert len(failed.tables) == 6 and "pending_job" not in json.loads(failed_receipt.read_text(encoding="utf-8"))

        # A base that never becomes readable stops the run with the note kept, and nothing is written.
        stuck = FakeApi(pending_after=3)
        stuck_receipt = Path(directory) / "stuck.json"
        rejected(lambda: importer.install(stuck, stuck.workspace, model, digest, stuck_receipt), "still running on the server")
        stuck.busy_reads, written = 10**9, stuck.mutations
        with Clock().running() as clock:
            rejected(lambda: importer.install(stuck, stuck.workspace, model, digest, stuck_receipt), "still busy with publication " + PENDING_JOB["id"])
        assert clock.now >= 1800 and max(clock.pauses) == 5 and stuck.mutations == written
        assert json.loads(stuck_receipt.read_text(encoding="utf-8"))["pending_job"]["intent_id"] == PENDING_JOB["id"]

        # Without a noted publication a busy read is an ordinary refusal: nothing is retried.
        fresh = FakeApi(busy_reads=1)
        with Clock().running() as clock:
            rejected(lambda: importer.install(fresh, fresh.workspace, model, digest, Path(directory) / "fresh.json"), "ERR_BUSY")
        assert clock.pauses == [] and fresh.busy_reads == 0 and len(fresh.tables) == 0
    print("Pending publication checks passed: job noted in the receipt, busy reads retried, resume by title, failed job recreated.")


class Response(io.BytesIO):
    def __init__(self, status: int, result: dict | bytes):
        super().__init__(result if isinstance(result, bytes) else json.dumps(result).encode())
        self.status = self.code = status


class Opener:
    def __init__(self, responses: list[tuple[int, dict]]):
        self.responses = responses
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout > 0
        if isinstance(self.responses[0], Exception):
            raise self.responses.pop(0)
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

    # The timeout comes from the caller, then the environment, then a default sized for a slow publication.
    assert importer.Api(root, token).request_timeout == 1800 and importer.job_timeout(600) == 600
    with patch.dict(os.environ, {"TABULA_SCHEMA_JOB_TIMEOUT_SECONDS": "120"}):
        assert importer.Api(root, token).request_timeout == 120 and importer.job_timeout(600) == 600
    for invalid in ["0", "soon", "99999"]:
        with patch.dict(os.environ, {"TABULA_SCHEMA_JOB_TIMEOUT_SECONDS": invalid}):
            rejected(lambda: importer.Api(root, token), "schema job timeout")
    rejected(lambda: importer.job_timeout(True), "schema job timeout")
    # Polling slows from half a second to five seconds, and every pause lets the caller keep its session alive.
    api = importer.Api(root, token)
    api.opener = Opener([(202, {"job": job})] * 9 + [(200, {"id": "result"})])
    waits: list[int] = []
    api.on_wait = lambda: waits.append(1)
    with Clock().running() as clock:
        assert api.request("POST", "/meta/tables", body={}) == {"id": "result"}
    assert clock.pauses[:3] == [0.5, 0.75, 1.125] and clock.pauses[-2:] == [5, 5] and clock.pauses == sorted(clock.pauses) and len(waits) == 9
    # A slow publication is followed up to the timeout, then reported truthfully.
    api = importer.Api(root, token, request_timeout=60)
    api.opener = Opener([(202, {"job": job})] * 100)
    with Clock().running() as clock:
        rejected(lambda: api.request("POST", "/meta/tables", body={}), "still running on the server after the polling timeout. Delete nothing")
    assert 55 <= clock.now <= 60
    # Polling needs the token that created the job: it stops before that token expires.
    api = importer.Api(root, token)
    api.expires_at = 1010
    api.opener = Opener([(202, {"job": job})] * 100)
    with Clock().running() as clock, patch.object(importer.time, "time", lambda: 1000 + clock.now):
        rejected(lambda: api.request("POST", "/meta/tables", body={}), "still running on the server")
    assert 5 <= clock.now <= 10
    api = importer.Api(root, token)
    api.opener = Opener([(409, {"error": "ERR_BUSY"})])
    try:
        api.request("GET", "/meta/tables", {"baseId": base_id})
    except importer.ApiError as error:
        assert error.status == 409 and error.code == "ERR_BUSY"
    else:
        raise AssertionError("Missing busy refusal")
    api = importer.Api(root, token)
    api.opener = Opener([(500, {"error": "server secret should not be printed"})])
    rejected(lambda: api.request("POST", "/meta/bases", body={}), "HTTP 500 (error)")
    assert len(api.opener.requests) == 1
    api.opener = Opener([(404, {"error": "ERR_NOT_FOUND"})])
    assert api.request("GET", "/data/records", missing_ok=True) is None
    print("Native transport checks passed: one safe route retry, stable idempotency, bounded same-origin polling, malformed/changed jobs rejected, "
          "configurable timeout, growing poll interval, polling stopped before token expiry.")


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
            def make_api(root, token, **options):
                result = actual_api(root, token, **options)
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
                    assert created[0].request_timeout == 1800 and created[0].expires_at == 4570
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


MANAGER = "https://app.tabula.test/native"


def scripted_run(settings: dict, model_path: Path, receipt: Path, responses: list, install=None, clock: Clock | None = None, spare: bool = False):
    """Run the bootstrap against a scripted native manager. Returns what the manager received and what the run reported."""
    transport, errors = Opener(responses), io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(patch.object(bootstrapper, "oidc_login", return_value=("fixture-jwt", "fixture-refresh")))
        refresh = stack.enter_context(patch.object(bootstrapper, "refresh_login", return_value=("refreshed-jwt", "rotated-refresh")))
        stack.enter_context(patch.object(bootstrapper, "choose_workspace", return_value=importer.ident("wsp", "fixture")))
        installed = stack.enter_context(patch.object(importer, "install", side_effect=install))
        stack.enter_context(patch.object(bootstrapper.urllib.request, "build_opener", return_value=transport))
        stack.enter_context(patch.object(bootstrapper, "form_request", return_value=(204, {}, b"")))
        stack.enter_context(patch.object(bootstrapper.time, "time", return_value=1000))
        stack.enter_context((clock or Clock()).running())
        stack.enter_context(redirect_stdout(io.StringIO()))
        stack.enter_context(redirect_stderr(errors))
        try:
            bootstrapper.run(settings, model_path, receipt, {})
            failure = None
        except importer.ImportError_ as error:
            failure = error
    calls = [(request.get_method(), request.full_url.removeprefix(MANAGER), json.loads(request.data) if request.data else None,
              request.get_header("Authorization")) for request in transport.requests]
    assert spare or not transport.responses, "The run stopped before the scripted manager answers were used"
    return calls, failure, errors.getvalue(), refresh, installed


def verify_bootstrap_recovery(model_path: Path, scratch: Path) -> None:
    native = {"backend_url": "https://database.tabula.test", "frontend_url": "https://app.tabula.test", "keycloak_url": "https://identity.tabula.test",
              "realm": "tabula", "admin_email": "alice@tabula.test", "database_name": "tabula-v2", "native_manager_url": "/native",
              "schema_job_timeout_seconds": None, "base_ready_timeout_seconds": None}
    busy, create, delete = (409, {"error": "busy"}), "/catalogue/createApiToken", "/catalogue/deleteApiToken"

    # The manager's short code is the only part of an error body that reaches a message.
    for body, code in [({"error": "busy"}, "busy"), ({"error": "token_not_found"}, "token_not_found"), ({"error": "Server Secret"}, "unknown"),
                       ({"error": "busy", "detail": "server secret"}, "unknown"), ({"error": "x" * 65}, "unknown"), (b"<html>server secret</html>", "unknown")]:
        with patch.object(bootstrapper.urllib.request, "build_opener", return_value=Opener([(409, body)])):
            try:
                bootstrapper.manager_request(MANAGER, "fixture-jwt", "deleteApiToken", {"tokenId": "fixture-token-id"})
            except bootstrapper.ManagerRefused as error:
                assert (error.status, error.code) == (409, code) and f"(HTTP 409, {code})" in str(error) and "ecret" not in str(error), str(error)
            else:
                raise AssertionError("Missing manager refusal")
    with patch.object(bootstrapper.urllib.request, "build_opener", return_value=Opener([urllib.error.URLError("fixture")])):
        try:
            bootstrapper.manager_request(MANAGER, "fixture-jwt", "deleteApiToken", {"tokenId": "fixture-token-id"})
        except bootstrapper.ManagerUnavailable as error:
            assert "URLError" in str(error)
        else:
            raise AssertionError("Missing transport failure")

    with tempfile.TemporaryDirectory(prefix="bootstrap-recovery-", dir=scratch) as temporary:
        directory = Path(temporary)

        # Busy at creation: retried with a growing pause and byte-identical arguments, since Tabula resumes only an identical command.
        clock = Clock()
        calls, failure, errors, _, installed = scripted_run(native, model_path, directory / "busy.json", [busy, busy, (200, {}), (200, {})], clock=clock)
        assert failure is None and errors == "" and installed.call_count == 1 and clock.pauses == [2.0, 3.0]
        assert [call[1] for call in calls] == [create, create, create, delete]
        assert json.dumps(calls[0][2]) == json.dumps(calls[1][2]) == json.dumps(calls[2][2]) and calls[0][2]["args"]["expiresAtMicros"] == "4600000000"
        assert list(calls[0][2]["args"]) == ["id", "name", "prefix", "hash", "readOnly", "baseIds", "expiresAtMicros"]
        assert calls[3][2] == {"args": {"tokenId": calls[0][2]["args"]["id"]}}

        # Busy for too long: a clean refusal, reported with its code, and no revocation of a token that never existed.
        clock = Clock()
        calls, failure, errors, _, installed = scripted_run(native, model_path, directory / "always-busy.json", [busy] * 99, clock=clock, spare=True)
        assert isinstance(failure, bootstrapper.ManagerRefused) and "(HTTP 409, busy)" in str(failure) and "Delete nothing" in str(failure)
        assert {call[1] for call in calls} == {create} and len({json.dumps(call[2]) for call in calls}) == 1
        assert 900 <= clock.now < 930 and max(clock.pauses) == 30 and errors == "" and installed.call_count == 0

        # No other code is retried, and a refusal never triggers a phantom revocation.
        for status, code in [(409, "conflict"), (409, "native_job_expired"), (403, "forbidden")]:
            calls, failure, errors, _, installed = scripted_run(native, model_path, directory / "refused.json", [(status, {"error": code})])
            assert [call[1] for call in calls] == [create] and f"(HTTP {status}, {code})" in str(failure)
            assert "revoked" not in errors and installed.call_count == 0

        # An unknown outcome does schedule a revocation; a token that turns out not to exist counts as revoked.
        for uncertain in [urllib.error.URLError("fixture"), (502, b"<html>gateway</html>")]:
            calls, failure, errors, _, _ = scripted_run(native, model_path, directory / "uncertain.json", [uncertain, (409, {"error": "token_not_found"})])
            assert [call[1] for call in calls] == [create, delete] and failure is not None and errors == ""
        # A revocation that meets a busy catalogue is retried briefly.
        clock = Clock()
        calls, failure, errors, _, _ = scripted_run(native, model_path, directory / "busy-revoke.json", [(200, {}), busy, busy, (200, {})], clock=clock)
        assert [call[1] for call in calls] == [create, delete, delete, delete] and failure is None and errors == "" and clock.pauses == [2.0, 4.0]
        clock = Clock()
        calls, failure, errors, _, _ = scripted_run(native, model_path, directory / "stuck-revoke.json", [(200, {})] + [busy] * 99, clock=clock, spare=True)
        assert failure is None and 60 <= clock.now < 75 and "could not be revoked; its maximum lifetime is 60 minutes" in errors

        # A publication that outlives the polling timeout keeps its token: revoking it would fail the queued job.
        receipt = directory / "pending.json"
        base_id = importer.ident("bas", "pending")
        noted = {"intent_id": PENDING_JOB["id"], "base_id": base_id, "step": "table", "title": "Fixture", "noted_at": "2000-01-01T00:00:00Z"}

        def pending_install(api, workspace, model, digest, path):
            importer.save(path, {"version": 1, "base_id": base_id, "pending_job": noted})
            raise importer.OperationPending(PENDING_JOB, api.root, "fixture-key")

        calls, failure, errors, _, _ = scripted_run(native, model_path, receipt, [(200, {})], install=pending_install)
        first_token = calls[0][2]["args"]["id"]
        assert [call[1] for call in calls] == [create] and isinstance(failure, importer.OperationPending)
        assert f"Temporary token {first_token} is kept" in errors and "The next run revokes it" in errors
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        assert saved["unrevoked_tokens"] == [first_token] and saved["pending_job"] == noted

        # Next run: wait for the base with the session alone, revoke the kept token, then work with a new one.
        runtime = "/bases/" + base_id + "/runtime"

        def state(status: str) -> tuple[int, dict]:
            return 200, {"baseId": base_id, "database": "tabula-native-" + base_id, "schemaVersion": 1, "status": status}

        clock = Clock()
        calls, failure, errors, refresh, installed = scripted_run(native, model_path, receipt, [
            state("updating"), (503, {"error": "native_server_unavailable"}), urllib.error.URLError("fixture"), (403, {"error": "forbidden"}),
            state("updating"), state("ready"), (200, {}), (200, {}), (200, {})], clock=clock)
        assert failure is None and errors == "" and installed.call_count == 1
        assert [call[:2] for call in calls] == [("GET", runtime)] * 6 + [("POST", delete), ("POST", create), ("POST", delete)]
        assert all(call[2] is None for call in calls[:6]) and calls[0][3] == "Bearer fixture-jwt" and calls[4][3] == "Bearer refreshed-jwt"
        assert calls[6][2] == {"args": {"tokenId": first_token}} and calls[7][2]["args"]["id"] not in {first_token, ""}
        assert calls[8][2] == {"args": {"tokenId": calls[7][2]["args"]["id"]}} and clock.pauses == [1.0, 1.5, 2.25, 3.375]
        assert "unrevoked_tokens" not in json.loads(receipt.read_text(encoding="utf-8")) and refresh.call_count == 2

        # A long wait renews the session; a base that stays in publication stops the run before any token exists.
        importer.save(receipt, {"version": 1, "base_id": base_id, "pending_job": noted})
        clock = Clock()
        calls, failure, errors, refresh, installed = scripted_run({**native, "base_ready_timeout_seconds": 300}, model_path, receipt, [state("updating")] * 99, clock=clock, spare=True)
        assert {call[:2] for call in calls} == {("GET", runtime)} and installed.call_count == 0 and 300 <= clock.now < 310 and refresh.call_count >= 4
        assert "still being published after 300 seconds" in str(failure) and "Delete nothing" in str(failure) and "[native-intents]" in str(failure)
        # A failed publication is not waited for: the message says where to look.
        calls, failure, errors, _, installed = scripted_run(native, model_path, receipt, [state("error")])
        assert len(calls) == 1 and installed.call_count == 0 and "error state" in str(failure) and "[native-intents]" in str(failure)
        for refused in [(404, {"error": "base_not_found"}), (200, {"baseId": base_id, "status": "archived"})]:
            calls, failure, _, _, _ = scripted_run({**native, "base_ready_timeout_seconds": 30}, model_path, receipt, [refused] * 99, spare=True)
            assert {call[1] for call in calls} == {runtime} and failure is not None
        # A recovery base is waited for as well, and bases that never went native do not block.
        calls, failure, _, _, _ = scripted_run({**native, "recovery_base_id": importer.ident("bas", "recovered")}, model_path, directory / "recovery.json",
                                               [(200, {"baseId": importer.ident("bas", "recovered"), "status": "legacy"}), (200, {}), (200, {})])
        assert [call[1] for call in calls] == ["/bases/" + importer.ident("bas", "recovered") + "/runtime", create, delete] and failure is None

        # The token outlives the longest poll by a margin, whatever the timeouts.
        calls, failure, _, _, _ = scripted_run({**native, "schema_job_timeout_seconds": 3600}, model_path, directory / "long.json", [(200, {}), (200, {})])
        assert failure is None and calls[0][2]["args"]["expiresAtMicros"] == str((1000 + 3600 + 300 + 900) * 1_000_000)
        calls, failure, _, _, _ = scripted_run({**native, "api_token_ttl_seconds": 1200}, model_path, directory / "short.json", [])
        assert calls == [] and "api_token_ttl_seconds must be between 2100 and 7200" in str(failure)
        calls, failure, _, _, _ = scripted_run({**native, "base_ready_timeout_seconds": 5}, model_path, directory / "invalid.json", [])
        assert calls == [] and "base_ready_timeout_seconds must be between 30 and 3600" in str(failure)
        (directory / "locked.json.lock").write_text("", encoding="utf-8")
        calls, failure, _, _, _ = scripted_run(native, model_path, directory / "locked.json", [])
        assert calls == [] and "Import lock exists" in str(failure)
    print("Bootstrap recovery checks passed: manager error codes, busy retried with identical arguments, no phantom revocation, "
          "kept token revoked by the next run, wait for a ready base, token lifetime above the polling timeout.")


if __name__ == "__main__":
    run()
