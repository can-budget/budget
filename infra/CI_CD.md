# Backend delivery from main

This is an implemented pipeline and an activation runbook. On 2026-10-14 the
bound GitHub connection and owner-enabled SSH target became available. Server
access, Docker and the reviewed host-preparation helper were verified successfully.
The task branch was published through the updated broker's `--json-file` input.
PR creation returned HTTP 422; remote workflow results remain unverified and the
application is not deployed. Actual evidence and remaining setup
are recorded in [IMPLEMENTATION_STATUS.md](../docs/IMPLEMENTATION_STATUS.md).
iOS delivery is described in [IOS_DELIVERY.md](../docs/IOS_DELIVERY.md).

## Branches and normal releases

- Work on `dev` or a feature branch. Pushes and pull requests run verification.
- Merge reviewed changes into `main`. A push to `main` runs Python/static tests,
  PostgreSQL integration tests and unsigned iOS build/tests for that same commit.
- Only after all three jobs pass, GitHub builds a Linux amd64 backend image,
  publishes it to private GHCR, and deploys its immutable digest to production.
- The server validates the image's source revision, takes and verifies an
  encrypted off-host backup, stops API/worker, applies forward migrations, starts
  the new API/worker and checks their health plus public HTTPS readiness.
- `/opt/can/current` and the successful release record advance only on success.
  There is a short maintenance interruption on this single-server setup.

`workflow_dispatch` runs checks only. Pull requests, forks, tags and `dev` never
receive deployment secrets or publish production images. No path filters silently
skip required checks. Changes confined to iOS/docs still run the main pipeline;
the iPhone binary is **not** uploaded or installed by the backend deployment.

Protect `main` against force pushes and require the `backend`, `postgres` and
`ios` checks for normal PR merges. Treat workflow/infra changes as privileged.
The repository's Actions policy must allow its pinned checkout/setup-python
actions and GHCR publishing. Branch rules, remote `dev` creation, and environment
configuration have not been applied by this task.

The current HQ connection permits task branches under `agent/`, `work/`,
`feature/`, `fix/` and `chore/`, plus draft PRs targeting `main`. It does not
permit direct `main` push/merge or creation of remote `dev`. The owner must create
`dev` through GitHub or explicitly enable that branch in the connection policy;
the existing local `dev` is not evidence of a remote branch. The prepared task
branch `agent/mvp-a31260f0` has been published. Use `hq runner broker git.push
--json-file /tmp/request.json` for a serialized bundle request; positional base64
can exceed the operating system's per-argument limit.

## GitHub configuration

Create an environment named `production`, restricted to branch `main`. To retain
the requested automatic deployment, do not add a required manual reviewer to
that environment. Verify feature availability for the owner's private repository
plan; if environment protection rules are unavailable, the workflow conditions
still apply, but the missing account-side restriction must be recorded.

| Environment setting | Type | Value / purpose |
| --- | --- | --- |
| `CAN_DEPLOY_HOST` | Variable | Confirmed public VPS IPv4 or DNS hostname; not the Mac alias |
| `CAN_DEPLOY_PORT` | Variable | SSH port, normally `22` |
| `CAN_DEPLOY_USER` | Variable | Dedicated `can-operator` account |
| `CAN_DEPLOY_SSH_KEY` | Secret | Separate CI Ed25519 private key; never reuse the owner's Mac/root key |
| `CAN_DEPLOY_KNOWN_HOSTS` | Secret | Verified OpenSSH host-key lines for exactly that host/port |

Obtain and verify the server host-key fingerprint using the provider console or
another already trusted channel before saving `known_hosts`. Do not use an
unverified `ssh-keyscan` result or disable host-key checking. For a nonstandard
port the entry must use `[host]:port`. Keep the private key out of this repository,
chat, shell arguments and build logs. GitHub injects it only into the deploy step;
temporary runner files are removed on exit.

The publish job uses its short-lived `GITHUB_TOKEN` with `packages: write`.
The deploy job has only `contents: read`; no registry write credential is sent
to the server. The image is associated with `can-budget/budget` through its OCI
source label. Keep package visibility private and allow this repository to publish
the package. For an already existing package, check its Actions access explicitly.
The bound connection confirms `can-budget/budget` as the repository target.
`ghcr.io/can-budget/budget-api` is the configured image destination; package
existence, visibility and Actions access have not yet been verified.

For server pulls, configure a separate owner-managed GHCR credential with package
read permission (`read:packages` PAT classic) and access to this private package.
Authorize organization SSO if applicable. Supply it through `docker login
--password-stdin` in a trusted owner session; do not put the value in commands or
in `runtime.conf`. Docker credentials belong to the operating account, outside
the release tree, with restricted permissions. Set a non-secret `DOCKER_CONFIG`
path in runtime configuration if necessary. Rotate/revoke the CI SSH key and
registry token independently.

## One-time VPS preparation

Confirm Ubuntu/Linux amd64, host Python 3.11+ at `/usr/bin/python3`/`python3`,
`curl`, the fixed public IPv4 (DNS is optional), Docker Engine/Compose, clock, disk and
outbound GHCR/Apple/backup access. The inspected VPS has 2 vCPU, 1.8 GiB usable
RAM and a 58 GiB system disk; application capacity has not been measured. Builds run in GitHub
to avoid putting compiler/package-install load on the small production VPS.

The owner selected direct IP HTTPS: `CAN_DOMAIN=151.243.217.6`. The name of this
setting is retained for compatibility; no ACME email or domain purchase is
required. See [IP HTTPS bootstrap](README.md#https-on-the-public-ip) for explicit
public ACME issuance, no-SNI clients, persistent renewal state and starting the
production Caddy service before application credentials are available. This
ingress-only stage does not satisfy the healthy baseline required by CD.

Follow [production operations](README.md) for credentials, PostgreSQL bootstrap,
deletion-journal initialization, systemd timers and a successful restore drill.
The receiver deliberately cannot bootstrap an empty database or invent secrets.
Have the owner provision:

| Host path | Owner / purpose |
| --- | --- |
| `/opt/can` and `/opt/can/releases` | `can-operator`, private deployment state and source bundles |
| `/opt/can/releases/<source-sha>` | Immutable `infra/` files for one exact commit; no secrets |
| `/opt/can/current` | Symlink to the already healthy baseline release |
| `/etc/can/runtime.conf` | root-owned, no group/other writes; non-secret settings and credential paths |
| `/usr/local/lib/can/receive-release.py` | root-owned installed receiver, outside writable release trees |

Use the same `can-operator` / Docker / `can-runtime` account/group arrangement as
the backup runbook. Docker group membership grants host-level authority; the
restricted SSH command does not turn arbitrary reviewed main code into a sandbox.

For Ubuntu 24.04 amd64 with Docker already installed, the owner-authorized
preparation helper performs these account/directory steps and installs missing
`age`/`rclone` packages. Supply a reviewed root-owned receiver file under a
root-owned directory, with no group/other writes:

```sh
sudo bash infra/scripts/prepare-host.sh /absolute/reviewed/receive-release.py
```

The helper checks conflicting accounts/GID before installation, preserves
supplementary groups, and can be rerun. It also prepares `/etc/can/registry` for
the operator's Docker credential store. It does not write credentials,
`runtime.conf`, `authorized_keys`, or `current`, and does not enable timers or
start production containers. Through the HQ SSH broker, run long package
installation as a remote job and inspect its exit status separately; a successful
job launch alone does not establish completed preparation.

On 2026-10-14 this helper at `b76ae3f` completed successfully on the bound VPS.
Independent checks verified the backup executables, existing operator account,
runtime group, directory permissions and installed receiver digest. The existing
operator uses `/bin/sh`, which is supported without changing its shell. Runtime
configuration, credentials, CI authorized key and application bootstrap remain
separate activation steps.

Install the reviewed receiver once from this repository:

```sh
sudo install -d -o root -g root -m 0755 /usr/local/lib/can
sudo install -o root -g root -m 0755 infra/scripts/receive-release.py /usr/local/lib/can/receive-release.py
```

Add **only the dedicated CI public key** to `can-operator`'s owner-managed
`authorized_keys` with these options before the actual public key:

```text
restrict,command="/usr/bin/python3 /usr/local/lib/can/receive-release.py" ssh-ed25519 PUBLIC_KEY_MATERIAL
```

Keep the owner's maintenance access separate. The receiver permits only
`release <40-character-sha> <fixed-ghcr-repository@sha256:digest> <run-number>`.
It rejects links, special files, traversal, duplicate paths and excessive archive
sizes; it accepts only an `infra/` archive. An existing source revision cannot be
replaced with different files. No SCP, shell, PTY or port forwarding is needed by CI.

`runtime.conf` must be compatible with both systemd and the deployment parser:
literal `KEY=value` lines, optional outer quotes, no shell commands, `export`,
variable expansion or inline comments. Include all non-secret settings from
the production/backup runbooks, especially `CAN_REPO_DIR=/opt/can/current`, the
baseline `CAN_RELEASE`, `CAN_IMAGE` digest, `CAN_DOMAIN`, journal and backup paths.
Set `PYTHONDONTWRITEBYTECODE=1`. The deployer supplies candidate SHA/image itself;
the old baseline values do not override its selected release. Do not store actual
database passwords or Apple/registry private keys in this file.

For the first baseline, obtain the image digest from a successful `publish` job
and its **exact** source commit. The first automatic deploy may fail safely because
the server is not initialized yet; that is expected, not a successful deployment.
From a trusted checkout of that commit, create the source-only release bundle:

```sh
git archive --format=tar REVIEWED_SOURCE_SHA infra > /tmp/can-infra.tar
```

The owner extracts those reviewed files under
`/opt/can/releases/REVIEWED_SOURCE_SHA`. Normalize that release's regular files to
mode `0644`, internal directories to `0755`, and its root directory to `0700`
owned by `can-operator`. Do not rely on archive modes or the extraction umask:
Git archives can contain group-writable files rejected by the deployer's trust
checks, while private inner directories prevent container bind-mount access.
The automatic receiver performs this normalization for subsequent releases.
Configure `current` to that directory and use `CAN_RELEASE=REVIEWED_SOURCE_SHA`
plus `CAN_IMAGE=ghcr.io/can-budget/budget-api@sha256:REVIEWED_DIGEST`.
Pull the image; perform the explicit first database startup/migration and journal
setup from [README.md](README.md), then verify API/worker/Caddy and HTTPS. Use
`--no-build` with the prebuilt image; an infra-only bundle intentionally contains
no application build context. Do not put a full checkout, runtime files or Python
caches inside the immutable bundle. Keep state outside it.

With the owner settings exported and journal initialized, the prebuilt-image
equivalent of the README's local-build startup is (run from the release root):

```sh
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml config --quiet
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml pull db caddy api worker
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml up -d --wait --no-build --pull never db
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml --profile maintenance run --rm --no-deps --pull never migrate
docker compose --project-name can-production --env-file /dev/null -f infra/compose.yaml up -d --wait --no-build --pull never api worker caddy
curl --fail --silent --show-error "https://$CAN_DOMAIN/health/ready"
```

Record a real backup and isolated restore before enabling unattended updates.
After a healthy baseline, rerun the failed deploy job or make the next reviewed
main commit. The first successful update creates the release-state records; the
owner does not hand-write success or sequence markers.

## Failure, retry and recovery

Main workflow runs are not cancelled in progress. GitHub may replace pending
runs, so intermediate commits are not guaranteed a separate deployment. A main
head check before publishing and before SSH refuses superseded runs. The host
also locks deployments and persists the highest **workflow run number** before
mutations; an older run cannot overwrite a later attempted release.

Keep this workflow identity stable. Renaming/recreating the workflow or moving to
another repository requires an owner-reviewed sequence-state migration, not
deleting the high-water mark to make an old run pass.

- A missing credential, failed pull, bad source label or failed backup stops the
  update before stopping the running API/worker.
- Changes to database/Caddy/network/volume infrastructure and their bound source
  files require an explicit infrastructure maintenance release. Ordinary backend
  CD does not silently replace a database image or change public ingress.
- After writers are stopped, a migration/start/health failure is reported as a
  failed deployment. The success record/current pointer do not advance. Inspect
  the candidate state and apply a forward fix; do not assume the old containers
  still serve traffic simply because `current` still names the previous release.
  An unfinished `can-production-release-migrate` container blocks the next
  attempt. Confirm it is stopped and inspect the database migration state in a
  trusted owner session before removing that one completed/failed container.
  Do not remove a running container to bypass the guard. Normal migration runs
  use `--rm`; abandoned containers require inspection, and volumes are never
  deleted by this release helper.
- Retry the failed deploy job with the **same SHA, digest and run number** after
  fixing the underlying issue. Rerunning all jobs can rebuild a different digest
  with the same run number, which is refused. Use a new reviewed commit for a
  changed image. Keep old successful images and release directories available.
  An exact retry of an already successful deployment rechecks health and returns
  its result without repeating backup/migration. Retain the baseline directory
  referenced by the running DB/Caddy bind mounts even after backend releases move on.
- A timeout, dropped SSH connection or cancelled GitHub job is not proof the
  server stopped working. Inspect host state before retrying; locks prevent a
  second deploy from running concurrently.

A host crash during the final pointer/state update can leave `current` and the
success record inconsistent. The next attempt must stop before live mutations;
reconcile them against actual container images and health in an owner maintenance
session. Do not erase sequence history or declare success from the symlink alone.

No automatic database downgrade, volume deletion, image pruning, or restore over
live data is performed. Recovery follows the authenticated deletion-journal
runbook and explicit phone-cache reconciliation. Older mobile versions remain
installed after a backend deployment: preserve `/v1` and existing client behavior,
and stage breaking protocol changes through an explicit mobile release plan.

## Acceptance before calling CD active

Run a real main commit end to end and record its workflow URL, source SHA, image
digest and host success record. Confirm a dev push runs checks without a deployment.
Verify HTTPS readiness from outside and, separately, from both phones in China
without VPN. Exercise one shared-budget operation and offline synchronization.
Prove a failed candidate leaves a visible failed workflow and a recoverable server;
do not intentionally break a live family ledger to test this. Use an isolated
disposable deployment or the first pre-use setup window for failure drills.

Local tests cover hostile archives, stale runs, release failure ordering and fake
transports. They do not establish GitHub permissions, SSH reachability, real Docker
health, external backups or availability from China.

## Primary references

- [GitHub: publishing Docker images](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
- [GitHub: container registry authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [GitHub: workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [GitHub: run number and contexts](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)
