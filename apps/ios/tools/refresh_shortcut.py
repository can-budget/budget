"""Build a quiet Wi-Fi-only refresh shortcut with LocalDevVPN enabled then disabled."""

import argparse
import hashlib
import json
import os
import plistlib
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from adhoc_release import ROOT, UTC, ReleaseError, run

OUTPUT = ROOT / "build/ios-refresh"
VPN_BUNDLE = "com.jkcoxson.LocalDevVPN"
# Verified in the iPhone's working export; migrated actions use the legacy ID.
REFRESH_INTENT = "RefreshAllIntent"


def select_bundle(apps: list[dict], requested: str = "") -> str:
    matches = [
        app["bundleIdentifier"]
        for app in apps
        if re.fullmatch(
            r"com\.SideStore\.SideStore(?:\.[A-Z0-9]{10})?", app.get("bundleIdentifier", "")
        )
        and (not requested or app["bundleIdentifier"] == requested)
    ]
    if len(matches) != 1:
        raise ReleaseError("Expected exactly one matching SideStore on the selected iPhone.")
    return matches[0]


def discover_bundle(device: str, requested: str = "") -> str:
    with tempfile.TemporaryDirectory(prefix="can-refresh-device-") as temp:
        report = Path(temp) / "apps.json"
        run(
            [
                "xcrun",
                "devicectl",
                "--timeout",
                "30",
                "device",
                "info",
                "apps",
                "--device",
                device,
                "--json-output",
                str(report),
            ]
        )
        apps = json.loads(report.read_text())["result"]["apps"]
    return select_bundle(apps, requested)


def workflow(bundle: str) -> dict:
    if not re.fullmatch(r"com\.SideStore\.SideStore(?:\.[A-Z0-9]{10})?", bundle):
        raise ReleaseError("Use the actual installed SideStore bundle identifier.")

    def intent(app: str, name: str, identifier: str, **parameters) -> dict:
        descriptor = {"AppIntentIdentifier": identifier, "BundleIdentifier": app, "Name": name}
        if app == bundle and re.fullmatch(r"[A-Z0-9]{10}", app.rsplit(".", 1)[-1]):
            descriptor["TeamIdentifier"] = app.rsplit(".", 1)[-1]
        return {
            "WFWorkflowActionIdentifier": app + "." + identifier,
            "WFWorkflowActionParameters": {
                "AppIntentDescriptor": descriptor,
                "OpenWhenRun": False,
                # Suppress the intent's result dialog as well as opening the app.
                "ShowWhenRun": False,
                "UUID": str(
                    uuid5(
                        NAMESPACE_URL,
                        app + "." + identifier + json.dumps(parameters, sort_keys=True),
                    )
                ).upper(),
                **parameters,
            },
        }

    wifi_uuid = str(uuid5(NAMESPACE_URL, "can-refresh-wifi")).upper()
    wifi_group = str(uuid5(NAMESPACE_URL, "can-refresh-wifi-condition")).upper()
    return {
        "WFWorkflowName": "Can-Refresh",
        "WFQuickActionSurfaces": [],
        "WFWorkflowClientVersion": "2106.0.3",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": 61456,
            "WFWorkflowIconStartColor": 946986751,
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.getwifi",
                "WFWorkflowActionParameters": {
                    "WFNetworkDetailsNetwork": "Wi-Fi",
                    "WFWiFiDetail": "Network Name",
                    "UUID": wifi_uuid,
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                "WFWorkflowActionParameters": {
                    "GroupingIdentifier": wifi_group,
                    "WFControlFlowMode": 0,
                    "WFCondition": 100,
                    "WFInput": {
                        "Type": "Variable",
                        "Variable": {
                            "Value": {
                                "OutputUUID": wifi_uuid,
                                "OutputName": "Network Details",
                                "Type": "ActionOutput",
                            },
                            "WFSerializationType": "WFTextTokenAttachment",
                        },
                    },
                },
            },
            intent(VPN_BUNDLE, "LocalDevVPN", "ControlLocalDevVPNIntent", operation="enable"),
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.delay",
                "WFWorkflowActionParameters": {"WFDelayTime": 5},
            },
            intent(bundle, "SideStore", REFRESH_INTENT),
            intent(VPN_BUNDLE, "LocalDevVPN", "ControlLocalDevVPNIntent", operation="disable"),
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.conditional",
                "WFWorkflowActionParameters": {
                    "GroupingIdentifier": wifi_group,
                    "WFControlFlowMode": 2,
                },
            },
        ],
    }


def build(bundle: str) -> Path:
    content = workflow(bundle)
    now = datetime.now(UTC)
    directory = OUTPUT / now.strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True, mode=0o700)
    unsigned = directory / "Can-Refresh.unsigned.shortcut"
    unsigned.write_bytes(plistlib.dumps(content, fmt=plistlib.FMT_BINARY))
    signed = directory / "Can-Refresh.shortcut"
    # Apple's normal share validation; only action names and public app identifiers are sent.
    run(
        [
            "shortcuts",
            "sign",
            "--mode",
            "anyone",
            "--input",
            str(unsigned),
            "--output",
            str(signed),
        ],
        directory / "sign.log",
    )
    if not signed.is_file() or signed.read_bytes()[:4] != b"AEA1":
        raise ReleaseError(
            "Apple did not produce a signed shortcut; nothing was sent to the phone."
        )
    report = {
        "shortcut": str(signed),
        "sidestore_bundle_id": bundle,
        "sha256": hashlib.sha256(signed.read_bytes()).hexdigest(),
        "generated_at": now.isoformat(),
        "requires_phone_verification": True,
    }
    (directory / "release.json").write_text(json.dumps(report, indent=2) + "\n")
    pointer = directory / "latest.tmp"
    pointer.write_text(json.dumps(report) + "\n")
    pointer.replace(OUTPUT / "latest.json")
    return signed


def send(shortcut: Path, device: str, bundle: str) -> None:
    run(
        [
            "xcrun",
            "devicectl",
            "--timeout",
            "30",
            "device",
            "copy",
            "to",
            "--device",
            device,
            "--source",
            str(shortcut),
            "--destination",
            "Documents/Can-Refresh.shortcut",
            "--domain-type",
            "appDataContainer",
            "--domain-identifier",
            bundle,
        ]
    )
    print("On iPhone: Files > Browse > On My iPhone > SideStore > Can-Refresh.shortcut")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=os.environ.get("IOS_DEVICE", ""))
    parser.add_argument("--sidestore-bundle-id", default="")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    try:
        if sys.platform != "darwin":
            raise ReleaseError(
                "Signing the shortcut requires macOS and the built-in shortcuts CLI."
            )
        if args.send and not args.device:
            raise ReleaseError("Select the iPhone with IOS_DEVICE before sending the shortcut.")
        bundle = (
            discover_bundle(args.device, args.sidestore_bundle_id)
            if args.device
            else args.sidestore_bundle_id
        )
        if not bundle:
            raise ReleaseError(
                "Set IOS_DEVICE, or pass --sidestore-bundle-id from your installed SideStore."
            )
        shortcut = build(bundle)
        print(f"Signed shortcut: {shortcut}")
        print(
            "No success notification actions. Errors remain visible; daily scheduling is configured on iPhone."
        )
        if args.send:
            send(shortcut, args.device, bundle)
        return 0
    except (ReleaseError, OSError, ValueError, KeyError) as error:
        print(f"Refresh shortcut: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
