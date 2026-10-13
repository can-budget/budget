# Backend delivery from main

This is an implemented pipeline and an activation runbook. No remote workflow,
SSH connection, deployment or production migration has been executed by this
task. HQ currently reports `repository has no company Git mapping`; the task
also has no SSH/production administration connector. Owner-side setup below is
still required. iOS delivery is described in [IOS_DELIVERY.md](../docs/IOS_DELIVERY.md).

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
`can-budget/budget` and `ghcr.io/can-budget/budget-api` are the configured targets
from the owner's project details, not verified evidence that the external
repository/package connection currently exists.

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
`curl`, public IP, DNS, Docker Engine/Compose, clock, disk and
outbound GHCR/Apple/backup access. The intended small VPS is 2 vCPU / 2 GiB RAM;
actual capacity and runtime behavior have not been measured. Builds run in GitHub
to avoid putting compiler/package-install load on the small production VPS.

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
`/opt/can/releases/REVIEWED_SOURCE_SHA`, configures `current` to that directory and
uses `CAN_RELEASE=REVIEWED_SOURCE_SHA` plus `CAN_IMAGE=ghcr.io/can-budget/budget-api@sha256:REVIEWED_DIGEST`.
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
