from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tomllib
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
import yaml

_WILDCARD_BIND = "0.0.0.0"  # noqa: S104
_TMPFS_PREFIX = "/tmp:"  # noqa: S108
_BEARER_FILE = "/run/secrets/http_bearer_token"
_CACHE_KEY_FILE = "/run/secrets/cache_key"
_NETEASE_AUTH_MOUNT = "/run/secrets/netease_auth_code"
_SMOKE_BEARER = "smoke-test-token"

REPOSITORY = Path(__file__).resolve().parents[1]
KIT = REPOSITORY / "deploy" / "synology"
COMPOSE = KIT / "docker-compose.yml"
ENV_EXAMPLE = KIT / "env.example"
CONFIG_EXAMPLE = KIT / "config.example.toml"
README = KIT / "README.md"
SMOKE = REPOSITORY / "scripts" / "smoke.sh"
_HEX_SECRET = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_INLINE_PASSWORD = re.compile(
    r"(?i)^(MCP_EMAIL_(?:HTTP_BEARER_TOKEN|CACHE_KEY)|MCP_EMAIL_SERVER_ACCOUNT_[A-Za-z0-9_]+_PASSWORD)="
)


def _load_compose() -> dict[str, object]:
    loaded = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_synology_compose_is_hardened_and_defaults_to_lan_bind() -> None:
    compose = _load_compose()
    services = compose["services"]
    assert isinstance(services, dict)
    service = services["mcp-email"]
    assert isinstance(service, dict)
    assert service["image"] == "ghcr.io/shane-jay/mcp-email-server:latest"
    assert "sha256" in COMPOSE.read_text(encoding="utf-8")
    assert service["user"] == "1000:1000"
    assert service.get("group_add") == ["100"]
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    tmpfs = service["tmpfs"]
    assert isinstance(tmpfs, list)
    assert any(str(entry).startswith(_TMPFS_PREFIX) and "uid=1000" in str(entry) for entry in tmpfs)
    ports = service["ports"]
    assert isinstance(ports, list)
    assert ports == [f"${{MCP_BIND_IP:-{_WILDCARD_BIND}}}:9557:9557"]
    local_override = yaml.safe_load((KIT / "compose.local.yml").read_text(encoding="utf-8"))
    assert isinstance(local_override, dict)
    local_services = local_override["services"]
    assert isinstance(local_services, dict)
    local_service = local_services["mcp-email"]
    assert isinstance(local_service, dict)
    assert local_service["ports"] == ["127.0.0.1:9557:9557"]
    volumes = service["volumes"]
    assert isinstance(volumes, list)
    joined = "\n".join(str(item) for item in volumes)
    assert "/volume1/docker/mcp-email-server/data" in joined
    assert "/data/attachments" in joined
    health = service["healthcheck"]
    assert isinstance(health, dict)
    assert "/healthz" in str(health["test"])
    environment = service["environment"]
    assert isinstance(environment, dict)
    assert environment["MCP_EMAIL_HTTP_BEARER_TOKEN_FILE"] == _BEARER_FILE
    assert environment["MCP_EMAIL_CACHE_KEY_FILE"] == _CACHE_KEY_FILE
    assert environment["MCP_EMAIL_SERVER_ACCOUNT_netease_PASSWORD_FILE"] == _NETEASE_AUTH_MOUNT
    secrets = service["secrets"]
    assert isinstance(secrets, list)
    assert set(secrets) >= {
        "http_bearer_token",
        "cache_key",
        "netease_auth_code",
        "aliyun_auth_code",
        "icloud_app_password",
    }
    defined = compose["secrets"]
    assert isinstance(defined, dict)
    for name in secrets:
        source = defined[name]
        assert isinstance(source, dict)
        assert str(source["file"]).endswith(name) or name in str(source["file"])


def test_synology_env_example_uses_presets_and_secret_files() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key] = value
        assert _INLINE_PASSWORD.match(stripped) is None
        assert _HEX_SECRET.search(value) is None

    assert values["MCP_ACCESS_MODE"] == "lan"
    assert values["MCP_BIND_IP"] == _WILDCARD_BIND
    assert "127.0.0.1" in text
    assert "MCP_ACCESS_MODE=local" in text
    assert values["MCP_ALLOWED_HOSTS"] == "192.168.1.50:9557,nas.local:9557"
    assert values["MCP_ENABLE_DNS_REBINDING_PROTECTION"] == "true"
    assert values["MCP_EMAIL_HTTP_INSECURE"] == "false"
    assert "nas.tail-xxxx.ts.net" not in text
    assert "100.64." not in values["MCP_ALLOWED_HOSTS"]
    assert values["MCP_EMAIL_SERVER_IMAP_POOL"] == "true"
    assert values["MCP_EMAIL_SERVER_SEND_DRAFT"] == "safe"
    allow = values["MCP_EMAIL_SERVER_TOOL_ALLOW"].split(",")
    assert allow == [
        "list_available_accounts",
        "list_emails_metadata",
        "get_emails_content",
        "send_email",
        "reply_email",
        "forward_email",
        "save_draft",
        "send_draft",
        "list_mailboxes",
    ]
    assert "email_command" not in allow
    assert "download_attachment" not in allow
    assert values["MCP_EMAIL_SERVER_ACCOUNTS"] == "netease,aliyun,icloud"
    assert values["MCP_EMAIL_SERVER_ACCOUNT_netease_PRESET"] == "163"
    assert values["MCP_EMAIL_SERVER_ACCOUNT_aliyun_PRESET"] == "aliyun"
    assert values["MCP_EMAIL_SERVER_ACCOUNT_icloud_PRESET"] == "icloud"
    assert values["MCP_EMAIL_SERVER_ACCOUNT_netease_EMAIL_ADDRESS"] == "you@163.com"
    assert values["MCP_EMAIL_SERVER_ACCOUNT_aliyun_EMAIL_ADDRESS"] == "you@company.example"
    assert values["MCP_EMAIL_SERVER_ACCOUNT_icloud_EMAIL_ADDRESS"] == "you@icloud.com"
    assert "PASSWORD_FILE=/run/secrets/" in text


def test_synology_config_example_documents_three_presets_without_secrets() -> None:
    text = CONFIG_EXAMPLE.read_text(encoding="utf-8")
    parsed = tomllib.loads(text)
    assert "emails" not in parsed
    assert "you@163.com" in text
    assert "imap.163.com" in text
    assert "you@company.example" in text
    assert "imap.qiye.aliyun.com" in text
    assert "you@icloud.com" in text
    assert "imap.mail.me.com" in text
    assert "smtp.mail.me.com" in text
    assert "start_ssl = true" in text
    assert "PRESET=163" in text
    assert "PRESET=aliyun" in text
    assert "PRESET=icloud" in text
    assert 'password = "' not in text
    assert _HEX_SECRET.search(text) is None


def test_synology_readme_covers_required_steps_in_english_and_chinese() -> None:
    text = README.read_text(encoding="utf-8")
    for needle in (
        "openssl rand -hex 32",
        "chmod 640",
        "group_add",
        "incomplete configuration",
        "/volume1/docker/mcp-email-server",
        "docker compose up -d",
        "/healthz",
        "scripts/smoke.sh",
        "tools/list",
        "MCP_EMAIL_HTTP_BEARER_TOKENS",
        "MCP_ACCESS_MODE=lan",
        f"MCP_BIND_IP={_WILDCARD_BIND}",
        "MCP_ACCESS_MODE=local",
        "MCP_BIND_IP=127.0.0.1",
        "plain HTTP",
        "Reverse Proxy",
        "WireGuard",
        "群晖",
        "授权码",
        "轮换",
        "明文 HTTP",
        "反向代理",
        "ghcr.io/shane-jay/mcp-email-server",
        "sha256",
    ):
        assert needle in text
    assert "chown -R 1000:1000 /volume1/docker/mcp-email-server\n" not in text
    assert "chmod 700 /volume1/docker/mcp-email-server/app/secrets" not in text
    assert "chmod 600 secrets/*" not in text


def test_smoke_script_is_executable_and_requires_args() -> None:
    assert SMOKE.is_file()
    assert SMOKE.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    git = shutil.which("git")
    assert git is not None
    listed = subprocess.run(  # noqa: S603 - repository git index mode
        [git, "ls-files", "-s", "--", "scripts/smoke.sh"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )
    assert listed.stdout.startswith("100755 "), listed.stdout
    if os.name == "posix":
        assert SMOKE.stat().st_mode & stat.S_IXUSR


class _SmokeHandler(BaseHTTPRequestHandler):
    expected_bearer = _SMOKE_BEARER
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        return

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length) if length else b"{}"
        loaded = json.loads(payload.decode("utf-8"))
        assert isinstance(loaded, dict)
        return loaded

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/healthz":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/mcp":
            self.send_error(404)
            return
        auth = self.headers.get("Authorization")
        if auth != f"Bearer {self.expected_bearer}":
            self.send_error(401)
            return
        message = self._read_json()
        method = message.get("method")
        if method == "notifications/initialized":
            self.send_response(202)
            self.send_header("mcp-session-id", "sess-1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "initialize":
            result = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "mcp-email-server", "version": "1.15.0"},
                },
            }
        elif method == "tools/list":
            result = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {"tools": [{"name": "list_available_accounts"}]},
            }
        else:
            self.send_error(400)
            return
        payload = f"event: message\ndata: {json.dumps(result)}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("mcp-session-id", "sess-1")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def smoke_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SmokeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_smoke_script_healthz_and_authenticated_tools_list(tmp_path: Path, smoke_server: str) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(f"{_SmokeHandler.expected_bearer}\n", encoding="utf-8")
    token_file.chmod(0o600)
    env = os.environ.copy()
    bash = shutil.which("bash")
    assert bash is not None
    completed = subprocess.run(  # noqa: S603 - repository smoke script
        [bash, str(SMOKE), smoke_server, str(token_file)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert "healthz ok" in completed.stdout
    assert "tools/list ok" in completed.stdout
    assert "list_available_accounts" in completed.stdout


def test_smoke_script_rejects_wrong_token(tmp_path: Path, smoke_server: str) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("wrong-token\n", encoding="utf-8")
    bash = shutil.which("bash")
    assert bash is not None
    completed = subprocess.run(  # noqa: S603 - repository smoke script
        [bash, str(SMOKE), smoke_server, str(token_file)],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode != 0
    assert "401" in completed.stderr
