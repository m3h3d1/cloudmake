# Modal Sandbox transport: the SSH source/ownership flow with SDK file and exec
# calls in place of ssh and rsync.

REMOTE_ROOT := $(MODAL_REMOTE_ROOT)
REMOTE_SRC := $(REMOTE_ROOT)/src
REMOTE_OWNER_FILE := $(REMOTE_ROOT)/.cloudmake-owner.json
REMOTE_FINGERPRINT := $(REMOTE_ROOT)/.cloudmake-source.sha256
REMOTE_SOURCE_ARCHIVE := $(REMOTE_ROOT)/.cloudmake-source.tar.gz
REMOTE_DELETE_SCRIPT := $(REMOTE_ROOT)/.cloudmake-source-delete.sh
REMOTE_ARTIFACT_ARCHIVE := $(REMOTE_ROOT)/.cloudmake-artifacts.tar.gz
REMOTE_MAKEFILE ?= $(PROJECT_MAKEFILE)

MODAL_STATE_DIR := $(CLOUDMAKE_STATE_ROOT)/$(BACKEND)/$(BACKEND_RESOURCE_ID)
MODAL_RESOURCE_STATE := $(MODAL_STATE_DIR)/resource-state
MODAL_REMOTE_OWNER_COPY := $(MODAL_STATE_DIR)/remote-owner.json
MODAL_REMOTE_FINGERPRINT_COPY := $(MODAL_STATE_DIR)/remote-source.sha256
MODAL_SOURCE_ARCHIVE := $(MODAL_STATE_DIR)/source.tar.gz
MODAL_SOURCE_DELETE_SCRIPT := $(MODAL_STATE_DIR)/source-delete.sh
MODAL_TARGET_RESULT := $(MODAL_STATE_DIR)/target-result.json
MODAL_ARTIFACT_ARCHIVE := $(MODAL_STATE_DIR)/artifacts.tar.gz
BACKEND_CONTEXT_RESOURCE_STATE_FILE := $(MODAL_RESOURCE_STATE)

MODAL_CONTROL = '$(MODAL_PYTHON)' '$(CLOUDMAKE_TOOL_ROOT)/backend/modal-sandbox/sandbox.py' \
	--app '$(MODAL_APP)' --name '$(MODAL_SANDBOX)'

.PHONY: help start status stop sync dispatch collect fetch shell open \
	_modal-start _modal-sync _modal-sync-unlocked _modal-execute _modal-collect \
	_modal-fetch _modal-shell _modal-open _modal-stop

help:
	@echo 'Usage: make BACKEND=modal-sandbox <target>'
	@echo 'Engine operations: start status stop sync collect fetch shell open'
	@echo '                   sync-dry-run prerequisites doctor backend-info'
	@echo 'Internal project-target entry point: dispatch (launcher managed)'

start: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _modal-start

status: doctor
	@mkdir -p '$(CLOUDMAKE_STATE_ROOT)/status'
	@set -e; temporary='$(CLOUDMAKE_STATE_ROOT)/status/$(BACKEND)-$$$$.tmp'; \
		if $(MODAL_CONTROL) status > "$$temporary" 2>&1; then \
			cat "$$temporary"; \
			$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/normalize_status.py' --backend '$(BACKEND)' < "$$temporary"; \
			rm -f "$$temporary"; \
		else code=$$?; cat "$$temporary"; rm -f "$$temporary"; exit $$code; fi

stop: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _modal-stop

sync: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _modal-sync

dispatch: doctor
	@if test -z '$(REMOTE_TARGET_B64)'; then echo 'REMOTE_TARGET_B64 is required for dispatch' >&2; exit 2; fi
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _modal-execute

collect: doctor
	@if test -z '$(REMOTE_TARGET_B64)'; then echo 'REMOTE_TARGET_B64 is required for collect' >&2; exit 2; fi
	@if test -z '$(REMOTE_COLLECT_DIR_B64)'; then echo 'REMOTE_COLLECT_DIR_B64 is required for collect' >&2; exit 2; fi
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _modal-collect

fetch: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _modal-fetch

shell: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _modal-shell

open:
	@if test -z '$(MODAL_WEB_PORT)'; then echo 'MODAL_WEB_PORT is required; restart with MODAL_WEB_PORT=PORT.' >&2; exit 2; fi
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _modal-open

_modal-start: ensure-owner
	@mkdir -p '$(MODAL_STATE_DIR)'
	@$(MODAL_CONTROL) start --gpu '$(MODAL_GPU)' --cpu '$(MODAL_CPU)' \
		--memory '$(MODAL_MEMORY)' --timeout '$(MODAL_TIMEOUT)' \
		--idle-timeout '$(MODAL_IDLE_TIMEOUT)' --image '$(MODAL_IMAGE)' \
		--apt '$(MODAL_APT)' --pip '$(MODAL_PIP)' \
		$(if $(strip $(MODAL_WEB_PORT)),--web-port '$(MODAL_WEB_PORT)') \
		--persist '$(CLOUDMAKE_NATIVE_PERSIST)' --volume '$(MODAL_VOLUME)' \
		--remote-root '$(REMOTE_ROOT)' --ready-timeout '$(MODAL_READY_TIMEOUT)' \
		--operation-state '$(CLOUDMAKE_OPERATION_STATE)' \
		--resource-state '$(MODAL_RESOURCE_STATE)'
	@CLOUDMAKE_RESOURCE_STATE="$$(cat '$(MODAL_RESOURCE_STATE)')"; $(CLOUDMAKE_PRINT_CONTEXT)

_modal-sync: _modal-start
	@$(MAKE) --no-print-directory _modal-sync-unlocked

# Exit status 3 from `download` is positive absence; any other failure is
# ambiguous and must not be treated as a fresh workspace.
_modal-sync-unlocked: ensure-owner
	@mkdir -p '$(MODAL_STATE_DIR)' '$(CLOUDMAKE_MANIFEST_DIR)'
	@$(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state ready
	@set -eu; rm -f '$(MODAL_REMOTE_OWNER_COPY)'; \
	set +e; $(MODAL_CONTROL) download '$(REMOTE_OWNER_FILE)' '$(MODAL_REMOTE_OWNER_COPY)'; code=$$?; set -e; \
	case $$code in \
		0) adopt=''; test '$(CLOUDMAKE_ADOPT)' = 1 && adopt='--adopt' || :; \
			$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/project_identity.py' check \
				--expected '$(CLOUDMAKE_OWNER_FILE)' \
				--actual '$(MODAL_REMOTE_OWNER_COPY)' \
				--resource '$(BACKEND) sandbox $(MODAL_SANDBOX)' $$adopt ;; \
		3) echo '[modal] Fresh workspace: no Cloudmake owner record.' ;; \
		*) $(CLOUDMAKE_RECORD_STATE) --phase ownership --provider-state unknown \
				--retry-safe true --failure-code ownership_unavailable; \
			echo '[modal] Ownership record could not be read; refusing to treat the workspace as fresh.' >&2; \
			exit 74 ;; \
	esac
	@$(CLOUDMAKE_RECORD_STATE) --phase synchronization --provider-state ready
	@set -eu; \
	fingerprint="$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/source_fingerprint.py' \
		--root '$(PROJECT_DIR)' --manifest '$(CLOUDMAKE_CURRENT_MANIFEST)' \
		$(CLOUDMAKE_SECRET_OPTION) \
		--warn-mb '$(SOURCE_WARN_MB)' --max-mb '$(SOURCE_MAX_MB)' )"; \
	rm -f '$(MODAL_REMOTE_FINGERPRINT_COPY)'; remote=''; \
	set +e; $(MODAL_CONTROL) download '$(REMOTE_FINGERPRINT)' '$(MODAL_REMOTE_FINGERPRINT_COPY)'; code=$$?; set -e; \
	case $$code in \
		0) remote="$$(cat '$(MODAL_REMOTE_FINGERPRINT_COPY)')" ;; \
		3) ;; \
		*) $(CLOUDMAKE_RECORD_STATE) --phase synchronization --provider-state unknown \
				--retry-safe true --failure-code source_state_unavailable; \
			echo '[modal] Remote source fingerprint could not be read.' >&2; exit 74 ;; \
	esac; \
	$(MODAL_CONTROL) upload '$(CLOUDMAKE_OWNER_FILE)' '$(REMOTE_OWNER_FILE)'; \
	if test "$$remote" = "$$fingerprint"; then \
		echo '[modal] Source unchanged; skipping archive upload.'; \
	else \
		$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/source_reconcile.py' \
			--previous '$(CLOUDMAKE_MANIFEST)' --current '$(CLOUDMAKE_CURRENT_MANIFEST)' \
			--script '$(MODAL_SOURCE_DELETE_SCRIPT)'; \
		$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/source_fingerprint.py' \
			--root '$(PROJECT_DIR)' --archive '$(MODAL_SOURCE_ARCHIVE)' \
			$(CLOUDMAKE_SECRET_OPTION) \
			--warn-mb '$(SOURCE_WARN_MB)' --max-mb '$(SOURCE_MAX_MB)' >/dev/null; \
		$(MODAL_CONTROL) upload '$(MODAL_SOURCE_ARCHIVE)' '$(REMOTE_SOURCE_ARCHIVE)'; \
		$(MODAL_CONTROL) upload '$(MODAL_SOURCE_DELETE_SCRIPT)' '$(REMOTE_DELETE_SCRIPT)'; \
		$(MODAL_CONTROL) exec "mkdir -p '$(REMOTE_SRC)' && sh '$(REMOTE_DELETE_SCRIPT)' '$(REMOTE_SRC)' && tar -xzf '$(REMOTE_SOURCE_ARCHIVE)' -C '$(REMOTE_SRC)' && rm -f '$(REMOTE_SOURCE_ARCHIVE)' '$(REMOTE_DELETE_SCRIPT)' && printf '%s' '$$fingerprint' > '$(REMOTE_FINGERPRINT)'"; \
		echo '[modal] Synchronized source to $(REMOTE_SRC)'; \
	fi
	@mv '$(CLOUDMAKE_CURRENT_MANIFEST)' '$(CLOUDMAKE_MANIFEST)'

_modal-execute: _modal-start
	@$(MAKE) --no-print-directory _modal-sync-unlocked
	@set -eu; \
	if test -n '$(REMOTE_COLLECT_DIR_B64)'; then \
		$(MODAL_CONTROL) exec "rm -f '$(REMOTE_ARTIFACT_ARCHIVE)'"; \
	fi; \
	command="$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/remote_make_command.py' \
		--source '$(REMOTE_SRC)' --makefile '$(REMOTE_MAKEFILE)' \
		--jobs '$(JOBS)' --target-b64 '$(REMOTE_TARGET_B64)' \
		--arguments-b64 '$(CLOUDMAKE_PROJECT_ARGS_B64)' --runner native )"; \
	rm -f '$(MODAL_TARGET_RESULT)'; \
	$(CLOUDMAKE_RECORD_STATE) --phase target_execution --provider-state running \
		--target-submission ambiguous --retry-safe false; \
	set +e; $(MODAL_CONTROL) exec --result '$(MODAL_TARGET_RESULT)' \
		--target-b64 '$(REMOTE_TARGET_B64)' "$$command"; set -e; \
	if test ! -f '$(MODAL_TARGET_RESULT)'; then \
		$(CLOUDMAKE_RECORD_STATE) --phase target_execution --provider-state unknown \
			--target-submission ambiguous --retry-safe false \
			--failure-code ambiguous_execution; \
		echo '[modal] The target may have run; automatic replay is unsafe. Next: cloudmake --status' >&2; \
		exit 1; \
	fi; \
	if test '$(CLOUDMAKE_NATIVE_PERSIST)' = 1; then \
		$(MODAL_CONTROL) exec "sync '$(REMOTE_ROOT)'" || :; \
	fi; \
	set +e; $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/target_result.py' \
		--result '$(MODAL_TARGET_RESULT)'; code=$$?; set -e; \
	if test $$code -eq 0; then provider=succeeded; else provider=failed; fi; \
	$(CLOUDMAKE_RECORD_STATE) --phase target_execution --provider-state $$provider \
		--target-submission submitted --retry-safe false; \
	if test $$code -ne 0; then exit $$code; fi; \
	if test -n '$(REMOTE_COLLECT_DIR_B64)'; then \
		collect_command="$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/remote_collect_command.py' \
			--source '$(REMOTE_SRC)' --directory-b64 '$(REMOTE_COLLECT_DIR_B64)' \
			--archive '$(REMOTE_ARTIFACT_ARCHIVE)' )"; \
		$(MODAL_CONTROL) exec "$$collect_command"; \
	fi

_modal-collect: _modal-execute
	@$(MAKE) --no-print-directory _modal-fetch

# Fetch never allocates: it reads the archive from the running sandbox only.
_modal-fetch: ensure-owner
	@mkdir -p '$(MODAL_STATE_DIR)'
	@$(CLOUDMAKE_RECORD_STATE) --phase artifact_retrieval --provider-state ready
	@set -eu; rm -f '$(MODAL_ARTIFACT_ARCHIVE).tmp'; \
	set +e; $(MODAL_CONTROL) download '$(REMOTE_ARTIFACT_ARCHIVE)' '$(MODAL_ARTIFACT_ARCHIVE).tmp'; code=$$?; set -e; \
	case $$code in \
		0) ;; \
		3) echo '[modal] No collected artifacts are available; run cloudmake --collect DIR TARGET first.' >&2; exit 1 ;; \
		*) exit $$code ;; \
	esac; \
	mv '$(MODAL_ARTIFACT_ARCHIVE).tmp' '$(MODAL_ARTIFACT_ARCHIVE)'; \
	$(CLOUDMAKE_SAFE_EXTRACT) \
		--archive '$(MODAL_ARTIFACT_ARCHIVE)' --destination '$(ARTIFACT_DIR)'

_modal-shell: _modal-start
	@$(MODAL_CONTROL) shell --modal-bin '$(MODAL_BIN)' --source '$(REMOTE_SRC)'

_modal-open: _modal-start
	@$(MODAL_CONTROL) connect-token --port '$(MODAL_WEB_PORT)'

_modal-stop: ensure-owner
	@$(MODAL_CONTROL) stop
	@rm -f '$(MODAL_RESOURCE_STATE)'
