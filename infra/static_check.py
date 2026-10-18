"""Validate deploy configuration without contacting Docker or loading secret files."""

import ast
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    compose = yaml.safe_load((ROOT / "infra/compose.yaml").read_text())
    restore = yaml.safe_load((ROOT / "infra/compose.restore.yaml").read_text())
    ingress = yaml.safe_load((ROOT / "infra/compose.ingress.yaml").read_text())
    workflow = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
    assert compose["name"] == "can-production"
    assert compose["networks"]["data"]["internal"] is True
    assert set(compose["services"]) == {"db", "api", "worker", "migrate", "caddy"}
    assert ingress["name"] == compose["name"]
    assert set(ingress["services"]) == {"caddy"}
    # Bootstrap must reuse the exact production ingress and persistent storage.
    production_caddy = dict(compose["services"]["caddy"])
    production_caddy.pop("depends_on")
    assert ingress["services"]["caddy"] == production_caddy
    assert ingress["networks"] == {"edge": compose["networks"]["edge"]}
    assert ingress["volumes"] == {
        key: compose["volumes"][key] for key in ("caddy_data", "caddy_config")
    }
    for name, service in compose["services"].items():
        assert not service.get("privileged")
        if name != "caddy":
            assert "ports" not in service, f"Unexpected public port on {name}"
    assert compose["services"]["db"]["networks"] == ["data"]
    assert "postgres_data:/var/lib/postgresql" in compose["services"]["db"]["volumes"]
    for name in ("api", "worker"):
        service = compose["services"][name]
        assert service["read_only"] is True and service["cap_drop"] == ["ALL"]
        assert "healthcheck" in service
        for key, value in service["environment"].items():
            if any(
                token in key for token in ("PASSWORD", "ENCRYPTION_KEY", "HMAC_KEY", "PRIVATE_KEY")
            ):
                assert key.endswith("_FILE") and str(value).startswith("/run/secrets/")
        journal_mounts = [mount for mount in service["volumes"] if "can-deletions" in str(mount)]
        assert len(journal_mounts) == 1
        assert str(journal_mounts[0]).endswith(":ro") is (name == "api")
    for secret in compose["secrets"].values():
        assert set(secret) == {"file"} and "${CAN_SECRETS_DIR:?" in secret["file"]
    assert set(restore["services"]) == {"restore-db"}
    assert restore["networks"]["isolated"]["internal"] is True
    assert "ports" not in restore["services"]["restore-db"]
    assert restore["volumes"].keys().isdisjoint(compose["volumes"])
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}"
    publish = workflow["jobs"]["publish"]
    deploy = workflow["jobs"]["deploy"]
    assert set(publish["needs"]) == {"backend", "postgres", "ios"}
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    assert deploy["needs"] == "publish"
    assert deploy["permissions"] == {"contents": "read"}
    assert deploy["environment"] == "production"
    for job in (publish, deploy):
        assert job["if"] == (
            "github.event_name == 'push' && github.ref == 'refs/heads/main' "
            "&& github.repository == 'can-budget/budget'"
        )
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
    shell_files = sorted((ROOT / "infra/scripts").glob("*.sh"))
    for script in shell_files:
        subprocess.run(["bash", "-n", str(script)], check=True)
    ast.parse((ROOT / "infra/verify_restore.py").read_text())
    ast.parse((ROOT / "infra/deletion_journal.py").read_text())
    for script in (ROOT / "infra/scripts").glob("*.py"):
        ast.parse(script.read_text())
    recovery = (ROOT / "infra/scripts/restore.sh").read_text()
    assert recovery.index('deletion_journal.py" fetch') < recovery.index('"${compose[@]}" up')
    assert "export-pending" in recovery and 'deletion_journal.py" replicate' in recovery
    verify = (ROOT / "infra/scripts/verify-restore.sh").read_text()
    assert verify.index('" check') < verify.index("docker run")
    print(
        f"PASS: 3 Compose files, CI/CD policy, {len(shell_files)} shell scripts, infrastructure Python syntax"
    )
    print("NOT RUN: Docker image build, Compose schema/runtime, HTTPS, PostgreSQL, backup/restore.")


if __name__ == "__main__":
    main()
