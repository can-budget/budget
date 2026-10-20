"""Ad Hoc helpers use synthetic profiles and fake Apple commands; no signing or device writes."""

import copy
import importlib.util
import json
import plistlib
import stat
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "adhoc_release", ROOT / "apps/ios/tools/adhoc_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
CONFIG = {
    "CAN_DEVELOPMENT_TEAM": "ABCDE12345",
    "CAN_BUNDLE_ID": "com.example.can",
    "CAN_WIDGET_BUNDLE_ID": "com.example.can.widget",
    "CAN_APP_GROUP": "group.com.example.can",
    "CAN_KEYCHAIN_GROUP": "com.example.can.shared",
    "CAN_API_BASE_URL": "https:/$()/151.243.217.6",
    "MARKETING_VERSION": "1.0.0",
}
CERT = b"synthetic-public-distribution-certificate"


def profile(bundle="com.example.can", days=300):
    allowed = {
        "get-task-allow": False,
        "application-identifier": "ABCDE12345." + bundle,
        "aps-environment": "production",
        "keychain-access-groups": ["ABCDE12345.*"],
        "com.apple.security.application-groups": [CONFIG["CAN_APP_GROUP"]],
        "com.apple.developer.applesignin": ["Default"],
    }
    return {
        "Entitlements": allowed,
        "ExpirationDate": NOW + timedelta(days=days),
        "ApplicationIdentifierPrefix": ["ABCDE12345"],
        "TeamIdentifier": ["ABCDE12345"],
        "ProvisionedDevices": ["first-phone", "second-phone"],
        "DeveloperCertificates": [CERT],
    }


def rights(bundle="com.example.can"):
    result = copy.deepcopy(profile(bundle)["Entitlements"])
    result["com.apple.developer.team-identifier"] = "ABCDE12345"
    result["keychain-access-groups"] = [
        "ABCDE12345." + bundle,
        "ABCDE12345.com.example.can.shared",
    ]
    return result


@pytest.mark.parametrize(
    "case",
    [
        "development",
        "enterprise",
        "store",
        "personal",
        "expired",
        "team",
        "app",
        "push",
        "groups",
        "keychain",
        "apple",
    ],
)
def test_profiles_reject_wrong_distribution_or_capabilities(case):
    p, r = profile(), rights()
    if case == "development":
        p["Entitlements"]["get-task-allow"] = True
    if case == "enterprise":
        p["ProvisionsAllDevices"] = True
    if case == "store":
        p.pop("ProvisionedDevices")
    if case == "personal":
        p["LocalProvision"] = True
    if case == "expired":
        p["ExpirationDate"] = NOW - timedelta(seconds=1)
    if case == "team":
        r["com.apple.developer.team-identifier"] = "OTHER12345"
    if case == "app":
        r["application-identifier"] = "ABCDE12345.com.example.other"
    if case == "push":
        r["aps-environment"] = "development"
    if case == "groups":
        p["Entitlements"]["com.apple.security.application-groups"] = []
    if case == "keychain":
        r["keychain-access-groups"] = ["ABCDE12345.*"]
    if case == "apple":
        r.pop("com.apple.developer.applesignin")
    with pytest.raises(release.ReleaseError):
        release.validate_profile(p, r, CONFIG, CONFIG["CAN_BUNDLE_ID"], NOW)


def test_legacy_app_id_prefix_is_not_assumed_to_equal_team_id():
    p, r = profile(), rights()
    p["ApplicationIdentifierPrefix"] = ["LEGACY0001"]
    for source in [p["Entitlements"], r]:
        source["application-identifier"] = "LEGACY0001.com.example.can"
        source["keychain-access-groups"] = [
            x.replace("ABCDE12345.", "LEGACY0001.")
            for x in source["keychain-access-groups"]
        ]
    result = release.validate_profile(p, r, CONFIG, CONFIG["CAN_BUNDLE_ID"], NOW)
    assert result["prefix"] == "LEGACY0001."


@pytest.mark.parametrize("case", ["valid", "expired", "development"])
def test_actual_signer_certificate_type_and_expiration(monkeypatch, case):
    end = (
        "Jan  1 00:00:00 2031 GMT" if case != "expired" else "Jan  1 00:00:00 2029 GMT"
    )
    name = "Apple Development" if case == "development" else "Apple Distribution"
    monkeypatch.setattr(
        release,
        "run",
        lambda args: f"notAfter={end}\nsubject=CN={name}: Fixture\n".encode(),
    )
    if case == "valid":
        assert release.certificate_expiry(CERT, NOW) == datetime(
            2031, 1, 1, tzinfo=timezone.utc
        )
    else:
        with pytest.raises(release.ReleaseError):
            release.certificate_expiry(CERT, NOW)


def test_ipa_extraction_preserves_executable_bits(tmp_path):
    ipa = tmp_path / "Can.ipa"
    with zipfile.ZipFile(ipa, "w") as archive:
        binary = zipfile.ZipInfo("Payload/Can.app/Can")
        binary.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(binary, b"synthetic-binary")
    app = release.unpack(ipa, tmp_path / "unpacked")
    assert (app / "Can").stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize(
    "path",
    ["../escaped", "/absolute", "Payload\\escaped", "Payload/Can.app/../escaped"],
)
def test_ipa_rejects_path_traversal(tmp_path, path):
    ipa = tmp_path / "bad.ipa"
    with zipfile.ZipFile(ipa, "w") as archive:
        archive.writestr(path, b"fixture")
    with pytest.raises(release.ReleaseError):
        release.unpack(ipa, tmp_path / "output")


def test_ipa_rejects_symlinks_and_normalized_duplicate_paths(tmp_path):
    for kind in ["symlink", "duplicate"]:
        ipa = tmp_path / (kind + ".ipa")
        with zipfile.ZipFile(ipa, "w") as archive:
            if kind == "symlink":
                entry = zipfile.ZipInfo("Payload/Can.app/link")
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, "../../../outside")
            else:
                archive.writestr("Payload/Can.app/Info.plist", "first")
                archive.writestr("Payload/./Can.app/Info.plist", "second")
        with pytest.raises(release.ReleaseError):
            release.unpack(ipa, tmp_path / kind)


def test_inspection_uses_actual_certificates_and_earliest_expiry(tmp_path, monkeypatch):
    app = tmp_path / "Can.app"
    widget = app / "PlugIns/CanWidget.appex"
    for bundle, identifier in [
        (app, CONFIG["CAN_BUNDLE_ID"]),
        (widget, CONFIG["CAN_WIDGET_BUNDLE_ID"]),
    ]:
        bundle.mkdir(parents=True, exist_ok=True)
        info = {
            "CFBundleIdentifier": identifier,
            "CFBundleSupportedPlatforms": ["iPhoneOS"],
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.2.3",
            "CanAPNSEnvironment": "production",
            "CanAppGroup": CONFIG["CAN_APP_GROUP"],
            "CanAPIBaseURL": "https://151.243.217.6",
            "CanKeychainGroup": "ABCDE12345.com.example.can.shared",
            "CanPrivateKeychainGroup": "ABCDE12345." + identifier,
        }
        (bundle / "Info.plist").write_bytes(plistlib.dumps(info))
    actual_signers = []

    def fake_run(args):
        is_widget = "CanWidget.appex" in args[-1] or "CanWidget.appex" in str(args)
        identifier = (
            CONFIG["CAN_WIDGET_BUNDLE_ID"] if is_widget else CONFIG["CAN_BUNDLE_ID"]
        )
        if args[:2] == ["security", "cms"]:
            p = profile(identifier, days=200 if is_widget else 300)
            if is_widget:
                p["ProvisionedDevices"] = ["first-phone"]
            return plistlib.dumps(p)
        if "--entitlements" in args:
            return plistlib.dumps(rights(identifier))
        if "--extract-certificates" in args:
            prefix = args[args.index("--extract-certificates") + 1]
            Path(prefix + "0").write_bytes(CERT)
        return b""

    monkeypatch.setattr(release, "run", fake_run)

    def expires(cert, now):
        actual_signers.append(cert)
        return NOW + timedelta(days=90)

    monkeypatch.setattr(release, "certificate_expiry", expires)
    result = release.inspect_app(app, CONFIG)
    assert actual_signers == [CERT, CERT]
    assert result["devices"] == ["first-phone"]
    assert datetime.fromisoformat(result["expires_at"]) == NOW + timedelta(days=90)
    assert datetime.fromisoformat(result["renew_before"]) == NOW + timedelta(days=60)


@pytest.mark.parametrize("registered", [True, False])
def test_install_checks_selected_phone_before_writing_and_never_uninstalls(
    tmp_path, monkeypatch, registered
):
    calls = []

    def fake_run(args):
        calls.append(args)
        if "--json-output" in args:
            Path(args[-1]).write_text(
                json.dumps(
                    {
                        "result": {
                            "device": {
                                "hardwareProperties": {
                                    "udid": "first-phone"
                                    if registered
                                    else "other-phone"
                                },
                                "deviceProperties": {"developerModeStatus": "enabled"},
                            }
                        }
                    }
                )
            )
        return b""

    monkeypatch.setattr(release, "run", fake_run)
    selector = "Family iPhone; literal name"
    if registered:
        release.install(
            tmp_path / "Exported.app", {"devices": ["first-phone"]}, selector
        )
        assert calls[-1] == [
            "xcrun",
            "devicectl",
            "device",
            "install",
            "app",
            "--device",
            selector,
            str(tmp_path / "Exported.app"),
        ]
    else:
        with pytest.raises(release.ReleaseError, match="BOTH"):
            release.install(
                tmp_path / "Exported.app", {"devices": ["first-phone"]}, selector
            )
        assert len(calls) == 1
    assert all("uninstall" not in call for call in calls)


def test_release_exports_locally_and_only_advances_latest_after_validation(
    tmp_path, monkeypatch
):
    output = tmp_path / "build"
    output.mkdir()
    pointer = output / "latest.json"
    pointer.write_text('{"ipa":"previous.ipa"}')
    monkeypatch.setattr(release, "OUTPUT", output)
    monkeypatch.setattr(release, "doctor", lambda: CONFIG)
    monkeypatch.setattr(
        release,
        "source_info",
        lambda: {"source_commit": "fixture", "source_dirty": False},
    )
    calls = []

    def fake_run(args, log=None):
        calls.append(args)
        if "-exportArchive" in args:
            destination = Path(args[args.index("-exportPath") + 1])
            destination.mkdir()
            with zipfile.ZipFile(destination / "Can.ipa", "w") as archive:
                archive.writestr("Payload/Can.app/Info.plist", b"synthetic")
        return b""

    monkeypatch.setattr(release, "run", fake_run)
    check = Mock(side_effect=release.ReleaseError("invalid export"))
    monkeypatch.setattr(release, "inspect_app", check)
    with pytest.raises(release.ReleaseError, match="invalid export"):
        release.build()
    assert json.loads(pointer.read_text()) == {"ipa": "previous.ipa"}
    check.side_effect = None
    check.return_value = {
        "version": "1",
        "build_number": "1",
        "devices": ["first-phone"],
        "targets": [],
        "expires_at": (NOW + timedelta(days=300)).isoformat(),
        "renew_before": NOW.isoformat(),
    }
    release.build()
    assert Path(json.loads(pointer.read_text())["ipa"]).is_file()
    archive_args, export_args = calls[-2:]
    assert "Release" in archive_args and "generic/platform=iOS" in archive_args
    options = plistlib.loads(
        Path(export_args[export_args.index("-exportOptionsPlist") + 1]).read_bytes()
    )
    assert options["method"] == "release-testing" and options["destination"] == "export"
    assert options["teamID"] == CONFIG["CAN_DEVELOPMENT_TEAM"]
    assert "install" not in repr(calls) and "upload" not in repr(options)


def test_build_number_is_valid_and_overridable(monkeypatch):
    monkeypatch.delenv("IOS_BUILD_NUMBER", raising=False)
    assert release.build_number(NOW) == "3653.0.0"
    monkeypatch.setenv("IOS_BUILD_NUMBER", "42")
    assert release.build_number(NOW) == "42"
    for bad in ["0", "10000", "1.100.1", "1.1.100", "1; touch file"]:
        monkeypatch.setenv("IOS_BUILD_NUMBER", bad)
        with pytest.raises(release.ReleaseError):
            release.build_number(NOW)


def test_unconfigured_team_cannot_reach_archive_or_device_install(
    tmp_path, monkeypatch
):
    path = tmp_path / "apps/ios/Config"
    path.mkdir(parents=True)
    (path / "Owner.xcconfig").write_text("CAN_DEVELOPMENT_TEAM = YOUR_TEAM_ID\n")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    command = Mock()
    monkeypatch.setattr(release, "run", command)
    with pytest.raises(release.ReleaseError, match="CAN_DEVELOPMENT_TEAM"):
        release.settings()
    command.assert_not_called()
