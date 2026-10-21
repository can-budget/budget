# Contributing / Участие в разработке

Small, focused pull requests are welcome. Describe the behavior that changes and how you verified it. For a substantial change to budget arithmetic, authentication or synchronization, open an issue first so the invariants can be agreed before implementation.

Небольшие PR приветствуются. Опишите изменение поведения и выполненные проверки. Существенные изменения расчётов, авторизации или синхронизации сначала обсудите в issue.

## Checks

```sh
make install
make verify
```

For iOS changes, also run `make ios-build` and `make ios-test` on macOS with Xcode. PostgreSQL integration tests require an explicitly disposable `TEST_DATABASE_URL`; CI supplies its own isolated database. Never point tests at a live family ledger.

Use integer minor units for amounts. Preserve command idempotency, membership checks, historical calculations and unresolved offline intentions. Changes to a public contract should include matching client and server changes and relevant regression cases.

Do not commit credentials, personal financial data, provisioning profiles, device pairing files or exported sessions. Use synthetic data in screenshots and tests. See [SECURITY.md](SECURITY.md) for private vulnerability reports.

Contributions are provided under the [MIT license](LICENSE).
