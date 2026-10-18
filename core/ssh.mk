REMOTE_SRC := $(REMOTE_ROOT)/src
REMOTE_OWNER_FILE := $(REMOTE_ROOT)/.cloudmake-owner.json
REMOTE_LOCK := $(REMOTE_ROOT)/.cloudmake-lock
REMOTE_ARTIFACT_ARCHIVE := $(REMOTE_ROOT)/.cloudmake-artifacts.tar.gz
REMOTE_OCI_TOOL := $(REMOTE_ROOT)/.cloudmake-oci-runner.py
REMOTE_DEVCONTAINER_TOOL := $(REMOTE_ROOT)/.cloudmake-devcontainer.py
REMOTE_OCI_CACHE := $(REMOTE_ROOT)/.cloudmake-oci-cache
REMOTE_OCI_RESULT := $(REMOTE_ROOT)/.cloudmake-oci-result.json
REMOTE_LOCK_STALE ?= 7200

SSH_BIN ?= ssh
RSYNC_BIN ?= rsync
SSH_OPTIONS ?=
BACKEND_PREREQUISITE ?=
BACKEND_START ?= :
BACKEND_PRE_CONNECT ?= :
BACKEND_STATUS ?= :
BACKEND_STOP ?= :
BACKEND_STOP_PREREQUISITE ?= doctor
BACKEND_REMOTE_REQUIRED_COMMANDS ?= $(if $(filter oci,$(CLOUDMAKE_RUNNER)),python3,make) rsync tar
BACKEND_OCI_REMOTE_REQUIRED_COMMANDS ?= python3 rsync tar
CLOUDMAKE_REMOTE_REQUIRED_COMMANDS := $(if $(filter oci,$(CLOUDMAKE_RUNNER)),$(BACKEND_OCI_REMOTE_REQUIRED_COMMANDS),$(BACKEND_REMOTE_REQUIRED_COMMANDS))
CLOUDMAKE_REMOTE_RUNNER := $(if $(and $(filter oci,$(CLOUDMAKE_RUNNER)),$(filter yes,$(BACKEND_OCI_NATIVE))),native,$(CLOUDMAKE_RUNNER))
REMOTE_MAKEFILE ?= $(PROJECT_MAKEFILE)
SSH_REFRESH_MESSAGE ?= SSH connection failed; refreshing generated configuration once.

# SSH_HOST may be discovered from a configuration file generated as a target
# prerequisite. Keep this recursive so it is resolved after that file exists.
SSH = $(SSH_BIN) $(SSH_OPTIONS) $(SSH_HOST)
SSH_EXECUTE = $(SSH_BIN) $(SSH_OPTIONS) $(CLOUDMAKE_SSH_FORWARD_OPTIONS) $(SSH_HOST)
RSYNC_RSH := $(SSH_BIN) $(SSH_OPTIONS)
SSH_REMOTE_OWNER_COPY := $(CLOUDMAKE_STATE_ROOT)/$(BACKEND)/$(BACKEND_RESOURCE_ID)/remote-owner.json
CLOUDMAKE_RSYNC_IGNORE := $(if $(wildcard $(PROJECT_DIR)/.cloudmakeignore),--exclude-from='$(PROJECT_DIR)/.cloudmakeignore',)
SSH_ARTIFACT_ARCHIVE := $(CLOUDMAKE_STATE_ROOT)/$(BACKEND)/$(BACKEND_RESOURCE_ID)/artifacts.tar.gz
SSH_SOURCE_DELETE_SCRIPT := $(CLOUDMAKE_STATE_ROOT)/$(BACKEND)/$(BACKEND_RESOURCE_ID)/source-delete.sh
SSH_OCI_RESULT := $(CLOUDMAKE_STATE_ROOT)/$(BACKEND)/$(BACKEND_RESOURCE_ID)/oci-result.json

.PHONY: help start status stop sync collect dispatch fetch shell open \
	_ssh-backend-start _ssh-start _ssh-sync _ssh-sync-unlocked _ssh-execute \
	_ssh-fetch _ssh-shell _ssh-stop

ifneq ($(strip $(BACKEND_PREREQUISITE)),)
$(BACKEND_PREREQUISITE): | _ssh-backend-start
endif

help:
	@echo 'Usage: make BACKEND=<name> <target>'
	@echo 'Local backend: local (direct project Make invocation)'
	@echo 'Notebook backends: colab-notebook (default), kaggle-notebook'
	@echo 'SSH backends: codespaces-ssh, gcp-compute-ssh, host-ssh, lightning-studio-ssh'
	@echo 'Historical SSH backend: colab-ssh'
	@echo 'SDK backends: modal-sandbox'
	@echo
	@echo 'Engine operations: start status stop sync collect fetch shell'
	@echo '                   sync-dry-run prerequisites doctor backend-info'
	@echo 'Internal project-target entry point: dispatch (launcher managed)'

start: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _ssh-start

status: doctor
	@mkdir -p '$(CLOUDMAKE_STATE_ROOT)/status'
	@set -e; temporary='$(CLOUDMAKE_STATE_ROOT)/status/$(BACKEND)-$$$$.tmp'; \
		if $(BACKEND_STATUS) > "$$temporary" 2>&1; then \
			cat "$$temporary"; \
			$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/normalize_status.py' --backend '$(BACKEND)' < "$$temporary"; \
			rm -f "$$temporary"; \
		else code=$$?; cat "$$temporary"; rm -f "$$temporary"; exit $$code; fi

stop: $(BACKEND_STOP_PREREQUISITE)
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _ssh-stop

sync: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _ssh-sync

dispatch: doctor
	@if test -z '$(REMOTE_TARGET_B64)'; then echo 'REMOTE_TARGET_B64 is required for dispatch' >&2; exit 2; fi
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _ssh-execute

collect: doctor
	@if test -z '$(REMOTE_TARGET)' && test -z '$(REMOTE_TARGET_B64)'; then echo 'REMOTE_TARGET or REMOTE_TARGET_B64 is required for collect' >&2; exit 2; fi
	@if test -z '$(REMOTE_COLLECT_DIR_B64)'; then echo 'REMOTE_COLLECT_DIR_B64 is required for collect' >&2; exit 2; fi
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _ssh-collect

fetch: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _ssh-fetch

shell: doctor
	@$(CLOUDMAKE_WITH_LOCK) $(MAKE) --no-print-directory _ssh-shell

open:
	@echo 'The $(BACKEND) backend uses SSH; run: make BACKEND=$(BACKEND) shell'

_ssh-backend-start: ensure-owner
	@$(BACKEND_START)

_ssh-start: ensure-owner $(BACKEND_PREREQUISITE)
	@$(BACKEND_PRE_CONNECT)
	@if ! $(SSH) true; then \
		echo '[cloudmake] $(SSH_REFRESH_MESSAGE)' >&2; \
		$(MAKE) --no-print-directory refresh-ssh-config; \
		$(SSH) true; \
	fi
	@missing=0; \
	for command in $(CLOUDMAKE_REMOTE_REQUIRED_COMMANDS); do \
		if ! $(SSH) "command -v '$$command' >/dev/null 2>&1"; then \
			echo "Missing required remote command: $$command" >&2; \
			missing=1; \
		fi; \
	done; \
	if test "$$missing" -ne 0; then \
		echo 'Install the missing tool in the remote image before running cloudmake.' >&2; \
		exit 2; \
	fi
	@resource_state='$(BACKEND_CONTEXT_RESOURCE_STATE)'; \
		if test -n '$(BACKEND_CONTEXT_RESOURCE_STATE_FILE)' \
			&& test -f '$(BACKEND_CONTEXT_RESOURCE_STATE_FILE)'; then \
			resource_state="$$(cat '$(BACKEND_CONTEXT_RESOURCE_STATE_FILE)')"; \
		fi; \
		CLOUDMAKE_RESOURCE_STATE="$$resource_state"; $(CLOUDMAKE_PRINT_CONTEXT)

_ssh-sync: _ssh-start
	@set -eu; \
	token="$$(cat '$(CLOUDMAKE_OWNER_ID_FILE)')-$$$$-$$(date +%s)"; \
	$(SSH) "mkdir -p '$(REMOTE_ROOT)' && sh -s -- acquire '$(REMOTE_LOCK)' '$$token' '$(REMOTE_LOCK_STALE)'" < '$(CLOUDMAKE_TOOL_ROOT)/core/remote_lock.sh'; \
	cleanup() { $(SSH) "sh -s -- release '$(REMOTE_LOCK)' '$$token' '$(REMOTE_LOCK_STALE)'" < '$(CLOUDMAKE_TOOL_ROOT)/core/remote_lock.sh' >/dev/null 2>&1 || :; }; \
	trap cleanup EXIT HUP INT TERM; \
	$(MAKE) --no-print-directory _ssh-sync-unlocked

_ssh-sync-unlocked: ensure-owner $(BACKEND_PREREQUISITE)
	@mkdir -p '$(dir $(SSH_REMOTE_OWNER_COPY))'
	@mkdir -p '$(CLOUDMAKE_MANIFEST_DIR)'
	@$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/source_fingerprint.py' \
		--root '$(PROJECT_DIR)' \
		--manifest '$(CLOUDMAKE_CURRENT_MANIFEST)' \
		$(CLOUDMAKE_SECRET_OPTION) \
		--warn-mb '$(SOURCE_WARN_MB)' --max-mb '$(SOURCE_MAX_MB)' >/dev/null
	@$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/source_reconcile.py' \
		--previous '$(CLOUDMAKE_MANIFEST)' \
		--current '$(CLOUDMAKE_CURRENT_MANIFEST)' \
		--script '$(SSH_SOURCE_DELETE_SCRIPT)'
	@set -eu; \
	temporary='$(SSH_REMOTE_OWNER_COPY).tmp'; \
	if $(SSH) "cat '$(REMOTE_OWNER_FILE)'" > "$$temporary" 2>/dev/null && test -s "$$temporary"; then \
		mv "$$temporary" '$(SSH_REMOTE_OWNER_COPY)'; \
		adopt=''; test '$(CLOUDMAKE_ADOPT)' = 1 && adopt='--adopt' || :; \
		$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/project_identity.py' check \
			--expected '$(CLOUDMAKE_OWNER_FILE)' \
			--actual '$(SSH_REMOTE_OWNER_COPY)' \
			--resource '$(BACKEND) workspace $(REMOTE_ROOT)' $$adopt; \
	else \
		rm -f "$$temporary" '$(SSH_REMOTE_OWNER_COPY)'; \
	fi
	$(SSH) 'mkdir -p $(REMOTE_SRC)'
	$(SSH) "sh -s -- '$(REMOTE_SRC)'" < '$(SSH_SOURCE_DELETE_SCRIPT)'
	$(RSYNC_BIN) -az \
		--exclude='/.git/' \
		--exclude='/.cloud-state/' \
		--exclude='/.cloudmake/' \
		$(CLOUDMAKE_RSYNC_IGNORE) \
		-e '$(RSYNC_RSH)' \
		'$(PROJECT_DIR)/' $(SSH_HOST):$(REMOTE_SRC)/
	$(RSYNC_BIN) -az -e '$(RSYNC_RSH)' \
		'$(CLOUDMAKE_OWNER_FILE)' $(SSH_HOST):$(REMOTE_OWNER_FILE)
	@if test '$(CLOUDMAKE_RUNNER)' = oci && test '$(BACKEND_OCI_NATIVE)' != yes; then \
		$(RSYNC_BIN) -az -e '$(RSYNC_RSH)' \
			'$(CLOUDMAKE_TOOL_ROOT)/core/oci_runner.py' $(SSH_HOST):$(REMOTE_OCI_TOOL); \
	fi
	@if test -n '$(CLOUDMAKE_DEVCONTAINER_ACTIVE)'; then \
		$(RSYNC_BIN) -az -e '$(RSYNC_RSH)' \
			'$(CLOUDMAKE_TOOL_ROOT)/core/devcontainer_config.py' $(SSH_HOST):$(REMOTE_DEVCONTAINER_TOOL); \
	fi
	@mv '$(CLOUDMAKE_CURRENT_MANIFEST)' '$(CLOUDMAKE_MANIFEST)'

_ssh-execute: _ssh-start
	@set -eu; \
	token="$$(cat '$(CLOUDMAKE_OWNER_ID_FILE)')-$$$$-$$(date +%s)"; \
	$(SSH) "mkdir -p '$(REMOTE_ROOT)' && sh -s -- acquire '$(REMOTE_LOCK)' '$$token' '$(REMOTE_LOCK_STALE)'" < '$(CLOUDMAKE_TOOL_ROOT)/core/remote_lock.sh'; \
	cleanup() { $(SSH) "sh -s -- release '$(REMOTE_LOCK)' '$$token' '$(REMOTE_LOCK_STALE)'" < '$(CLOUDMAKE_TOOL_ROOT)/core/remote_lock.sh' >/dev/null 2>&1 || :; }; \
	trap cleanup EXIT HUP INT TERM; \
	$(MAKE) --no-print-directory _ssh-sync-unlocked; \
	if test -n '$(REMOTE_COLLECT_DIR_B64)'; then \
		$(SSH) "rm -f '$(REMOTE_ARTIFACT_ARCHIVE)'"; \
	fi; \
	if test '$(CLOUDMAKE_RUNNER)' = oci && test '$(BACKEND_OCI_NATIVE)' != yes; then \
		$(SSH) "rm -f '$(REMOTE_OCI_RESULT)'"; \
	fi; \
	command="$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/remote_make_command.py' \
		--source '$(REMOTE_SRC)' --makefile '$(REMOTE_MAKEFILE)' \
		--jobs '$(JOBS)' --target '$(REMOTE_TARGET)' \
		--target-b64 '$(REMOTE_TARGET_B64)' \
		--arguments-b64 '$(CLOUDMAKE_PROJECT_ARGS_B64)' \
		--runner '$(CLOUDMAKE_REMOTE_RUNNER)' \
		--oci-image-b64 '$(CLOUDMAKE_OCI_IMAGE_B64)' \
		--oci-devices-b64 '$(CLOUDMAKE_OCI_DEVICES_B64)' \
		--oci-runtimes-b64 '$(CLOUDMAKE_OCI_RUNTIMES_B64)' \
		--environment-b64 '$(CLOUDMAKE_OCI_ENVIRONMENT_B64)' \
		--host-requirements-b64 '$(CLOUDMAKE_HOST_REQUIREMENTS_B64)' \
		--forward-ports-b64 '$(CLOUDMAKE_FORWARD_PORTS_B64)' \
		--devcontainer-tool '$(REMOTE_DEVCONTAINER_TOOL)' \
		--oci-runtime '$(CLOUDMAKE_OCI_RUNTIME)' \
		--oci-tool '$(REMOTE_OCI_TOOL)' --oci-cache '$(REMOTE_OCI_CACHE)' \
		--oci-result '$(REMOTE_OCI_RESULT)' )"; \
	rm -f '$(SSH_OCI_RESULT)' '$(SSH_OCI_RESULT).tmp'; \
	set +e; $(SSH_EXECUTE) "$$command"; execute_status=$$?; set -e; \
	if test '$(CLOUDMAKE_RUNNER)' = oci; then \
		if test '$(BACKEND_OCI_NATIVE)' = yes; then \
			$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/provider_oci_result.py' \
				--result '$(SSH_OCI_RESULT)' --runtime provider-native \
				--image-b64 '$(CLOUDMAKE_OCI_IMAGE_B64)' \
				--target-b64 '$(REMOTE_TARGET_B64)' --exit-code "$$execute_status"; \
		else \
			mkdir -p '$(dir $(SSH_OCI_RESULT))'; \
			$(RSYNC_BIN) -az -e '$(RSYNC_RSH)' \
				$(SSH_HOST):$(REMOTE_OCI_RESULT) '$(SSH_OCI_RESULT).tmp' >/dev/null 2>&1 || :; \
			if test -f '$(SSH_OCI_RESULT).tmp'; then \
				mv '$(SSH_OCI_RESULT).tmp' '$(SSH_OCI_RESULT)'; \
			fi; \
		fi; \
		set +e; \
		$(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/oci_result.py' \
			--result '$(SSH_OCI_RESULT)' --expect terminal \
			--process-status "$$execute_status"; result_status=$$?; \
		set -e; \
		if test $$result_status -ne 0; then exit $$result_status; fi; \
	fi; \
	if test $$execute_status -ne 0; then exit $$execute_status; fi; \
	if test -n '$(REMOTE_COLLECT_DIR_B64)'; then \
		collect_command="$$( $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/remote_collect_command.py' \
			--source '$(REMOTE_SRC)' --directory-b64 '$(REMOTE_COLLECT_DIR_B64)' \
			--archive '$(REMOTE_ARTIFACT_ARCHIVE)' )"; \
		$(SSH) "$$collect_command"; \
	fi

.PHONY: _ssh-collect
_ssh-collect: _ssh-execute
	@$(MAKE) --no-print-directory _ssh-fetch

_ssh-fetch: _ssh-start
	@set -eu; \
	token="$$(cat '$(CLOUDMAKE_OWNER_ID_FILE)')-$$$$-$$(date +%s)"; \
	$(SSH) "mkdir -p '$(REMOTE_ROOT)' && sh -s -- acquire '$(REMOTE_LOCK)' '$$token' '$(REMOTE_LOCK_STALE)'" < '$(CLOUDMAKE_TOOL_ROOT)/core/remote_lock.sh'; \
	cleanup() { $(SSH) "sh -s -- release '$(REMOTE_LOCK)' '$$token' '$(REMOTE_LOCK_STALE)'" < '$(CLOUDMAKE_TOOL_ROOT)/core/remote_lock.sh' >/dev/null 2>&1 || :; }; \
	trap cleanup EXIT HUP INT TERM; \
	mkdir -p '$(dir $(SSH_ARTIFACT_ARCHIVE))'; \
	$(RSYNC_BIN) -az -e '$(RSYNC_RSH)' \
		$(SSH_HOST):$(REMOTE_ARTIFACT_ARCHIVE) '$(SSH_ARTIFACT_ARCHIVE).tmp'; \
	mv '$(SSH_ARTIFACT_ARCHIVE).tmp' '$(SSH_ARTIFACT_ARCHIVE)'; \
	$(CLOUDMAKE_SAFE_EXTRACT) \
		--archive '$(SSH_ARTIFACT_ARCHIVE)' --destination '$(ARTIFACT_DIR)'

_ssh-shell: _ssh-start
	$(SSH_BIN) -t $(SSH_OPTIONS) $(SSH_HOST) \
		'cd $(REMOTE_SRC) && exec bash -l'

_ssh-stop: ensure-owner
	$(BACKEND_STOP)
