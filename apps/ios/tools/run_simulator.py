"""Build a dedicated iPhone Simulator and import an eight-hour owner-issued test session."""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
TOOLS = Path(__file__).resolve().parent
BUNDLE = "com.canbudget.simulator"
CACHE = Path.home() / "Library/Application Support/CanSimulator"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def read_json(command: list[str]) -> dict:
    return json.loads(run(command, stdout=subprocess.PIPE).stdout)


def api_origin() -> str:
    config = (ROOT / "apps/ios/Config/Owner.xcconfig").read_text()
    match = re.search(r"^CAN_API_BASE_URL\s*=\s*(\S+)\s*$", config, re.MULTILINE)
    if match is None:
        raise ValueError("CAN_API_BASE_URL is missing")
    origin = match[1].replace("$()", "")
    url = urlsplit(origin)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
    ):
        raise ValueError("The configured API must be an HTTPS origin")
    return origin


def account_directory(name: str) -> Path:
    # A runtime upgrade may replace the simulator UUID; retain the named test account.
    return CACHE / "accounts" / hashlib.sha256(name.encode()).hexdigest()[:24]


def device(name: str) -> str:
    runtimes = read_json(["xcrun", "simctl", "list", "runtimes", "-j"])["runtimes"]
    available = [r for r in runtimes if r.get("isAvailable") and r["name"].startswith("iOS ")]
    if not available:
        raise ValueError("Install iOS in Xcode > Settings > Components, then run make ios-simulator again.")
    runtime = max(available, key=lambda r: tuple(map(int, r["version"].split("."))))
    devices = read_json(["xcrun", "simctl", "list", "devices", "available", "-j"])["devices"]
    existing = next((d for d in devices.get(runtime["identifier"], []) if d["name"] == name), None)
    if existing:
        return existing["udid"]
    types = read_json(["xcrun", "simctl", "list", "devicetypes", "-j"])["devicetypes"]
    model = next(t for t in types if t["name"] == "iPhone 17 Pro")
    return run(
        ["xcrun", "simctl", "create", name, model["identifier"], runtime["identifier"]],
        stdout=subprocess.PIPE,
    ).stdout.strip()


def build(destination: str, directory: Path) -> Path:
    log = directory / "build.log"
    print("Building the Debug Simulator app…", flush=True)
    with log.open("w") as output:
        try:
            run(
                [
                    "xcodebuild", "-quiet", "-project", str(ROOT / "apps/ios/Can.xcodeproj"),
                    "-scheme", "Can", "-configuration", "Debug", "-sdk", "iphonesimulator",
                    "-destination", f"platform=iOS Simulator,id={destination}",
                    "-derivedDataPath", str(CACHE / "DerivedData"),
                    "CODE_SIGN_IDENTITY=-", "CODE_SIGNING_ALLOWED=YES", "CODE_SIGN_STYLE=Manual",
                    "DEVELOPMENT_TEAM=", "CAN_DEVELOPMENT_TEAM=", "AppIdentifierPrefix=SIMULATOR.",
                    f"CAN_BUNDLE_ID={BUNDLE}", f"CAN_WIDGET_BUNDLE_ID={BUNDLE}.widget",
                    f"CAN_APP_GROUP=group.{BUNDLE}", f"CAN_KEYCHAIN_GROUP={BUNDLE}.shared", "build",
                ],
                stdout=output, stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError:
            print("\n".join(log.read_text().splitlines()[-30:]), file=sys.stderr)
            raise
    return CACHE / "DerivedData/Build/Products/Debug-iphonesimulator/Can.app"


def issue_session(host: str, container: str, subject: str, origin: str) -> dict:
    remote = shlex.join(
        ["docker", "exec", "-i", container, "python", "-", "--subject", subject, "--api-origin", origin]
    )
    result = run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
         "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2", host, remote],
        input=(TOOLS / "issue_simulator_session.py").read_text(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    # Never print the response: it contains access and refresh credentials.
    payload = json.loads(result.stdout)
    if payload.get("api_origin") != origin or not payload.get("session", {}).get("access_token"):
        raise ValueError("The SSH operator returned an invalid session")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-name", default="Can Test iPhone")
    parser.add_argument("--ssh-host", default="budget")
    parser.add_argument("--container", default="can-production-api-1")
    parser.add_argument("--skip-build", action="store_true", help="Reuse the previously built Debug app")
    args = parser.parse_args()
    os.umask(0o077)
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CACHE, 0o700)
    origin = api_origin()
    directory = account_directory(args.device_name)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    identity_file = directory / "identity.json"
    if not identity_file.exists():
        identity_file.write_text(json.dumps({"subject": "can-simulator:" + str(uuid4()), "device_name": args.device_name}))
    subject = json.loads(identity_file.read_text())["subject"]
    target = device(args.device_name)
    app = CACHE / "DerivedData/Build/Products/Debug-iphonesimulator/Can.app" if args.skip_build else build(target, directory)
    if not app.is_dir():
        raise ValueError("No Simulator build exists; run without --skip-build")
    state = read_json(["xcrun", "simctl", "list", "devices", "-j"])["devices"]
    booted = any(d["udid"] == target and d["state"] == "Booted" for ds in state.values() for d in ds)
    if not booted:
        run(["xcrun", "simctl", "boot", target])
    run(["open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", target])
    run(["xcrun", "simctl", "bootstatus", target, "-b"])
    run(["xcrun", "simctl", "install", target, str(app)])
    # Stop before replacing credentials; keep the user's existing app data and outbox.
    subprocess.run(["xcrun", "simctl", "terminate", target, BUNDLE], capture_output=True)
    container = Path(run(["xcrun", "simctl", "get_app_container", target, BUNDLE, "data"], stdout=subprocess.PIPE).stdout.strip())
    inbox = container / "Library/Application Support/Can/simulator-session.json"
    inbox.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f"Issuing a separate eight-hour test session on {origin} through SSH…", flush=True)
    payload = issue_session(args.ssh_host, args.container, subject, origin)
    temporary = inbox.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(payload))
        os.chmod(temporary, 0o600)
        temporary.replace(inbox)
        run(["xcrun", "simctl", "launch", target, BUNDLE, "--can-import-simulator-session"])
        # Launch returning a PID does not mean SwiftUI has imported the credentials yet.
        deadline = time.monotonic() + 90
        while inbox.exists():
            if time.monotonic() >= deadline:
                raise ValueError("The app has not imported the session. Check its message in Simulator and retry.")
            time.sleep(1)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Opened {args.device_name}. Test access expires at {payload['expires_at']}.")
    print("Use make ios-simulator again to renew access; the test account and budget remain the same.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Simulator setup failed: {error}", file=sys.stderr)
        sys.exit(1)
