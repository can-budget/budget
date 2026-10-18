.PHONY: check reference-check contract-check swift-check spec
check: reference-check contract-check
reference-check:
	cd reference && python -m unittest -v test_budget_engine.py
contract-check:
	python qa/verify_contracts.py
swift-check:
	mkdir -p .build
	swiftc reference/BudgetEngine.swift reference/verify.swift -o .build/verify
	.build/verify reference/golden_cases.json
spec:
	python qa/build_spec.py
