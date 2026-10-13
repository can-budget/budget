"""Static project/fixture consistency checks. Does not compile Swift or run XCTest."""
from pathlib import Path
import hashlib
import json
import plistlib
import re
import struct
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

scheme = ET.parse(ROOT / "Can.xcodeproj/xcshareddata/xcschemes/Can.xcscheme")
assert scheme.find(".//TestableReference/BuildableReference").attrib["BlueprintIdentifier"] == targets["CanTests"][0]
for folder, entitlement in [("Can", "Can.entitlements"), ("CanWidget", "Widget.entitlements")]:
    info = plistlib.loads((ROOT / folder / "Info.plist").read_bytes())
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
reference = ROOT.parents[1] / "Can_Developer_Handoff_v1.1/reference"
original_engine = next(reference.rglob("BudgetEngine.swift"))
original_fixtures = next(reference.rglob("golden_cases.json"))
for copy, original in [(ROOT / "Shared/BudgetEngine.swift", original_engine), (fixtures, original_fixtures)]:
    assert hashlib.sha256(copy.read_bytes()).digest() == hashlib.sha256(original.read_bytes()).digest(), copy
count = sum(len(re.findall(r'\bfunc test\w+\(', path.read_text())) for path in (ROOT / "CanTests").glob("*.swift"))
assert count >= 13
print(f"PASS static project: 3 targets, shared test scheme, private/shared Keychain groups, APNs environments, 1024px icon; {count} XCTest methods and 240 unchanged fixtures registered. Swift build/XCTest NOT RUN.")
