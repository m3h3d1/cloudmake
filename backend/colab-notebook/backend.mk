# Colab notebook backend: native contents and kernel APIs, independent of SSH
# and GitHub/Gist credentials.
BACKEND_TRANSPORT := colab-native
BACKEND_ACCESS_CLASS := notebook
BACKEND_API_VERSION := 1
BACKEND_PRODUCT_STATUS := supported
BACKEND_SESSION_REUSE := yes
BACKEND_LIFECYCLE_CONTROL := provider-managed
BACKEND_WORKSPACE_DURABILITY := ephemeral
BACKEND_TARGET_REPLAY := none
BACKEND_OCI_NATIVE := no
BACKEND_CAPABILITIES := sync execute status incremental-sync open artifacts gpu tpu cancel capacity-retry persistent-workspace checkpoint-persistence environment-profile oci-runner devcontainer
BACKEND_OCI_RUNTIMES := crun
BACKEND_DEVCONTAINER_ADAPTER_CAPABILITIES := image-digest environment host-requirements cdi security-policy
BACKEND_DEVCONTAINER_NATIVE_CAPABILITIES := none
BACKEND_DEVCONTAINER_REALIZATION_ORDER := adapter
BACKEND_INTERNET_INBOUND := no
BACKEND_INTERNET_OUTBOUND := yes

COLAB_SESSION ?= $(PROJECT_SLUG)
COLAB_GPU ?=
COLAB_TPU ?=
COLAB_TIMEOUT ?= 3600
COLAB_READY_TIMEOUT ?= 120
COLAB_READY_POLL_SECONDS ?= 3
COLAB_READY_PROBE_TIMEOUT ?= 15
COLAB_SESSION_PREPARE_TARGET ?=
COLAB_BIN ?= colab
COLAB_NOTEBOOK ?= $(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/notebook.ipynb
PYTHON_BIN ?= python3
CLOUDMAKE_CHECKPOINT ?= 0
CLOUDMAKE_PROJECT_KEY ?=
CLOUDMAKE_WORKSPACE_ID ?= $(CLOUDMAKE_PROJECT_KEY)
CLOUDMAKE_CHECKPOINT_KEYCHAIN ?= $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/core/checkpoint_keychain.py'

BACKEND_REQUIRED_COMMANDS := $(COLAB_BIN) $(PYTHON_BIN) tar $(if $(filter 1,$(CLOUDMAKE_CHECKPOINT)),openssl)
BACKEND_REQUIRES_PYTHON := yes
BACKEND_INSTALL_HINT := Install the official CLI with: uv tool install google-colab-cli
BACKEND_VALIDATE := case '$(COLAB_SESSION)' in ''|*[!A-Za-z0-9._-]*) echo 'COLAB_SESSION contains unsupported characters' >&2; exit 2;; esac; case '$(COLAB_GPU)' in *[!A-Za-z0-9._-]*) echo 'COLAB_GPU contains unsupported characters' >&2; exit 2;; esac; case '$(COLAB_TIMEOUT)' in ''|*[!0-9]*) echo 'COLAB_TIMEOUT must be a positive integer' >&2; exit 2;; 0) echo 'COLAB_TIMEOUT must be a positive integer' >&2; exit 2;; esac; case '$(COLAB_READY_TIMEOUT)' in ''|*[!0-9.]*) echo 'COLAB_READY_TIMEOUT must be a positive number' >&2; exit 2;; esac; case '$(COLAB_READY_PROBE_TIMEOUT)' in ''|*[!0-9.]*) echo 'COLAB_READY_PROBE_TIMEOUT must be a positive number' >&2; exit 2;; esac; case '$(COLAB_READY_POLL_SECONDS)' in ''|*[!0-9.]*) echo 'COLAB_READY_POLL_SECONDS must be a non-negative number' >&2; exit 2;; esac; case '$(COLAB_SESSION_PREPARE_TARGET)' in *[!A-Za-z0-9._/-]*) echo 'COLAB_SESSION_PREPARE_TARGET must be a simple Make target name' >&2; exit 2;; esac; if test '$(CLOUDMAKE_CHECKPOINT)' = 1; then $(CLOUDMAKE_CHECKPOINT_KEYCHAIN) check; fi
BACKEND_VALIDATE += ; case '$(COLAB_TPU)' in ''|v5e1|v6e1) ;; *) echo 'COLAB_TPU must be v5e1 or v6e1' >&2; exit 2;; esac; if test -n '$(COLAB_GPU)' && test -n '$(COLAB_TPU)'; then echo 'COLAB_GPU and COLAB_TPU cannot be combined' >&2; exit 2; fi
BACKEND_DOCTOR_COMPATIBILITY := '$(PYTHON_BIN)' '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/cli_compatibility.py' --colab-bin '$(COLAB_BIN)'
BACKEND_DOCTOR_PROBE := $(COLAB_BIN) version >/dev/null && $(COLAB_BIN) sessions >/dev/null
BACKEND_VERSION_COMMAND := $(COLAB_BIN) version
BACKEND_TESTED_CLIENT := google-colab-cli 0.6.x
BACKEND_RESOURCE_ID := $(COLAB_SESSION)
BACKEND_CONTEXT_RESOURCE_LABEL := session
BACKEND_CONTEXT_ACCELERATOR := $(if $(strip $(COLAB_TPU)),tpu-$(COLAB_TPU),$(if $(strip $(COLAB_GPU)),$(COLAB_GPU),$(CLOUDMAKE_CONTEXT_ACCELERATOR)))
COLAB_CHECKPOINT_HOST ?= $(PYTHON_BIN) '$(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/checkpoint_host.py'
