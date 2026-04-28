VENV ?= .venv
ARGS ?=

GXWF_ABSTRACT_EXPORT := $(VENV)/bin/gxwf-abstract-export
GXWF_LINT := $(VENV)/bin/gxwf-lint
GXWF_TO_FORMAT2 := $(VENV)/bin/gxwf-to-format2
GXWF_TO_NATIVE := $(VENV)/bin/gxwf-to-native
GXWF_VIZ := $(VENV)/bin/gxwf-viz

# Capture extra positional args after the target name so callers can write
#   make gxwf-to-native path/to.gxwf.yml
# instead of
#   make gxwf-to-native ARGS='path/to.gxwf.yml'
KNOWN_TARGETS := help check-gxformat2 list-gxformat2-commands \
                 gxwf-abstract-export gxwf-lint gxwf-to-format2 \
                 gxwf-to-native gxwf-viz
EXTRA := $(filter-out $(KNOWN_TARGETS),$(MAKECMDGOALS))
ifeq ($(strip $(ARGS)),)
ARGS := $(EXTRA)
endif

# Swallow stray positional args so make doesn't try to build them as targets.
%::
	@true

.PHONY: help check-gxformat2 list-gxformat2-commands gxwf-abstract-export gxwf-lint gxwf-to-format2 gxwf-to-native gxwf-viz

help:
	@printf "gxformat2 command wrappers\n"
	@printf "Usage: make <target> ARGS='<command args>'\n"
	@printf "Note: gxwf-lint without ARGS lints all generated/*.gxwf.yml and generated/*.gxwf.yaml files\n\n"
	@printf "Targets:\n"
	@printf "  list-gxformat2-commands  List all wrapped gxformat2 commands\n"
	@printf "  gxwf-abstract-export     Run gxwf-abstract-export $(ARGS)\n"
	@printf "  gxwf-lint                Run gxwf-lint $(ARGS)\n"
	@printf "  gxwf-to-format2          Run gxwf-to-format2 $(ARGS)\n"
	@printf "  gxwf-to-native           Run gxwf-to-native $(ARGS)\n"
	@printf "  gxwf-viz                 Run gxwf-viz $(ARGS)\n"

check-gxformat2:
	@test -x "$(GXWF_ABSTRACT_EXPORT)" || (echo "Missing $(GXWF_ABSTRACT_EXPORT). Create and populate $(VENV) first."; exit 1)
	@test -x "$(GXWF_LINT)" || (echo "Missing $(GXWF_LINT). Create and populate $(VENV) first."; exit 1)
	@test -x "$(GXWF_TO_FORMAT2)" || (echo "Missing $(GXWF_TO_FORMAT2). Create and populate $(VENV) first."; exit 1)
	@test -x "$(GXWF_TO_NATIVE)" || (echo "Missing $(GXWF_TO_NATIVE). Create and populate $(VENV) first."; exit 1)
	@test -x "$(GXWF_VIZ)" || (echo "Missing $(GXWF_VIZ). Create and populate $(VENV) first."; exit 1)

list-gxformat2-commands:
	@printf "gxwf-abstract-export\n"
	@printf "gxwf-lint\n"
	@printf "gxwf-to-format2\n"
	@printf "gxwf-to-native\n"
	@printf "gxwf-viz\n"

gxwf-abstract-export: check-gxformat2
	$(GXWF_ABSTRACT_EXPORT) $(ARGS)

gxwf-lint: check-gxformat2
	@if [ -n "$(strip $(ARGS))" ]; then \
		$(GXWF_LINT) $(ARGS); \
	else \
		FILES="$$(find generated -type f \( -name '*.gxwf.yml' -o -name '*.gxwf.yaml' \) 2>/dev/null)"; \
		if [ -z "$$FILES" ]; then \
			echo "No generated workflow files found (.gxwf.yml/.gxwf.yaml under generated/)."; \
			echo "Use: make gxwf-lint ARGS='path/to/workflow.gxwf.yml'"; \
			exit 1; \
		fi; \
		for f in $$FILES; do \
			echo "Linting $$f"; \
			$(GXWF_LINT) "$$f" || exit $$?; \
		done; \
	fi

gxwf-to-format2: check-gxformat2
	$(GXWF_TO_FORMAT2) $(ARGS)

gxwf-to-native: check-gxformat2
	@words="$(words $(ARGS))"; \
	if [ "$$words" = "1" ]; then \
		in="$(firstword $(ARGS))"; \
		out="$${in%.gxwf.yml}"; out="$${out%.gxwf.yaml}.ga"; \
		echo "Converting $$in -> $$out"; \
		$(GXWF_TO_NATIVE) "$$in" "$$out"; \
	else \
		$(GXWF_TO_NATIVE) $(ARGS); \
	fi

gxwf-viz: check-gxformat2
	$(GXWF_VIZ) $(ARGS)
