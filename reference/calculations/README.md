# Version-2 calculations

Pure Python and Swift engines for historical budget projections. Inputs include integer minor units, business dates, daily totals, carry decisions and accepted revisions. Fixed bank adjustments must not be counted again as topups or expenses.

`golden_cases.json` contains 240 regression scenarios. The app uses an unchanged copy of this engine and fixture set for compatibility. Current same-day topup behavior is layered in the production calculation adapters and tested separately.

From the repository root: `make reference-check` and `make -C reference swift-check`.
