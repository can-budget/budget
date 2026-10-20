"""Manual local Ad Hoc archive, signing inspection and installation. Standard library only."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "build/ios-adhoc"
UTC = timezone.utc


class ReleaseError(Exception):
    pass


def run(args: list[str], log: Path | None = None) -> bytes:
    if log:
        with log.open("wb") as stream:
            result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ReleaseError(f"{args[0]} failed. See {log}")
        return b""
    result = subprocess.run(
        args, capture_output=True, env={**os.environ, "LC_ALL": "C"}
    )
    if result.returncode:
        raise ReleaseError(
            f"{args[0]} failed: {result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


def settings() -> dict[str, str]:
    path = ROOT / "apps/ios/Config/Owner.xcconfig"
    values = dict(re.findall(r"^(\w+)\s*=\s*(\S+)\s*$", path.read_text(), re.MULTILINE))
    keys = [
        "CAN_DEVELOPMENT_TEAM",
        "CAN_BUNDLE_ID",
        "CAN_WIDGET_BUNDLE_ID",
        "CAN_APP_GROUP",
        "CAN_KEYCHAIN_GROUP",
        "CAN_API_BASE_URL",
        "MARKETING_VERSION",
    ]
    for key in keys:
        value = values.get(key, "")
        if (
            not value
            or "YOUR_" in value
            or "yourcompany" in value
            or ".simulator" in value
        ):
            raise ReleaseError(
                f"Set {key} in {path}. See docs/IOS_DELIVERY.md; signing needs a paid Apple team."
            )
    if not re.fullmatch(r"[A-Z0-9]{10}", values["CAN_DEVELOPMENT_TEAM"]):
        raise ReleaseError(
            "CAN_DEVELOPMENT_TEAM must be the paid team's 10-character Team ID."
        )
    for key in ["CAN_BUNDLE_ID", "CAN_WIDGET_BUNDLE_ID", "CAN_KEYCHAIN_GROUP"]:
        if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", values[key]):
            raise ReleaseError(f"Invalid {key}; use a stable explicit identifier.")
    if not values["CAN_WIDGET_BUNDLE_ID"].startswith(values["CAN_BUNDLE_ID"] + "."):
        raise ReleaseError(
            "The widget bundle ID must be prefixed by the app's bundle ID."
        )
    if not values["CAN_APP_GROUP"].startswith("group."):
        raise ReleaseError("CAN_APP_GROUP must start with group.")
    origin = values["CAN_API_BASE_URL"].replace("$()", "")
    url = urlsplit(origin)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.hostname.endswith(".invalid")
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
    ):
        raise ReleaseError("CAN_API_BASE_URL must be a production HTTPS origin.")
    return values


def doctor() -> dict[str, str]:
    if sys.platform != "darwin":
        raise ReleaseError(
            "Ad Hoc release/install requires a Mac with full Xcode 26 or later."
        )
    version = run(["xcodebuild", "-version"]).decode().strip()
    print(version)
    if not re.search(r"Xcode (?:2[6-9]|[3-9]\d)\.", version):
        raise ReleaseError("Select full Xcode 26 or later with xcode-select.")
    run(["xcrun", "--find", "devicectl"])
    identities = run(["security", "find-identity", "-v", "-p", "codesigning"]).decode()
    count = re.search(r"(\d+) valid identities found", identities)
    print(f"Valid local signing identities: {count.group(1) if count else 'unknown'}")
    config = settings()
    if not count or int(count.group(1)) == 0:
        raise ReleaseError(
            "No signing identity. Add your paid Apple account in Xcode Settings > Accounts and configure signing for Can and CanWidget."
        )
    print(
        f"Paid Team ID: {config['CAN_DEVELOPMENT_TEAM']}; app: {config['CAN_BUNDLE_ID']}"
    )
    print(
        "Local prerequisites found. Apple membership, profiles and registered devices are checked by Xcode during signing."
    )
    return config


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def validate_profile(
    profile: dict, rights: dict, config: dict, bundle: str, now: datetime
) -> dict:
    allowed = profile.get("Entitlements", {})
    team = config["CAN_DEVELOPMENT_TEAM"]
    devices = profile.get("ProvisionedDevices", [])
    if (
        not isinstance(devices, list)
        or not devices
        or not all(isinstance(d, str) and d for d in devices)
        or profile.get("ProvisionsAllDevices")
        or profile.get("LocalProvision")
        or allowed.get("get-task-allow") is not False
        or rights.get("get-task-allow", False)
    ):
        raise ReleaseError(
            "Expected an Ad Hoc distribution profile, not Personal Team, Development, Enterprise or App Store signing."
        )
    if (
        team not in profile.get("TeamIdentifier", [])
        or rights.get("com.apple.developer.team-identifier") != team
    ):
        raise ReleaseError(f"Signing team mismatch for {bundle}.")
    prefixes = profile.get("ApplicationIdentifierPrefix", [])
    if len(prefixes) != 1:
        raise ReleaseError(f"Invalid App ID prefix for {bundle}.")
    prefix = prefixes[0] + "."
    if (
        allowed.get("application-identifier") != prefix + bundle
        or rights.get("application-identifier") != prefix + bundle
    ):
        raise ReleaseError(f"Signed application identifier does not match {bundle}.")
    for key, required in [
        ("aps-environment", "production"),
        ("com.apple.security.application-groups", config["CAN_APP_GROUP"]),
    ]:
        for source in [allowed, rights]:
            value = source.get(key)
            if required not in value if isinstance(value, list) else value != required:
                raise ReleaseError(f"Missing {key}={required} for {bundle}.")
    expected_groups = {prefix + bundle, prefix + config["CAN_KEYCHAIN_GROUP"]}
    groups = rights.get("keychain-access-groups", [])
    if set(groups) != expected_groups or not all(
        any(
            fnmatch.fnmatchcase(group, pattern)
            for pattern in allowed.get("keychain-access-groups", [])
        )
        for group in groups
    ):
        raise ReleaseError(
            f"Private/shared Keychain entitlement mismatch for {bundle}."
        )
    if bundle == config["CAN_BUNDLE_ID"] and any(
        "Default" not in source.get("com.apple.developer.applesignin", [])
        for source in [allowed, rights]
    ):
        raise ReleaseError("The app's Sign in with Apple capability is missing.")
    expiry = profile.get("ExpirationDate")
    if not isinstance(expiry, datetime) or utc(expiry) <= now:
        raise ReleaseError(
            f"Provisioning profile expired or has no valid expiration date: {bundle}."
        )
    return {
        "bundle_id": bundle,
        "profile_expires_at": utc(expiry).isoformat(),
        "devices": sorted(set(devices)),
        "prefix": prefix,
    }


def certificate_expiry(cert: bytes, now: datetime) -> datetime:
    with tempfile.TemporaryDirectory(prefix="can-public-certificate-") as temp:
        path = Path(temp) / "signer.cer"
        path.write_bytes(cert)
        data = run(
            [
                "openssl",
                "x509",
                "-inform",
                "DER",
                "-in",
                str(path),
                "-noout",
                "-enddate",
                "-subject",
            ]
        ).decode()
    if "Apple Distribution:" not in data and "iPhone Distribution:" not in data:
        raise ReleaseError("The app must use an Apple distribution certificate.")
    match = re.search(r"^notAfter=(.+)$", data, re.MULTILINE)
    if not match:
        raise ReleaseError("Could not read the signing certificate expiration date.")
    expiry = datetime.strptime(match.group(1), "%b %d %H:%M:%S %Y GMT").replace(
        tzinfo=UTC
    )
    if expiry <= now:
        raise ReleaseError("The distribution certificate has expired.")
    return expiry


def unpack(ipa: Path, directory: Path) -> Path:
    with zipfile.ZipFile(ipa) as archive:
        members = archive.infolist()
        if (
            len(members) > 20000
            or sum(item.file_size for item in members) > 2_000_000_000
        ):
            raise ReleaseError("IPA exceeds the local inspection size limit.")
        names = set()
        for item in members:
            path = PurePosixPath(item.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in item.filename
                or not path.parts
                or stat.S_ISLNK(item.external_attr >> 16)
                or path.as_posix() in names
            ):
                raise ReleaseError("Unsafe or duplicate path in IPA.")
            names.add(path.as_posix())
        archive.extractall(directory)
        # zipfile does not restore Unix permissions. Device installation needs the
        # original executable bits of the app, extension and embedded frameworks.
        for item in members:
            mode = (item.external_attr >> 16) & 0o777
            if mode and not item.is_dir():
                (directory / item.filename).chmod(mode)
    apps = list((directory / "Payload").glob("*.app"))
    if len(apps) != 1:
        raise ReleaseError("Expected exactly one application in IPA/Payload.")
    return apps[0]


def inspect_app(app: Path, config: dict) -> dict:
    run(["codesign", "--verify", "--deep", "--strict", str(app)])
    widgets = list((app / "PlugIns").glob("*.appex"))
    if len(widgets) != 1:
        raise ReleaseError("Expected the single embedded Can widget extension.")
    now = datetime.now(UTC)
    targets, infos = [], []
    for bundle, identifier in [
        (app, config["CAN_BUNDLE_ID"]),
        (widgets[0], config["CAN_WIDGET_BUNDLE_ID"]),
    ]:
        info = plistlib.loads((bundle / "Info.plist").read_bytes())
        if info["CFBundleIdentifier"] != identifier or "iPhoneOS" not in info.get(
            "CFBundleSupportedPlatforms", []
        ):
            raise ReleaseError(
                "Expected the configured physical iPhone app/widget, not a Simulator build."
            )
        for key, value in [
            ("CanAPNSEnvironment", "production"),
            ("CanAppGroup", config["CAN_APP_GROUP"]),
            ("CanAPIBaseURL", config["CAN_API_BASE_URL"].replace("$()", "")),
        ]:
            if info.get(key) != value:
                raise ReleaseError(f"{identifier}: incorrect {key}.")
        profile = plistlib.loads(
            run(
                [
                    "security",
                    "cms",
                    "-D",
                    "-i",
                    str(bundle / "embedded.mobileprovision"),
                ]
            )
        )
        rights = plistlib.loads(
            run(["codesign", "--display", "--entitlements", "-", "--xml", str(bundle)])
        )
        target = validate_profile(profile, rights, config, identifier, now)
        if (
            info.get("CanKeychainGroup")
            != target["prefix"] + config["CAN_KEYCHAIN_GROUP"]
            or info.get("CanPrivateKeychainGroup") != target["prefix"] + identifier
        ):
            raise ReleaseError(
                f"{identifier}: Keychain configuration does not match signing."
            )
        with tempfile.TemporaryDirectory(prefix="can-signer-") as temp:
            prefix = Path(temp) / "certificate"
            run(
                [
                    "codesign",
                    "--display",
                    "--extract-certificates",
                    str(prefix),
                    str(bundle),
                ]
            )
            cert = Path(str(prefix) + "0").read_bytes()
        if cert not in profile.get("DeveloperCertificates", []):
            raise ReleaseError(
                "The actual signing certificate is not authorized by the profile."
            )
        target["certificate_expires_at"] = certificate_expiry(cert, now).isoformat()
        targets.append(target)
        infos.append(info)
    for key in ["CFBundleShortVersionString", "CFBundleVersion"]:
        if infos[0][key] != infos[1][key]:
            raise ReleaseError("App and widget version/build numbers differ.")
    devices = sorted(set(targets[0]["devices"]) & set(targets[1]["devices"]))
    if not devices:
        raise ReleaseError("App and widget profiles have no common registered devices.")
    expiry = min(
        datetime.fromisoformat(t[key])
        for t in targets
        for key in ["profile_expires_at", "certificate_expires_at"]
    )
    return {
        "version": infos[0]["CFBundleShortVersionString"],
        "build_number": infos[0]["CFBundleVersion"],
        "team_id": config["CAN_DEVELOPMENT_TEAM"],
        "targets": targets,
        "devices": devices,
        "expires_at": expiry.isoformat(),
        "renew_before": (expiry - timedelta(days=30)).isoformat(),
    }


def print_report(report: dict) -> None:
    print(f"Can {report['version']} ({report['build_number']}); Ad Hoc; app + widget")
    print(f"Registered devices covered by BOTH profiles: {len(report['devices'])}")
    for target in report["targets"]:
        print(
            f"  {target['bundle_id']}: profile until {target['profile_expires_at']}; certificate until {target['certificate_expires_at']}"
        )
    print(f"Effective expiration: {report['expires_at']}")
    print(f"Rebuild/re-sign before: {report['renew_before']} (30-day planning margin)")
    if datetime.fromisoformat(report["expires_at"]) < datetime.now(UTC) + timedelta(
        days=30
    ):
        print(
            "WARNING: less than 30 days remain. Renew signing material for a longer-lived installation."
        )


def source_info() -> dict:
    try:
        top = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if top.returncode or Path(top.stdout.strip()).resolve() != ROOT.resolve():
            return {"source_commit": None, "source_dirty": None}
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        status = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        return {
            "source_commit": commit.stdout.strip(),
            "source_dirty": bool(status.stdout.strip()),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"source_commit": None, "source_dirty": None}


def build_number(now: datetime) -> str:
    # Legal CFBundleVersion components (4.2.2 digits), monotonic across Macs until 2047.
    now = utc(now)
    value = (
        os.environ.get("IOS_BUILD_NUMBER")
        or f"{(now.date() - datetime(2020, 1, 1).date()).days}.{now.hour}.{now.minute}"
    )
    if not re.fullmatch(r"[1-9]\d{0,3}(?:\.\d{1,2}){0,2}", value):
        raise ReleaseError(
            "IOS_BUILD_NUMBER must contain 1-3 numeric components with at most 4.2.2 digits."
        )
    return value


def build() -> None:
    config = doctor()
    now = datetime.now(UTC)
    directory = OUTPUT / now.strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True, mode=0o700)
    archive = directory / "Can.xcarchive"
    number = build_number(now)
    options = {
        "method": "release-testing",
        "destination": "export",
        "signingStyle": "automatic",
        "teamID": config["CAN_DEVELOPMENT_TEAM"],
        "thinning": "<none>",
        "manageAppVersionAndBuildNumber": False,
    }
    options_path = directory / "ExportOptions.plist"
    options_path.write_bytes(plistlib.dumps(options))
    print(f"Building Release archive locally. Logs/artifacts: {directory}", flush=True)
    run(
        [
            "xcodebuild",
            "-project",
            str(ROOT / "apps/ios/Can.xcodeproj"),
            "-scheme",
            "Can",
            "-configuration",
            "Release",
            "-destination",
            "generic/platform=iOS",
            "-archivePath",
            str(archive),
            "-derivedDataPath",
            str(OUTPUT / "DerivedData"),
            "-allowProvisioningUpdates",
            "CODE_SIGN_STYLE=Automatic",
            "CODE_SIGN_IDENTITY=Apple Development",
            # Archive with development provisioning; export re-signs the shipping app.
            # Runtime APNs stays production and is verified in the final exported IPA.
            "CAN_APNS_ENTITLEMENT_ENVIRONMENT=development",
            f"CURRENT_PROJECT_VERSION={number}",
            "archive",
        ],
        directory / "archive.log",
    )
    run(
        [
            "xcodebuild",
            "-exportArchive",
            "-archivePath",
            str(archive),
            "-exportPath",
            str(directory / "export"),
            "-exportOptionsPlist",
            str(options_path),
            "-allowProvisioningUpdates",
        ],
        directory / "export.log",
    )
    ipas = list((directory / "export").glob("*.ipa"))
    if len(ipas) != 1:
        raise ReleaseError("Xcode did not export exactly one universal IPA.")
    ipa = ipas[0]
    with tempfile.TemporaryDirectory(prefix="can-release-check-") as temp:
        report = inspect_app(unpack(ipa, Path(temp)), config)
    report.update(
        ipa=str(ipa),
        sha256=hashlib.sha256(ipa.read_bytes()).hexdigest(),
        built_at=now.isoformat(),
        **source_info(),
    )
    report_path = directory / "release.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    # A failed build never replaces the last verified release pointer.
    pointer = directory / "latest.tmp"
    pointer.write_text(json.dumps({"ipa": str(ipa), "report": str(report_path)}) + "\n")
    pointer.replace(OUTPUT / "latest.json")
    print_report(report)
    print(
        f'IPA: {ipa}\nReport: {report_path}\nInstall: make ios-install IOS_DEVICE="iPhone name"'
    )


def select_ipa() -> Path:
    supplied = os.environ.get("IOS_IPA")
    if supplied:
        ipa = Path(supplied).expanduser().resolve()
    else:
        pointer = OUTPUT / "latest.json"
        if not pointer.exists():
            raise ReleaseError(
                "No verified release. Run make ios-release, or set IOS_IPA=/path/to/Can.ipa."
            )
        ipa = Path(json.loads(pointer.read_text())["ipa"])
    if not ipa.is_file() or ipa.suffix != ".ipa":
        raise ReleaseError(f"IPA not found: {ipa}")
    return ipa


def install(app: Path, report: dict, selector: str) -> None:
    with tempfile.TemporaryDirectory(prefix="can-device-") as temp:
        path = Path(temp) / "device.json"
        run(
            [
                "xcrun",
                "devicectl",
                "device",
                "info",
                "details",
                "--device",
                selector,
                "--json-output",
                str(path),
            ]
        )
        result = json.loads(path.read_text())["result"]
    device = result.get("device", result)
    udid = device.get("hardwareProperties", {}).get("udid")
    if not udid or udid not in report["devices"]:
        raise ReleaseError(
            "This iPhone is not included in BOTH Ad Hoc profiles. Register it in the paid team and rebuild; installation was not attempted."
        )
    if (
        device.get("deviceProperties", {}).get("developerModeStatus", "enabled")
        != "enabled"
    ):
        raise ReleaseError(
            "Enable Developer Mode on this iPhone, restart and confirm it, then retry."
        )
    # Install the exported distribution bundle, never the development-signed archive.
    # Update in place: do not uninstall the app or erase its local data.
    run(
        [
            "xcrun",
            "devicectl",
            "device",
            "install",
            "app",
            "--device",
            selector,
            str(app),
        ]
    )
    print(
        "Installed app + widget. Unlock the iPhone and open Можно; add widgets from iOS."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["doctor", "build", "inspect", "devices", "install"]
    )
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            doctor()
        elif args.command == "build":
            build()
        elif args.command == "devices":
            print(run(["xcrun", "devicectl", "list", "devices"]).decode())
        else:
            selector = os.environ.get("IOS_DEVICE", "").strip()
            if args.command == "install" and not selector:
                raise ReleaseError(
                    'Choose the iPhone: make ios-devices; make ios-install IOS_DEVICE="name or identifier"'
                )
            config, ipa = settings(), select_ipa()
            with tempfile.TemporaryDirectory(prefix="can-ad-hoc-") as temp:
                app = unpack(ipa, Path(temp))
                report = inspect_app(app, config)
                print_report(report)
                if args.command == "install":
                    install(app, report, selector)
        return 0
    except (
        ReleaseError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        plistlib.InvalidFileException,
        zipfile.BadZipFile,
    ) as error:
        print(f"Ad Hoc: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
