"""Fail closed before publication/deployment when a main run has become obsolete."""

import json
import os
import re
import urllib.request


def main() -> None:
    if (
        os.environ.get("GITHUB_EVENT_NAME") != "push"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise SystemExit("Production requires a push to main.")
    if os.environ.get("GITHUB_REPOSITORY") != "can-budget/budget":
        raise SystemExit("Production repository binding does not match.")
    sha = os.environ["GITHUB_SHA"]
    if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
        raise SystemExit("Invalid source revision.")
    request = urllib.request.Request(
        "https://api.github.com/repos/can-budget/budget/git/ref/heads/main",
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        head = json.load(response)["object"]["sha"]
    if sha != head:
        raise SystemExit("This main run was superseded; the newer commit must deploy.")
    print("Production source still matches the current main branch.")


if __name__ == "__main__":
    main()
