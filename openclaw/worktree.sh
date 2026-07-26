#!/usr/bin/env bash
# Spawn an OpenCode ACP session inside a throwaway git worktree.
#
#   bash openclaw/worktree.sh <repo-path> <task-slug> ["task description"]
#
# Example:
#   bash openclaw/worktree.sh ~/code/India-voice-ai fix-tts-latency \
#     "Diagnose the 400ms TTS gap in tts_coalesce.py and fix it. Run pytest."
#
# WHY THIS EXISTS
#
# ACP harnesses run OUTSIDE OpenClaw's sandbox. OpenCode executes on your Mac
# with its own CLI permissions — the `sandbox` config that contains the `dev`
# agent does nothing here. The only real boundary is the working directory.
#
# So: every session gets its own worktree on its own branch. The agent can do
# whatever it likes in there. Worst case you delete a directory and a branch.
# Nothing it does touches your working copy, your staged changes, or main.
#
# This is what makes permissionMode "approve-all" a defensible setting rather
# than a reckless one. Point a session at a live working copy instead and that
# reasoning evaporates.
#
# BUT BE CLEAR ABOUT WHAT THIS IS NOT. A worktree bounds the EXPECTED blast
# radius; it is not a security boundary. It shares the repo's git common
# directory, and a harness with shell access can simply `cd` elsewhere. If you
# need real isolation — untrusted code, or an agent you don't want near your
# SSH keys — run the harness as a dedicated OS user. See docs/coding-agents.md.

set -euo pipefail

REPO="${1:-}"
SLUG="${2:-}"
TASK="${3:-}"

WORKTREE_ROOT="${WORKTREE_ROOT:-$HOME/code/worktrees}"
BASE_BRANCH="${BASE_BRANCH:-}"

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '  %s\n' "$*"; }

if [[ -z "$REPO" || -z "$SLUG" ]]; then
  cat >&2 <<'EOF'
usage: worktree.sh <repo-path> <task-slug> ["task description"]

  repo-path   existing git repo to branch from
  task-slug   short kebab-case name; becomes the branch and directory name
  task        optional; if given, the OpenCode session starts with this prompt

env:
  WORKTREE_ROOT  where worktrees live (default: ~/code/worktrees)
  BASE_BRANCH    branch to fork from (default: the repo's current HEAD branch)
EOF
  exit 1
fi

[[ "$SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]] \
  || die "task-slug must be kebab-case: [a-z0-9-], starting alphanumeric"

REPO="$(cd "$REPO" 2>/dev/null && pwd)" || die "no such directory: ${1}"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: ${REPO}"

REPO_NAME="$(basename "$REPO")"
BRANCH="agent/${SLUG}"
WT_PATH="${WORKTREE_ROOT}/${REPO_NAME}/${SLUG}"

if [[ -z "$BASE_BRANCH" ]]; then
  BASE_BRANCH="$(git -C "$REPO" symbolic-ref --quiet --short HEAD 2>/dev/null || echo main)"
fi

echo
echo "  repo    ${REPO}"
echo "  base    ${BASE_BRANCH}"
echo "  branch  ${BRANCH}"
echo "  path    ${WT_PATH}"
echo

if [[ -e "$WT_PATH" ]]; then
  die "worktree already exists at ${WT_PATH}
       resume it:  /acp spawn opencode --cwd ${WT_PATH} --mode persistent
       or remove:  git -C ${REPO} worktree remove ${WT_PATH}"
fi

if git -C "$REPO" show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  die "branch ${BRANCH} already exists — pick another slug, or delete it:
       git -C ${REPO} branch -D ${BRANCH}"
fi

# Fetch so we branch from something current, but don't fail offline.
info "fetching ${BASE_BRANCH}..."
git -C "$REPO" fetch origin "$BASE_BRANCH" --quiet 2>/dev/null \
  || info "(fetch failed — branching from local ${BASE_BRANCH})"

START_POINT="$BASE_BRANCH"
git -C "$REPO" rev-parse --verify --quiet "origin/${BASE_BRANCH}" >/dev/null \
  && START_POINT="origin/${BASE_BRANCH}"

mkdir -p "$(dirname "$WT_PATH")"
info "creating worktree from ${START_POINT}..."
git -C "$REPO" worktree add -b "$BRANCH" "$WT_PATH" "$START_POINT" --quiet

# Secrets are NOT copied by default.
#
# A fresh worktree has no .env, so tests needing credentials fail confusingly —
# but copying real API keys into a directory you just handed an agent write
# access to hands those keys to anything that compromises the session. Opt in
# per invocation, and prefer a scoped test-only .env over your live one.
if [[ "${COPY_ENV:-0}" == "1" ]]; then
  for f in .env .env.local; do
    if [[ -f "${REPO}/${f}" ]]; then
      cp "${REPO}/${f}" "${WT_PATH}/${f}"
      info "copied ${f}  (COPY_ENV=1)"
    fi
  done
  echo
  info "WARNING: real credentials are now inside the agent's writable worktree"
else
  if [[ -f "${REPO}/.env" ]]; then
    info "note: .env NOT copied. Tests needing credentials will fail."
    info "      re-run with COPY_ENV=1, or better, drop a scoped test-only"
    info "      .env into ${WT_PATH} by hand"
  fi
fi

# A venv is per-worktree; symlinking one across worktrees causes odd failures.
if [[ -d "${REPO}/.venv" ]]; then
  info "note: .venv not copied — recreate inside the worktree if tests need it"
fi

echo
info "worktree ready"
echo

if command -v openclaw >/dev/null 2>&1; then
  if [[ -n "$TASK" ]]; then
    info "spawning OpenCode session..."
    openclaw acp spawn opencode \
      --cwd "$WT_PATH" \
      --mode persistent \
      --label "${REPO_NAME}/${SLUG}" \
      --message "$TASK"
  else
    cat <<EOF
  Start a session against it:

    /acp spawn opencode --cwd ${WT_PATH} --mode persistent --label ${REPO_NAME}/${SLUG}

  or from the shell:

    openclaw acp spawn opencode --cwd ${WT_PATH} --mode persistent

EOF
  fi
else
  info "openclaw not on PATH — worktree created, spawn it manually"
fi

cat <<EOF

  Review when it's done:

    git -C ${WT_PATH} diff ${BASE_BRANCH}...${BRANCH}
    git -C ${WT_PATH} log --oneline ${BASE_BRANCH}..${BRANCH}

  Keep it:

    git -C ${REPO} merge ${BRANCH}          # or open a PR from the branch

  Throw it away:

    git -C ${REPO} worktree remove --force ${WT_PATH}
    git -C ${REPO} branch -D ${BRANCH}

EOF
