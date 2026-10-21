# Calculation references

This directory contains version-2 Python and Swift calculation engines, their 240 unchanged regression fixtures, and the historical API schema used by those fixtures. The production API and current calculation policy are in [`../contracts`](../contracts).

```sh
make reference-check   # from the repository root
make contract-check
make -C reference swift-check
```

`calculations/` is a pure deterministic reference, with no network, authentication or persistence. The app and backend also test version-3 same-day topups separately. The fixture engines are retained for historical compatibility; changes to current behavior should not silently rewrite old expected results.
