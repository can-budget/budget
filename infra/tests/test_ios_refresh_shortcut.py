"""Check device identity selection and prevent publishing an unsigned refresh shortcut."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "apps/ios/tools"
spec = importlib.util.spec_from_file_location("refresh_shortcut", TOOLS / "refresh_shortcut.py")
refresh = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(TOOLS))
try:
    spec.loader.exec_module(refresh)
finally:
    sys.path.pop(0)

BUNDLE = "com.SideStore.SideStore.ABC1234567"


def test_wrong_or_ambiguous_sidestore_is_rejected():
    apps = [
        {"bundleIdentifier": BUNDLE},
        {"bundleIdentifier": "com.SideStore.SideStore.XYZ1234567"},
    ]
    assert refresh.select_bundle(apps, BUNDLE) == BUNDLE
    for candidates in [[], apps, [{"bundleIdentifier": "com.example.fake"}]]:
        with pytest.raises(refresh.ReleaseError):
            refresh.select_bundle(candidates)
    with pytest.raises(refresh.ReleaseError):
        refresh.workflow("com.SideStore.SideStore.invalid/../../")


def test_quiet_background_workflow_uses_installed_sidestore_and_enables_rather_than_toggles_vpn():
    actions = refresh.workflow(BUNDLE)["WFWorkflowActions"][2:-1]
    assert [a["WFWorkflowActionIdentifier"] for a in actions] == [
        "com.jkcoxson.LocalDevVPN.ControlLocalDevVPNIntent",
        "is.workflow.actions.delay",
        BUNDLE + ".RefreshAllIntent",
        "com.jkcoxson.LocalDevVPN.ControlLocalDevVPNIntent",
    ]
    assert actions[0]["WFWorkflowActionParameters"]["operation"] == "enable"
    assert actions[-1]["WFWorkflowActionParameters"]["operation"] == "disable"
    assert (
        actions[0]["WFWorkflowActionParameters"]["UUID"]
        != actions[-1]["WFWorkflowActionParameters"]["UUID"]
    )
    for action in [actions[0], actions[2], actions[3]]:
        assert action["WFWorkflowActionParameters"]["OpenWhenRun"] is False
        assert action["WFWorkflowActionParameters"]["ShowWhenRun"] is False
    assert (
        actions[2]["WFWorkflowActionParameters"]["AppIntentDescriptor"]["TeamIdentifier"]
        == "ABC1234567"
    )


def test_wifi_gate_wraps_all_vpn_and_refresh_operations_without_notifications():
    actions = refresh.workflow(BUNDLE)["WFWorkflowActions"]
    network, gate, end = actions[0], actions[1], actions[-1]
    assert network["WFWorkflowActionIdentifier"] == "is.workflow.actions.getwifi"
    assert network["WFWorkflowActionParameters"]["WFNetworkDetailsNetwork"] == "Wi-Fi"
    assert network["WFWorkflowActionParameters"]["WFWiFiDetail"] == "Network Name"
    start_params, end_params = gate["WFWorkflowActionParameters"], end["WFWorkflowActionParameters"]
    assert start_params["WFCondition"] == 100  # Has any value; cellular-only returns no Wi-Fi name.
    assert start_params["WFControlFlowMode"] == 0 and end_params["WFControlFlowMode"] == 2
    assert start_params["GroupingIdentifier"] == end_params["GroupingIdentifier"]
    assert (
        start_params["WFInput"]["Variable"]["Value"]["OutputUUID"]
        == network["WFWorkflowActionParameters"]["UUID"]
    )
    assert len(actions) == 7  # No unconditional VPN action, notification, URL or app launch.


def test_refresh_identity_matches_the_working_iphone_export():
    captured = json.loads(
        (Path(__file__).parent / "fixtures/sidestore_refresh_action.json").read_text()
    )
    action = next(
        a
        for a in refresh.workflow(BUNDLE)["WFWorkflowActions"]
        if a["WFWorkflowActionIdentifier"].startswith(BUNDLE + ".")
    )
    assert action["WFWorkflowActionIdentifier"] == captured["WFWorkflowActionIdentifier"]
    assert (
        action["WFWorkflowActionParameters"]["AppIntentDescriptor"]
        == captured["AppIntentDescriptor"]
    )


def test_failed_signing_preserves_last_successful_shortcut(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, "OUTPUT", tmp_path)
    pointer = tmp_path / "latest.json"
    previous = {"shortcut": "previous verified shortcut"}
    pointer.write_text(json.dumps(previous))

    def failed_sign(args, log):
        Path(args[-1]).write_bytes(b"not an Apple-signed shortcut")

    monkeypatch.setattr(refresh, "run", failed_sign)
    with pytest.raises(refresh.ReleaseError):
        refresh.build(BUNDLE)
    assert json.loads(pointer.read_text()) == previous
