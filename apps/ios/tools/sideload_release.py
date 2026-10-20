"""Build an IPA for SideStore to sign with the owner's free Apple account."""

import hashlib
import json
import plistlib
import re
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from adhoc_release import ROOT, UTC, ReleaseError, build_number, run, source_info

OUTPUT = ROOT / "build/ios-sideload"
BUNDLE = "com.canbudget.can"
GROUP = "group." + BUNDLE


def inspect_payload(app: Path, origin: str) -> dict:
    run(["codesign", "--verify", "--deep", "--strict", str(app)])
    extensions = list((app / "PlugIns").glob("*.appex"))
    if len(extensions) != 1:
        raise ReleaseError("Keep the Can widget extension when importing this IPA into SideStore.")
    infos = []
    for target, identifier in [(app, BUNDLE), (extensions[0], BUNDLE + ".widget")]:
        info = plistlib.loads((target / "Info.plist").read_bytes())
        if any(
            info.get(key) != value
            for key, value in {
                "CFBundleIdentifier": identifier,
                "CanDistribution": "sideload",
                "CanPushEnabled": "NO",
                "CanAppGroup": GROUP,
                "CanAPIBaseURL": origin,
            }.items()
        ) or "iPhoneOS" not in info.get("CFBundleSupportedPlatforms", []):
            raise ReleaseError(
                "Expected the physical iPhone SideStore app/widget with push disabled."
            )
        rights = plistlib.loads(
            run(["codesign", "--display", "--entitlements", "-", "--xml", str(target)])
        )
        if rights != {"com.apple.security.application-groups": [GROUP]}:
            raise ReleaseError("SideStore IPA must contain only the shared App Group capability.")
        if (target / "embedded.mobileprovision").exists():
            raise ReleaseError(
                "A SideStore input IPA must not include somebody else's provisioning profile."
            )
        infos.append(info)
    for key in ["CFBundleVersion", "CFBundleShortVersionString"]:
        if infos[0][key] != infos[1][key]:
            raise ReleaseError("App and widget versions differ.")
    return {
        "version": infos[0]["CFBundleShortVersionString"],
        "build_number": infos[0]["CFBundleVersion"],
    }


def build() -> Path:
    if sys.platform != "darwin":
        raise ReleaseError("Build on a Mac with full Xcode 26 or later.")
    version = run(["xcodebuild", "-version"]).decode()
    if not re.search(r"Xcode (?:2[6-9]|[3-9]\d)\.", version):
        raise ReleaseError("Select full Xcode 26 or later.")
    owner = (ROOT / "apps/ios/Config/Owner.xcconfig").read_text()
    origin_match = re.search(r"^CAN_API_BASE_URL\s*=\s*(\S+)\s*$", owner, re.MULTILINE)
    origin = origin_match.group(1).replace("$()", "") if origin_match else ""
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
        raise ReleaseError("Owner.xcconfig must specify the production HTTPS API origin.")
    now = datetime.now(UTC)
    directory = OUTPUT / now.strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True, mode=0o700)
    archive = directory / "Can.xcarchive"
    print(f"Building a SideStore input IPA locally: {directory}", flush=True)
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
            "CODE_SIGNING_ALLOWED=NO",
            "CAN_DEVELOPMENT_TEAM=",
            "CAN_DISTRIBUTION=sideload",
            "CAN_PUSH_ENABLED=NO",
            "SWIFT_ACTIVE_COMPILATION_CONDITIONS=CAN_SIDELOAD",
            f"CAN_BUNDLE_ID={BUNDLE}",
            f"CAN_WIDGET_BUNDLE_ID={BUNDLE}.widget",
            f"CAN_APP_GROUP={GROUP}",
            f"CAN_KEYCHAIN_GROUP={BUNDLE}.shared",
            f"CURRENT_PROJECT_VERSION={build_number(now)}",
            "archive",
        ],
        directory / "archive.log",
    )
    app = archive / "Products/Applications/Can.app"
    # A local anonymous signature preserves capabilities for SideStore's importer.
    # This is NOT Apple provisioning and does not make the IPA installable by itself.
    rights = directory / "SideStore.entitlements"
    rights.write_bytes(plistlib.dumps({"com.apple.security.application-groups": [GROUP]}))
    for target in [*sorted((app / "PlugIns").glob("*.appex")), app]:
        run(["codesign", "--force", "--sign", "-", "--entitlements", str(rights), str(target)])
    report = inspect_payload(app, origin)
    ipa = directory / "Can-SideStore.ipa"
    with zipfile.ZipFile(ipa, "w", zipfile.ZIP_DEFLATED) as package:
        for path in sorted(app.rglob("*")):
            if path.is_symlink():
                raise ReleaseError("Unexpected symlink in the physical iPhone bundle.")
            package.write(path, Path("Payload/Can.app") / path.relative_to(app))
    # Check the ZIP payload too, including executable permissions after extraction.
    from adhoc_release import unpack

    with tempfile.TemporaryDirectory(prefix="can-sideload-check-") as temp:
        inspect_payload(unpack(ipa, Path(temp)), origin)
    report.update(
        ipa=str(ipa),
        sha256=hashlib.sha256(ipa.read_bytes()).hexdigest(),
        built_at=now.isoformat(),
        requires_sidestore_signing=True,
        push_enabled=False,
        **source_info(),
    )
    (directory / "release.json").write_text(json.dumps(report, indent=2) + "\n")
    pointer = directory / "latest.tmp"
    pointer.write_text(json.dumps(report) + "\n")
    pointer.replace(OUTPUT / "latest.json")
    print(
        f"IPA: {ipa}\nImport into SideStore and KEEP the widget extension.\nApple signing and seven-day refresh happen in SideStore, not in this build command."
    )
    return ipa


if __name__ == "__main__":
    try:
        build()
    except (ReleaseError, OSError, ValueError, KeyError) as error:
        print(f"SideStore build: {error}", file=sys.stderr)
        raise SystemExit(1) from None
