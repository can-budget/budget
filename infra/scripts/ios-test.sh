#!/usr/bin/env bash
set -euo pipefail
destination=${IOS_DESTINATION:-}
if [[ -z "$destination" ]]; then
  destination="$(python3 - <<'PY'
import json
import re
import subprocess

result = subprocess.run(
    ["xcrun", "simctl", "list", "devices", "available", "--json"],
    check=True, capture_output=True, text=True,
)
devices = json.loads(result.stdout)["devices"]
candidates = []
for runtime, items in devices.items():
    match = re.search(r"\.iOS-(\d+)-(\d+)(?:-(\d+))?$", runtime)
    if not match:
        continue
    version = tuple(int(part or 0) for part in match.groups())
    if version < (17, 0, 0):
        continue
    for item in items:
        if item.get("isAvailable") and item["name"].startswith("iPhone"):
            candidates.append((version, item["name"], item["udid"]))
if not candidates:
    raise SystemExit("No available iPhone simulator with iOS >=17. Install a reviewed runtime in Xcode.")
_, _, device_id = sorted(candidates, reverse=True)[0]
print(f"platform=iOS Simulator,id={device_id}")
PY
)"
fi
exec xcodebuild -project apps/ios/Can.xcodeproj -scheme Can \
  -destination "$destination" CODE_SIGNING_ALLOWED=NO test
