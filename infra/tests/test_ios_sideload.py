"""Validate the SideStore input format without Apple credentials or device writes."""

import importlib.util
import plistlib
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "apps/ios/tools"
spec = importlib.util.spec_from_file_location("sideload_release", TOOLS / "sideload_release.py")
release = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(TOOLS))
try:
    spec.loader.exec_module(release)
finally:
    sys.path.pop(0)


def fixture_app(tmp_path):
    app = tmp_path / "Can.app"
    targets = [app, app / "PlugIns/CanWidget.appex"]
    for target, bundle in zip(targets, [release.BUNDLE, release.BUNDLE + ".widget"], strict=True):
        target.mkdir(parents=True, exist_ok=True)
        info = {
            "CFBundleIdentifier": bundle,
            "CFBundleDisplayName": "Can" if target == app else "CanWidget",
            "CFBundleName": "Can" if target == app else "CanWidget",
            "CFBundleSupportedPlatforms": ["iPhoneOS"],
            "CanDistribution": "sideload",
            "CanPushEnabled": "NO",
            "CanAppGroup": release.GROUP,
            "CanAPIBaseURL": "https://151.243.217.6",
            "CFBundleVersion": "42",
            "CFBundleShortVersionString": "0.1.0",
        }
        (target / "Info.plist").write_bytes(plistlib.dumps(info))
    return app, targets


def test_registration_uses_latin_names_and_preserves_russian_home_screen_names(tmp_path):
    app, targets = fixture_app(tmp_path)
    original_names = ["Можно", "Можно сегодня"]
    for target, name in zip(targets, original_names, strict=True):
        path = target / "Info.plist"
        info = plistlib.loads(path.read_bytes())
        info["CFBundleDisplayName"] = name
        path.write_bytes(plistlib.dumps(info))

    release.prepare_registration_names(app)

    for target, name, original in zip(targets, ["Can", "CanWidget"], original_names, strict=True):
        info = plistlib.loads((target / "Info.plist").read_bytes())
        assert info["CFBundleDisplayName"] == info["CFBundleName"] == name
        localized = plistlib.loads((target / "ru.lproj/InfoPlist.strings").read_bytes())
        assert localized["CFBundleDisplayName"] == original


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "simulator",
        "apple-push",
        "wrong-api",
        "missing-widget",
        "different-version",
        "foreign-profile",
        "apple-signin",
        "russian-app-name",
        "russian-widget-name",
    ],
)
def test_sideload_payload_rejects_wrong_platform_capabilities_or_widget(
    tmp_path, monkeypatch, case
):
    app, targets = fixture_app(tmp_path)
    info = plistlib.loads((targets[1] / "Info.plist").read_bytes())
    if case == "simulator":
        info["CFBundleSupportedPlatforms"] = ["iPhoneSimulator"]
    elif case == "apple-push":
        info["CanPushEnabled"] = "YES"
    elif case == "wrong-api":
        info["CanAPIBaseURL"] = "https://wrong.invalid"
    elif case == "missing-widget":
        targets[1].rename(app / "PlugIns/Removed")
    elif case == "different-version":
        info["CFBundleVersion"] = "43"
    elif case == "foreign-profile":
        (app / "embedded.mobileprovision").write_bytes(b"some other account")
    elif case == "russian-app-name":
        app_info = plistlib.loads((app / "Info.plist").read_bytes())
        app_info["CFBundleDisplayName"] = "Можно"
        (app / "Info.plist").write_bytes(plistlib.dumps(app_info))
    elif case == "russian-widget-name":
        info["CFBundleDisplayName"] = "Можно сегодня"
    if case != "missing-widget":
        (targets[1] / "Info.plist").write_bytes(plistlib.dumps(info))
    rights = {"com.apple.security.application-groups": [release.GROUP]}
    if case == "apple-signin":
        rights["com.apple.developer.applesignin"] = ["Default"]
    calls = []

    def run(args):
        calls.append(args)
        return plistlib.dumps(rights) if "--entitlements" in args else b""

    monkeypatch.setattr(release, "run", run)
    if case == "valid":
        assert release.inspect_payload(app, "https://151.243.217.6")["build_number"] == "42"
        assert calls[0][:4] == ["codesign", "--verify", "--deep", "--strict"]
    else:
        with pytest.raises(release.ReleaseError):
            release.inspect_payload(app, "https://151.243.217.6")
