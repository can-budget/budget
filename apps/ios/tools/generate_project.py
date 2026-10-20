"""Generate the checked-in native Xcode project using only Python's standard library.

The generated .xcodeproj is sufficient for Xcode; this script is optional tooling.
"""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
objects = {}

def ident(name):
    return hashlib.sha256(name.encode()).hexdigest()[:24].upper()

def add(object_key, **value):
    key = ident(object_key)
    objects[key] = value
    return key

def emit(value, depth=0):
    if isinstance(value, dict):
        return "{\n" + "\n".join("\t" * (depth + 1) + json.dumps(k) + " = " + emit(v, depth + 1) + ";" for k, v in value.items()) + "\n" + "\t" * depth + "}"
    if isinstance(value, list):
        return "(" + ", ".join(emit(v, depth + 1) for v in value) + ")"
    return json.dumps(str(value), ensure_ascii=False)

def file(path, kind=None):
    kinds = {".swift": "sourcecode.swift", ".plist": "text.plist.xml", ".entitlements": "text.plist.entitlements", ".xcconfig": "text.xcconfig", ".xcprivacy": "text.plist.xml", ".json": "text.json", ".xcassets": "folder.assetcatalog"}
    return add("file:" + path, isa="PBXFileReference", lastKnownFileType=kind or kinds.get(Path(path).suffix, "text"), path=path, sourceTree="<group>")

paths = sorted(str(p.relative_to(ROOT)) for folder in ["Can", "Shared", "CanWidget", "CanTests", "Config"] for p in (ROOT / folder).glob("*") if p.is_file() or p.suffix == ".xcassets")
refs = {path: file(path) for path in paths}
products = {}
for target, suffix, kind in [("Can", ".app", "wrapper.application"), ("CanWidget", ".appex", "wrapper.app-extension"), ("CanTests", ".xctest", "wrapper.cfbundle")]:
    products[target] = add("product:" + target, isa="PBXFileReference", explicitFileType=kind, includeInIndex="0", path=target + suffix, sourceTree="BUILT_PRODUCTS_DIR")
product_group = add("products", isa="PBXGroup", children=list(products.values()), name="Products", sourceTree="<group>")
main_group = add("main", isa="PBXGroup", children=list(refs.values()) + [product_group], sourceTree="<group>")

def phase(target, name, files, isa):
    builds = [add(f"build:{target}:{name}:{path}", isa="PBXBuildFile", fileRef=refs[path]) for path in files]
    return add(f"phase:{target}:{name}", isa=isa, buildActionMask="2147483647", files=builds, runOnlyForDeploymentPostprocessing="0")

def configs(target, settings):
    result = []
    for config in ["Debug", "Release"]:
        base = {**settings, "CAN_DISTRIBUTION": "owner", "CAN_PUSH_ENABLED": "YES", "CAN_APNS_ENVIRONMENT": "sandbox" if config == "Debug" else "production",
                "CAN_APNS_ENTITLEMENT_ENVIRONMENT": "development" if config == "Debug" else "production"}
        base.update({"SWIFT_OPTIMIZATION_LEVEL": "-Onone" if config == "Debug" else "-O", "DEBUG_INFORMATION_FORMAT": "dwarf" if config == "Debug" else "dwarf-with-dsym"})
        if config == "Debug": base.update({"SWIFT_ACTIVE_COMPILATION_CONDITIONS": "DEBUG", "ENABLE_TESTABILITY": "YES"})
        result.append(add(f"config:{target}:{config}", isa="XCBuildConfiguration", baseConfigurationReference=refs["Config/Owner.xcconfig"], buildSettings=base, name=config))
    return add("configs:" + target, isa="XCConfigurationList", buildConfigurations=result, defaultConfigurationIsVisible="0", defaultConfigurationName="Release")

shared = [p for p in paths if p.startswith("Shared/") and p.endswith(".swift")]
target_ids = {name: ident("target:" + name) for name in products}
widget_proxy = add("widget-proxy", isa="PBXContainerItemProxy", containerPortal=ident("project"), proxyType="1", remoteGlobalIDString=target_ids["CanWidget"], remoteInfo="CanWidget")
widget_dependency = add("widget-dependency", isa="PBXTargetDependency", target=target_ids["CanWidget"], targetProxy=widget_proxy)
app_proxy = add("app-proxy", isa="PBXContainerItemProxy", containerPortal=ident("project"), proxyType="1", remoteGlobalIDString=target_ids["Can"], remoteInfo="Can")
test_dependency = add("test-dependency", isa="PBXTargetDependency", target=target_ids["Can"], targetProxy=app_proxy)
embed_file = add("embed-widget-file", isa="PBXBuildFile", fileRef=products["CanWidget"], settings={"ATTRIBUTES": ["RemoveHeadersOnCopy"]})
embed = add("embed-widget", isa="PBXCopyFilesBuildPhase", buildActionMask="2147483647", dstPath="", dstSubfolderSpec="13", files=[embed_file], name="Embed App Extensions", runOnlyForDeploymentPostprocessing="0")

for name in products:
    source = [p for p in paths if p.startswith(name + "/") and p.endswith(".swift")]
    if name != "CanTests": source += shared
    resources = ["CanTests/golden_cases.json", "CanTests/today_topup_cases.json"] if name == "CanTests" else ["Shared/PrivacyInfo.xcprivacy"]
    if name == "Can": resources += ["Can/Assets.xcassets"]
    phases = [phase(name, "Sources", source, "PBXSourcesBuildPhase"), phase(name, "Frameworks", [], "PBXFrameworksBuildPhase"), phase(name, "Resources", resources, "PBXResourcesBuildPhase")]
    if name == "Can": phases.append(embed)
    settings = {"PRODUCT_NAME": "$(TARGET_NAME)", "CODE_SIGN_STYLE": "Automatic", "DEVELOPMENT_TEAM": "$(CAN_DEVELOPMENT_TEAM)", "SWIFT_VERSION": "5.0", "IPHONEOS_DEPLOYMENT_TARGET": "17.0", "TARGETED_DEVICE_FAMILY": "1", "SDKROOT": "iphoneos", "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator", "SWIFT_STRICT_CONCURRENCY": "targeted", "GENERATE_INFOPLIST_FILE": "NO", "SUPPORTS_MACCATALYST": "NO", "LD_RUNPATH_SEARCH_PATHS": ["$(inherited)", "@executable_path/Frameworks"]}
    if name == "Can": settings.update({"PRODUCT_BUNDLE_IDENTIFIER": "$(CAN_BUNDLE_ID)", "INFOPLIST_FILE": "Can/Info.plist", "CODE_SIGN_ENTITLEMENTS": "Config/Can.entitlements", "ASSETCATALOG_COMPILER_APPICON_NAME": "AppIcon"})
    elif name == "CanWidget": settings.update({"PRODUCT_BUNDLE_IDENTIFIER": "$(CAN_WIDGET_BUNDLE_ID)", "INFOPLIST_FILE": "CanWidget/Info.plist", "CODE_SIGN_ENTITLEMENTS": "Config/Widget.entitlements", "APPLICATION_EXTENSION_API_ONLY": "YES", "SKIP_INSTALL": "YES", "LD_RUNPATH_SEARCH_PATHS": ["$(inherited)", "@executable_path/Frameworks", "@executable_path/../../Frameworks"]})
    else: settings.update({"PRODUCT_BUNDLE_IDENTIFIER": "$(CAN_BUNDLE_ID).tests", "GENERATE_INFOPLIST_FILE": "YES", "TEST_HOST": "$(BUILT_PRODUCTS_DIR)/Can.app/Can", "BUNDLE_LOADER": "$(TEST_HOST)"})
    add("target:" + name, isa="PBXNativeTarget", buildConfigurationList=configs(name, settings), buildPhases=phases, buildRules=[], dependencies=[widget_dependency] if name == "Can" else [test_dependency] if name == "CanTests" else [], name=name, productName=name, productReference=products[name], productType={"Can": "com.apple.product-type.application", "CanWidget": "com.apple.product-type.app-extension", "CanTests": "com.apple.product-type.bundle.unit-test"}[name])

project = add("project", isa="PBXProject", attributes={"BuildIndependentTargetsInParallel": "YES", "LastUpgradeCheck": "2600", "TargetAttributes": {target_ids[n]: {"CreatedOnToolsVersion": "26.0"} for n in target_ids}}, buildConfigurationList=configs("Project", {"CLANG_ENABLE_MODULES": "YES", "CLANG_ENABLE_OBJC_ARC": "YES", "SWIFT_VERSION": "5.0", "IPHONEOS_DEPLOYMENT_TARGET": "17.0"}), compatibilityVersion="Xcode 14.0", developmentRegion="ru", hasScannedForEncodings="0", knownRegions=["ru", "en", "Base"], mainGroup=main_group, productRefGroup=product_group, projectDirPath="", projectRoot="", targets=list(target_ids.values()))
document = {"archiveVersion": "1", "classes": {}, "objectVersion": "56", "objects": objects, "rootObject": project}
(ROOT / "Can.xcodeproj/xcshareddata/xcschemes").mkdir(parents=True, exist_ok=True)
(ROOT / "Can.xcodeproj/project.pbxproj").write_text("// !$*UTF8*$!\n" + emit(document) + "\n")

scheme = ET.Element("Scheme", LastUpgradeVersion="2600", version="1.3")
build = ET.SubElement(scheme, "BuildAction", parallelizeBuildables="YES", buildImplicitDependencies="YES")
entries = ET.SubElement(build, "BuildActionEntries")
def reference(parent, name):
    return ET.SubElement(parent, "BuildableReference", BuildableIdentifier="primary", BlueprintIdentifier=target_ids[name], BuildableName=name + (".app" if name == "Can" else ".xctest"), BlueprintName=name, ReferencedContainer="container:Can.xcodeproj")
for name in ["Can", "CanTests"]:
    test = name == "CanTests"
    entry = ET.SubElement(entries, "BuildActionEntry", buildForTesting="YES", buildForRunning="NO" if test else "YES", buildForProfiling="NO" if test else "YES", buildForArchiving="NO" if test else "YES", buildForAnalyzing="YES")
    reference(entry, name)
test_action = ET.SubElement(scheme, "TestAction", buildConfiguration="Debug", selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB", selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB", shouldUseLaunchSchemeArgsEnv="YES")
reference(ET.SubElement(ET.SubElement(test_action, "Testables"), "TestableReference", skipped="NO"), "CanTests")
launch = ET.SubElement(scheme, "LaunchAction", buildConfiguration="Debug", selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB", selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB", launchStyle="0", useCustomWorkingDirectory="NO", ignoresPersistentStateOnLaunch="NO", debugDocumentVersioning="YES", debugServiceExtension="internal", allowLocationSimulation="YES")
reference(ET.SubElement(launch, "BuildableProductRunnable", runnableDebuggingMode="0"), "Can")
profile = ET.SubElement(scheme, "ProfileAction", buildConfiguration="Release", shouldUseLaunchSchemeArgsEnv="YES", savedToolIdentifier="", useCustomWorkingDirectory="NO", debugDocumentVersioning="YES")
reference(ET.SubElement(profile, "BuildableProductRunnable", runnableDebuggingMode="0"), "Can")
ET.SubElement(scheme, "AnalyzeAction", buildConfiguration="Debug")
ET.SubElement(scheme, "ArchiveAction", buildConfiguration="Release", revealArchiveInOrganizer="YES")
ET.indent(scheme)
ET.ElementTree(scheme).write(ROOT / "Can.xcodeproj/xcshareddata/xcschemes/Can.xcscheme", encoding="utf-8", xml_declaration=True)
print("Generated native Can.xcodeproj and shared Can scheme")
