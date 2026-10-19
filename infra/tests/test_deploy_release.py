"""Deployment ordering and failure containment using fake Docker/backup commands."""

import fcntl
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "deploy_release", Path(__file__).parents[1] / "scripts/deploy-release.py"
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

OLD_SHA = "1" * 40
NEW_SHA = "2" * 40
IMAGE = "ghcr.io/can-budget/budget-api@sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64


class FakeCommands:
    def __init__(self):
        self.calls = []
        self.fail_stage = None
        self.revision = NEW_SHA
        self.candidate_mutation = None
        self.started = False
        self.unhealthy_service = None
        self.wrong_image = False
        self.platform = "linux/amd64"
        self.architecture = "amd64"
        self.active_migration = False
        self.fail_stop_migration = False

    def run(self, args, env, stage, timeout=30):
        self.calls.append((stage, args[:], env.copy()))
        if stage == "forward schema migration":
            self.active_migration = True
        if stage == "contain unfinished migration":
            if self.fail_stop_migration:
                raise release.DeployError("simulated daemon unavailable")
            self.active_migration = False
        if stage == self.fail_stage:
            raise release.DeployError("simulated command failure")
        if stage == "verify Docker platform":
            return self.platform
        if stage == "check for unfinished migration":
            return "e" * 64 if self.active_migration else ""
        if stage == "verify public HTTPS readiness":
            return '{"status":"ok"}'
        if stage == "forward schema migration":
            self.active_migration = False
        if stage.startswith("validate"):
            root = env["CAN_REPO_DIR"]
            result = {
                "services": {
                    "db": {
                        "image": "postgres:18.6-bookworm",
                        "volumes": [
                            {"source": root + "/infra/postgres/init.sql", "target": "/init.sql"}
                        ],
                    },
                    "caddy": {
                        "image": "caddy:2.11.7-alpine",
                        "volumes": [{"source": root + "/infra/caddy", "target": "/etc/caddy"}],
                    },
                    "api": {"image": env["CAN_IMAGE"]},
                    "worker": {"image": env["CAN_IMAGE"]},
                    "migrate": {"image": env["CAN_IMAGE"]},
                },
                "networks": {"data": {"internal": True}},
                "volumes": {"postgres_data": {}},
                "secrets": {"encryption_key": {"file": "/owner/encryption_key"}},
            }
            # Real Compose excludes inactive profile services from `config`.
            if "--profile" not in args:
                del result["services"]["migrate"]
            if stage == "validate candidate Compose" and self.candidate_mutation:
                self.candidate_mutation(result)
            return json.dumps(result)
        if stage.startswith("locate "):
            return {"api": "a", "worker": "b", "db": "c", "caddy": "d"}[stage.split()[-1]] * 64
        if stage.startswith("inspect "):
            service = stage.split()[-1]
            healthy = not (self.started and service == self.unhealthy_service)
            return json.dumps(
                {
                    "State": {
                        "Running": healthy,
                        "Health": {"Status": "healthy" if healthy else "unhealthy"},
                    },
                    "Image": "sha256:" + "c" * 64 if self.wrong_image else IMAGE_ID,
                }
            )
        if stage == "verify image revision":
            return json.dumps(
                {
                    "Id": IMAGE_ID,
                    "Os": "linux",
                    "Architecture": self.architecture,
                    "Config": {"Labels": {"org.opencontainers.image.revision": self.revision}},
                }
            )
        if stage == "start and wait for backend health":
            self.started = True
        return ""


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "can"
    current = root / "releases" / OLD_SHA
    candidate = root / "releases" / NEW_SHA
    for directory in (current, candidate):
        for relative in (
            "infra/compose.yaml",
            "infra/scripts/backup.sh",
            "infra/deletion_journal.py",
            "infra/caddy/Caddyfile",
            "infra/postgres/init.sql",
        ):
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("reviewed source\n")
            path.chmod(0o644)
    (root / "current").symlink_to(current, target_is_directory=True)
    runtime = {
        "CAN_DOMAIN": "api.example.invalid",
        "CAN_BACKUP_REMOTE": "owner:backups",
        "CAN_DELETION_JOURNAL_REMOTE": "owner:deletions",
    }
    for key in ("CAN_SECRETS_DIR", "CAN_DELETION_JOURNAL_DIR", "CAN_BACKUP_DIR"):
        path = tmp_path / key.lower()
        path.mkdir()
        runtime[key] = str(path)
    for key in ("CAN_BACKUP_RECIPIENTS_FILE", "CAN_RCLONE_CONFIG_FILE", "CAN_ENCRYPTION_KEY_FILE"):
        path = tmp_path / key.lower()
        path.write_text("opaque test fixture file")
        runtime[key] = str(path)
    commands = FakeCommands()
    return SimpleNamespace(
        root=root, current=current, candidate=candidate, runtime=runtime, commands=commands
    )


def invoke(setup, sequence="10", image=IMAGE, sha=NEW_SHA):
    return release.deploy(
        sha,
        image,
        sequence,
        candidate=setup.candidate,
        root=setup.root,
        runtime=setup.runtime,
        commands=setup.commands,
    )


def stages(setup):
    return [stage for stage, _, _ in setup.commands.calls]


def assert_old_release(setup):
    assert (setup.root / "current").resolve() == setup.current
    assert not (setup.root / "state/last-success.json").exists()


def test_success_backs_up_before_migration_and_promotes_only_healthy_image(setup):
    result = invoke(setup)
    calls = stages(setup)
    ordered = [
        "verify image revision",
        "verified encrypted off-host backup",
        "stop backend writes",
        "forward schema migration",
        "start and wait for backend health",
    ]
    assert [calls.index(stage) for stage in ordered] == sorted(
        calls.index(stage) for stage in ordered
    )
    assert calls[-5:] == [
        "locate api",
        "inspect api",
        "locate worker",
        "inspect worker",
        "verify public HTTPS readiness",
    ]
    backup = next(call for call in setup.commands.calls if call[0] == ordered[1])
    assert backup[1] == ["bash", str(setup.current / "infra/scripts/backup.sh")]
    assert backup[2]["CAN_REPO_DIR"] == str(setup.current)
    assert (setup.root / "current").resolve() == setup.candidate
    assert json.loads((setup.root / "state/last-success.json").read_text()) == result
    assert json.loads((setup.root / "state/high-water.json").read_text())["run_number"] == 10
    migration = next(
        args for stage, args, _ in setup.commands.calls if stage == "forward schema migration"
    )
    startup = next(
        args
        for stage, args, _ in setup.commands.calls
        if stage == "start and wait for backend health"
    )
    assert "--no-deps" in migration and migration[-1] == "migrate"
    assert "--no-deps" in startup and "--no-build" in startup and startup[-2:] == ["api", "worker"]
    assert not any(
        "down" in args or "--volumes" in args or "downgrade" in args
        for _, args, _ in setup.commands.calls
    )


def test_explicit_single_server_deploy_needs_no_backup_storage_or_backup_commands(setup):
    setup.runtime["CAN_RECOVERY_MODE"] = "single_server"
    for key in (
        "CAN_BACKUP_REMOTE",
        "CAN_DELETION_JOURNAL_REMOTE",
        "CAN_BACKUP_DIR",
        "CAN_BACKUP_RECIPIENTS_FILE",
        "CAN_RCLONE_CONFIG_FILE",
    ):
        setup.runtime.pop(key)
    result = invoke(setup)
    assert result["recovery_mode"] == "single_server"
    assert "verified encrypted off-host backup" not in stages(setup)
    assert "forward schema migration" in stages(setup)
    assert stages(setup)[-1] == "verify public HTTPS readiness"


def test_invalid_recovery_mode_stops_before_any_deployment_command(setup):
    setup.runtime["CAN_RECOVERY_MODE"] = "disabled"
    with pytest.raises(release.DeployError, match="Invalid recovery mode"):
        invoke(setup)
    assert not setup.commands.calls


def test_profiled_migration_image_is_validated_before_pulling_or_stopping_services(setup):
    def change_migration(config):
        config["services"]["migrate"]["image"] = "can-api:unreviewed"

    setup.commands.candidate_mutation = change_migration
    with pytest.raises(release.DeployError, match="one immutable image"):
        invoke(setup)
    assert "pull reviewed image" not in stages(setup)
    assert "stop backend writes" not in stages(setup)


@pytest.mark.parametrize(
    "stage",
    [
        "pull reviewed image",
        "verify image revision",
        "smoke candidate runtime",
        "verified encrypted off-host backup",
    ],
)
def test_pre_stop_failure_preserves_running_release(setup, stage):
    setup.commands.fail_stage = stage
    with pytest.raises(release.DeployError):
        invoke(setup)
    assert "stop backend writes" not in stages(setup)
    assert "forward schema migration" not in stages(setup)
    assert_old_release(setup)


@pytest.mark.parametrize(
    "stage",
    [
        "stop backend writes",
        "forward schema migration",
        "start and wait for backend health",
        "verify public HTTPS readiness",
    ],
)
def test_post_stop_failure_never_promotes_or_automatically_rolls_back_sql(setup, stage):
    setup.commands.fail_stage = stage
    with pytest.raises(release.DeployError, match="No SQL/image rollback"):
        invoke(setup)
    assert stages(setup)[-1] == "contain failed deployment"
    assert_old_release(setup)
    if stage == "forward schema migration":
        assert "start and wait for backend health" not in stages(setup)
    assert not any(
        "restore" in " ".join(args) or "down" in args for _, args, _ in setup.commands.calls
    )


@pytest.mark.parametrize("service", ["api", "worker"])
def test_wait_success_is_not_enough_without_container_health(setup, service):
    setup.commands.unhealthy_service = service
    with pytest.raises(release.DeployError):
        invoke(setup)
    assert_old_release(setup)
    assert stages(setup)[-1] == "contain failed deployment"


def test_wrong_running_image_cannot_be_recorded_as_success(setup):
    setup.commands.wrong_image = True
    with pytest.raises(release.DeployError):
        invoke(setup)
    assert_old_release(setup)


def test_image_source_label_mismatch_blocks_backup_and_restart(setup):
    setup.commands.revision = OLD_SHA
    with pytest.raises(release.DeployError, match="revision label"):
        invoke(setup)
    assert "verified encrypted off-host backup" not in stages(setup)
    assert_old_release(setup)


@pytest.mark.parametrize(
    "sha,image,counter",
    [
        ("../escape", IMAGE, "10"),
        ("A" * 40, IMAGE, "10"),
        (NEW_SHA, "ghcr.io/can-budget/budget-api:latest", "10"),
        (NEW_SHA, "ghcr.io/another/repo@sha256:" + "a" * 64, "10"),
        (NEW_SHA, IMAGE + ";anything", "10"),
        (NEW_SHA, IMAGE, "-1"),
        (NEW_SHA, IMAGE, "1;anything"),
        (NEW_SHA, IMAGE, "01"),
    ],
)
def test_invalid_identity_never_runs_a_command(setup, sha, image, counter):
    with pytest.raises(release.DeployError):
        invoke(setup, sha=sha, image=image, sequence=counter)
    assert not setup.commands.calls
    assert_old_release(setup)


def test_high_water_blocks_late_old_run_after_newer_backup_failure(setup):
    setup.commands.fail_stage = "verified encrypted off-host backup"
    with pytest.raises(release.DeployError):
        invoke(setup, sequence="20")
    setup.commands.calls.clear()
    with pytest.raises(release.DeployError, match="newer workflow"):
        invoke(setup, sequence="19")
    assert not setup.commands.calls
    assert_old_release(setup)


def test_exact_same_run_retry_allowed_but_same_counter_other_digest_rejected(setup):
    setup.commands.fail_stage = "verified encrypted off-host backup"
    with pytest.raises(release.DeployError):
        invoke(setup)
    setup.commands.calls.clear()
    with pytest.raises(release.DeployError, match="different release contents"):
        invoke(setup, image=IMAGE[:-1] + "b")
    assert not setup.commands.calls
    setup.commands.fail_stage = None
    assert invoke(setup)["run_number"] == 10


def test_production_lock_prevents_concurrent_release(setup):
    directory = setup.root / "state"
    directory.mkdir()
    with (directory / "deploy.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(release.DeployError, match="Another deployment"):
            invoke(setup)
    assert not setup.commands.calls


@pytest.mark.parametrize("section", ["db", "caddy", "networks", "volumes", "secrets"])
def test_infrastructure_change_is_not_silently_ignored_by_backend_deploy(setup, section):
    def mutate(config):
        if section in ("db", "caddy"):
            config["services"][section]["image"] += "-changed"
        else:
            config[section]["unexpected"] = {}

    setup.commands.candidate_mutation = mutate
    with pytest.raises(release.DeployError, match="maintenance"):
        invoke(setup)
    assert "pull reviewed image" not in stages(setup)
    assert_old_release(setup)


@pytest.mark.parametrize("relative", ["infra/caddy/Caddyfile", "infra/postgres/init.sql"])
def test_changed_mounted_source_requires_separate_owner_maintenance(setup, relative):
    (setup.candidate / relative).write_text("changed infrastructure")
    with pytest.raises(release.DeployError, match="maintenance"):
        invoke(setup)
    assert_old_release(setup)


def test_absent_bootstrap_never_creates_database_or_services(setup):
    (setup.root / "current").unlink()
    with pytest.raises(release.DeployError, match="bootstrap"):
        invoke(setup)
    assert not setup.commands.calls


def test_symlink_or_writable_release_input_is_rejected(setup, tmp_path):
    target = setup.candidate / "infra/compose.yaml"
    target.chmod(0o666)
    with pytest.raises(release.DeployError, match="non-writable"):
        invoke(setup)
    target.unlink()
    other = tmp_path / "other"
    other.write_text("not part of this release")
    target.symlink_to(other)
    with pytest.raises(release.DeployError, match="symlink"):
        invoke(setup)
    assert not setup.commands.calls


def test_runtime_loader_accepts_only_literal_owner_configuration(tmp_path):
    path = tmp_path / "runtime.conf"
    path.write_text(
        'CAN_DOMAIN=api.example.invalid\nCAN_BACKUP_DIR="/private/backups"\n'
        'DOCKER_CONFIG="/private/registry"\nPYTHONDONTWRITEBYTECODE=1\n# comment\n'
    )
    path.chmod(0o600)
    assert release.load_runtime(path, owner=os.getuid()) == {
        "CAN_DOMAIN": "api.example.invalid",
        "CAN_BACKUP_DIR": "/private/backups",
        "DOCKER_CONFIG": "/private/registry",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for content in (
        "CAN_DOMAIN=$(anything)",
        "PATH=/untrusted",
        "CAN_DOMAIN=a\nCAN_DOMAIN=b",
        "source other.conf",
        "PYTHONDONTWRITEBYTECODE=0",
    ):
        path.write_text(content)
        with pytest.raises(release.DeployError):
            release.load_runtime(path, owner=os.getuid())
    path.write_text("CAN_DOMAIN=api.example.invalid")
    path.chmod(0o666)
    with pytest.raises(release.DeployError):
        release.load_runtime(path, owner=os.getuid())


def test_environment_does_not_forward_client_docker_or_python_overrides(setup, monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "untrusted")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    monkeypatch.setenv("COMPOSE_FILE", "/untrusted")
    result = release.environment(
        setup.runtime, setup.candidate, release.validate_identity(NEW_SHA, IMAGE, "10")
    )
    assert not {"DOCKER_HOST", "PYTHONPATH", "COMPOSE_FILE"} & result.keys()
    assert result["CAN_IMAGE"] == IMAGE


def test_runtime_accepts_provider_file_paths_but_rejects_inline_credentials(tmp_path):
    path = tmp_path / "runtime.conf"
    path.write_text(
        "CAN_APPLE_PRIVATE_KEY_FILE=/run/provider-secrets/apple_private_key\n"
        "CAN_APNS_PRIVATE_KEY_FILE=/run/provider-secrets/apns_private_key\n"
    )
    path.chmod(0o600)
    parsed = release.load_runtime(path, owner=os.getuid())
    assert parsed["CAN_APPLE_PRIVATE_KEY_FILE"] == "/run/provider-secrets/apple_private_key"
    assert parsed["CAN_APNS_PRIVATE_KEY_FILE"] == "/run/provider-secrets/apns_private_key"
    for key in ("CAN_APPLE_PRIVATE_KEY", "CAN_APNS_PRIVATE_KEY"):
        path.write_text(f"{key}=test-only-value\n")
        with pytest.raises(release.DeployError):
            release.load_runtime(path, owner=os.getuid())


def test_private_subprocess_output_never_appears_in_deploy_error(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=1, stdout="PRIVATE-SUBPROCESS-DETAIL", stderr="PRIVATE-SUBPROCESS-DETAIL"
        )

    monkeypatch.setattr(release.subprocess, "run", fake_run)
    with pytest.raises(release.DeployError) as error:
        release.Commands().run(["docker", "info"], {}, "preflight")
    assert "PRIVATE-SUBPROCESS-DETAIL" not in str(error.value)


def test_successful_exact_retry_replays_receipt_without_backup_or_restart(setup):
    completed = invoke(setup)
    setup.commands.calls.clear()
    setup.commands.fail_stage = "forward schema migration"
    assert invoke(setup) == completed
    assert "verified encrypted off-host backup" not in stages(setup)
    assert "stop backend writes" not in stages(setup)
    assert "forward schema migration" not in stages(setup)
    assert json.loads((setup.root / "state/last-success.json").read_text()) == completed


@pytest.mark.parametrize("component", ["platform", "architecture"])
def test_incompatible_platform_stops_before_backup_or_migration(setup, component):
    setattr(setup.commands, component, "linux/arm64" if component == "platform" else "arm64")
    with pytest.raises(release.DeployError, match="amd64"):
        invoke(setup)
    assert "verified encrypted off-host backup" not in stages(setup)
    assert "stop backend writes" not in stages(setup)
    assert_old_release(setup)


@pytest.mark.parametrize(
    "domain",
    [
        "https://api.example.invalid",
        "api.example.invalid:443",
        "api.example.invalid/path",
        "a@b.invalid",
        "api.example.invalid?key=value",
        "https://151.243.217.6",
        "151.243.217.6:443",
        "151.243.217.6/path",
        "owner@151.243.217.6",
        "151.243.217.6?key=value",
        "151.243.217.6#fragment",
        "151.243.217.6\\path",
        "151.243.217.6\n",
    ],
)
def test_domain_cannot_inject_url_components(setup, domain):
    setup.runtime["CAN_DOMAIN"] = domain
    with pytest.raises(release.DeployError, match="DNS hostname"):
        invoke(setup)
    assert not setup.commands.calls


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        "198.18.0.1",
        "224.0.0.1",
        "239.255.255.255",
        "240.0.0.1",
        "255.255.255.255",
    ],
)
def test_nonpublic_or_nonunicast_ipv4_stops_before_commands_or_state_changes(setup, host):
    setup.runtime["CAN_DOMAIN"] = host
    with pytest.raises(release.DeployError, match="public and unicast"):
        invoke(setup)
    assert not setup.commands.calls
    assert not (setup.root / "state").exists()
    assert_old_release(setup)


@pytest.mark.parametrize(
    "host",
    [
        "151.243.217.006",
        "151.243.217.6.",
        "151.243.55558",
        "2549348614",
        "0x97f3d906",
        "0x97.0xf3.0xd9.0x6",
        "0227.0363.0331.06",
        "151.243.217.256",
        "127.1",
    ],
)
def test_ambiguous_numeric_hosts_are_never_treated_as_dns(setup, host):
    setup.runtime["CAN_DOMAIN"] = host
    with pytest.raises(release.DeployError, match="canonical public IPv4"):
        invoke(setup)
    assert not setup.commands.calls
    assert_old_release(setup)


@pytest.mark.parametrize("host", ["2001:4860:4860::8888", "[2001:4860:4860::8888]", "::1"])
def test_ipv6_requires_explicit_support_before_deployment(setup, host):
    setup.runtime["CAN_DOMAIN"] = host
    with pytest.raises(release.DeployError, match="does not support IPv6"):
        invoke(setup)
    assert not setup.commands.calls


@pytest.mark.parametrize("host", ["151.243.217.6", "budget-api.example.com"])
def test_public_endpoint_deploys_using_verified_https_without_url_rewriting(setup, host):
    setup.runtime["CAN_DOMAIN"] = host
    invoke(setup)
    checks = [
        (args, env)
        for stage, args, env in setup.commands.calls
        if stage == "verify public HTTPS readiness"
    ]
    assert checks
    for args, env in checks:
        assert args[-1] == "https://" + host + "/health/ready"
        assert args[args.index("--proto") + 1] == "=https"
        assert not {"--insecure", "-k", "--location", "-L"} & set(args)
        assert env["CAN_DOMAIN"] == host
    assert (setup.root / "current").resolve() == setup.candidate


def test_https_check_retries_caddy_transient_503_with_bounded_timeout(setup):
    invoke(setup)
    args = next(
        args for stage, args, _ in setup.commands.calls if stage == "verify public HTTPS readiness"
    )
    for flag, expected in (
        ("--retry", "6"),
        ("--retry-delay", "5"),
        ("--retry-max-time", "65"),
        ("--max-time", "10"),
    ):
        assert args[args.index(flag) + 1] == expected
    assert args[-1] == "https://api.example.invalid/health/ready"


def test_migration_timeout_with_daemon_alive_blocks_a_second_migration(setup):
    setup.commands.fail_stage = "forward schema migration"
    setup.commands.fail_stop_migration = True
    with pytest.raises(release.DeployError, match="No SQL/image rollback"):
        invoke(setup)
    assert setup.commands.active_migration
    assert "contain unfinished migration" in stages(setup)
    setup.commands.calls.clear()
    setup.commands.fail_stage = None
    with pytest.raises(release.DeployError, match="prior migration container"):
        invoke(setup)
    assert "forward schema migration" not in stages(setup)
    assert "stop backend writes" not in stages(setup)
    assert "pull reviewed image" not in stages(setup)
    assert_old_release(setup)


def test_existing_manual_migration_refuses_a_deployment(setup):
    setup.commands.active_migration = True
    with pytest.raises(release.DeployError, match="prior migration container"):
        invoke(setup)
    assert "verified encrypted off-host backup" not in stages(setup)


def test_unhealthy_success_receipt_retry_does_not_restart_or_rewrite_receipt(setup):
    completed = invoke(setup)
    setup.commands.calls.clear()
    setup.commands.unhealthy_service = "api"
    with pytest.raises(release.DeployError, match="not healthy"):
        invoke(setup)
    assert "stop backend writes" not in stages(setup)
    assert "contain failed deployment" not in stages(setup)
    assert json.loads((setup.root / "state/last-success.json").read_text()) == completed


@pytest.mark.parametrize("prior_success", [False, True])
def test_partial_promotion_requires_reconciliation_before_retry(setup, monkeypatch, prior_success):
    state = setup.root / "state"
    state.mkdir()
    if prior_success:
        release.atomic_json(
            state / "last-success.json", release.validate_identity(OLD_SHA, IMAGE, "9")
        )
    original = release.atomic_json

    def crash_success_receipt(path, value):
        if path.name == "last-success.json":
            raise OSError("simulated durable write failure")
        return original(path, value)

    monkeypatch.setattr(release, "atomic_json", crash_success_receipt)
    with pytest.raises(release.DeployError):
        invoke(setup)
    assert (setup.root / "current").resolve() == setup.candidate
    setup.commands.calls.clear()
    monkeypatch.setattr(release, "atomic_json", original)
    with pytest.raises(release.DeployError, match="reconciliation"):
        invoke(setup)
    assert not setup.commands.calls


def test_orphaned_promotion_temporary_path_does_not_poison_next_success(setup):
    orphan = setup.root / ".promote-crashed"
    orphan.mkdir()
    (orphan / "current").symlink_to(setup.candidate)
    assert invoke(setup)["source_sha"] == NEW_SHA
