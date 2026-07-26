# OpenClaw personal assistant — task runner
#
#   make help
#
# Thin, readable wrappers. Nothing here hides anything you couldn't type
# yourself; the point is that the safe form is the convenient one.

SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

OPENCLAW_HOME ?= $(HOME)/.openclaw
WORKSPACE     := $(OPENCLAW_HOME)/workspace
CONFIG        := $(OPENCLAW_HOME)/openclaw.json
BACKUP_DIR    ?= $(HOME)/backups/openclaw
STAMP         := $(shell date +%Y%m%d-%H%M%S)

.PHONY: help
help: ## Show this help
	@echo "OpenClaw personal assistant"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Setup order:  install -> secrets -> skills -> cron -> verify"

# ── Setup ───────────────────────────────────────────────────────────────────

.PHONY: install
install: ## Guided install + hardening (macOS)
	bash bootstrap.sh

.PHONY: skills
skills: ## Copy custom skills into the workspace
	@mkdir -p "$(WORKSPACE)/skills"
	@cp -R skills/*/ "$(WORKSPACE)/skills/" 2>/dev/null || true
	@echo "Skills copied to $(WORKSPACE)/skills"
	@echo "Note: skills snapshot at session start — changes land in the NEXT session."

.PHONY: identity
identity: ## Copy SOUL/IDENTITY/USER/AGENTS into the workspace (never overwrites)
	@mkdir -p "$(WORKSPACE)"
	@for f in workspace/*.md; do \
	  base=$$(basename $$f); \
	  if [ -e "$(WORKSPACE)/$$base" ]; then \
	    echo "skip  $$base (exists)"; \
	  else \
	    cp "$$f" "$(WORKSPACE)/$$base"; echo "copy  $$base"; \
	  fi; \
	done

.PHONY: cron
cron: ## Install the starter automation set
	bash cron-jobs.sh

# ── Verify ──────────────────────────────────────────────────────────────────

.PHONY: verify
verify: ## Full setup check (read-only)
	@bash verify.sh

.PHONY: audit
audit: ## OpenClaw security audit (report only)
	openclaw security audit

.PHONY: audit-fix
audit-fix: ## Security audit + apply safe fixes (tightens perms to 600/700)
	openclaw security audit --fix

.PHONY: doctor
doctor: ## Probe MCP servers for connectivity
	openclaw mcp doctor --probe

.PHONY: check
check: verify audit ## verify + audit

# ── Day to day ──────────────────────────────────────────────────────────────

.PHONY: status
status: ## Gateway status
	openclaw gateway status

.PHONY: restart
restart: ## Restart the gateway
	openclaw gateway restart

.PHONY: dashboard
dashboard: ## Open the Control UI
	openclaw dashboard

.PHONY: jobs
jobs: ## List scheduled jobs
	openclaw cron list

.PHONY: mcp
mcp: ## List MCP servers and their status
	openclaw mcp status --verbose

# ── Maintenance ─────────────────────────────────────────────────────────────

.PHONY: update
update: ## Update OpenClaw, restart, re-audit
	@echo "Check the changelog first — minor versions change behaviour:"
	@echo "  https://github.com/openclaw/openclaw/releases"
	@echo
	@read -p "Continue? [y/N] " r; [[ "$$r" =~ ^[Yy]$$ ]] || exit 0; \
	npm install -g openclaw@latest && \
	openclaw gateway restart && \
	openclaw security audit

.PHONY: backup
backup: ## Encrypted backup of ~/.openclaw (needs `age`)
	@command -v age >/dev/null || { echo "install age first: brew install age"; exit 1; }
	@test -n "$$AGE_RECIPIENT" || { echo "set AGE_RECIPIENT to your age public key"; exit 1; }
	@mkdir -p "$(BACKUP_DIR)"
	@tar czf - -C "$(HOME)" .openclaw \
	  | age -r "$$AGE_RECIPIENT" > "$(BACKUP_DIR)/openclaw-$(STAMP).tar.gz.age"
	@echo "Wrote $(BACKUP_DIR)/openclaw-$(STAMP).tar.gz.age"
	@echo "Contains live credentials and private transcripts — keep it encrypted at rest."

.PHONY: config-backup
config-backup: ## Timestamped copy of the config file
	@cp "$(CONFIG)" "$(CONFIG).bak.$(STAMP)"
	@echo "Wrote $(CONFIG).bak.$(STAMP)"

# ── Lint ────────────────────────────────────────────────────────────────────

.PHONY: lint
lint: ## Syntax-check the scripts and config in this repo
	@for f in bootstrap.sh cron-jobs.sh verify.sh; do \
	  bash -n "$$f" && echo "ok    $$f"; \
	done
	@python3 -c "import json5, sys; json5.load(open('openclaw.config.json5')); print('ok    openclaw.config.json5')" \
	  2>/dev/null || echo "skip  openclaw.config.json5 (pip install json5 to check)"
