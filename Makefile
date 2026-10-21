PYTHON ?= .venv/bin/python
export IOS_DESTINATION
API_ROOT := services/api
SIMULATOR_TOOLS := apps/ios/tools/run_simulator.py apps/ios/tools/issue_simulator_session.py
IOS_TOOLS := $(SIMULATOR_TOOLS) apps/ios/tools/adhoc_release.py apps/ios/tools/sideload_release.py apps/ios/tools/refresh_shortcut.py
export IOS_DEVICE IOS_IPA IOS_BUILD_NUMBER
export PYTHONPATH := $(CURDIR)/$(API_ROOT)

.PHONY: install dev test test-unit test-postgres test-one reference-check contract-check infra-check infra-test ios-project-check secret-check lint format typecheck verify ios-test ios-build ios-simulator ios-doctor ios-release ios-inspect ios-devices ios-install ios-sideload ios-refresh-shortcut ios-refresh-send
install:
	python3 scripts/bootstrap.py
	$(PYTHON) -m pip install -c services/api/requirements-dev.lock -e './services/api[dev]'
dev:
	$(PYTHON) -m uvicorn can_api.app:app --host 127.0.0.1 --port 8000 --reload
test:
	$(PYTHON) -m pytest services/api/tests
test-unit:
	$(PYTHON) -m pytest services/api/tests -m 'not postgres'
test-postgres:
	@test -n "$$TEST_DATABASE_URL" || { echo 'Set TEST_DATABASE_URL to an explicitly authorized disposable PostgreSQL database.'; exit 2; }
	$(PYTHON) -m pytest services/api/tests -m postgres -v
test-one:
	$(PYTHON) -m pytest $(TEST)
reference-check:
	cd Can_Developer_Handoff_v1.1/reference && $(abspath $(PYTHON)) -m unittest -v test_budget_engine.py
contract-check:
	$(PYTHON) Can_Developer_Handoff_v1.1/qa/verify_contracts.py
	$(PYTHON) scripts/verify_contract.py
infra-check:
	$(PYTHON) infra/static_check.py
infra-test:
	# Recovery and release safety tests use temporary files and fake transports.
	$(PYTHON) -m pytest infra/tests
ios-project-check:
	$(PYTHON) apps/ios/tools/check_project.py
secret-check:
	$(PYTHON) scripts/check_secrets.py
lint:
	$(PYTHON) -m ruff check --config services/api/pyproject.toml services/api scripts infra $(IOS_TOOLS)
format:
	$(PYTHON) -m ruff format --config services/api/pyproject.toml services/api scripts infra $(IOS_TOOLS)
typecheck:
	$(PYTHON) -m mypy --config-file services/api/pyproject.toml services/api/can_api
verify: lint typecheck test-unit reference-check contract-check infra-check infra-test ios-project-check secret-check
ios-test:
	bash infra/scripts/ios-test.sh
ios-build:
	xcodebuild -project apps/ios/Can.xcodeproj -scheme Can -sdk iphonesimulator CODE_SIGNING_ALLOWED=NO build
ios-simulator:
	python3 apps/ios/tools/run_simulator.py $(SIMULATOR_ARGS)
ios-doctor:
	python3 apps/ios/tools/adhoc_release.py doctor
ios-release:
	python3 apps/ios/tools/adhoc_release.py build
ios-inspect:
	python3 apps/ios/tools/adhoc_release.py inspect
ios-devices:
	python3 apps/ios/tools/adhoc_release.py devices
ios-install:
	python3 apps/ios/tools/adhoc_release.py install
ios-sideload:
	python3 apps/ios/tools/sideload_release.py
ios-refresh-shortcut:
	python3 apps/ios/tools/refresh_shortcut.py
ios-refresh-send:
	python3 apps/ios/tools/refresh_shortcut.py --send
