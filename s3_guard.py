"""Read-only checks; never fetch an object body or log credentials."""
import datetime
import hashlib
import hmac
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class IsolationError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        response.close()
        raise IsolationError("redirection S3 refusée")


class S3Probe:
    def __init__(self, host, region, access, secret):
        self.host, self.region, self.access, self.secret = host, region, access, secret

    def request(self, method, bucket, key="", query=None):
        # GET is reserved for bucket configuration; object content is never read.
        if method not in {"HEAD", "GET"} or (method == "GET" and (key or query not in ({"acl": ""}, {"policy": ""}))):
            raise IsolationError("opération de diagnostic S3 interdite")
        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = urllib.parse.quote("/" + bucket + ("/" + key if key else ""), safe="/-_.~")
        query_string = urllib.parse.urlencode(sorted((query or {}).items()), quote_via=urllib.parse.quote)
        payload = hashlib.sha256(b"").hexdigest()
        headers = f"host:{self.host}\nx-amz-content-sha256:{payload}\nx-amz-date:{amz_date}\n"
        signed = "host;x-amz-content-sha256;x-amz-date"
        canonical = "\n".join([method, path, query_string, headers, signed, payload])
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])

        def mac(key, value):
            return hmac.new(key, value.encode(), hashlib.sha256).digest()

        signing_key = mac(mac(mac(mac(("AWS4" + self.secret).encode(), day), self.region), "s3"), "aws4_request")
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        request = urllib.request.Request("https://" + self.host + path + ("?" + query_string if query_string else ""), method=method, headers={
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={self.access}/{scope}, SignedHeaders={signed}, Signature={signature}",
        })
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
                body = response.read(1024 * 1024 + 1) if method == "GET" else b""
                if len(body) > 1024 * 1024:
                    raise IsolationError("configuration S3 trop volumineuse")
                return response.status, body
        except urllib.error.HTTPError as error:
            error.close()
            return error.code, b""
        except (OSError, urllib.error.URLError):
            raise IsolationError("diagnostic S3 indisponible ; aucun accès validé") from None


def _values(value):
    return value if isinstance(value, list) else [value]


def _exact_deny(policy, principal, bucket):
    if not isinstance(policy, dict):
        return False
    resources = {f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"}
    statements = policy.get("Statement", [])
    return any(
        isinstance(statement, dict)
        and statement.get("Effect") == "Deny"
        and "s3:*" in _values(statement.get("Action"))
        and resources.issubset(set(_values(statement.get("Resource"))))
        and isinstance(statement.get("Principal"), dict)
        and principal in _values(statement["Principal"].get("AWS"))
        and not any(name in statement for name in ("Condition", "NotAction", "NotPrincipal", "NotResource"))
        for statement in _values(statements)
    )


def verify_isolation(store, host, region, state_bucket, application_bucket, factory=S3Probe):
    required = ("DRIVE_S3_ACCESS_KEY_ID", "DRIVE_S3_SECRET_ACCESS_KEY", "TFSTATE_ACCESS_KEY_ID", "TFSTATE_SECRET_ACCESS_KEY")
    if any(not store.get(key) for key in required):
        raise IsolationError("identifiants S3 applicatifs ou Terraform manquants")
    if store[required[0]] == store[required[2]]:
        raise IsolationError("clés applicatives et Terraform identiques : isolation impossible")
    if application_bucket == state_bucket:
        raise IsolationError("le bucket applicatif ne peut pas être celui de l'état Terraform")
    app = factory(host, region, store[required[0]], store[required[1]])
    state = factory(host, region, store[required[2]], store[required[3]])
    if state.request("HEAD", state_bucket, "infra/terraform.tfstate")[0] != 200:
        raise IsolationError("accès Terraform à son état non confirmé")
    if app.request("HEAD", application_bucket)[0] != 200:
        raise IsolationError("accès au bucket applicatif existant non confirmé")
    acl_status, acl_body = app.request("GET", application_bucket, query={"acl": ""})
    policy_status, policy_body = state.request("GET", state_bucket, query={"policy": ""})
    try:
        acl = ET.fromstring(acl_body)
        owner_element = next(element for element in acl.iter() if element.tag.split("}")[-1] == "Owner")
        owner = next(element.text for element in owner_element if element.tag.split("}")[-1] == "ID")
        if acl_status != 200 or not isinstance(owner, str) or not re.fullmatch(r"p[0-9]+", owner):
            raise ValueError()
        policy = json.loads(policy_body) if policy_status == 200 else {}
        principal = "arn:aws:iam:::user/" + owner + ":" + store[required[0]]
        if not _exact_deny(policy, principal, state_bucket):
            raise ValueError()
    except (ValueError, TypeError, KeyError, StopIteration, ET.ParseError):
        raise IsolationError("policy Terraform : refus complet explicite de la clé applicative non confirmé") from None
    for key in ("", "infra/terraform.tfstate"):
        if app.request("HEAD", state_bucket, key)[0] != 403:
            raise IsolationError("la clé applicative n'est pas refusée par le bucket d'état (403 attendu)")
