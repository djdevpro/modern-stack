import json
import io
import unittest
import urllib.request
import urllib.response
from email.message import Message
from unittest.mock import patch
from s3_guard import IsolationError, NoRedirect, S3Probe, verify_isolation

BUCKET = "state-test"
APP = "application-test"
STORE = {
    "DRIVE_S3_ACCESS_KEY_ID": "app-test-key",
    "DRIVE_S3_SECRET_ACCESS_KEY": "app-test-secret",
    "TFSTATE_ACCESS_KEY_ID": "state-test-key",
    "TFSTATE_SECRET_ACCESS_KEY": "state-test-secret",
}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.policy = {"Version": "2012-10-17", "Statement": [{
            "Sid": "DenyApplicationKeyOnTerraformState", "Effect": "Deny", "Action": "s3:*",
            "Resource": [f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/*"],
            "Principal": {"AWS": "arn:aws:iam:::user/p123:app-test-key"},
        }]}
        self.overrides = {}

    def factory(self, host, region, access, secret):
        owner = self

        class Probe:
            def request(self, method, bucket, key="", query=None):
                call = (access, method, bucket, key, tuple((query or {}).items()))
                owner.calls.append(call)
                if call in owner.overrides:
                    return owner.overrides[call]
                if query == {"acl": ""}:
                    return 200, b'<AccessControlPolicy xmlns="urn:test"><Owner><ID>p123</ID></Owner></AccessControlPolicy>'
                if query == {"policy": ""}:
                    return 200, json.dumps(owner.policy).encode()
                if bucket == BUCKET and access == STORE["DRIVE_S3_ACCESS_KEY_ID"]:
                    return 403, b""
                return 200, b""

        return Probe()

    def verify(self, store=None):
        verify_isolation(store or STORE, "s3.tabula.test", "test", BUCKET, APP, self.factory)

    def test_accepts_exact_policy_and_preserved_state_application_access(self):
        self.verify()
        self.assertTrue(self.calls)
        self.assertFalse(any(method == "GET" and key for _, method, _, key, _ in self.calls))

    def test_same_key_refused_before_network(self):
        store = dict(STORE, DRIVE_S3_ACCESS_KEY_ID=STORE["TFSTATE_ACCESS_KEY_ID"])
        with self.assertRaises(IsolationError): self.verify(store)
        self.assertEqual(self.calls, [])

    def test_missing_credentials_refused_before_network(self):
        store = dict(STORE, DRIVE_S3_SECRET_ACCESS_KEY="")
        with self.assertRaises(IsolationError): self.verify(store)
        self.assertEqual(self.calls, [])

    def test_missing_policy_refused_even_if_heads_forbidden(self):
        self.policy = {}
        with self.assertRaises(IsolationError): self.verify()

    def test_wrong_key_in_policy_refused(self):
        self.policy["Statement"][0]["Principal"]["AWS"] = "arn:aws:iam:::user/p123:someone-else"
        with self.assertRaises(IsolationError): self.verify()

    def test_conditional_or_unsupported_deny_refused(self):
        for field in ["Condition", "NotAction", "NotPrincipal", "NotResource"]:
            with self.subTest(field=field):
                self.policy["Statement"][0][field] = {}
                with self.assertRaises(IsolationError): self.verify()
                del self.policy["Statement"][0][field]

    def test_partial_read_deny_refused(self):
        self.policy["Statement"][0]["Action"] = "s3:GetObject"
        with self.assertRaises(IsolationError): self.verify()

    def test_missing_object_resource_refused(self):
        self.policy["Statement"][0]["Resource"] = [f"arn:aws:s3:::{BUCKET}"]
        with self.assertRaises(IsolationError): self.verify()

    def test_terraform_auth_failure_is_not_isolation(self):
        self.overrides[("state-test-key", "HEAD", BUCKET, "infra/terraform.tfstate", ())] = (403, b"")
        with self.assertRaises(IsolationError): self.verify()

    def test_application_access_lost_refused(self):
        self.overrides[("app-test-key", "HEAD", APP, "", ())] = (403, b"")
        with self.assertRaises(IsolationError): self.verify()

    def test_state_still_accessible_or_missing_refused(self):
        for status in (200, 404, 500):
            with self.subTest(status=status):
                self.overrides[("app-test-key", "HEAD", BUCKET, "infra/terraform.tfstate", ())] = (status, b"")
                with self.assertRaises(IsolationError): self.verify()

    def test_probe_cannot_read_object_body_or_mutate(self):
        probe = S3Probe("s3.tabula.test", "test", "test", "test")
        for method, key, query in [("GET", "infra/terraform.tfstate", None), ("PUT", "", {"policy": ""}), ("DELETE", "object", None), ("GET", "", {})]:
            with self.subTest(method=method, key=key):
                with self.assertRaises(IsolationError): probe.request(method, BUCKET, key, query)

    def test_probe_never_follows_redirect_or_forwards_authorization(self):
        for status in (301, 302, 303, 307, 308):
            for destination in ("https://other.tabula.test/collect", "http://s3.tabula.test/downgrade", "https://s3.tabula.test/other"):
                with self.subTest(status=status, destination=destination):
                    requests = []

                    class RedirectTransport(urllib.request.HTTPSHandler):
                        def https_open(self, request):
                            requests.append(request)
                            headers = Message()
                            headers["Location"] = destination
                            response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, status)
                            response.msg = "Redirect"
                            return response

                    opener = urllib.request.build_opener(NoRedirect(), RedirectTransport())
                    with patch("s3_guard.urllib.request.build_opener", return_value=opener):
                        with self.assertRaisesRegex(IsolationError, "redirection S3 refusée"):
                            S3Probe("s3.tabula.test", "test", "fixture-key", "fixture-secret").request("HEAD", BUCKET)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0].full_url, "https://s3.tabula.test/" + BUCKET)
                    self.assertTrue(requests[0].has_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
