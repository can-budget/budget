# Backend delivery from main

GitHub Actions verifies the backend and disposable PostgreSQL tests, publishes a GHCR image, and delivers its immutable digest through the restricted production receiver. The iOS workflow is independent and does not gate backend deployment.

The default personal deployment uses `CAN_RECOVERY_MODE=single_server`; off-host backups are optional and require separate configuration. For your own fork, update the canonical repository guards in `.github/workflows/verify.yml` and the bound GHCR image identities in the sender/receiver/deployer before enabling deployment. Forks do not inherit production secrets or permission to deploy to the maintainer’s server.

## Branches and normal releases

- Work on `dev` or a feature branch. Pushes and pull requests run verification.
- Merge reviewed changes into `main`. A push to `main` runs Python/static tests
  and PostgreSQL integration tests for that same commit.
- Only after both backend jobs pass, GitHub builds a Linux amd64 backend image,
  publishes it to private GHCR, and deploys its immutable digest to production.
- The server validates the image's source revision, takes and verifies an
  encrypted off-host backup only in `off_host` mode, stops API/worker, applies forward migrations, starts
  the new API/worker and checks their health plus public HTTPS readiness.
- `/opt/can/current` and the successful release record advance only on success.
  There is a short maintenance interruption on this single-server setup.

`workflow_dispatch` runs checks only. Pull requests, forks, tags and `dev` never
receive deployment secrets or publish production images. No path filters silently
skip required checks. Changes confined to iOS/docs still run the main pipeline;
the iPhone binary is **not** uploaded or installed by the backend deployment.

Protect `main` against force pushes and require the `backend` and `postgres`
checks for normal PR merges when repository protection is available. Treat workflow/infra changes as privileged.
The repository's Actions policy must allow its pinned checkout/setup-python
actions and GHCR publishing. The independent iOS workflow runs for changes to
its sources and supports manual dispatch; it has no production secrets.
Branch protection availability depends on the owner's GitHub plan.

The workstation uses the owner's existing GitHub and pinned SSH access. Changes
are reviewed in task-branch PRs targeting `main`; no remote `dev` branch is needed
for this one-server deployment. Merging a PR triggers the main pipeline.

Compose validation explicitly enables the `maintenance` profile for both current
and candidate releases. Without it, real Compose omits `migrate` from the JSON
configuration, preventing verification that API, worker and migration use the
same immutable image. Runtime source/image/health checks remain mandatory.

## Temporary HTTP/1.1 ingress hardening

Caddy serves HTTPS over HTTP/1.1 while its pinned stable release contains the
affected HTTP/2 implementation described in [GO-2026-6603](https://pkg.go.dev/vuln/GO-2026-6603),
[GO-2026-6610](https://pkg.go.dev/vuln/GO-2026-6610),
[GO-2026-6611](https://pkg.go.dev/vuln/GO-2026-6611),
[GO-2026-6612](https://pkg.go.dev/vuln/GO-2026-6612) and
[GO-2026-6617](https://pkg.go.dev/vuln/GO-2026-6617). TLS, certificates and API
authorization remain enabled. Reassess this policy after a patched stable Caddy
release; enabling other protocols requires separate infrastructure maintenance.

The initial change requires the owner to create `/etc/can/approved-http1-release`
on the VPS with exactly the reviewed 40-character **merged main commit SHA** and
an optional final newline. The file and its parent must be root-owned and not
writable by group or others; mode `0644` allows the deployment operator to read
the non-secret SHA. Create it before the deploy job, or rerun that job after
approval. Remove the file after verifying the successful release.

This approval permits only the exact insertion of `servers { protocols h1 }`
into the existing Caddy global block. It does not permit changes to TLS,
upstreams, container images, Compose settings, PostgreSQL or persistent storage.
The approved release recreates Caddy with its existing certificate volumes,
checks its health and public HTTPS before promotion. Ordinary backend updates
do not restart Caddy. A failed maintenance does not promote the successful
release record and requires forward recovery, as with other deployment failures.

## GitHub configuration

Configure a `production` environment with a deployment branch policy restricted to `main`. Keep the dedicated SSH key and verified host keys in environment secrets. After making a repository public, verify branch protection, fork workflow approval, read-only default workflow permissions and secret scanning. Public workflow definitions and logs are visible to visitors; secret values remain private.

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
The deploy job has `contents: read` and `packages: read`; no registry write
credential is sent to the server. The image is associated with `can-budget/budget` through its OCI
source label. Keep package visibility private and allow this repository to publish
the package. For an already existing package, check its Actions access explicitly.
The bound connection confirms `can-budget/budget` as the repository target.
The canonical image is `ghcr.io/can-budget/budget-api`. Package visibility is configured separately from repository visibility; verify registry access on your own deployment.

For automatic pulls, the sender prefixes the infra archive with a bounded JSON
envelope containing the deploy job's short-lived, read-only `GITHUB_TOKEN`. It
travels inside the pinned SSH channel, never in command arguments or the source
archive. The installed receiver validates the envelope, writes a GHCR-only Docker
configuration under a private temporary directory (0700/0600), and passes only
its path to the deployer. Success and failure both remove the configuration.
No permanent PAT or Docker login is needed on the VPS. Upgrade the root-owned
receiver to this reviewed version before enabling the updated sender. Legacy
source-only archives remain supported for owner-managed registry access.

## One-time VPS preparation

Confirm Ubuntu/Linux amd64, host Python 3.11+ at `/usr/bin/python3`/`python3`,
`curl`, the fixed public IPv4 (DNS is optional), Docker Engine/Compose, clock, disk and
outbound GHCR/Apple/backup access. The inspected VPS has 2 vCPU, 1.8 GiB usable
RAM and a 58 GiB system disk; application capacity has not been measured. Builds run in GitHub
to avoid putting compiler/package-install load on the small production VPS.

Set `CAN_DOMAIN` to your public IPv4 or DNS host. For an IP endpoint, follow [IP HTTPS bootstrap](README.md#https-on-the-public-ip): issuance, renewal state and no-SNI clients are configured explicitly. An ingress-only startup is not the healthy backend baseline required by CD.

Follow [production operations](README.md) for credentials and PostgreSQL bootstrap.
External journals, backup timers and restore drills are not used in the selected
`single_server` mode.
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
Set `PYTHONDONTWRITEBYTECODE=1` and `CAN_RECOVERY_MODE=single_server`. The deployer supplies candidate SHA/image itself;
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

For optional `off_host` mode, record a real backup and isolated restore before
enabling unattended updates. The selected `single_server` mode skips backups.
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
