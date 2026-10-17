"""Creates Niadra's own tenant once (company "Niadra", project "Atendimento e cobrança", spaces
`atendimento` and `atendimento-sandbox`, synthetic data only) and keeps its secrets in the secret
store. Its admin, e2e@niadra.com, is the account of the end-to-end check; people get their own
accounts beside it (control_cli add-admin).

The control plane prints an admin password, a TOTP secret and one key per source only at creation,
so they go straight to Secrets Manager and never to a log. A second run finds the secret and reads it.

Then it waits until the cell serves the tenant's spaces. Each space has its own database, which the
cell creates from the control plane's snapshot: before the snapshot arrives the cell answers 401, and
while the database is being created 503 with Retry-After. The sandbox is checked with its whatsapp key,
the call the end-to-end check starts with; every space of the project, production included, with the
admin's person token. Past the deadline the job fails, and with it the deploy.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from aiobotocore.session import get_session

SECRET = os.environ["SANDBOX_SECRET"]
REGION = os.environ["NIADRA_REGION"]
CONTROL_URL = os.environ.get("NIADRA_CONTROL_URL", "http://control:8000")
READ_URL = os.environ.get("CELL_READ_URL", "http://read:8000")
ADMIN_URL = os.environ.get("CELL_ADMIN_URL", "http://admin:8000")
READY_TIMEOUT_S = 480
# The snapshot has not reached the cell yet (401), or the space's database is being created (503).
NOT_YET = {401: "keys not in the cell yet", 503: "space database being created"}
# Synthetic customer, as in e2e_sandbox.py: a number in the range Anatel reserves for fiction and tests.
SUBJECT = {"type": "phone_e164", "value": "+5511900001234"}


async def main() -> int:
    async with get_session().create_client("secretsmanager", region_name=REGION) as secrets:
        try:
            stored = await secrets.get_secret_value(SecretId=SECRET)
            document = json.loads(stored["SecretString"])
            print("sandbox tenant already exists")
        except secrets.exceptions.ResourceNotFoundException:
            document = create_tenant()
            await secrets.create_secret(Name=SECRET, SecretString=json.dumps(document))
            print("sandbox tenant created; its secrets are in", SECRET)
    return 0 if await served(document) else 1


def create_tenant() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / "bootstrap.json"
        command = [
            sys.executable, "-m", "niadra.entrypoints.control_cli", "bootstrap",
            "--tenant", "Niadra", "--slug", "niadra",
            "--project", "Atendimento e cobrança", "--project-slug", "atendimento",
            "--email", "e2e@niadra.com",
            "--region", REGION, "--cell", os.environ["NIADRA_CELL_ID"],
            "--cell-address", os.environ["CELL_ADDRESS"], "--output", str(output),
        ]  # fmt: skip
        subprocess.run(command, check=True)
        document: dict[str, Any] = json.loads(output.read_text())
    return document


async def served(document: dict[str, Any]) -> bool:
    deadline = time.monotonic() + READY_TIMEOUT_S
    async with httpx.AsyncClient(timeout=15) as http:
        key = {"authorization": f"Bearer {document['keys']['whatsapp']}"}
        sandbox = await wait(
            "the sandbox, with its whatsapp key",
            lambda: http.post(f"{READ_URL}/v1/context", json={"subject": SUBJECT}, headers=key),
            deadline,
        )
        if not sandbox:
            return False
        try:
            spaces = await project_spaces(http, document)
            people = {space_id: await person_token(http, document, space_id) for space_id, _ in spaces}
        except (httpx.HTTPError, KeyError, ValueError, StopIteration) as exc:
            print(f"the control plane did not answer for the project's spaces: {exc!r}")
            return False
        for space_id, environment in spaces:
            person = people[space_id]
            ok = await wait(
                f"the {environment} space {space_id}, with the admin's token",
                lambda person=person: http.get(
                    f"{ADMIN_URL}/v1/receipts", params={"limit": 1}, headers=person
                ),
                deadline,
            )
            if not ok:
                return False
    print("the cell serves every space of the sandbox tenant")
    return True


async def wait(what: str, call: Any, deadline: float) -> bool:
    """Calls until the answer is neither 401 nor 503; a pod still starting counts as not yet."""
    last = ""
    while True:
        try:
            response = await call()
            status, pause = response.status_code, retry_after(response)
            state = f"{status} ({NOT_YET[status]})" if status in NOT_YET else str(status)
        except httpx.HTTPError as exc:
            status, pause, state = 0, 5.0, f"unreachable ({type(exc).__name__})"
        if status and status not in NOT_YET:
            if status == 200:
                print(f"served: {what}")
                return True
            print(f"{what}: unexpected {status}: {response.text[:200]}")
            return False
        if state != last:
            print(f"waiting for {what}: {state}")
            last = state
        if time.monotonic() + pause > deadline:
            print(f"{what} is still not served after {READY_TIMEOUT_S} s: {state}")
            return False
        await asyncio.sleep(pause)


def retry_after(response: httpx.Response, default: float = 5.0) -> float:
    try:
        return min(max(float(response.headers.get("retry-after", default)), 1.0), 30.0)
    except ValueError:
        return default


async def project_spaces(http: httpx.AsyncClient, document: dict[str, Any]) -> list[tuple[str, str]]:
    person = await person_token(http, document, None)
    response = await http.get(f"{CONTROL_URL}/v1/projects", headers=person)
    response.raise_for_status()
    project = next(p for p in response.json() if p["project_id"] == document["project_id"])
    return [(s["space_id"], s["environment"]) for s in project["spaces"]]


async def person_token(
    http: httpx.AsyncClient, document: dict[str, Any], space_id: str | None
) -> dict[str, str]:
    body: dict[str, Any] = {"email": document["admin_email"], "password": document["password"]}
    if space_id:
        body["space_id"] = space_id
    for attempt in range(5):
        if document.get("totp_secret"):
            body["totp"] = await fresh_totp(document["totp_secret"])
        try:
            response = await http.post(f"{CONTROL_URL}/v1/auth/login", json=body)
        except httpx.TransportError:
            if attempt == 4:
                raise
            await asyncio.sleep(3)
            continue
        if response.status_code == 401 and "totp" in body and attempt < 4:
            continue  # another login (the end-to-end check) took this step's code: the next one is fresh
        response.raise_for_status()
        return {"authorization": f"Bearer {response.json()['access_token']}"}
    raise AssertionError("unreachable")


_used_step: int | None = None


async def fresh_totp(secret_b32: str) -> str:
    """A code of a 30 s step no login of this job used yet: the control plane takes each code once."""
    global _used_step
    if _used_step is not None and int(time.time() // 30) <= _used_step:
        await asyncio.sleep((_used_step + 1) * 30 - time.time() + 0.5)
    _used_step = int(time.time() // 30)
    return totp(secret_b32)


def totp(secret_b32: str) -> str:
    """RFC 6238, SHA-1, 6 digits, 30 s, like e2e_sandbox.py."""
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", int(time.time() // 30)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
