.PHONY: check reference-check contract-check swift-check
check: reference-check contract-check
reference-check:
	cd calculations && python -m unittest -v test_budget_engine.py
contract-check:
	python qa/verify_contracts.py
swift-check:
	mkdir -p .build
	swiftc calculations/BudgetEngine.swift calculations/verify.swift -o .build/verify
	.build/verify calculations/golden_cases.json
