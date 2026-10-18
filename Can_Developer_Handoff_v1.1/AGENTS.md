# Coding agent instructions — Can v1.1

Read DEVELOPER_HANDOFF.md and the referenced spec before implementation. Product language is Russian. Do not treat reference scripts or design contracts as production components already implemented.

Implement the supplied backlog in reviewable increments. Start with M0: inspect the actual repository, run the existing reference/contract checks, report contradictions and true blockers, record versions and the signing/deployment plan. Do not overwrite existing code blindly. No GitHub/Apple/server resources are claimed to have been created by this package.

Hard constraints: Apple-only; 1–10 equal members; one active membership per user; creator is not admin; single active sprint; amount editing sets CURRENT BANK via fixed audited delta; preserve original budget/history; end-date revisions do not rewrite earlier days; late entries are never absorbed by an old target; offline queue is durable/idempotent; shared carry; code+QR24h; staging persists; no categories/bank integrations/monetization/Android/Telegram.

Server must authorize every cabinet ID and serialize conflicting commands with actual PostgreSQL transactions. Do not substitute last-write-wins balances, floats, cache-only receipts or auth mocks for real implementation. update_sprint needs basis revision AND business date, atomic changes and no-op detection. Snapshot includes accepted revisions. Do not ship with open R-01 deletion policy.

Never place secrets in files/logs/PRs or request owner's account password as routine collaboration. Use placeholders and safe per-role access. No production deletion/deploy, paid resource creation, destructive migrations or irreversible account operations without authorization. Keep staging and production isolation.

For each task report changed files, executable test commands and outputs, device/SDK coverage and untested portions. A Linux Swift reference check is not an iOS app build. A unit test is not a PostgreSQL race test. Do not mark unexecuted tests PASS. Use a new agreed change record before altering product scope or math; update spec/contracts/tests together.

Finish with actual runnable app/backend, physical-device TestFlight evidence, all acceptance cases, owner-controlled resources and runbooks—not a generated README saying development is complete.
