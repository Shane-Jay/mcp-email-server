#!/usr/bin/env bash
# Verify Streamable HTTP: GET /healthz, then authenticated tools/list.
# Usage: scripts/smoke.sh <base-url> <token-file>
# Example: scripts/smoke.sh http://nas.tail-xxxx.ts.net:9557 ./secrets/http_bearer_token
set -euo pipefail

usage() {
  printf 'Usage: %s <base-url> <token-file>\n' "$(basename "$0")" >&2
  exit 2
}

[[ ${1:-} == -h || ${1:-} == --help ]] && usage
[[ $# -eq 2 ]] || usage

BASE_URL=$1
TOKEN_FILE=$2

if [[ ! -f $TOKEN_FILE ]]; then
  printf 'Token file not found: %s\n' "$TOKEN_FILE" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  printf 'python3 is required\n' >&2
  exit 1
fi

python3 - "$BASE_URL" "$TOKEN_FILE" <<'PY'
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

base = sys.argv[1].strip()
token_path = Path(sys.argv[2])
token = token_path.read_bytes()
if token.endswith(b"\r\n"):
    token = token[:-2]
elif token.endswith(b"\n") or token.endswith(b"\r"):
    token = token[:-1]
secret = token.decode("utf-8")
if not secret:
    raise SystemExit(f"Token file is empty: {token_path}")

parts = urlsplit(base)
if not parts.scheme or not parts.netloc:
    raise SystemExit(f"Invalid URL: {base!r}")
path = parts.path.rstrip("/")
if path.endswith("/mcp"):
    path = path[: -len("/mcp")]
origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
healthz = f"{origin}{path}/healthz"
mcp_url = f"{origin}{path}/mcp"


def request(url: str, *, data: bytes | None = None, headers: dict[str, str], method: str) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:  # noqa: S310 - operator-supplied URL
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise SystemExit(f"{method} {url} -> HTTP {exc.code}: {body[:500]!r}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.reason}") from None


status, _headers, body = request(
    healthz,
    headers={"Accept": "text/plain"},
    method="GET",
)
if status != 200:
    raise SystemExit(f"GET {healthz} -> HTTP {status}")
print(f"healthz ok ({body.decode('utf-8', errors='replace').strip() or status})")


def parse_rpc(payload: bytes, content_type: str) -> dict[str, object] | None:
    text = payload.decode("utf-8")
    if "text/event-stream" in content_type:
        chunks: list[str] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                chunks.append(line[5:].lstrip())
        if not chunks:
            return None
        return json.loads("\n".join(chunks))
    stripped = text.strip()
    if not stripped:
        return None
    return json.loads(stripped)


session: str | None = None


def mcp(method: str, *, rpc_id: int | None, params: dict[str, object] | None = None) -> dict[str, object] | None:
    global session
    message: dict[str, object] = {"jsonrpc": "2.0", "method": method}
    if rpc_id is not None:
        message["id"] = rpc_id
    if params is not None:
        message["params"] = params
    headers = {
        "Authorization": f"Bearer {secret}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }
    if session:
        headers["mcp-session-id"] = session
    status_code, response_headers, payload = request(
        mcp_url,
        data=json.dumps(message).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    header_map = {key.lower(): value for key, value in response_headers.items()}
    session = header_map.get("mcp-session-id", session)
    if status_code in {202, 204} and rpc_id is None:
        return None
    if status_code != 200:
        raise SystemExit(f"POST {mcp_url} {method} -> HTTP {status_code}")
    parsed = parse_rpc(payload, header_map.get("content-type", ""))
    if rpc_id is None:
        return parsed
    if not isinstance(parsed, dict):
        raise SystemExit(f"{method} returned a non-object body")
    if parsed.get("error"):
        raise SystemExit(f"{method} error: {parsed['error']}")
    return parsed


initialized = mcp(
    "initialize",
    rpc_id=1,
    params={
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "mcp-email-server-smoke", "version": "1"},
    },
)
if not isinstance(initialized, dict) or "result" not in initialized:
    raise SystemExit("initialize did not return a result")
mcp("notifications/initialized", rpc_id=None)
listed = mcp("tools/list", rpc_id=2, params={})
if not isinstance(listed, dict):
    raise SystemExit("tools/list returned no body")
result = listed.get("result")
if not isinstance(result, dict):
    raise SystemExit("tools/list result is not an object")
tools = result.get("tools")
if not isinstance(tools, list):
    raise SystemExit("tools/list did not return a tools array")
names = [item.get("name") for item in tools if isinstance(item, dict)]
print(f"tools/list ok ({len(names)} tools): {', '.join(str(name) for name in names)}")
PY
