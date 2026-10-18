ifndef CLOUDMAKE_TOOL_ROOT
CLOUDMAKE_TOOL_ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
endif
PROJECT_DIR ?= $(CURDIR)
PROJECT ?= cloud-build-prototype
PROJECT_SLUG ?= $(PROJECT)
PROJECT_MAKEFILE ?= Makefile
ARTIFACT_DIR ?= $(abspath $(PROJECT_DIR))/.cloudmake/artifacts
CLOUDMAKE_STATE_ROOT ?= .cloud-state
CLOUDMAKE_CACHE_ROOT ?= $(CLOUDMAKE_STATE_ROOT)/cache
BACKEND ?= colab-notebook
JOBS ?= 4
PREFIX ?= $(HOME)/.local
VERSION := $(strip $(shell sed -n '1p' '$(CLOUDMAKE_TOOL_ROOT)/VERSION'))
CLOUDMAKE_RUNTIME_DIR ?= $(PREFIX)/libexec/cloudmake
DIST_DIR ?= $(CLOUDMAKE_TOOL_ROOT)/dist

CLOUDMAKE_RUNTIME_FILES := $(CLOUDMAKE_TOOL_ROOT)/Makefile $(CLOUDMAKE_TOOL_ROOT)/VERSION \
	$(wildcard $(CLOUDMAKE_TOOL_ROOT)/core/*.mk) \
	$(wildcard $(CLOUDMAKE_TOOL_ROOT)/core/*.py) \
	$(wildcard $(CLOUDMAKE_TOOL_ROOT)/core/*.sh) \
	$(foreach directory,$(wildcard $(CLOUDMAKE_TOOL_ROOT)/backend/*), \
		$(wildcard $(directory)/*.mk) \
		$(wildcard $(directory)/*.py) \
		$(wildcard $(directory)/*.sh) \
		$(wildcard $(directory)/*.ipynb) \
		$(wildcard $(directory)/*.conf) \
		$(wildcard $(directory)/*.json) \
		$(wildcard $(directory)/*.Dockerfile) \
		$(wildcard $(directory)/README.md))

.DEFAULT_GOAL := help

BACKEND_FILE := $(CLOUDMAKE_TOOL_ROOT)/backend/$(BACKEND)/backend.mk

ifeq ($(wildcard $(BACKEND_FILE)),)
$(error Unknown backend "$(BACKEND)"; expected $(BACKEND_FILE))
endif

include $(BACKEND_FILE)
include $(CLOUDMAKE_TOOL_ROOT)/core/prerequisites.mk
include $(CLOUDMAKE_TOOL_ROOT)/core/resilience.mk

ifeq ($(BACKEND_TRANSPORT),colab-native)
include $(CLOUDMAKE_TOOL_ROOT)/backend/colab-notebook/transport.mk
else ifeq ($(BACKEND_TRANSPORT),kaggle-kernel)
include $(CLOUDMAKE_TOOL_ROOT)/backend/kaggle-notebook/transport.mk
else ifeq ($(BACKEND_TRANSPORT),ssh)
include $(CLOUDMAKE_TOOL_ROOT)/core/ssh.mk
else ifeq ($(BACKEND_TRANSPORT),local)
include $(CLOUDMAKE_TOOL_ROOT)/backend/local/transport.mk
else ifeq ($(BACKEND_TRANSPORT),modal-sandbox)
include $(CLOUDMAKE_TOOL_ROOT)/backend/modal-sandbox/transport.mk
else
$(error Backend "$(BACKEND)" has unknown BACKEND_TRANSPORT "$(BACKEND_TRANSPORT)")
endif

.PHONY: install dist
install:
	@mkdir -p '$(DESTDIR)$(PREFIX)/bin'
	@mkdir -p '$(DESTDIR)$(CLOUDMAKE_RUNTIME_DIR)'
	@for file in $(CLOUDMAKE_RUNTIME_FILES); do \
		relative=$${file#'$(CLOUDMAKE_TOOL_ROOT)'/}; \
		mkdir -p '$(DESTDIR)$(CLOUDMAKE_RUNTIME_DIR)/'"$$(dirname "$$relative")"; \
		install -m 644 "$$file" '$(DESTDIR)$(CLOUDMAKE_RUNTIME_DIR)/'"$$relative"; \
	done
	@install -m 755 '$(CLOUDMAKE_TOOL_ROOT)/cmd/cloudmake' '$(DESTDIR)$(PREFIX)/bin/cloudmake'
	@echo 'Installed cloudmake $(VERSION) to $(DESTDIR)$(PREFIX)/bin/cloudmake'

dist:
	@mkdir -p '$(DIST_DIR)'
	@git -C '$(CLOUDMAKE_TOOL_ROOT)' archive \
		--format=tar --prefix='cloudmake-$(VERSION)/' HEAD | \
		gzip -n > '$(DIST_DIR)/cloudmake-$(VERSION).tar.gz'
	@if command -v sha256sum >/dev/null 2>&1; then \
		cd '$(DIST_DIR)' && sha256sum 'cloudmake-$(VERSION).tar.gz' > 'cloudmake-$(VERSION).tar.gz.sha256'; \
	else \
		cd '$(DIST_DIR)' && shasum -a 256 'cloudmake-$(VERSION).tar.gz' > 'cloudmake-$(VERSION).tar.gz.sha256'; \
	fi
	@echo 'Created $(DIST_DIR)/cloudmake-$(VERSION).tar.gz'
