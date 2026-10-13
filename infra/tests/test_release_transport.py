"""Exercise the SSH receiver trust boundary without SSH, Docker or production files."""

import importlib.util
import io
import os
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_receiver", ROOT / "infra/scripts/receive-release.py"
)
receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver)
HEAD_SPEC = importlib.util.spec_from_file_location(
    "deploy_head", ROOT / "scripts/check_deploy_head.py"
)
head = importlib.util.module_from_spec(HEAD_SPEC)
HEAD_SPEC.loader.exec_module(head)
SHA = "a" * 40
IMAGE = "ghcr.io/can-budget/budget-api@sha256:" + "b" * 64


def archive(extra=None, omit=None, content=b"reviewed source"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as stream:
        for name in sorted(receiver.REQUIRED - {omit}):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            stream.addfile(entry, io.BytesIO(content))
        if extra is not None:
            stream.addfile(extra, io.BytesIO(b""))
    return output.getvalue()


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    (tmp_path / "releases").mkdir()
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(receiver.subprocess, "run", run)
    return tmp_path, run


@pytest.mark.parametrize(
    "name", ["/infra/evil", "infra/../escape", "infra//evil", "infra/./evil", "outside/file"]
)
def test_archive_cannot_escape_release(deployment, name):
    root, run = deployment
    with pytest.raises(ValueError):
        receiver.receive(
            f"release {SHA} {IMAGE} 10", io.BytesIO(archive(tarfile.TarInfo(name))), root
        )
    run.assert_not_called()
    assert list((root / "releases").iterdir()) == []


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE]
)
def test_archive_rejects_links_and_special_files(deployment, kind):
    root, run = deployment
    entry = tarfile.TarInfo("infra/evil")
    entry.type = kind
    entry.linkname = "/etc/can/runtime.conf"
    with pytest.raises(ValueError):
        receiver.receive(f"release {SHA} {IMAGE} 10", io.BytesIO(archive(entry)), root)
    run.assert_not_called()


def test_archive_requires_all_runtime_files(deployment):
    root, run = deployment
    with pytest.raises(ValueError, match="incomplete"):
        receiver.receive(
            f"release {SHA} {IMAGE} 10", io.BytesIO(archive(omit="infra/compose.yaml")), root
        )
    run.assert_not_called()


def test_duplicate_files_and_oversized_archives_are_refused(deployment):
    root, run = deployment
    for payload in (
        archive(tarfile.TarInfo("infra/compose.yaml")),
        b"x" * (receiver.MAX_BYTES + 1),
    ):
        with pytest.raises(ValueError):
            receiver.receive(f"release {SHA} {IMAGE} 10", io.BytesIO(payload), root)
    run.assert_not_called()


@pytest.mark.parametrize(
    "command",
    [
        "bash",
        f"release {SHA} {IMAGE} 0",
        f"release {SHA} {IMAGE} 1;id",
        f"release {SHA} ghcr.io/other/api@sha256:{'b' * 64} 10",
    ],
)
def test_receiver_refuses_unexpected_commands_before_reading_input(deployment, command):
    root, run = deployment
    stream = Mock()
    with pytest.raises(ValueError):
        receiver.receive(command, stream, root)
    stream.read.assert_not_called()
    run.assert_not_called()


def test_exact_retry_reuses_immutable_files_and_propagates_exit(deployment, monkeypatch):
    root, run = deployment
    monkeypatch.setenv("CAN_IMAGE", "untrusted-inherited-value")
    command = f"release {SHA} {IMAGE} 10"
    assert receiver.receive(command, io.BytesIO(archive()), root) == 0
    run.return_value.returncode = 74
    assert receiver.receive(command, io.BytesIO(archive()), root) == 74
    assert run.call_count == 2
    assert run.call_args.args[0] == [
        "bash",
        str(root / f"releases/{SHA}/infra/scripts/deploy-release.sh"),
        SHA,
        IMAGE,
        "10",
    ]
    assert "CAN_IMAGE" not in run.call_args.kwargs["env"]
    assert run.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert (root / f"releases/{SHA}/infra/compose.yaml").stat().st_mode & 0o022 == 0


def test_existing_revision_cannot_be_replaced(deployment):
    root, run = deployment
    command = f"release {SHA} {IMAGE} 10"
    receiver.receive(command, io.BytesIO(archive()), root)
    run.reset_mock()
    with pytest.raises(ValueError, match="different release files"):
        receiver.receive(command, io.BytesIO(archive(content=b"altered")), root)
    run.assert_not_called()
    assert (root / f"releases/{SHA}/infra/compose.yaml").read_bytes() == b"reviewed source"


def test_destination_symlink_is_refused(deployment, tmp_path):
    root, run = deployment
    (root / f"releases/{SHA}").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        receiver.receive(f"release {SHA} {IMAGE} 10", io.BytesIO(archive()), root)
    run.assert_not_called()


def test_source_must_be_durable_before_running_deployer(deployment, monkeypatch):
    root, run = deployment

    def fail_sync(path):
        raise OSError("synthetic filesystem failure")

    monkeypatch.setattr(receiver, "sync_tree", fail_sync)
    with pytest.raises(OSError):
        receiver.receive(f"release {SHA} {IMAGE} 10", io.BytesIO(archive()), root)
    run.assert_not_called()
    assert not (root / f"releases/{SHA}").exists()


@pytest.mark.parametrize("revision", [SHA, "c" * 40])
def test_head_gate_accepts_only_current_main_revision(monkeypatch, revision):
    for key, value in {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "can-budget/budget",
        "GITHUB_SHA": SHA,
        "GH_TOKEN": "synthetic-test-value",
    }.items():
        monkeypatch.setenv(key, value)
    response = io.BytesIO(('{"object":{"sha":"' + revision + '"}}').encode())
    monkeypatch.setattr(head.urllib.request, "urlopen", lambda *args, **kwargs: response)
    if revision == SHA:
        head.main()
    else:
        with pytest.raises(SystemExit, match="superseded"):
            head.main()


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REF", "refs/heads/dev"),
        ("GITHUB_REPOSITORY", "other/budget"),
    ],
)
def test_head_gate_refuses_nonproduction_context_before_network(monkeypatch, key, value):
    for env_key, env_value in {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "can-budget/budget",
    }.items():
        monkeypatch.setenv(env_key, env_value)
    monkeypatch.setenv(key, value)
    network = Mock()
    monkeypatch.setattr(head.urllib.request, "urlopen", network)
    with pytest.raises(SystemExit):
        head.main()
    network.assert_not_called()


def test_send_transport_refuses_shell_content_without_ssh(tmp_path):
    result = subprocess.run(
        ["bash", str(ROOT / "infra/scripts/send-release.sh")],
        env={
            **os.environ,
            "CAN_DEPLOY_HOST": "host;id",
            "CAN_DEPLOY_USER": "can-operator",
            "CAN_DEPLOY_SSH_KEY": "synthetic-test-value",
            "CAN_DEPLOY_KNOWN_HOSTS": "synthetic-test-value",
            "CAN_IMAGE": IMAGE,
            "GITHUB_SHA": SHA,
            "GITHUB_RUN_NUMBER": "10",
        },
        cwd=tmp_path,
        capture_output=True,
    )
    assert result.returncode == 64
    assert b"synthetic-test-value" not in result.stdout + result.stderr
