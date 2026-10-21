# Security

## Reporting a vulnerability

Report a vulnerability privately through [GitHub Security Advisories](https://github.com/can-budget/budget/security/advisories/new). If private reporting is unavailable, contact [Egor Logutov](mailto:gerc0g@justchimera.com). Include the affected revision, a reproduction using synthetic data, impact and a proposed fix if available. Do not post credentials, account data or an exploit against a live deployment in a public issue.

Сообщайте об уязвимостях через закрытый GitHub Security Advisory или по указанной почте. Для воспроизведения используйте тестовые данные; пароли и финансовые данные в публичные issue не отправляйте.

Security fixes target the current `main`. There is no separate supported legacy release branch.

## Trust boundaries

- The API requires HTTPS with normal certificate and hostname/IP validation. A public IP address is a routing address, not a credential. IP certificates are valid when their SAN matches the requested IP; certificate renewal must remain automated.
- Accounts are provisioned by the operator. Passwords use salted PBKDF2-SHA256 with 600,000 iterations. Login has per-IP, per-login and global rate limits and generic credential errors.
- Random bearer credentials are hashed in PostgreSQL. Access tokens expire after 15 minutes; rotating refresh tokens have bounded retry responses and reuse revocation. Sensitive retry/provider material is encrypted separately.
- Every budget read/mutation checks membership. Cabinet locks, revisions and immutable receipts serialize updates and protect against accidental duplicate application.
- The app stores session credentials in its private Keychain group. The widget uses a separate read-only credential. Local files use iOS data protection. Displaying a budget on the Lock Screen is a user-selected privacy tradeoff.
- PostgreSQL has no published host port. The API and worker run as an unprivileged UID with a read-only root filesystem, dropped capabilities and read-only secret mounts. The secret directory is outside the repository and protected by host permissions.

## CI/CD and a public repository

Workflow files, commit history, PRs and available Actions logs are public when the repository is public. GitHub Secrets values are not exposed by changing visibility; secret masking is not a reason to log them.

Deployment runs only on a push to the canonical repository's `main`, after backend and PostgreSQL checks pass. Pull requests and forks do not receive production credentials. Production uses an environment restricted to `main`, pinned action revisions, a dedicated restricted SSH key and short-lived registry credentials. Treat changes to workflows and deployment scripts as privileged code changes. Docker/operator access is effectively host administration.

Production serves the API, not interactive Swagger/ReDoc or its runtime OpenAPI document. The versioned contract is available in [`contracts/openapi.yaml`](contracts/openapi.yaml).

## Limits and operations

This is a personal, self-hosted application. It does not promise resistance to volumetric denial of service or continuous availability. Keep the host, container images, dependencies and account security updated. A single VPS without off-site backups cannot recover data after disk/server loss. Do not expose database ports, disable TLS verification or put credentials into build configuration.

Free iOS signing and SideStore renewal are separate from API authentication. Renewal can require manual intervention after an Apple, pairing, iOS or network failure. WidgetKit controls refresh timing; a displayed value can be stale.

A source/history credential scan and live configuration review are point-in-time checks, not an independent penetration test or a guarantee that every vulnerability has been found.

References: [GitHub repository visibility](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility), [GitHub Actions security](https://docs.github.com/en/actions/reference/security/secure-use), [Let's Encrypt IP certificates](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability).
