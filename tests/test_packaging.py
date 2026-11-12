from __future__ import annotations

import contextlib
import hashlib
import http.cookiejar
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from types import ModuleType

import pytest
import yaml

pty: ModuleType | None
try:
    import pty
except ImportError:  # pragma: no cover - collected only by non-POSIX test runners
    pty = None

REPOSITORY = Path(__file__).resolve().parents[1]
STATIC_PREFIX = "mcp_email_server/web_ui/static/"
_LAUNCH_URL = re.compile(r"http://127\.0\.0\.1:\d+/manage-[A-Za-z0-9_-]+/#bootstrap=[A-Za-z0-9_-]+")


def _start_ui_in_terminal(
    command: list[str],
    *,
    environment: dict[str, str],
) -> tuple[subprocess.Popen[bytes], int, str]:
    if pty is None:
        raise RuntimeError("PTY-backed UI smoke is unavailable on this platform")
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(  # noqa: S603 - exact test-owned executable and arguments
            command,
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=slave,
            env=environment,
            start_new_session=True,
        )
    finally:
        os.close(slave)

    output = ""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        readable, _, _ = select.select([master], [], [], min(0.25, deadline - time.monotonic()))
        if not readable:
            continue
        try:
            output = f"{output}{os.read(master, 4096).decode(errors='replace')}"[-16_384:]
        except OSError:
            break
        match = _LAUNCH_URL.search(output)
        if match is not None:
            return process, master, match.group(0)

    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=10)
    os.close(master)
    raise AssertionError("Installed UI did not produce an interactive launch URL")


def _stop_ui(process: subprocess.Popen[bytes], master: int) -> None:
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    os.close(master)


def _assert_authenticated_ui(launch_url: str) -> None:
    parsed = urllib.parse.urlsplit(launch_url)
    fragment = urllib.parse.parse_qs(parsed.fragment, strict_parsing=True)
    assert set(fragment) == {"bootstrap"}
    assert len(fragment["bootstrap"]) == 1
    token = fragment["bootstrap"][0]
    base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    origin = f"{parsed.scheme}://{parsed.netloc}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    with opener.open(base, timeout=10) as response:
        html = response.read()
        assert response.status == 200
        assert b"Local Email Management" in html
        assert response.headers["Cache-Control"] == "no-store"

    bootstrap = urllib.request.Request(  # noqa: S310 - exact loopback child process
        urllib.parse.urljoin(base, "api/bootstrap"),
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    with opener.open(bootstrap, timeout=10) as response:
        exchange = json.loads(response.read())
        assert response.status == 200
        assert isinstance(exchange.get("csrf"), str)

    with opener.open(urllib.parse.urljoin(base, "api/status"), timeout=10) as response:
        status = json.loads(response.read())
        assert response.status == 200
        assert status["mode"] == "legacy"
        assert response.headers["Cache-Control"] == "no-store"


def _release_distributions() -> Path | None:
    value = os.getenv("MCP_EMAIL_SERVER_TEST_DIST_DIR")
    return Path(value).resolve() if value else None


def _static_files(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as archive:
        return {
            name.removeprefix(STATIC_PREFIX): archive.read(name)
            for name in archive.namelist()
            if name.startswith(STATIC_PREFIX) and not name.endswith("/")
        }


def _static_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _workflow_job(path: Path, job_name: str) -> dict[str, object]:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get(job_name)
    assert isinstance(job, dict)
    return job


def _workflow_steps(path: Path, job_name: str) -> list[dict[str, object]]:
    job = _workflow_job(path, job_name)
    steps = job.get("steps")
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def _workflow_job_runs(path: Path, job_name: str) -> list[str]:
    runs: list[str] = []
    for step in _workflow_steps(path, job_name):
        assert isinstance(step, dict)
        command = step.get("run")
        if command is not None:
            assert isinstance(command, str)
            runs.append(command.strip())
    return runs


def test_container_build_context_and_runtime_copy_are_restricted() -> None:
    dockerignore = (REPOSITORY / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert dockerignore == [
        "**",
        "!README.md",
        "!LICENSE",
        "!pyproject.toml",
        "!uv.lock",
        "!mcp_email_server/",
        "!mcp_email_server/**",
    ]

    dockerfile = (REPOSITORY / "Dockerfile").read_text(encoding="utf-8")
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    uv_requirement = next(
        requirement for requirement in project["dependency-groups"]["dev"] if requirement.startswith("uv==")
    )
    assert f"ARG UV_VERSION={uv_requirement.removeprefix('uv==')}" in dockerfile
    assert "ghcr.io/astral-sh/uv:latest" not in dockerfile
    assert "COPY . /app" not in dockerfile
    assert "COPY mcp_email_server ./mcp_email_server" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert "COPY --from=builder /app/.venv /app/.venv" in dockerfile
    assert "COPY --from=builder /app /app" not in dockerfile
    assert "apt-get upgrade -y --no-install-recommends" in dockerfile
    assert "python -m pip uninstall -y --disable-pip-version-check pip setuptools wheel" in dockerfile
    assert 'org.opencontainers.image.licenses="BSD-3-Clause"' in dockerfile


def test_distribution_is_node_free_and_embeds_reproducible_ui(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    distributions = _release_distributions()
    if distributions is None:
        distributions = tmp_path / "distributions"
        subprocess.run(  # noqa: S603 - controlled test command
            [uv, "build", "--out-dir", str(distributions)],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
            text=True,
        )
    sdist = next(distributions.glob("*.tar.gz"))
    wheel = next(distributions.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = set(archive.namelist())
        wheel_runtime = {
            name: archive.read(name)
            for name in wheel_names
            if name.startswith("mcp_email_server/") and not name.endswith("/")
        }
        entry_points_name = next(name for name in wheel_names if name.endswith(".dist-info/entry_points.txt"))
        entry_points = archive.read(entry_points_name).decode("utf-8")
    assert "mcp-email-server = mcp_email_server.cli:app" in entry_points
    assert "mcp-email-server-plugin = mcp_email_server.cli:plugin_stdio" in entry_points
    expected_runtime = {
        path.relative_to(REPOSITORY).as_posix(): path.read_bytes()
        for path in (REPOSITORY / "mcp_email_server").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    assert wheel_runtime == expected_runtime
    assert all(
        name.startswith("mcp_email_server/") or name.split("/", maxsplit=1)[0].endswith(".dist-info")
        for name in wheel_names
    )
    assert len([name for name in wheel_names if name.endswith(".dist-info/licenses/LICENSE")]) == 1

    packaged = _static_files(wheel)
    staged = {
        path.relative_to(REPOSITORY / STATIC_PREFIX).as_posix(): path.read_bytes()
        for path in (REPOSITORY / STATIC_PREFIX).rglob("*")
        if path.is_file()
    }
    assert packaged == staged
    assert set(packaged) >= {"index.html", "THIRD_PARTY_NOTICES.md"}
    assert any(name.startswith("assets/") and name.endswith(".js") for name in packaged)
    assert any(name.startswith("assets/") and name.endswith(".css") for name in packaged)
    assert not any(name.endswith(".map") for name in packaged)

    extracted = tmp_path / "source"
    with tarfile.open(sdist) as archive:
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    for required in (
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/THIRD_PARTY_NOTICES.md",
        "frontend/embedded-assets.json",
        "frontend/src/App.tsx",
        "frontend/e2e/local-management.spec.ts",
        "frontend/playwright.config.ts",
        "mcp_email_server/web_ui/static/index.html",
        "mcp_email_server/web_ui/static/THIRD_PARTY_NOTICES.md",
        ".agents/plugins/marketplace.json",
        ".claude-plugin/marketplace.json",
        ".dockerignore",
        ".github/actions/setup-python-env/action.yml",
        ".github/workflows/main.yml",
        ".github/workflows/on-release-main.yml",
        ".github/workflows/publish-image.yml",
        ".github/workflows/unit-and-e2e.yml",
        ".pre-commit-config.yaml",
        "codecov.yaml",
        "Dockerfile",
        "deploy/synology/docker-compose.yml",
        "deploy/synology/env.example",
        "deploy/synology/config.example.toml",
        "deploy/synology/README.md",
        "scripts/smoke.sh",
        "plugins/mcp-email-server/.codex-plugin/plugin.json",
        "plugins/mcp-email-server/.claude-plugin/plugin.json",
        "plugins/mcp-email-server/.mcp.json",
        "plugins/mcp-email-server/skills/safe-email-operations/SKILL.md",
        "plugins/mcp-email-server/skills/safe-email-operations/references/installation.md",
        "plugins/mcp-email-server/skills/safe-email-operations/references/safe-commands.md",
        "dev/build_frontend.py",
        "dev/set_release_version.py",
        "dev/verify_container.py",
        "dev/install_claude_desktop.py",
        "dev/claude_desktop_config.json",
        "dev/greenmail/compose.yml",
        "dev/greenmail/file_keyring.py",
        "dev/greenmail/run-e2e.sh",
        "LICENSE",
    ):
        assert (source / required).is_file()
    assert not (source / "frontend/node_modules").exists()
    assert not (source / "frontend/dist").exists()
    assert not (source / "frontend/test-results").exists()
    assert not (source / "frontend/playwright-report").exists()
    assert not list(source.rglob("__pycache__"))
    assert not list(source.rglob("*.pyc"))

    blockers = tmp_path / "blockers"
    blockers.mkdir()
    for executable in ("node", "npm", "npx"):
        path = blockers / executable
        path.write_text(f"#!/bin/sh\necho '{executable} must not run' >&2\nexit 97\n")
        path.chmod(0o755)
    rebuilt = tmp_path / "rebuilt"
    environment = os.environ.copy()
    environment["PATH"] = f"{blockers}{os.pathsep}{environment['PATH']}"
    frontend_check = [sys.executable, "dev/build_frontend.py", "--check"]
    subprocess.run(  # noqa: S603 - fixed interpreter and repository script
        frontend_check,
        cwd=source,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(  # noqa: S603 - controlled test command
        [uv, "build", "--wheel", "--out-dir", str(rebuilt)],
        cwd=source,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    rebuilt_wheel = next(rebuilt.glob("*.whl"))
    rebuilt_files = _static_files(rebuilt_wheel)
    assert rebuilt_files == packaged
    assert _static_digest(rebuilt_files) == _static_digest(staged)


@pytest.mark.skipif(os.name != "posix", reason="The V2 UI filesystem and PTY contract is POSIX-only")
def test_isolated_wheel_and_local_uvx_serve_authenticated_ui(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    distributions = _release_distributions()
    if distributions is None:
        distributions = tmp_path / "distributions"
        subprocess.run(  # noqa: S603 - controlled test command
            [uv, "build", "--out-dir", str(distributions)],
            cwd=REPOSITORY,
            check=True,
            capture_output=True,
            text=True,
        )
    release_wheel = next(distributions.glob("*.whl"))
    sdist = next(distributions.glob("*.tar.gz"))

    extracted = tmp_path / "rebuilt-source"
    with tarfile.open(sdist) as archive:
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    blockers = tmp_path / "rebuilt-blockers"
    blockers.mkdir()
    for blocked_name in ("node", "npm", "npx"):
        blocker = blockers / blocked_name
        blocker.write_text(f"#!/bin/sh\necho '{blocked_name} must not run' >&2\nexit 97\n")
        blocker.chmod(0o755)
    rebuild_environment = os.environ.copy()
    rebuild_environment["PATH"] = f"{blockers}{os.pathsep}{rebuild_environment['PATH']}"
    rebuilt = tmp_path / "rebuilt-distribution"
    subprocess.run(  # noqa: S603 - controlled test command
        [uv, "build", "--wheel", "--out-dir", str(rebuilt)],
        cwd=source,
        env=rebuild_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(rebuilt.glob("*.whl"))

    environment = tmp_path / "environment"
    subprocess.run(  # noqa: S603 - controlled test command
        [uv, "venv", "--python", sys.executable, str(environment)],
        check=True,
        capture_output=True,
        text=True,
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable = environment / ("Scripts/mcp-email-server.exe" if os.name == "nt" else "bin/mcp-email-server")
    subprocess.run(  # noqa: S603 - controlled test command
        [uv, "pip", "install", "--python", str(python), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    run_root = tmp_path / "run"
    run_root.mkdir(mode=0o700)
    run_root.chmod(0o700)
    runtime_environment = os.environ.copy()
    runtime_environment["MCP_EMAIL_SERVER_CONFIG_PATH"] = str(run_root / "config.toml")

    process, terminal, launch_url = _start_ui_in_terminal(
        [str(executable), "ui", "--no-open", "--port", "0"],
        environment=runtime_environment,
    )
    try:
        _assert_authenticated_ui(launch_url)
    finally:
        _stop_ui(process, terminal)

    release_uvx_environment = runtime_environment.copy()
    release_uvx_environment["UV_TOOL_DIR"] = str(tmp_path / "release-uv-tools")
    release_process, release_terminal, release_launch_url = _start_ui_in_terminal(
        [
            uv,
            "tool",
            "run",
            "--from",
            str(release_wheel),
            "mcp-email-server",
            "ui",
            "--no-open",
            "--port",
            "0",
        ],
        environment=release_uvx_environment,
    )
    try:
        _assert_authenticated_ui(release_launch_url)
    finally:
        _stop_ui(release_process, release_terminal)


def test_container_security_workflow_scans_runtime_image_from_selected_commit() -> None:
    workflow_path = REPOSITORY / ".github/workflows/container-security.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    events = workflow[True]  # PyYAML interprets the YAML 1.1 key "on" as True.
    assert events["push"]["branches"] == ["main"]
    assert events["schedule"] == [{"cron": "17 4 * * 1"}]
    assert "workflow_dispatch" in events
    assert "pull_request" not in events
    expected_permissions = {
        "contents": "read",
        "actions": "read",
        "security-events": "write",
    }
    assert workflow["permissions"] == expected_permissions
    assert _workflow_job(workflow_path, "scan")["permissions"] == expected_permissions

    steps = _workflow_steps(workflow_path, "scan")
    build = next(step for step in steps if step.get("name") == "Build runtime image from selected commit")
    assert "if" not in build
    assert build["run"] == "docker build --pull --tag mcp-email-server:security ."
    trivy_steps = [step for step in steps if str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")]
    assert len(trivy_steps) == 2
    sarif = next(step for step in trivy_steps if step["with"]["format"] == "sarif")
    gate = next(step for step in trivy_steps if step["with"]["format"] == "table")
    sarif_options = sarif["with"]
    gate_options = gate["with"]
    for options in (sarif_options, gate_options):
        assert options["image-ref"] == "mcp-email-server:security"
        assert "wh1isper" not in options["image-ref"]
        assert options["scan-type"] == "image"
        assert options["scanners"] == "vuln"
        assert options["severity"] == "HIGH,CRITICAL"
        assert options["ignore-unfixed"] is True
    assert sarif_options["limit-severities-for-sarif"] is True
    assert sarif_options["exit-code"] == "0"
    assert gate_options["exit-code"] == "1"
    assert "if" not in gate
    assert "continue-on-error" not in gate
    artifact = next(step for step in steps if step.get("name") == "Upload SARIF artifact")
    assert artifact["uses"] == "actions/upload-artifact@v4"
    assert "if" not in artifact
    upload = next(step for step in steps if step.get("uses") == "github/codeql-action/upload-sarif@v4")
    assert upload["with"]["sarif_file"] == sarif_options["output"]
    assert upload["with"]["category"] == "container-main-build"
    assert upload.get("continue-on-error") is True
    condition = upload.get("if")
    assert isinstance(condition, str)
    assert "ENABLE_CODE_SCANNING" in condition
    names = [step.get("name") for step in steps]
    assert names.index("Scan runtime image") < names.index("Upload SARIF artifact")
    assert names.index("Upload SARIF artifact") < names.index("Upload vulnerability report")
    assert names.index("Upload vulnerability report") < names.index("Fail on unfixed HIGH or CRITICAL findings")


def test_ci_and_release_use_the_same_exact_artifact_verification_path() -> None:
    main = REPOSITORY / ".github/workflows/main.yml"
    release = REPOSITORY / ".github/workflows/on-release-main.yml"

    assert _workflow_job_runs(main, "documentation") == ["make docs-test"]
    assert _workflow_job_runs(main, "artifacts") == ["make build", "make verify-dist"]
    assert _workflow_job_runs(main, "container") == ["make container-check CONTAINER_IMAGE=mcp-email-server:ci"]

    supported = ["3.11", "3.12", "3.13", "3.14"]
    main_matrix = _workflow_job(main, "tests-and-type-check")
    release_matrix = _workflow_job(release, "release-python-matrix")
    assert main_matrix["strategy"]["matrix"]["python-version"] == supported  # type: ignore[index]
    assert release_matrix["strategy"]["matrix"]["python-version"] == supported  # type: ignore[index]
    assert _workflow_job_runs(main, "tests-and-type-check")[0] in _workflow_job_runs(release, "release-python-matrix")

    validation_runs = _workflow_job_runs(release, "release-validation-and-build")
    release_container_check = (
        "make container-check CONTAINER_IMAGE=mcp-email-server:release "
        "CONTAINER_VERSION=${{ needs.verify-release-source.outputs.release_version }}"
    )
    assert release_container_check in validation_runs
    assert validation_runs.index(release_container_check) < validation_runs.index("make build")
    assert validation_runs.index("make build") < validation_runs.index("make verify-dist")

    release_jobs = yaml.safe_load(release.read_text(encoding="utf-8"))["jobs"]
    assert "publish" not in release_jobs
    assert "publish-container" not in release_jobs
    release_text = release.read_text(encoding="utf-8")
    assert "uv publish" not in release_text
    assert "PYPI_TOKEN" not in release_text
    assert "ghcr.io/wh1isper" not in release_text

    image_workflow = REPOSITORY / ".github/workflows/publish-image.yml"
    image_steps = _workflow_steps(image_workflow, "build-and-push")
    metadata = next(step for step in image_steps if step.get("uses") == "docker/metadata-action@v5")
    metadata_options = metadata["with"]
    assert isinstance(metadata_options, dict)
    assert metadata_options["images"] == "ghcr.io/${{ github.repository }}"
    image_build = next(step for step in image_steps if step.get("uses") == "docker/build-push-action@v6")
    image_options = image_build["with"]
    assert isinstance(image_options, dict)
    assert image_options["platforms"] == "linux/amd64,linux/arm64"
    assert image_options["push"] is True

    docs_workflow = yaml.safe_load((REPOSITORY / ".github/workflows/docs.yml").read_text(encoding="utf-8"))
    assert "deploy" not in docs_workflow["jobs"]
    docs_text = (REPOSITORY / ".github/workflows/docs.yml").read_text(encoding="utf-8")
    assert "CLOUDFLARE" not in docs_text
    assert "wrangler pages deploy" not in docs_text

    workflow = yaml.safe_load(release.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    for job_name in ("verify-release-source", "release-python-matrix", "release-validation-and-build"):
        assert _workflow_job(release, job_name).get("permissions", {"contents": "read"}) != {"contents": "write"}

    source_steps = _workflow_steps(release, "verify-release-source")
    source_checkout = next(step for step in source_steps if step.get("uses") == "actions/checkout@v6")
    checkout_options = source_checkout["with"]
    assert isinstance(checkout_options, dict)
    assert checkout_options == {"ref": "${{ github.sha }}", "fetch-depth": 0}
    source_check = next(step for step in source_steps if step.get("name") == "Verify exact release tag commit")
    source_command = source_check["run"]
    assert isinstance(source_command, str)
    assert "refs/tags/${RELEASE_TAG}^{commit}" in source_command
    assert 'test "$tag_sha" = "$EVENT_SHA"' in source_command
    assert 'release_version="$(python3 dev/set_release_version.py "$RELEASE_TAG")"' in source_command
    assert 'test "$RELEASE_TAG" = "$release_version" || test "$RELEASE_TAG" = "v${release_version}"' in source_command
    assert 'echo "release_version=$release_version" >> "$GITHUB_OUTPUT"' in source_command

    expected_ref = "${{ needs.verify-release-source.outputs.release_sha }}"
    stamp_command = 'python3 dev/set_release_version.py "${{ needs.verify-release-source.outputs.release_version }}"'
    for job_name in ("release-python-matrix", "release-validation-and-build"):
        steps = _workflow_steps(release, job_name)
        checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v6")
        options = checkout["with"]
        assert isinstance(options, dict)
        assert options["ref"] == expected_ref
        assert stamp_command in _workflow_job_runs(release, job_name)

    release_commands = [
        command
        for job_name in (
            "verify-release-source",
            "release-python-matrix",
            "release-validation-and-build",
        )
        for command in _workflow_job_runs(release, job_name)
    ]
    assert sum("set_release_version" in command for command in release_commands) == 3
    assert "uv lock" not in release_commands
