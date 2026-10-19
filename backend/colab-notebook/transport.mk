COLAB_STATE_DIR := $(CLOUDMAKE_STATE_ROOT)/colab-notebook/$(COLAB_SESSION)
COLAB_ARCHIVE := $(COLAB_STATE_DIR)/source.tar.gz
COLAB_FINGERPRINT := $(COLAB_STATE_DIR)/source.sha256
COLAB_REMOTE_FINGERPRINT_COPY := $(COLAB_STATE_DIR)/remote-source.sha256
COLAB_REMOTE_OWNER_COPY := $(COLAB_STATE_DIR)/remote-owner.json
COLAB_CONTROL_STATE_COPY := $(COLAB_STATE_DIR)/remote-control-state.txt
COLAB_SYNC_RESULT := $(COLAB_STATE_DIR)/sync-result.sha256
COLAB_TARGET_FILE := $(COLAB_STATE_DIR)/target
COLAB_TARGET_RESULT := $(COLAB_STATE_DIR)/target-result.json
COLAB_ENVIRONMENT_PROFILE := $(COLAB_STATE_DIR)/environment-profile.json
COLAB_OCI_CONTROL := $(COLAB_STATE_DIR)/oci-control
COLAB_OCI_PREFLIGHT := $(COLAB_STATE_DIR)/oci-preflight.json
COLAB_RESOURCE_STATE := $(COLAB_STATE_DIR)/resource-state
COLAB_ARTIFACT_ARCHIVE := $(COLAB_STATE_DIR)/artifacts.tar.gz
COLAB_RUN_NOTEBOOK := $(COLAB_STATE_DIR)/runner.ipynb
CLOUDMAKE_ALLOCATION_RESULT ?= $(COLAB_STATE_DIR)/allocation-result.json
CLOUDMAKE_RETRY_FOR_SECONDS ?= 0
COLAB_CHECKPOINT_STATE_DIR := $(COLAB_STATE_DIR)/checkpoint
COLAB_CHECKPOINT_RESTORE_RESULT := $(COLAB_CHECKPOINT_STATE_DIR)/$(if $(CLOUDMAKE_RUN_ID),$(CLOUDMAKE_RUN_ID),operation)-restore.json
COLAB_CHECKPOINT_PUBLISH_RESULT := $(COLAB_CHECKPOINT_STATE_DIR)/$(if $(CLOUDMAKE_RUN_ID),$(CLOUDMAKE_RUN_ID),operation)-publish.json

COLAB_REMOTE_ROOT := /content/.cloud-build/workspace
COLAB_REMOTE_ARCHIVE := /content/cloud-build-source.tar.gz
COLAB_REMOTE_FINGERPRINT_INCOMING := /content/cloud-build-source.sha256
COLAB_REMOTE_FINGERPRINT := $(COLAB_REMOTE_ROOT)/source.sha256
COLAB_REMOTE_MANIFEST_INCOMING := /content/cloud-build-source-manifest.json
COLAB_REMOTE_PREVIOUS_MANIFEST_INCOMING := /content/cloud-build-previous-manifest.json
COLAB_REMOTE_OWNER_INCOMING := /content/cloud-build-owner.json
COLAB_REMOTE_OWNER := $(COLAB_REMOTE_ROOT)/.cloudmake-owner.json
COLAB_REMOTE_SYNC_RESULT := $(COLAB_REMOTE_ROOT)/sync-result.sha256
COLAB_REMOTE_PREPARED_INCOMING := /content/cloud-build-prepared
COLAB_REMOTE_PREPARED := $(COLAB_REMOTE_ROOT)/.cloudmake-prepared
COLAB_REMOTE_TARGET := /content/cloud-build-target
COLAB_REMOTE_TARGET_RESULT := /content/.cloud-build/target-result.json
COLAB_REMOTE_ENVIRONMENT_PROFILE := /content/.cloud-build/vm-capabilities.json
COLAB_REMOTE_OCI_CONTROL := /content/cloudmake-oci-control
COLAB_REMOTE_OCI_RUNNER := /content/cloudmake-oci-runner.py
COLAB_REMOTE_OCI_PREFLIGHT := /content/.cloud-build/oci-preflight.json
COLAB_REMOTE_OCI_CACHE := /content/.cloud-build/oci-cache
COLAB_REMOTE_ARTIFACTS := /content/.cloud-build/artifacts.tar.gz

COLAB_INVOCATION_ID := $(if $(CLOUDMAKE_RUN_ID),$(CLOUDMAKE_RUN_ID),manual)
COLAB_CREATED_MARKER := $(COLAB_STATE_DIR)/created-$(COLAB_INVOCATION_ID)
COLAB_RESET_MARKER := $(COLAB_STATE_DIR)/fresh-runtime-$(COLAB_INVOCATION_ID)
COLAB_PREPARED_COPY := $(COLAB_STATE_DIR)/remote-prepared
COLAB_PREPARED_RECEIPT := $(COLAB_STATE_DIR)/prepared
COLAB_PREPARE_TARGET_FILE := $(COLAB_STATE_DIR)/prepare-target

COLAB_ACCELERATOR := $(if $(strip $(COLAB_GPU)),--gpu $(COLAB_GPU),)

.PHONY: help start status stop sync collect dispatch fetch shell open environment \
	_colab-start _colab-sync _colab-prepare _colab-execute _colab-collect _colab-fetch \
	_colab-oci-prepare \
	_colab-fetch-ready _colab-open _colab-stop _colab-start-ready \
	_colab-checkpoint-restore _colab-checkpoint-publish _colab-environment workspace-purge

help:
	@echo 'Usage: make BACKEND=colab-notebook <target>'
	@echo 'Transport: native Colab contents/kernel APIs (no SSH or Git remote)'
	@echo 'Local backend: local (direct project Make invocation)'
	@echo 'Notebook backends: colab-notebook, kaggle-notebook'
	@echo 'SSH backends: colab-ssh, codespaces-ssh, host-ssh, lightning-studio-ssh'
	@echo 'SDK backends: modal-sandbox'
	@echo
	@echo 'Targets:'
	@echo '  prerequisites  Check required local commands and settings'
	@echo '  doctor   Check prerequisites plus Colab authentication (no allocation)'
	@echo '  start    Create or reuse the named Colab session'
	@echo '  sync     Upload the current working tree through colab upload'
	@echo '  sync-dry-run  Show selected source changes without contacting Colab'
	@echo '  dispatch  Internal arbitrary project-target execution (launcher managed)'
	@echo '  collect  Run REMOTE_TARGET, collect REMOTE_COLLECT_DIR_B64, and fetch it'
	@echo '  fetch    Download and safely extract prepared artifacts'
	@echo '  open     Open the same CLI-owned runtime in Colab'
	@echo '  stop     Release the runtime'

start: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-start-ready

status: doctor
	@mkdir -p '$(CLOUDMAKE_STATE_ROOT)/status'
	@set -e; temporary='$(CLOUDMAKE_STATE_ROOT)/status/$(BACKEND)-$$$$.tmp'; \
		if $(COLAB_BIN) status -s '$(COLAB_SESSION)' > "$$temporary" 2>&1; then \
			cat "$$temporary"; \
			$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/normalize_status.py' --backend '$(BACKEND)' < "$$temporary"; \
			rm -f "$$temporary"; \
		else code=$$?; cat "$$temporary"; rm -f "$$temporary"; exit $$code; fi

environment: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-environment

stop: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-stop

sync: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-sync

dispatch: doctor
	@if test -z '$(REMOTE_TARGET_B64)'; then echo 'REMOTE_TARGET_B64 is required for dispatch' >&2; exit 2; fi
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-execute

collect: doctor
	@if test -z '$(REMOTE_TARGET)' && test -z '$(REMOTE_TARGET_B64)'; then echo 'REMOTE_TARGET or REMOTE_TARGET_B64 is required for collect' >&2; exit 2; fi
	@if test -z '$(REMOTE_COLLECT_DIR_B64)'; then echo 'REMOTE_COLLECT_DIR_B64 is required for collect' >&2; exit 2; fi
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-collect

fetch: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-fetch

open: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-open

shell:
	@echo 'The native Colab backend intentionally has no SSH shell.' >&2
	@echo 'Use "make BACKEND=colab-notebook open" for the notebook execution surface.' >&2
	@exit 2

$(COLAB_STATE_DIR):
	mkdir -p '$@'

_colab-start: ensure-owner | $(COLAB_STATE_DIR)
	@rm -f '$(COLAB_CREATED_MARKER)' '$(COLAB_RESET_MARKER)'
	@$(CLOUDMAKE_RECORD_STATE) --phase allocation --provider-state unknown \
		--session-created false --target-submission not_submitted --retry-safe true
	@$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/allocate.py' \
		--client '$(COLAB_BIN)' --session '$(COLAB_SESSION)' \
		$(if $(strip $(COLAB_GPU)),--gpu '$(COLAB_GPU)') \
		--retry-seconds '$(CLOUDMAKE_RETRY_FOR_SECONDS)' \
		--resource-state '$(COLAB_RESOURCE_STATE)' \
		--result '$(CLOUDMAKE_ALLOCATION_RESULT)' \
		--state-file '$(CLOUDMAKE_OPERATION_STATE)'
	@$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/lifecycle.py' \
		--client '$(COLAB_BIN)' --session '$(COLAB_SESSION)' \
		$(if $(strip $(COLAB_GPU)),--gpu '$(COLAB_GPU)') \
		--probe-file '$(CLOUDMAKE_TOOL_ROOT)/core/remote_prerequisites.py' \
		--command-timeout '$(COLAB_READY_PROBE_TIMEOUT)' \
		--ready-timeout '$(COLAB_READY_TIMEOUT)' \
		--poll-seconds '$(COLAB_READY_POLL_SECONDS)' \
		--state-file '$(CLOUDMAKE_OPERATION_STATE)' \
		--created-marker '$(COLAB_CREATED_MARKER)' \
		--resource-state '$(COLAB_RESOURCE_STATE)' --preallocated
	@resource_state="$$(cat '$(COLAB_RESOURCE_STATE)')"; \
		CLOUDMAKE_RESOURCE_STATE=$$resource_state; $(CLOUDMAKE_PRINT_CONTEXT)

_colab-start-ready: _colab-start
	@if test '$(CLOUDMAKE_CHECKPOINT)' = 1; then \
		$(MAKE) --no-print-directory _colab-checkpoint-restore; \
	fi

_colab-environment: _colab-start | $(COLAB_STATE_DIR)
	@rm -f '$(COLAB_ENVIRONMENT_PROFILE)' '$(COLAB_ENVIRONMENT_PROFILE).tmp'
	@$(COLAB_BIN) rm -s '$(COLAB_SESSION)' \
		'$(COLAB_REMOTE_ENVIRONMENT_PROFILE)' >/dev/null 2>&1 || :
	@$(COLAB_BIN) exec -s '$(COLAB_SESSION)' --timeout '$(COLAB_TIMEOUT)' \
		-f '$(CLOUDMAKE_TOOL_ROOT)/core/vm_capabilities.py'
	@if ! $(COLAB_BIN) download -s '$(COLAB_SESSION)' \
		'$(COLAB_REMOTE_ENVIRONMENT_PROFILE)' \
		'$(COLAB_ENVIRONMENT_PROFILE).tmp' >/dev/null 2>&1; then \
		echo '[cloudmake] infrastructure failure: environment probe did not produce a profile' >&2; \
		rm -f '$(COLAB_ENVIRONMENT_PROFILE).tmp'; exit 1; \
	fi
	@mv '$(COLAB_ENVIRONMENT_PROFILE).tmp' '$(COLAB_ENVIRONMENT_PROFILE)'
	@$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/vm_capabilities.py' \
		--render '$(COLAB_ENVIRONMENT_PROFILE)'

_colab-checkpoint-restore: | $(COLAB_STATE_DIR)
	@if test -z '$(CLOUDMAKE_PROJECT_KEY)'; then \
		echo '[cloudmake] persistent-workspace project identity is unavailable' >&2; exit 2; \
	fi
	@resource_state="$$(cat '$(COLAB_RESOURCE_STATE)')"; \
	$(COLAB_CHECKPOINT_HOST) restore \
		--client '$(COLAB_BIN)' --session '$(COLAB_SESSION)' \
		--project-key '$(CLOUDMAKE_PROJECT_KEY)' \
		--workspace-id '$(CLOUDMAKE_WORKSPACE_ID)' --resource-state "$$resource_state" \
		$(if $(filter 1,$(CLOUDMAKE_ADOPT)),--allow-attach) \
		$(if $(CLOUDMAKE_ADOPT_FROM),--adopt-from '$(CLOUDMAKE_ADOPT_FROM)') \
		--tool-root '$(CLOUDMAKE_TOOL_ROOT)' \
		--state-dir '$(COLAB_CHECKPOINT_STATE_DIR)' \
		--result '$(COLAB_CHECKPOINT_RESTORE_RESULT)'

_colab-checkpoint-publish: | $(COLAB_STATE_DIR)
	@if test -z '$(CLOUDMAKE_PROJECT_KEY)'; then \
		echo '[cloudmake] persistent-workspace project identity is unavailable' >&2; exit 2; \
	fi
	@resource_state="$$(cat '$(COLAB_RESOURCE_STATE)')"; \
	$(COLAB_CHECKPOINT_HOST) publish \
		--client '$(COLAB_BIN)' --session '$(COLAB_SESSION)' \
		--project-key '$(CLOUDMAKE_PROJECT_KEY)' \
		--workspace-id '$(CLOUDMAKE_WORKSPACE_ID)' --resource-state "$$resource_state" \
		--tool-root '$(CLOUDMAKE_TOOL_ROOT)' \
		--state-dir '$(COLAB_CHECKPOINT_STATE_DIR)' \
		--result '$(COLAB_CHECKPOINT_PUBLISH_RESULT)'

workspace-purge: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _colab-workspace-purge

.PHONY: _colab-workspace-purge
_colab-workspace-purge: _colab-start | $(COLAB_STATE_DIR)
	@resource_state="$$(cat '$(COLAB_RESOURCE_STATE)')"; \
	$(COLAB_CHECKPOINT_HOST) purge \
		--client '$(COLAB_BIN)' --session '$(COLAB_SESSION)' \
		--project-key '$(CLOUDMAKE_PROJECT_KEY)' \
		--workspace-id '$(CLOUDMAKE_WORKSPACE_ID)' --resource-state "$$resource_state" \
		--tool-root '$(CLOUDMAKE_TOOL_ROOT)' \
		--state-dir '$(COLAB_CHECKPOINT_STATE_DIR)' \
		--result '$(COLAB_CHECKPOINT_STATE_DIR)/workspace-purge.json'

_colab-sync: _colab-start-ready | $(COLAB_STATE_DIR)
	@set -e; \
	$(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state ready \
		--target-submission not_submitted --retry-safe true; \
	rm -f '$(COLAB_CONTROL_STATE_COPY)' '$(COLAB_CONTROL_STATE_COPY).tmp'; \
	if ! $(COLAB_BIN) exec -s '$(COLAB_SESSION)' --timeout '$(COLAB_READY_PROBE_TIMEOUT)' \
		-f '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/control_state.py' \
		> '$(COLAB_CONTROL_STATE_COPY).tmp' 2>&1; then \
		cat '$(COLAB_CONTROL_STATE_COPY).tmp' >&2; \
		rm -f '$(COLAB_CONTROL_STATE_COPY).tmp'; \
		$(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state unknown \
			--runtime-state unreachable --target-submission not_submitted \
			--retry-safe true --failure-code control_state_unreachable; \
		echo '[colab] Remote ownership state could not be verified; synchronization was refused and the target was not submitted.' >&2; \
		echo '[colab] Retrying is safe. Next: cloudmake -b colab --status' >&2; \
		exit 74; \
	fi; \
	mv '$(COLAB_CONTROL_STATE_COPY).tmp' '$(COLAB_CONTROL_STATE_COPY)'; \
	cat '$(COLAB_CONTROL_STATE_COPY)'; \
	if ! owner_state="$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/control_state.py' \
		--parse '$(COLAB_CONTROL_STATE_COPY)' --field owner )"; then \
		$(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state unknown \
			--runtime-state unreachable --target-submission not_submitted \
			--retry-safe true --failure-code control_state_invalid; \
		echo '[colab] Remote ownership probe was inconclusive; synchronization was refused and the target was not submitted.' >&2; \
		exit 74; \
	fi; \
	if test "$$owner_state" = present; then \
		if ! $(COLAB_BIN) download -s '$(COLAB_SESSION)' \
			'$(COLAB_REMOTE_OWNER)' '$(COLAB_REMOTE_OWNER_COPY).tmp' >/dev/null 2>&1 || \
			test ! -s '$(COLAB_REMOTE_OWNER_COPY).tmp'; then \
			rm -f '$(COLAB_REMOTE_OWNER_COPY).tmp'; \
			$(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state unknown \
				--runtime-state unreachable --target-submission not_submitted \
				--retry-safe true --failure-code owner_record_unreadable; \
			echo '[colab] The remote owner exists but could not be read; synchronization was refused and the target was not submitted.' >&2; \
			echo '[colab] Retrying is safe. Next: cloudmake -b colab --status' >&2; \
			exit 74; \
		fi; \
		mv '$(COLAB_REMOTE_OWNER_COPY).tmp' '$(COLAB_REMOTE_OWNER_COPY)'; \
		adopt=''; test '$(CLOUDMAKE_ADOPT)' = 1 && adopt='--adopt' || :; \
		if ! $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/project_identity.py' check \
			--expected '$(CLOUDMAKE_OWNER_FILE)' \
			--actual '$(COLAB_REMOTE_OWNER_COPY)' \
			--resource 'Colab session $(COLAB_SESSION)' $$adopt \
			$(if $(CLOUDMAKE_ADOPT_FROM),--adopt-from '$(CLOUDMAKE_ADOPT_FROM)'); then \
			$(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state ready \
				--runtime-state foreign --target-submission not_submitted \
				--retry-safe true --failure-code foreign_workspace; exit 73; \
		fi; \
		$(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state ready \
			--runtime-state same --target-submission not_submitted --retry-safe true; \
	else \
		rm -f '$(COLAB_REMOTE_OWNER_COPY).tmp' '$(COLAB_REMOTE_OWNER_COPY)'; \
		touch '$(COLAB_RESET_MARKER)'; \
		if test -f '$(COLAB_CREATED_MARKER)'; then lifecycle=fresh; else lifecycle=reset; fi; \
		echo "[colab] Detected $$lifecycle runtime: remote Cloudmake ownership state is absent." >&2; \
		echo '[colab] A full source sync is required; runtime-local generated state may be gone.' >&2; \
		$(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state ready \
			--runtime-state $$lifecycle --target-submission not_submitted --retry-safe true; \
	fi
	@mkdir -p '$(CLOUDMAKE_MANIFEST_DIR)'
	@$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/source_fingerprint.py' \
		--root '$(PROJECT_DIR)' \
		--manifest '$(CLOUDMAKE_CURRENT_MANIFEST)' \
		$(CLOUDMAKE_SECRET_OPTION) \
		--warn-mb '$(SOURCE_WARN_MB)' --max-mb '$(SOURCE_MAX_MB)' \
		> '$(COLAB_FINGERPRINT).tmp'
	@mv '$(COLAB_FINGERPRINT).tmp' '$(COLAB_FINGERPRINT)'
	@set -e; \
	$(CLOUDMAKE_RECORD_STATE) --phase synchronization --provider-state ready \
		--target-submission not_submitted --retry-safe true; \
	remote_fingerprint=''; \
	if ! fingerprint_state="$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/control_state.py' \
		--parse '$(COLAB_CONTROL_STATE_COPY)' --field fingerprint )"; then \
		$(CLOUDMAKE_RECORD_STATE) --phase synchronization --provider-state unknown \
			--runtime-state unreachable --target-submission not_submitted \
			--retry-safe true --failure-code control_state_invalid; exit 74; \
	fi; \
	if test ! -f '$(COLAB_RESET_MARKER)' && test "$$fingerprint_state" = present; then \
		if ! $(COLAB_BIN) download -s '$(COLAB_SESSION)' \
			'$(COLAB_REMOTE_FINGERPRINT)' '$(COLAB_REMOTE_FINGERPRINT_COPY)' \
			>/dev/null 2>&1; then \
			$(CLOUDMAKE_RECORD_STATE) --phase synchronization --provider-state unknown \
				--runtime-state unreachable --target-submission not_submitted \
				--retry-safe true --failure-code fingerprint_unreadable; \
			echo '[colab] The remote fingerprint exists but could not be read; synchronization was refused and the target was not submitted.' >&2; \
			echo '[colab] Retrying is safe. Next: cloudmake -b colab --status' >&2; \
			exit 74; \
		fi; \
		remote_fingerprint=1; \
	fi; \
	if test "$$remote_fingerprint" = 1 && \
		cmp -s '$(COLAB_FINGERPRINT)' '$(COLAB_REMOTE_FINGERPRINT_COPY)' && \
		cmp -s '$(CLOUDMAKE_OWNER_FILE)' '$(COLAB_REMOTE_OWNER_COPY)'; then \
		echo '[colab] Source unchanged; skipping archive upload.'; \
		mv '$(CLOUDMAKE_CURRENT_MANIFEST)' '$(CLOUDMAKE_MANIFEST)'; \
	else \
		if test ! -f '$(COLAB_RESET_MARKER)' && test "$$fingerprint_state" = absent && \
			test -f '$(COLAB_REMOTE_OWNER_COPY)'; then \
			touch '$(COLAB_RESET_MARKER)'; \
			echo '[colab] Remote fingerprint state is absent; performing a full source sync.' >&2; \
			$(CLOUDMAKE_RECORD_STATE) --phase synchronization --provider-state ready \
				--runtime-state reset --target-submission not_submitted --retry-safe true; \
		fi; \
		$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/source_fingerprint.py' \
			--root '$(PROJECT_DIR)' \
			--archive '$(COLAB_ARCHIVE)' \
			--manifest '$(CLOUDMAKE_CURRENT_MANIFEST)' \
			$(CLOUDMAKE_SECRET_OPTION) \
			--warn-mb '$(SOURCE_WARN_MB)' --max-mb '$(SOURCE_MAX_MB)' \
			> '$(COLAB_FINGERPRINT).tmp'; \
		mv '$(COLAB_FINGERPRINT).tmp' '$(COLAB_FINGERPRINT)'; \
		$(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
			'$(COLAB_ARCHIVE)' '$(COLAB_REMOTE_ARCHIVE)'; \
		$(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
			'$(COLAB_FINGERPRINT)' '$(COLAB_REMOTE_FINGERPRINT_INCOMING)'; \
		$(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
			'$(CLOUDMAKE_CURRENT_MANIFEST)' '$(COLAB_REMOTE_MANIFEST_INCOMING)'; \
		if test -f '$(CLOUDMAKE_MANIFEST)'; then \
			$(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
				'$(CLOUDMAKE_MANIFEST)' '$(COLAB_REMOTE_PREVIOUS_MANIFEST_INCOMING)'; \
		fi; \
		$(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
			'$(CLOUDMAKE_OWNER_FILE)' '$(COLAB_REMOTE_OWNER_INCOMING)'; \
		$(COLAB_BIN) rm -s '$(COLAB_SESSION)' \
			'$(COLAB_REMOTE_SYNC_RESULT)' >/dev/null 2>&1 || :; \
		$(COLAB_BIN) exec -s '$(COLAB_SESSION)' --timeout '$(COLAB_TIMEOUT)' \
			-f '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/sync.py'; \
		if ! $(COLAB_BIN) download -s '$(COLAB_SESSION)' \
			'$(COLAB_REMOTE_SYNC_RESULT)' '$(COLAB_SYNC_RESULT).tmp' >/dev/null 2>&1; then \
			echo '[cloudmake] infrastructure failure: source synchronization did not produce a success receipt' >&2; \
			rm -f '$(COLAB_SYNC_RESULT).tmp'; exit 1; \
		fi; \
		if ! cmp -s '$(COLAB_FINGERPRINT)' '$(COLAB_SYNC_RESULT).tmp'; then \
			echo '[cloudmake] infrastructure failure: source synchronization receipt does not match the uploaded source' >&2; \
			rm -f '$(COLAB_SYNC_RESULT).tmp'; exit 1; \
		fi; \
		mv '$(COLAB_SYNC_RESULT).tmp' '$(COLAB_SYNC_RESULT)'; \
		mv '$(CLOUDMAKE_CURRENT_MANIFEST)' '$(CLOUDMAKE_MANIFEST)'; \
	fi

_colab-prepare: _colab-sync | $(COLAB_STATE_DIR)
	@set -e; \
	if test -z '$(COLAB_SESSION_PREPARE_TARGET)'; then exit 0; fi; \
	$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/prepare_receipt.py' \
		--target '$(COLAB_SESSION_PREPARE_TARGET)' \
		--source-fingerprint '$(COLAB_FINGERPRINT)' \
		--output '$(COLAB_PREPARED_RECEIPT)'; \
	prepared=''; \
	if test ! -f '$(COLAB_RESET_MARKER)' && \
		$(COLAB_BIN) download -s '$(COLAB_SESSION)' \
		'$(COLAB_REMOTE_PREPARED)' '$(COLAB_PREPARED_COPY)' >/dev/null 2>&1 && \
		cmp -s '$(COLAB_PREPARED_RECEIPT)' '$(COLAB_PREPARED_COPY)'; then prepared=1; fi; \
	if test "$$prepared" = 1; then \
		echo '[colab] Session preparation is current; skipping the preparation target.'; \
		exit 0; \
	fi; \
	echo '[colab] Running declared idempotent session preparation target: $(COLAB_SESSION_PREPARE_TARGET)' >&2; \
	$(CLOUDMAKE_RECORD_STATE) --phase preparation --provider-state ready \
		--preparation-state ambiguous --target-submission not_submitted --retry-safe true; \
	printf '%s\n%s\n%s\n%s\n%s\n' \
		"$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/encode_value.py' '$(COLAB_SESSION_PREPARE_TARGET)' )" \
		'$(JOBS)' '$(PROJECT_MAKEFILE)' 'W10=' '' > '$(COLAB_PREPARE_TARGET_FILE).tmp'; \
	mv '$(COLAB_PREPARE_TARGET_FILE).tmp' '$(COLAB_PREPARE_TARGET_FILE)'; \
	cp '$(COLAB_NOTEBOOK)' '$(COLAB_RUN_NOTEBOOK).tmp'; mv '$(COLAB_RUN_NOTEBOOK).tmp' '$(COLAB_RUN_NOTEBOOK)'; \
	$(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
		'$(COLAB_PREPARE_TARGET_FILE)' '$(COLAB_REMOTE_TARGET)'; \
	if ! $(COLAB_BIN) exec -s '$(COLAB_SESSION)' --timeout '$(COLAB_TIMEOUT)' \
		-f '$(COLAB_RUN_NOTEBOOK)'; then \
		$(CLOUDMAKE_RECORD_STATE) --phase preparation --provider-state unknown \
			--preparation-state ambiguous --target-submission not_submitted \
			--retry-safe true --failure-code preparation_ambiguous; \
		echo '[colab] Session preparation outcome is ambiguous; the requested target was not submitted.' >&2; \
		exit 1; \
	fi; \
	rm -f '$(COLAB_TARGET_RESULT)' '$(COLAB_TARGET_RESULT).tmp'; \
	if ! $(COLAB_BIN) download -s '$(COLAB_SESSION)' \
		'$(COLAB_REMOTE_TARGET_RESULT)' '$(COLAB_TARGET_RESULT).tmp'; then \
		$(CLOUDMAKE_RECORD_STATE) --phase preparation --provider-state unknown \
			--preparation-state ambiguous --target-submission not_submitted \
			--retry-safe true --failure-code preparation_result_unavailable; \
		echo '[colab] Session preparation receipt is unavailable; the requested target was not submitted.' >&2; \
		exit 1; \
	fi; \
	mv '$(COLAB_TARGET_RESULT).tmp' '$(COLAB_TARGET_RESULT)'; \
	if ! $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/target_result.py' \
		--result '$(COLAB_TARGET_RESULT)'; then \
		$(CLOUDMAKE_RECORD_STATE) --phase preparation --provider-state failed \
			--preparation-state failed --target-submission not_submitted \
			--retry-safe true --failure-code preparation_failed; \
		echo '[colab] Session preparation failed; the requested target was not submitted.' >&2; \
		exit 1; \
	fi; \
	if ! $(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
		'$(COLAB_PREPARED_RECEIPT)' '$(COLAB_REMOTE_PREPARED_INCOMING)'; then \
		$(CLOUDMAKE_RECORD_STATE) --phase preparation --provider-state unknown \
			--preparation-state succeeded --target-submission not_submitted \
			--retry-safe true --failure-code preparation_receipt_upload_failed; \
		echo '[colab] Preparation succeeded, but its receipt upload failed; the requested target was not submitted and retrying is safe.' >&2; \
		exit 1; \
	fi; \
	if ! $(COLAB_BIN) exec -s '$(COLAB_SESSION)' --timeout '$(COLAB_TIMEOUT)' \
		-f '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/prepare.py'; then \
		$(CLOUDMAKE_RECORD_STATE) --phase preparation --provider-state unknown \
			--preparation-state ambiguous --target-submission not_submitted \
			--retry-safe true --failure-code preparation_receipt_install_ambiguous; \
		echo '[colab] Preparation receipt installation is ambiguous; the requested target was not submitted and retrying is safe.' >&2; \
		exit 1; \
	fi; \
	$(CLOUDMAKE_RECORD_STATE) --phase preparation --provider-state ready \
		--preparation-state succeeded --target-submission not_submitted --retry-safe true

_colab-oci-prepare: _colab-prepare | $(COLAB_STATE_DIR)
	@if test '$(CLOUDMAKE_RUNNER)' != oci; then exit 0; fi; \
	printf '%s\n%s\n%s\n%s\n%s\n%s\n' \
		'$(CLOUDMAKE_OCI_IMAGE_B64)' '$(CLOUDMAKE_OCI_DEVICES_B64)' \
		'$(COLAB_REMOTE_ROOT)/src' '$(COLAB_REMOTE_OCI_CACHE)' '$(PROJECT_MAKEFILE)' \
		'$(CLOUDMAKE_OCI_RUNTIMES_B64)' \
		> '$(COLAB_OCI_CONTROL).tmp'; \
	if test -n '$(CLOUDMAKE_DEVCONTAINER_ACTIVE)'; then \
		printf '%s\n%s\n' '$(CLOUDMAKE_OCI_ENVIRONMENT_B64)' \
			'$(CLOUDMAKE_HOST_REQUIREMENTS_B64)' >> '$(COLAB_OCI_CONTROL).tmp'; \
	fi; \
	mv '$(COLAB_OCI_CONTROL).tmp' '$(COLAB_OCI_CONTROL)'; \
	$(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
		'$(COLAB_OCI_CONTROL)' '$(COLAB_REMOTE_OCI_CONTROL)'; \
	$(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
		'$(CLOUDMAKE_TOOL_ROOT)/core/oci_runner.py' '$(COLAB_REMOTE_OCI_RUNNER)'; \
	$(COLAB_BIN) rm -s '$(COLAB_SESSION)' \
		'$(COLAB_REMOTE_OCI_PREFLIGHT)' >/dev/null 2>&1 || :; \
	$(CLOUDMAKE_RECORD_STATE) --phase runner_preflight --provider-state ready \
		--target-submission not_submitted --retry-safe true; \
	$(COLAB_BIN) exec -s '$(COLAB_SESSION)' --timeout '$(COLAB_TIMEOUT)' \
		-f '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/oci_prepare.py'; \
	rm -f '$(COLAB_OCI_PREFLIGHT)' '$(COLAB_OCI_PREFLIGHT).tmp'; \
	if ! $(COLAB_BIN) download -s '$(COLAB_SESSION)' \
		'$(COLAB_REMOTE_OCI_PREFLIGHT)' '$(COLAB_OCI_PREFLIGHT).tmp'; then \
		$(CLOUDMAKE_RECORD_STATE) --phase runner_preflight --provider-state unknown \
			--target-submission not_submitted --retry-safe true \
			--failure-code oci_preflight_receipt_unavailable; \
		echo '[colab] OCI preflight receipt is unavailable; the target was not submitted.' >&2; \
		exit 70; \
	fi; \
	mv '$(COLAB_OCI_PREFLIGHT).tmp' '$(COLAB_OCI_PREFLIGHT)'; \
	if ! $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/oci_result.py' \
		--result '$(COLAB_OCI_PREFLIGHT)' --expect ready; then \
		$(CLOUDMAKE_RECORD_STATE) --phase runner_preflight --provider-state failed \
			--target-submission not_submitted --retry-safe true \
			--failure-code oci_preflight_failed; \
		echo '[colab] OCI preflight failed; the target was not submitted.' >&2; \
		exit 70; \
	fi; \
	$(CLOUDMAKE_RECORD_STATE) --phase runner_preflight --provider-state ready \
		--target-submission not_submitted --retry-safe true

_colab-execute: _colab-oci-prepare | $(COLAB_STATE_DIR)
	@set -e; target_b64='$(REMOTE_TARGET_B64)'; \
		if test -z "$$target_b64"; then \
			target_b64="$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/encode_value.py' '$(REMOTE_TARGET)' )"; \
		fi; \
		printf '%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n' \
			"$$target_b64" '$(JOBS)' '$(PROJECT_MAKEFILE)' \
			'$(CLOUDMAKE_PROJECT_ARGS_B64)' '$(REMOTE_COLLECT_DIR_B64)' \
			'$(CLOUDMAKE_OCI_IMAGE_B64)' '$(CLOUDMAKE_OCI_DEVICES_B64)' \
			'$(CLOUDMAKE_OCI_RUNTIMES_B64)' \
			> '$(COLAB_TARGET_FILE).tmp'; \
		if test -n '$(CLOUDMAKE_DEVCONTAINER_ACTIVE)'; then \
			printf '%s\n%s\n' '$(CLOUDMAKE_OCI_ENVIRONMENT_B64)' \
				'$(CLOUDMAKE_HOST_REQUIREMENTS_B64)' >> '$(COLAB_TARGET_FILE).tmp'; \
		fi
	@mv '$(COLAB_TARGET_FILE).tmp' '$(COLAB_TARGET_FILE)'
	@cp '$(COLAB_NOTEBOOK)' '$(COLAB_RUN_NOTEBOOK).tmp'
	@mv '$(COLAB_RUN_NOTEBOOK).tmp' '$(COLAB_RUN_NOTEBOOK)'
	@$(CLOUDMAKE_RECORD_STATE) --phase target_submission --provider-state ready \
		--target-submission not_submitted --retry-safe true
	@set +e; $(COLAB_BIN) upload -s '$(COLAB_SESSION)' \
		'$(COLAB_TARGET_FILE)' '$(COLAB_REMOTE_TARGET)'; code=$$?; \
	if test $$code -ne 0; then \
		$(CLOUDMAKE_RECORD_STATE) --phase target_submission --provider-state unknown \
			--target-submission not_submitted --retry-safe true \
			--failure-code target_request_upload_failed; \
		echo '[colab] Target request upload failed before submission; retrying the command is safe.' >&2; \
	fi; exit $$code
	@$(CLOUDMAKE_RECORD_STATE) --phase target_execution --provider-state running \
		--target-submission ambiguous --retry-safe false
	@set +e; \
	$(COLAB_BIN) exec -s '$(COLAB_SESSION)' --timeout '$(COLAB_TIMEOUT)' \
		-f '$(COLAB_RUN_NOTEBOOK)'; \
	exec_status=$$?; \
	printf '%s\n' '[cloudmake] notebook=$(COLAB_RUN_NOTEBOOK)'; \
	if test $$exec_status -ne 0; then \
		printf '[cloudmake] infrastructure failure: notebook execution exited with status %s\n' "$$exec_status" >&2; \
		$(CLOUDMAKE_RECORD_STATE) --phase target_execution --provider-state unknown \
			--target-submission ambiguous --retry-safe false \
			--failure-code ambiguous_execution; \
		echo '[colab] Automatic replay is unsafe. Next: cloudmake -b colab --status' >&2; \
		created=false; test -f '$(COLAB_CREATED_MARKER)' && created=true || :; \
		echo "[cloudmake] failure phase=target_execution provider_state=unknown session_created=$$created target_submission=ambiguous retry_safe=false" >&2; \
	else \
		$(CLOUDMAKE_RECORD_STATE) --phase target_execution --provider-state ready \
			--target-submission submitted --retry-safe false; \
	fi; \
	exit $$exec_status
	@rm -f '$(COLAB_TARGET_RESULT)' '$(COLAB_TARGET_RESULT).tmp'
	@set +e; \
	$(COLAB_BIN) download -s '$(COLAB_SESSION)' \
		'$(COLAB_REMOTE_TARGET_RESULT)' '$(COLAB_TARGET_RESULT).tmp'; \
	download_status=$$?; \
	if test $$download_status -ne 0; then \
		printf '[cloudmake] infrastructure failure: target result receipt download exited with status %s\n' "$$download_status" >&2; \
		$(CLOUDMAKE_RECORD_STATE) --phase target_execution --provider-state unknown \
			--target-submission submitted --retry-safe false \
			--failure-code target_result_unavailable; \
		rm -f '$(COLAB_TARGET_RESULT).tmp'; \
	fi; \
	exit $$download_status
	@mv '$(COLAB_TARGET_RESULT).tmp' '$(COLAB_TARGET_RESULT)'
	@set +e; \
	if test '$(CLOUDMAKE_RUNNER)' = oci; then \
		$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/oci_result.py' \
			--result '$(COLAB_TARGET_RESULT)' --expect terminal; \
	else \
		$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/target_result.py' \
			--result '$(COLAB_TARGET_RESULT)'; \
	fi; code=$$?; \
	if test $$code -eq 0; then provider=succeeded; else provider=failed; fi; \
	$(CLOUDMAKE_RECORD_STATE) --phase target_execution --provider-state $$provider \
		--target-submission submitted --retry-safe false; exit $$code
	@if test '$(CLOUDMAKE_CHECKPOINT)' = 1; then \
		if ! $(MAKE) --no-print-directory _colab-checkpoint-publish; then \
			echo '[cloudmake] persistent-workspace publication failed after the project target completed; the target was not retried.' >&2; \
			exit 1; \
		fi; \
	fi

_colab-collect: _colab-execute
	@$(MAKE) --no-print-directory _colab-fetch-ready

_colab-fetch: _colab-start-ready | $(COLAB_STATE_DIR)
	@$(MAKE) --no-print-directory _colab-fetch-ready

_colab-fetch-ready: | $(COLAB_STATE_DIR)
	@$(CLOUDMAKE_RECORD_STATE) --phase artifact_retrieval --provider-state ready \
		--target-submission submitted --retry-safe false
	@set +e; $(COLAB_BIN) download -s '$(COLAB_SESSION)' \
		'$(COLAB_REMOTE_ARTIFACTS)' '$(COLAB_ARTIFACT_ARCHIVE).tmp'; code=$$?; \
	if test $$code -ne 0; then \
		$(CLOUDMAKE_RECORD_STATE) --phase artifact_retrieval --provider-state unknown \
			--target-submission submitted --retry-safe false \
			--failure-code artifact_download_failed; \
		echo '[colab] Artifact retrieval failed; the target will not be replayed. Next: cloudmake -b colab --fetch' >&2; \
	fi; exit $$code
	@mv '$(COLAB_ARTIFACT_ARCHIVE).tmp' '$(COLAB_ARTIFACT_ARCHIVE)'
	$(CLOUDMAKE_SAFE_EXTRACT) \
		--archive '$(COLAB_ARTIFACT_ARCHIVE)' --destination '$(ARTIFACT_DIR)'

_colab-open: _colab-start-ready
	$(COLAB_BIN) url -s '$(COLAB_SESSION)' --open

_colab-stop: ensure-owner
	$(COLAB_BIN) stop -s '$(COLAB_SESSION)'
