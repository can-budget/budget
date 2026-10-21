"""Static project/fixture consistency checks. Does not compile Swift or run XCTest."""
from pathlib import Path
import hashlib
import ipaddress
import json
import plistlib
import re
import struct
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "Can.xcodeproj/project.pbxproj").read_text()
tokens = re.findall(r'"(?:\\.|[^"\\])*"|[{}()=;,]', source)
position = 0

def consume(expected=None):
    global position
    value = tokens[position]
    position += 1
    if expected is not None:
        assert value == expected, (value, expected, position)
    return value

def parse():
    token = consume()
    if token == "{":
        result = {}
        while tokens[position] != "}":
            key = json.loads(consume())
            consume("=")
            assert key not in result, key
            result[key] = parse()
            consume(";")
        consume("}")
        return result
    if token == "(":
        result = []
        while tokens[position] != ")":
            result.append(parse())
            if tokens[position] == ",": consume(",")
        consume(")")
        return result
    return json.loads(token)

project = parse()
assert position == len(tokens)
objects = project["objects"]
assert objects[project["rootObject"]]["isa"] == "PBXProject"
references = {key: value["path"] for key, value in objects.items() if value["isa"] == "PBXFileReference" and value.get("sourceTree") == "<group>"}
for path in references.values():
    assert (ROOT / path).exists(), path
for value in objects.values():
    for key in ["fileRef", "target", "targetProxy", "containerPortal", "buildConfigurationList", "productReference", "baseConfigurationReference"]:
        if key in value: assert value[key] in objects, (key, value[key])
    for key in ["children", "files", "buildConfigurations", "buildPhases", "dependencies", "targets"]:
        for ref in value.get(key, []): assert ref in objects, (key, ref)

targets = {value["name"]: (key, value) for key, value in objects.items() if value["isa"] == "PBXNativeTarget"}
assert set(targets) == {"Can", "CanWidget", "CanTests"}
for name, (identifier, target) in targets.items():
    phases = [objects[ref] for ref in target["buildPhases"]]
    phase_files = lambda kind: {references[objects[ref]["fileRef"]] for phase in phases if phase["isa"] == kind for ref in phase["files"]}
    sources = phase_files("PBXSourcesBuildPhase")
    expected = {str(path.relative_to(ROOT)) for path in (ROOT / name).glob("*.swift")}
    if name != "CanTests": expected |= {str(path.relative_to(ROOT)) for path in (ROOT / "Shared").glob("*.swift")}
    assert sources == expected and sources, (name, sources, expected)
    resources = phase_files("PBXResourcesBuildPhase")
    assert ("CanTests/golden_cases.json" if name == "CanTests" else "Shared/PrivacyInfo.xcprivacy") in resources
    for ref in objects[target["buildConfigurationList"]]["buildConfigurations"]:
        config = objects[ref]
        settings = config["buildSettings"]
        assert settings["IPHONEOS_DEPLOYMENT_TARGET"] == "17.0"
        assert settings["CAN_APNS_ENTITLEMENT_ENVIRONMENT"] == ("development" if config["name"] == "Debug" else "production")
        assert settings["CAN_APNS_ENVIRONMENT"] == ("sandbox" if config["name"] == "Debug" else "production")
        if name != "CanTests":
            assert settings["INFOPLIST_PREPROCESS"] == "YES"
            assert settings["INFOPLIST_PREPROCESSOR_DEFINITIONS"] == "CAN_API_HOST_NAME=$(CAN_API_HOST)"

scheme = ET.parse(ROOT / "Can.xcodeproj/xcshareddata/xcschemes/Can.xcscheme")
assert scheme.find(".//TestableReference/BuildableReference").attrib["BlueprintIdentifier"] == targets["CanTests"][0]
endpoint_setting = re.search(r"^CAN_API_BASE_URL\s*=\s*(\S+)\s*$", (ROOT / "Config/Owner.xcconfig").read_text(), re.MULTILINE)
assert endpoint_setting, "Owner configuration must set an explicit API origin"
endpoint = urlsplit(endpoint_setting.group(1).replace("$()", ""))
assert endpoint.scheme == "https" and endpoint.hostname, "The API origin must use HTTPS"
assert endpoint.username is None and endpoint.password is None and endpoint.port is None
assert not endpoint.path and not endpoint.query and not endpoint.fragment, "The API origin cannot contain URL components"
expected_ats = {"NSExceptionDomains": {"CAN_API_HOST_NAME": {
    "NSExceptionAllowsInsecureHTTPLoads": False,
    "NSExceptionMinimumTLSVersion": "TLSv1.2",
    "NSExceptionRequiresForwardSecrecy": True,
}}}
try:
    endpoint_ip = ipaddress.ip_address(endpoint.hostname)
except ValueError:
    pass
else:
    assert endpoint_ip.version == 4 and endpoint_ip.is_global and not endpoint_ip.is_multicast, "Only public IPv4 endpoints are supported"
    # iOS 17+ requires an explicit IP entry; retain HTTPS and the default TLS protections.
for folder, entitlement in [("Can", "Can.entitlements"), ("CanWidget", "Widget.entitlements")]:
    info = plistlib.loads((ROOT / folder / "Info.plist").read_bytes())
    assert info["CanAPIBaseURL"] == "$(CAN_API_BASE_URL)"
    assert info.get("NSAppTransportSecurity", {}) == expected_ats, (folder, "ATS must match the API IP without weakening transport security")
    rights = plistlib.loads((ROOT / "Config" / entitlement).read_bytes())
    groups = rights["keychain-access-groups"]
    assert groups[0] == "$(AppIdentifierPrefix)$(PRODUCT_BUNDLE_IDENTIFIER)"
    assert info["CanPrivateKeychainGroup"] == groups[0]
    assert info["CanKeychainGroup"] == groups[1]
    assert rights["aps-environment"] == "$(CAN_APNS_ENTITLEMENT_ENVIRONMENT)"
plistlib.loads((ROOT / "Shared/PrivacyInfo.xcprivacy").read_bytes())
icon = ROOT / "Can/Assets.xcassets/AppIcon.appiconset/CanIcon.png"
assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
assert struct.unpack(">II", icon.read_bytes()[16:24]) == (1024, 1024)
fixtures = ROOT / "CanTests/golden_cases.json"
assert len(json.loads(fixtures.read_text())) == 240
reference = ROOT.parents[1] / "reference/calculations"
original_engine = next(reference.rglob("BudgetEngine.swift"))
original_fixtures = next(reference.rglob("golden_cases.json"))
for copy, original in [(ROOT / "Shared/BudgetEngine.swift", original_engine), (fixtures, original_fixtures)]:
    assert hashlib.sha256(copy.read_bytes()).digest() == hashlib.sha256(original.read_bytes()).digest(), copy
count = sum(len(re.findall(r'\bfunc test\w+\(', path.read_text())) for path in (ROOT / "CanTests").glob("*.swift"))
assert count >= 13
print(f"PASS static project: 3 targets, shared test scheme, private/shared Keychain groups, APNs environments, HTTPS origin/strict IP ATS, 1024px icon; {count} XCTest methods and 240 unchanged fixtures registered. Swift build/XCTest/network trust NOT RUN.")
