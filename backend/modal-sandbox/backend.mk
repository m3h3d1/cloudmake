# Modal Sandboxes controlled through the official Python SDK; no SSH or rsync.
BACKEND_TRANSPORT := modal-sandbox
BACKEND_ACCESS_CLASS := paid-tier
BACKEND_API_VERSION := 1
BACKEND_PRODUCT_STATUS := supported
BACKEND_SESSION_REUSE := yes
BACKEND_LIFECYCLE_CONTROL := provider-managed
BACKEND_WORKSPACE_DURABILITY := ephemeral
BACKEND_TARGET_REPLAY := none
BACKEND_OCI_NATIVE := no
BACKEND_CAPABILITIES := sync execute status incremental-sync artifacts gpu cancel shell open native-persistence
BACKEND_OCI_RUNTIMES := none
BACKEND_DEVCONTAINER_ADAPTER_CAPABILITIES := none
BACKEND_DEVCONTAINER_NATIVE_CAPABILITIES := none
BACKEND_DEVCONTAINER_REALIZATION_ORDER := none
BACKEND_INTERNET_INBOUND := conditional
BACKEND_INTERNET_OUTBOUND := yes

MODAL_SANDBOX ?= $(PROJECT_SLUG)
MODAL_APP ?= cloudmake
MODAL_GPU ?=
MODAL_CPU ?= 1
MODAL_MEMORY ?= 8192
MODAL_IDLE_TIMEOUT ?= 1800
MODAL_TIMEOUT ?= 86400
MODAL_READY_TIMEOUT ?= 300
MODAL_IMAGE ?=
MODAL_WEB_PORT ?=
MODAL_REMOTE_ROOT ?= /cloudmake
MODAL_VOLUME ?= cloudmake-$(MODAL_SANDBOX)
MODAL_BIN ?= modal
# The SDK lives beside the official CLI, e.g. in its `uv tool` environment.
MODAL_PYTHON ?= $(or $(shell sed -n '1s/^\#!//p' "$$(command -v '$(MODAL_BIN)')" 2>/dev/null),$(PYTHON_BIN))
CLOUDMAKE_NATIVE_PERSIST ?= 0

BACKEND_REQUIRED_COMMANDS := $(MODAL_BIN) $(PYTHON_BIN) tar
BACKEND_REQUIRES_PYTHON := yes
BACKEND_INSTALL_HINT := Install the official CLI with: uv tool install modal; then run: modal token new
BACKEND_VALIDATE := case '$(MODAL_SANDBOX)' in ''|*[!A-Za-z0-9._-]*) echo 'MODAL_SANDBOX contains unsupported characters' >&2; exit 2;; esac; case '$(MODAL_APP)' in ''|*[!A-Za-z0-9._-]*) echo 'MODAL_APP contains unsupported characters' >&2; exit 2;; esac; case '$(MODAL_GPU)' in *[!A-Za-z0-9._-]*) echo 'MODAL_GPU contains unsupported characters' >&2; exit 2;; esac; case '$(MODAL_CPU)' in ''|*[!0-9.]*) echo 'MODAL_CPU must be a positive number' >&2; exit 2;; esac; for setting in MODAL_MEMORY=$(MODAL_MEMORY) MODAL_IDLE_TIMEOUT=$(MODAL_IDLE_TIMEOUT) MODAL_TIMEOUT=$(MODAL_TIMEOUT) MODAL_READY_TIMEOUT=$(MODAL_READY_TIMEOUT); do case "$${setting\#*=}" in ''|*[!0-9]*|0) echo "$${setting%%=*} must be a positive integer" >&2; exit 2;; esac; done; case '$(MODAL_WEB_PORT)' in '') ;; *[!0-9]*) echo 'MODAL_WEB_PORT must be an integer from 1 to 65535' >&2; exit 2;; esac; test -z '$(MODAL_WEB_PORT)' || { test '$(MODAL_WEB_PORT)' -ge 1 && test '$(MODAL_WEB_PORT)' -le 65535; } || { echo 'MODAL_WEB_PORT must be an integer from 1 to 65535' >&2; exit 2; }; test '$(MODAL_TIMEOUT)' -le 86400 || { echo 'MODAL_TIMEOUT cannot exceed the 86400-second Modal Sandbox limit' >&2; exit 2; }; case '$(MODAL_IMAGE)' in *[!A-Za-z0-9._/:@-]*) echo 'MODAL_IMAGE contains unsupported characters' >&2; exit 2;; esac; case '$(MODAL_REMOTE_ROOT)' in /*) ;; *) echo 'MODAL_REMOTE_ROOT must be an absolute path' >&2; exit 2;; esac; case '$(MODAL_REMOTE_ROOT)' in *[!A-Za-z0-9._/-]*) echo 'MODAL_REMOTE_ROOT contains unsupported characters' >&2; exit 2;; esac
BACKEND_DOCTOR_COMPATIBILITY := '$(MODAL_PYTHON)' -c 'import modal' 2>/dev/null || { echo '[doctor] $(MODAL_PYTHON) cannot import the modal SDK; set MODAL_PYTHON to the interpreter of the modal CLI.' >&2; exit 2; }
BACKEND_DOCTOR_PROBE := $(MODAL_BIN) profile current >/dev/null && $(MODAL_BIN) app list >/dev/null
BACKEND_VERSION_COMMAND := $(MODAL_BIN) --version
BACKEND_TESTED_CLIENT := modal 1.6.x
BACKEND_RESOURCE_ID := $(MODAL_SANDBOX)
BACKEND_CONTEXT_RESOURCE_LABEL := sandbox
BACKEND_CONTEXT_ACCELERATOR := $(if $(strip $(MODAL_GPU)),$(MODAL_GPU),$(CLOUDMAKE_CONTEXT_ACCELERATOR))
