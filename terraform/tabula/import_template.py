#!/usr/bin/env python3
"""Install a versioned Tabula structure through its v3 API, without deleting user data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
FIELD_TYPES = {"SingleLineText", "LongText", "Email", "URL", "PhoneNumber", "Number", "Decimal", "Checkbox", "Date", "DateTime", "SingleSelect", "MultiSelect", "JSON", "Formula"}
OPERATORS = {"eq", "neq", "like", "nlike", "gt", "ge", "lt", "le", "blank", "notblank", "checked", "notchecked", "anyof", "nanyof", "allof", "nallof", "isWithin"}
DEFAULT_JOB_TIMEOUT = 1800  # seconds one schema publication is followed before the import stops
TOKEN_MARGIN = 300  # seconds a temporary token must outlive the job timeout: a queued publication fails when its token expires
LOGS_HINT = "read the native manager logs (lines starting with [native-intents])"


class ImportError_(Exception):
    pass


class ApiError(ImportError_):
    def __init__(self, status: int, message: str, code: str = ""):
        super().__init__(message)
        self.status, self.code = status, code


class OperationPending(ApiError):
    def __init__(self, job: dict, root: str, key: str):
        # Only the token that created a job may poll it, and that token lives in memory: a later run cannot resume the poll.
        super().__init__(408, f"Tabula publication {job['id']} is still running on the server after the polling timeout. Delete nothing: "
                              f"rerun the same targeted apply later, it resumes from the receipt. If it stays blocked, {LOGS_HINT}.")
        self.job, self.root, self.idempotency_key = job, root, key


def require(condition: object, message: str) -> None:
    if not condition:
        raise ImportError_(message)


def job_timeout(configured: object = None) -> int:
    """Seconds one schema publication may be followed: configuration first, then the environment, then the default."""
    value = configured if configured is not None else os.environ.get("TABULA_SCHEMA_JOB_TIMEOUT_SECONDS") or DEFAULT_JOB_TIMEOUT
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value):
        value = int(value)
    require(isinstance(value, int) and not isinstance(value, bool) and 30 <= value <= 3600, "The schema job timeout must be a whole number of seconds between 30 and 3600")
    return value


def ident(prefix: str, value: str) -> str:
    raw = hashlib.sha256(value.encode("utf-8")).digest()
    return prefix + "".join(ALPHABET[b % 32] for b in raw[:14])


def names_unique(items: list[dict], label: str) -> None:
    keys, titles = set(), set()
    for item in items:
        key, title = item.get("key"), item.get("title")
        require(isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]*", key), f"{label}: invalid key")
        require(isinstance(title, str) and 0 < len(title.strip()) <= 100, f"{label} {key}: invalid title")
        require(key not in keys and title.casefold() not in titles, f"{label}: duplicate key or title: {title}")
        keys.add(key)
        titles.add(title.casefold())


def validate(model: dict) -> None:
    require(model.get("version") == 1, "Unsupported model version")
    require(isinstance(model.get("key"), str) and re.fullmatch(r"[a-z][a-z0-9_-]*", model["key"]), "Invalid model key")
    require(isinstance(model.get("title"), str) and 0 < len(model["title"].strip()) <= 100, "Invalid base title")
    tables = model.get("tables")
    require(isinstance(tables, list) and 0 < len(tables) <= 200, "The model must contain tables")
    require(all(isinstance(x, dict) for x in tables), "Invalid table")
    names_unique(tables, "tables")
    fields: dict[str, dict[str, dict]] = {}
    titles: dict[str, set[str]] = {}
    for table in tables:
        items = table.get("fields")
        require(isinstance(items, list) and items and all(isinstance(x, dict) for x in items), f"{table['key']}: fields required")
        names_unique(items, f"fields in {table['key']}")
        require(items[0].get("type") == "SingleLineText", f"{table['key']}: first field must be SingleLineText")
        for item in items:
            require(item.get("type") in FIELD_TYPES, f"Unsupported field type in {table['key']}.{item['key']}")
            require(isinstance(item.get("options", {}), dict), f"Invalid options: {item['key']}")
            if item.get("type") in {"SingleSelect", "MultiSelect"}:
                choices = item.get("choices")
                require(isinstance(choices, list) and choices and all(isinstance(c, str) and 0 < len(c.strip()) <= 100 for c in choices), f"Invalid choices: {item['key']}")
                require(len({c.casefold() for c in choices}) == len(choices), f"Duplicate choice: {item['key']}")
        fields[table["key"]] = {x["key"]: x for x in items}
        titles[table["key"]] = {x["title"].casefold() for x in items}

    def add(table: str, item: dict) -> None:
        require(table in fields, f"Unknown table: {table}")
        names_unique([item], f"field in {table}")
        require(item["key"] not in fields[table] and item["title"].casefold() not in titles[table], f"Duplicate field in {table}: {item['title']}")
        fields[table][item["key"]] = item
        titles[table].add(item["title"].casefold())

    relations = model.get("relations", [])
    require(isinstance(relations, list), "relations must be a list")
    for relation in relations:
        require(isinstance(relation, dict), "Invalid relation")
        source, target = relation.get("table"), relation.get("target")
        require(target in fields and source in fields and source != target, "Invalid relation target")
        require(isinstance(relation.get("allow_multiple", True), bool), "Invalid relation cardinality")
        add(source, {**relation, "type": "Links"})
        add(target, {"key": relation.get("inverse_key"), "title": relation.get("inverse_title"), "type": "Links", "target": source})

    computed = model.get("computed", [])
    require(isinstance(computed, list), "computed must be a list")
    for item in computed:
        require(isinstance(item, dict) and item.get("type") in {"Rollup", "Formula"}, "Unsupported computed field")
        table = item.get("table")
        require(table in fields, "Unknown computed-field table")
        if item["type"] == "Rollup":
            link = fields[table].get(item.get("link"))
            require(link is not None and link.get("type") == "Links", f"Invalid rollup relation in {table}")
            target = link.get("target")
            require(target in fields and item.get("target_field") in fields[target], f"Invalid rollup target in {table}")
            require(item.get("function") in {"count", "countDistinct", "sum", "avg", "min", "max"}, "Invalid rollup function")
        else:
            options = item.get("options")
            require(isinstance(options, dict) and isinstance(options.get("formula"), str) and options["formula"], "A calculated formula is required")
        add(table, item)

    def check_filter(node: dict, table: str, depth: int = 1) -> None:
        require(isinstance(node, dict) and depth <= 3, "Invalid filter nesting")
        groups = [k for k in ("all", "any") if k in node]
        if groups:
            require(len(groups) == 1 and isinstance(node[groups[0]], list) and node[groups[0]], "Empty or ambiguous filter group")
            for child in node[groups[0]]:
                check_filter(child, table, depth + 1)
            return
        field = fields[table].get(node.get("field"))
        require(field is not None and node.get("op") in OPERATORS, f"Invalid filter in {table}")
        if "choice" in node or "choices" in node:
            wanted = [node["choice"]] if "choice" in node else node["choices"]
            require(isinstance(wanted, list) and all(c in field.get("choices", []) for c in wanted), f"Unknown filter choice in {table}")

    views = model.get("views", [])
    require(isinstance(views, list) and all(isinstance(v, dict) for v in views), "Invalid views")
    for table in tables:
        tk = table["key"]
        own = [v for v in views if v.get("table") == tk]
        names_unique(own, f"views in {tk}")
        require(sum(v.get("default") is True for v in own) == 1, f"{tk}: exactly one default view required")
        for view in own:
            require(view.get("type") in {"grid", "kanban", "calendar"}, f"Unsupported view type in {tk}")
            require(not view.get("default") or view["type"] == "grid", f"{tk}: default view must be grid")
            require(all(k in fields[tk] for k in view.get("fields", [])), f"Unknown view field in {tk}")
            for sort in view.get("sorts", []):
                require(sort.get("field") in fields[tk] and sort.get("direction") in {"asc", "desc"}, f"Invalid sort in {tk}")
            if view["type"] == "kanban":
                require(fields[tk].get(view.get("stack"), {}).get("type") == "SingleSelect", f"Invalid kanban stack in {tk}")
            if view["type"] == "calendar":
                require(fields[tk].get(view.get("date"), {}).get("type") in {"Date", "DateTime"}, f"Invalid calendar date in {tk}")
            if "filter" in view:
                check_filter(view["filter"], tk)
    require(all(v.get("table") in fields for v in views), "A view references an unknown table")
    seeds = model.get("seeds", [])
    require(isinstance(seeds, list), "Invalid seeds")
    seed_keys: set[tuple[str, str]] = set()
    for seed in seeds:
        table, key, values = seed.get("table"), seed.get("key"), seed.get("values")
        require(table in fields and isinstance(key, str) and key and isinstance(values, dict), "Invalid seed")
        require((table, key) not in seed_keys and all(k in fields[table] for k in values), "Duplicate seed or unknown field")
        seed_keys.add((table, key))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class Api:
    def __init__(self, root: str, token: str, request_timeout: float | None = None, poll_interval: float = 0.5, max_poll_interval: float = 5):
        parsed = urllib.parse.urlsplit(root)
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        require(parsed.scheme == "https" or (parsed.scheme == "http" and local), "HTTPS is required except on loopback")
        require(parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment, "Invalid API URL")
        require(re.fullmatch(r"tb_[A-Za-z0-9_-]{43}", token) is not None, "TABULA_API_TOKEN is missing or invalid")
        self.root = root.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())
        request_timeout = job_timeout() if request_timeout is None else request_timeout
        require(0 < request_timeout <= 3600 and 0 < poll_interval <= max_poll_interval <= 30, "Invalid API timeout or poll interval")
        self.request_timeout, self.poll_interval, self.max_poll_interval = request_timeout, poll_interval, max_poll_interval
        self.expires_at: float | None = None  # Unix time after which the token can no longer follow its own jobs
        self.on_wait = None  # called before each polling pause, for instance to keep an operator session alive

    def _remaining(self, deadline: float) -> float:
        left = deadline - time.monotonic()
        return left if self.expires_at is None else min(left, self.expires_at - time.time())

    def _exchange(self, method: str, url: str, headers: dict, data: bytes | None, timeout: float) -> tuple[int, dict]:
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            try:
                response = self.opener.open(request, timeout=max(0.001, min(30, timeout)))
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status, raw = response.code, response.read(8 * 1024 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ImportError_(f"Tabula API unavailable during {method}: {type(error).__name__}") from None
        require(len(raw) <= 8 * 1024 * 1024, "Tabula API response exceeded its size limit")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            if status >= 400:
                return status, {}
            raise ImportError_("Unexpected Tabula API response") from None
        require(isinstance(result, dict), "Unexpected Tabula API response")
        return status, result

    @staticmethod
    def _native_root(root: str, details: object) -> str | None:
        if not isinstance(details, dict):
            return None
        base_id = details.get("baseId")
        if not isinstance(base_id, str) or not re.fullmatch(r"bas[0-9a-hjkmnp-tv-z]{14}", base_id):
            return None
        database = "tabula-native-" + base_id
        if details.get("database") != database:
            return None
        parsed = urllib.parse.urlsplit(root)
        path = re.fullmatch(r"(.*\/v1\/database\/)[A-Za-z0-9_-]+(\/route\/api\/v3)", parsed.path)
        if not path:
            return None
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path[1] + database + path[2], "", ""))

    @staticmethod
    def _pending_job(result: dict, previous: dict | None) -> dict:
        job = result.get("job")
        require(isinstance(job, dict), "Invalid schema job response")
        job_id = job.get("id")
        require(isinstance(job_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id), "Invalid schema job identifier")
        require(job.get("status") in {"queued", "prepared"}
                and job.get("pollPath") == "/meta/schema-jobs?intentId=" + urllib.parse.quote(job_id, safe=""), "Invalid schema job pollPath")
        require(previous is None or previous["id"] == job_id, "Schema job changed during polling")
        return {"id": job_id, "status": job["status"], "pollPath": job["pollPath"]}

    def request(self, method: str, path: str, params: dict[str, str] | None = None, body: dict | None = None, missing_ok: bool = False) -> dict | None:
        require(path.startswith("/") and not path.startswith("//") and not any(c in path for c in "?#\\"), "Invalid API path")
        target = path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json", "Accept": "application/json"}
        key = str(uuid.uuid4()) if method != "GET" else ""
        if key:
            headers["Idempotency-Key"] = key
        deadline = time.monotonic() + self.request_timeout
        active_root = self.root
        status, result = self._exchange(method, active_root + target, headers, data, self.request_timeout)
        if status == 409 and result.get("error") == "ERR_NATIVE_ROUTE_REQUIRED":
            routed = self._native_root(self.root, result.get("details"))
            if routed and routed != self.root:
                active_root = routed
                # This precise response guarantees the mutation has not started; no other failure replays it.
                status, result = self._exchange(method, active_root + target, headers, data, deadline - time.monotonic())
        pending = None
        pause = self.poll_interval
        while status == 202:
            pending = self._pending_job(result, pending)
            remaining = self._remaining(deadline)
            if remaining <= 0:
                raise OperationPending(pending, active_root, key)
            if self.on_wait is not None:
                self.on_wait()
            time.sleep(min(pause, remaining))
            pause = min(pause * 1.5, self.max_poll_interval)
            remaining = self._remaining(deadline)
            if remaining <= 0:
                raise OperationPending(pending, active_root, key)
            status, result = self._exchange("GET", active_root + pending["pollPath"],
                                            {"Authorization": headers["Authorization"], "Accept": "application/json"}, None, remaining)
        if missing_ok and status == 404:
            return None
        if not 200 <= status < 300:
            code = result.get("error", "error")
            if not isinstance(code, str) or re.fullmatch(r"[A-Za-z0-9_]{1,80}", code) is None:
                code = "error"
            raise ApiError(status, f"Tabula API: HTTP {status} ({code}) on {method} {path}", code)
        return result


def retry_busy(api: Api, call, waiting_for: str):
    """Repeat one read while Tabula answers ERR_BUSY: a base being published refuses reads. A mutation is never repeated here."""
    deadline = time.monotonic() + api.request_timeout
    pause = api.poll_interval
    while True:
        try:
            return call()
        except ApiError as error:
            if error.code != "ERR_BUSY":
                raise
            remaining = deadline - time.monotonic()
            require(remaining > 0, f"The base is still busy with {waiting_for} after {api.request_timeout} seconds. Delete nothing: "
                                   f"rerun the same targeted apply later. If it stays blocked, {LOGS_HINT}.")
        time.sleep(min(pause, remaining))
        pause = min(pause * 1.5, api.max_poll_interval)


def save(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def by_title(items: list[dict], title: str) -> dict | None:
    found = [item for item in items if item.get("title", "").casefold() == title.casefold()]
    require(len(found) <= 1, f"Ambiguous existing title: {title}")
    return found[0] if found else None


def api_field(field: dict) -> dict:
    result = {"title": field["title"], "type": field["type"]}
    if field.get("description"):
        result["description"] = field["description"]
    if field.get("options") is not None:
        result["options"] = field["options"]
    if "choices" in field:
        result["options"] = {**result.get("options", {}), "choices": [{"title": c} for c in field["choices"]]}
    return result


def find_field(items: list[dict], spec: dict) -> dict | None:
    field = by_title(items, spec["title"])
    if field:
        require(field.get("type") == spec["type"], f"Existing field has a different type: {spec['title']}")
        if "choices" in spec:
            actual = [c.get("title") for c in field.get("options", {}).get("choices", [])]
            require(actual == spec["choices"], f"Existing choices differ: {spec['title']}")
    return field


def view_config(spec: dict, all_fields: list[dict], mapped: dict[str, dict]) -> dict:
    def field(key: str) -> dict:
        require(key in mapped, f"Missing field reference: {key}")
        return mapped[key]

    def choice(key: str, name: str) -> str:
        matches = [c["id"] for c in field(key).get("options", {}).get("choices", []) if c.get("title") == name]
        require(len(matches) == 1, f"Missing choice: {key} / {name}")
        return matches[0]

    def tree(node: dict, path: str) -> dict:
        groups = [k for k in ("all", "any") if k in node]
        if groups:
            group = groups[0]
            return {"id": ident("flt", path), "kind": "group", "conjunction": "and" if group == "all" else "or", "children": [tree(child, f"{path}/{i}") for i, child in enumerate(node[group])]}
        key = node["field"]
        result = {"id": ident("flt", path), "kind": "condition", "fieldId": field(key)["id"], "operator": node["op"]}
        if "choice" in node:
            result["value"] = choice(key, node["choice"])
        elif "choices" in node:
            result["value"] = [choice(key, c) for c in node["choices"]]
        elif "value" in node:
            result["value"] = node["value"]
        return result

    config: dict = {"type": spec["type"]}
    if "filter" in spec:
        config["filters"] = tree(spec["filter"], spec["table"] + "/" + spec["key"])
        config["filters"]["id"] = "flt00000000000000"
    if "sorts" in spec:
        config["sorts"] = [{"fieldId": field(s["field"])["id"], "direction": s["direction"]} for s in spec["sorts"]]
    selected = spec.get("fields")
    if selected is None:
        config["fields"] = [{"fieldId": f["id"], "visible": True} for f in all_fields]
    else:
        selected_ids = [field(key)["id"] for key in selected]
        config["fields"] = [{"fieldId": fid, "visible": True} for fid in selected_ids] + [{"fieldId": f["id"], "visible": False} for f in all_fields if f["id"] not in selected_ids]
    if spec["type"] == "kanban":
        config["stackFieldId"] = field(spec["stack"])["id"]
    if spec["type"] == "calendar":
        config["dateFieldId"] = field(spec["date"])["id"]
    return config


def config_matches(actual: dict, expected: dict) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def install(api: Api, workspace: str, model: dict, digest: str, receipt_path: Path) -> None:
    require(re.fullmatch(r"wsp[0-9a-hjkmnp-tv-z]{14}", workspace) is not None, "Invalid workspace id")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    lock = receipt_path.with_suffix(receipt_path.suffix + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ImportError_(f"Import lock exists: {lock}. If no import is running, remove it before retrying.") from None
    os.close(fd)
    try:
        _install(api, workspace, model, digest, receipt_path)
    finally:
        lock.unlink(missing_ok=True)


def _install(api: Api, workspace: str, model: dict, digest: str, receipt_path: Path) -> None:
    scope = hashlib.sha256((api.root + "\n" + workspace).encode()).hexdigest()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {"version": 1, "scope": scope, "workspace_id": workspace, "model": model["key"], "digest": digest}
    require(receipt.get("version") == 1 and receipt.get("scope") == scope and receipt.get("model") == model["key"], "Receipt belongs to another installation")
    require(receipt.get("digest") == digest, "The model changed after this installation started; use a new installation_id to create another base")
    require(receipt.get("pending_job") is None or isinstance(receipt["pending_job"], dict), "Invalid pending job in receipt")

    def mutate(step: str, title: str, method: str, path: str, params: dict, body: dict) -> dict:
        try:
            return api.request(method, path, params, body) or {}
        except OperationPending as error:
            # The publication goes on without this process: the next run needs to know what it is waiting for.
            receipt["pending_job"] = {"intent_id": error.job["id"], "base_id": receipt.get("base_id"), "step": step, "title": title,
                                      "noted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            save(receipt_path, receipt)
            raise

    def seen(step: str, title: str) -> None:
        noted = receipt.get("pending_job")
        if noted and noted.get("step") == step and noted.get("title") == title:
            del receipt["pending_job"]
            save(receipt_path, receipt)

    api.request("GET", "/meta/workspaces", {"workspaceId": workspace})
    base_id = receipt.get("base_id")
    if receipt.get("complete"):
        require(base_id, "Completed receipt has no base id")
        api.request("GET", "/meta/bases", {"baseId": base_id})
        print(f"Base already installed: {base_id}; existing content was left untouched.")
        return
    if not base_id and receipt.get("base_creation_started"):
        recovered = os.environ.get("TABULA_RECOVER_BASE_ID", "")
        require(re.fullmatch(r"bas[0-9a-hjkmnp-tv-z]{14}", recovered) is not None, "Base creation outcome is unknown. Check Tabula, then set TABULA_RECOVER_BASE_ID to the base created by this import before retrying.")
        base = api.request("GET", "/meta/bases", {"baseId": recovered}) or {}
        require(base.get("workspace_id") == workspace and base.get("title") == model["title"], "Recovery base does not match workspace and title")
        base_id = receipt["base_id"] = recovered
        save(receipt_path, receipt)
    if not base_id:
        bases = api.request("GET", "/meta/bases", {"workspaceId": workspace}) or {}
        require(by_title(bases.get("list", []), model["title"]) is None, "A base with this title already exists. It will not be adopted or modified; choose another title.")
        receipt["base_creation_started"] = True
        save(receipt_path, receipt)
        try:
            base = mutate("base", model["title"], "POST", "/meta/bases", {"workspaceId": workspace}, {"title": model["title"]})
        except ApiError as error:
            if 400 <= error.status < 500 and error.status not in {408, 429}:
                receipt["base_creation_started"] = False
                save(receipt_path, receipt)
            raise
        base_id = receipt["base_id"] = base["id"]
        save(receipt_path, receipt)
        print(f"Created base: {base_id}")
    seen("base", model["title"])

    def read_tables() -> list[dict]:
        return (api.request("GET", "/meta/tables", {"baseId": base_id}) or {}).get("list", [])

    noted = receipt.get("pending_job")
    table_rows = retry_busy(api, read_tables, f"publication {noted.get('intent_id')}") if noted else read_tables()
    table_ids: dict[str, str] = {}
    for spec in model["tables"]:
        row = by_title(table_rows, spec["title"])
        if row is None:
            body = {"title": spec["title"], "description": spec.get("description", ""), "fields": [api_field(f) for f in spec["fields"]]}
            row = mutate("table", spec["title"], "POST", "/meta/tables", {"baseId": base_id}, body)
            table_rows.append(row)
            print(f"Created table: {spec['title']}")
        seen("table", spec["title"])
        table_ids[spec["key"]] = row["id"]
        current = (api.request("GET", "/meta/fields", {"tableId": row["id"]}) or {}).get("list", [])
        for spec_field in spec["fields"]:
            require(find_field(current, spec_field) is not None, f"Existing table is incomplete: {spec['title']} / {spec_field['title']}")

    def current_fields(table: str) -> list[dict]:
        return (api.request("GET", "/meta/fields", {"tableId": table_ids[table]}) or {}).get("list", [])

    mapped: dict[str, dict[str, dict]] = {}
    for table in model["tables"]:
        rows = current_fields(table["key"])
        mapped[table["key"]] = {f["key"]: find_field(rows, f) for f in table["fields"]}
    for relation in model.get("relations", []):
        source, target = relation["table"], relation["target"]
        spec = {**relation, "type": "Links"}
        row = find_field(current_fields(source), spec)
        if row is None:
            row = mutate("relation", f"{source} / {relation['title']}", "POST", "/meta/fields", {"tableId": table_ids[source]}, {"title": relation["title"], "type": "Links", "options": {"related_table_id": table_ids[target], "allow_multiple": relation.get("allow_multiple", True)}})
            print(f"Created relation: {source} / {relation['title']}")
        seen("relation", f"{source} / {relation['title']}")
        options = row.get("options", {})
        require(options.get("related_table_id") == table_ids[target] and options.get("allow_multiple") == relation.get("allow_multiple", True), f"Relation differs: {relation['title']}")
        inverse_id = options.get("inverse_field_id")
        inverse = next((f for f in current_fields(target) if f.get("id") == inverse_id), None)
        require(inverse is not None, f"Missing inverse relation: {relation['title']}")
        if inverse["title"] != relation["inverse_title"]:
            require(by_title(current_fields(target), relation["inverse_title"]) is None, f"Inverse relation title already used: {relation['inverse_title']}")
            inverse = mutate("inverse relation", f"{target} / {relation['inverse_title']}", "PATCH", "/meta/fields", {"fieldId": inverse_id}, {"title": relation["inverse_title"]})
        seen("inverse relation", f"{target} / {relation['inverse_title']}")
        mapped[source][relation["key"]] = row
        mapped[target][relation["inverse_key"]] = inverse

    for spec in model.get("computed", []):
        table = spec["table"]
        if spec["type"] == "Rollup":
            link = mapped[table][spec["link"]]
            target_table_id = link["options"]["related_table_id"]
            target_table = next(k for k, v in table_ids.items() if v == target_table_id)
            options = {"related_field_id": link["id"], "related_table_rollup_field_id": mapped[target_table][spec["target_field"]]["id"], "rollup_function": spec["function"], "precision": 0}
        else:
            options = spec["options"]
        row = find_field(current_fields(table), spec)
        if row is None:
            row = mutate("calculated field", f"{table} / {spec['title']}", "POST", "/meta/fields", {"tableId": table_ids[table]}, {"title": spec["title"], "type": spec["type"], "options": options})
            print(f"Created calculated field: {table} / {spec['title']}")
        seen("calculated field", f"{table} / {spec['title']}")
        require(all(row.get("options", {}).get(k) == v for k, v in options.items()), f"Calculated field differs: {spec['title']}")
        mapped[table][spec["key"]] = row

    for spec in model["views"]:
        table = spec["table"]
        views = (api.request("GET", "/meta/views", {"tableId": table_ids[table]}) or {}).get("list", [])
        config = view_config(spec, current_fields(table), mapped[table])
        row = by_title(views, spec["title"])
        if row is None and spec.get("default"):
            row = by_title(views, "Grid view")
            require(row is not None and row.get("type") == "grid", f"Default view missing for {table}")
            row = mutate("view", f"{table} / {spec['title']}", "PATCH", "/meta/views", {"viewId": row["id"]}, {"title": spec["title"], "config": config})
            print(f"Configured view: {spec['title']}")
        elif row is None:
            row = mutate("view", f"{table} / {spec['title']}", "POST", "/meta/views", {"tableId": table_ids[table]}, {"title": spec["title"], "type": spec["type"], "config": config})
            print(f"Created view: {spec['title']}")
        seen("view", f"{table} / {spec['title']}")
        require(row.get("type") == spec["type"] and config_matches(row.get("config", {}), config), f"Existing view differs: {spec['title']}")

    for seed in model.get("seeds", []):
        table = seed["table"]
        record_id = ident("rec", base_id + "/" + table + "/" + seed["key"])
        found = api.request("GET", "/data/records", {"tableId": table_ids[table], "recordId": record_id}, missing_ok=True)
        if found is None:
            values = {mapped[table][key]["id"]: value for key, value in seed["values"].items()}
            mutate("seed", f"{table} / {seed['key']}", "POST", "/data/records", {"tableId": table_ids[table]}, {"id": record_id, "fields": values})
    receipt.pop("pending_job", None)
    receipt["complete"] = True
    save(receipt_path, receipt)
    print(f"Import complete: {base_id}")


def main() -> None:
    model_path = Path(os.environ.get("TABULA_MODEL_FILE", Path(__file__).parent / "templates" / "prospection.json"))
    raw = model_path.read_bytes()
    model = json.loads(raw)
    require(isinstance(model, dict), "Model must be a JSON object")
    validate(model)
    if "--check" in sys.argv[1:]:
        print(f"Valid model: {model['title']} ({len(model['tables'])} tables, {len(model.get('relations', []))} relations, {len(model['views'])} views)")
        return
    require(len(sys.argv) == 1, "Usage: import_template.py [--check]")
    api = Api(os.environ.get("TABULA_API_BASE_URL", ""), os.environ.get("TABULA_API_TOKEN", ""))
    workspace = os.environ.get("TABULA_WORKSPACE_ID", "")
    receipt = os.environ.get("TABULA_IMPORT_RECEIPT", "")
    require(receipt != "", "TABULA_IMPORT_RECEIPT is required")
    install(api, workspace, model, hashlib.sha256(raw).hexdigest(), Path(receipt))


if __name__ == "__main__":
    try:
        main()
    except (ImportError_, KeyError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Import refused: {error}", file=sys.stderr)
        sys.exit(1)
