"""What the SDK checks (scripts/replay_check.py, scripts/types_check.py) share: the sandbox's admin logged in to
the control plane, features and configuration documents approved as that admin, keys of the sandbox's
sources with the scopes a check needs, and steps that print PASS or FAIL. No key is ever printed.

The sandbox is the one scripts/local-e2e.sh bootstraps (bootstrap.json); the checks run against a cell it left
running (--keep).
"""

import base64
import hashlib
import hmac
import struct
import time
from collections.abc import Callable
from typing import Any

import httpx

# A new key, a feature or a type reaches the cell with the control plane's next snapshot: 401 and 404 until then.
READY_TIMEOUT_S = 90


class Run:
    def __init__(self) -> None:
        self.failed = 0

    def step(self, name: str, fn: Callable[[], str]) -> bool:
        started = time.monotonic()
        try:
            detail = fn()
        except Exception as error:  # a failed step is reported, and the next ones still run when they can
            self.failed += 1
            print(f"FAIL  {name} ({time.monotonic() - started:.1f}s) {type(error).__name__}: {error}")
            return False
        print(f"PASS  {name} ({time.monotonic() - started:.1f}s) {detail}")
        return True


def totp(secret_b32: str, at: float | None = None) -> str:
    """RFC 6238, SHA-1, 6 digits, 30 s."""
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    counter = struct.pack(">Q", int((at or time.time()) // 30))
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def expect(response: httpx.Response, *statuses: int) -> dict[str, Any]:
    if response.status_code not in statuses:
        request = response.request
        raise AssertionError(f"{request.method} {request.url.path} -> {response.status_code}: {response.text[:300]}")
    return response.json() if response.content else {}


def bearer(key: str) -> dict[str, str]:
    return {"authorization": f"Bearer {key}"}


class Sandbox:
    """The sandbox space, as its admin sees it from the control plane."""

    def __init__(self, api: str, control: str, secrets: dict[str, Any], purpose: str) -> None:
        self.api = httpx.Client(base_url=api, timeout=30)
        self.control = httpx.Client(base_url=control, timeout=30)
        self.secrets = secrets
        self.space = secrets["space_id"]
        self.purpose = purpose
        self.person: dict[str, str] = {}

    def login(self) -> str:
        secrets = self.secrets
        body = {"email": secrets["admin_email"], "password": secrets["password"], "space_id": self.space}
        code = {"totp": totp(secrets["totp_secret"])} if secrets.get("totp_secret") else {}
        response = self.control.post("/v1/auth/login", json={**body, **code})
        if response.status_code == 401 and code:
            # The control plane takes each code once: wait for the next one.
            time.sleep(30 - time.time() % 30 + 0.5)
            response = self.control.post("/v1/auth/login", json={**body, "totp": totp(secrets["totp_secret"])})
        self.person["authorization"] = f"Bearer {expect(response, 200)['access_token']}"
        return "the sandbox's admin"

    def approve(self, kind: str, document: dict[str, Any], reason: str) -> None:
        diff = {"space_id": self.space, "type": kind, "document": document, "reason": f"{self.purpose}: {reason}"}
        created = expect(self.control.post("/v1/config/diffs", json=diff, headers=self.person), 201)
        # A sandbox's author approves their own diffs.
        expect(self.control.post(f"/v1/config/diffs/{created['diff_id']}/approve", headers=self.person), 200)

    def features_on(self, *names: str) -> str:
        params = {"space_id": self.space}
        current = expect(self.control.get("/v1/config/features", params=params, headers=self.person), 200)
        enabled = set(current["document"].get("enabled") or ())
        missing = set(names) - enabled
        if not missing:
            return "already on"
        self.approve("features", {"enabled": sorted(enabled | missing)}, ", ".join(sorted(missing)))
        return f"turned on {', '.join(sorted(missing))}"

    def declare_types(self, *declared: dict[str, Any]) -> None:
        """Object types, each replacing an earlier declaration of its name; the others stay."""
        names = {t["type"] for t in declared}
        params = {"space_id": self.space}
        current = expect(self.control.get("/v1/config/object-types", params=params, headers=self.person), 200)
        types = current["document"].get("types") or []
        if all(t in types for t in declared):
            return
        kept = [t for t in types if t.get("type") not in names]
        self.approve("object-types", {**current["document"], "types": [*kept, *declared]}, ", ".join(sorted(names)))

    def key(self, source: str, *scopes: str, ready: str = "/v1/sdk/profile") -> str:
        """A new key of the sandbox source `source` with `scopes` (the sandbox's own keys predate them), once
        the cell serves it: `ready` answers something other than 401 and 404."""
        sources = expect(self.control.get("/v1/sources", params={"space_id": self.space}, headers=self.person), 200)
        source_id = next(s["source_id"] for s in sources if s["name"] == source)
        path = f"/v1/sources/{source_id}/keys"
        secret = expect(self.control.post(path, json={"scopes": list(scopes)}, headers=self.person), 201)["secret"]
        self.until(f"the cell serving the new {source} key", lambda: self.api.get(ready, headers=bearer(secret)))
        return secret

    def until(self, what: str, probe: Callable[[], httpx.Response], timeout: float = READY_TIMEOUT_S) -> None:
        """Waits while `probe` answers 401 or 404, then expects 200."""
        deadline = time.monotonic() + timeout
        while True:
            response = probe()
            if response.status_code not in (401, 404) or time.monotonic() > deadline:
                expect(response, 200)
                return
            time.sleep(3)
