#!/bin/bash
#
# FBP Trade Bot - Safe Commit & Push
# Usage: ./commit.sh
#
# Features:
#   - Detects current branch and always pushes the right thing
#   - Pre-flight fetch to warn about remote changes before you commit
#   - Guided conflict resolution (shows affected files, lets you choose)
#   - Never loses local or remote data
#

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Helpers ──────────────────────────────────────────────────────────

info()  { echo -e "${BLUE}$*${NC}"; }
ok()    { echo -e "${GREEN}$*${NC}"; }
warn()  { echo -e "${YELLOW}$*${NC}"; }
err()   { echo -e "${RED}$*${NC}"; }
bold()  { echo -e "${BOLD}$*${NC}"; }

abort_safe() {
    err "❌ $1"
    echo ""
    warn "Your local changes are safe — nothing was lost."
    warn "Run 'git status' to see current state."
    exit 1
}

# ── Pre-flight checks ───────────────────────────────────────────────

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [[ -z "$BRANCH" ]]; then
    abort_safe "Not inside a git repository."
fi

echo -e "${BLUE}📦 FBP Trade Bot - Safe Commit & Push${NC}"
echo "======================================="
echo ""

# Warn if not on main
if [[ "$BRANCH" != "main" ]]; then
    warn "⚠️  You are on branch '${BRANCH}', not 'main'."
    echo ""
    read -p "$(echo -e "${YELLOW}Switch to main before committing? [Y/n]:${NC} ")" -r
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        # Stash any uncommitted work so checkout is safe
        STASHED=false
        if [[ -n $(git status -s) ]]; then
            git stash push -m "commit.sh: auto-stash before branch switch" > /dev/null 2>&1
            STASHED=true
        fi

        if git checkout main -- 2>/dev/null; then
            ok "✅ Switched to main"

            # Fast-forward main to origin/main if behind
            git fetch origin main --quiet 2>/dev/null
            LOCAL=$(git rev-parse main --)
            REMOTE=$(git rev-parse origin/main -- 2>/dev/null || echo "")
            if [[ -n "$REMOTE" && "$LOCAL" != "$REMOTE" ]]; then
                if git merge origin/main --ff-only --quiet 2>/dev/null; then
                    ok "   ↳ Fast-forwarded main to origin/main"
                fi
            fi

            # Merge the old branch's commits onto main
            MERGE_BASE=$(git merge-base main "$BRANCH" -- 2>/dev/null || echo "")
            OLD_TIP=$(git rev-parse "$BRANCH" -- 2>/dev/null || echo "")
            if [[ -n "$MERGE_BASE" && -n "$OLD_TIP" && "$MERGE_BASE" != "$OLD_TIP" ]]; then
                AHEAD=$(git rev-list --count main.."$BRANCH" -- 2>/dev/null || echo 0)
                if [[ "$AHEAD" -gt 0 ]]; then
                    info "   ↳ Merging $AHEAD commit(s) from '$BRANCH' into main..."
                    if git merge "$BRANCH" --ff-only --quiet 2>/dev/null; then
                        ok "   ↳ Fast-forward merge succeeded"
                    elif git merge "$BRANCH" --no-edit --quiet 2>/dev/null; then
                        ok "   ↳ Merge succeeded"
                    else
                        warn "   ↳ Merge had conflicts — continuing on main with your uncommitted changes."
                        git merge --abort 2>/dev/null
                    fi
                fi
            fi

            # Restore stash
            if $STASHED; then
                git stash pop --quiet 2>/dev/null
            fi
        else
            if $STASHED; then
                git stash pop --quiet 2>/dev/null
            fi
            warn "⚠️  Could not switch to main. Continuing on '$BRANCH'."
        fi

        BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    fi
    echo ""
fi

# Check for changes
if [[ -z $(git status -s) ]]; then
    warn "⚠️  No changes to commit."
    exit 0
fi

# ── Show changes ─────────────────────────────────────────────────────

info "📋 Files changed:"
git status -s
echo ""

# ── Pre-flight: check if remote is ahead ────────────────────────────

git fetch origin "$BRANCH" --quiet 2>/dev/null || git fetch origin main --quiet 2>/dev/null || true
REMOTE_REF="origin/$BRANCH"
if ! git rev-parse "$REMOTE_REF" -- > /dev/null 2>&1; then
    REMOTE_REF="origin/main"
fi

BEHIND=$(git rev-list --count HEAD.."$REMOTE_REF" -- 2>/dev/null || echo 0)

if [[ "$BEHIND" -gt 0 ]]; then
    echo ""
    warn "📡 Remote has ${BEHIND} new commit(s) you don't have yet:"
    git --no-pager log --oneline HEAD.."$REMOTE_REF" -- 2>/dev/null | sed 's/^/   /'
    echo ""
    warn "These will be synced automatically after your commit."
    echo ""
fi

# ── Commit ───────────────────────────────────────────────────────────

read -p "$(echo -e "${YELLOW}Commit all these changes? [y/N]:${NC} ")" -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    err "❌ Commit cancelled"
    exit 1
fi

echo ""
info "✍️  Enter commit message:"
read -r commit_message

if [[ -z "$commit_message" ]]; then
    abort_safe "Commit message cannot be empty."
fi

echo ""
ok "📦 Adding all changes..."
git add .

ok "💾 Committing..."
git commit -m "$commit_message" -m "Co-Authored-By: Oz <oz-agent@warp.dev>"
echo ""

# ── Push ─────────────────────────────────────────────────────────────

read -p "$(echo -e "${YELLOW}Push to GitHub? [y/N]:${NC} ")" -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    ok "✅ Committed locally. Push later with: git push origin ${BRANCH}:main"
    exit 0
fi

# Always push current branch to main
PUSH_REFSPEC="${BRANCH}:main"

echo ""
ok "🚀 Pushing ${BRANCH} → origin/main..."

if git push origin "$PUSH_REFSPEC" 2>/dev/null; then
    echo ""
    ok "✅ Done! Changes committed and pushed."
    exit 0
fi

# ── Push failed — sync & retry ───────────────────────────────────────

echo ""
warn "⚠️  Push rejected — remote has commits you don't have."
echo ""

# Fetch and show what's different
git fetch origin main --quiet 2>/dev/null
BEHIND=$(git rev-list --count HEAD..origin/main -- 2>/dev/null || echo "?")
info "   Remote is ${BEHIND} commit(s) ahead:"
git --no-pager log --oneline HEAD..origin/main -- 2>/dev/null | sed 's/^/      /'
echo ""

bold "Options:"
echo -e "  ${GREEN}1)${NC} Rebase your commit on top of remote changes ${CYAN}(recommended)${NC}"
echo -e "  ${GREEN}2)${NC} Merge remote changes into your branch"
echo -e "  ${RED}3)${NC} Skip push — keep commit local for now"
echo ""
read -p "Choose [1/2/3]: " -r sync_choice

# ── Conflict resolution helper ───────────────────────────────────────

resolve_conflicts() {
    local strategy=$1  # "rebase" or "merge"

    echo ""
    err "⚠️  Conflict detected!"
    echo ""
    info "Conflicted files:"
    git diff --name-only --diff-filter=U 2>/dev/null | while read -r f; do
        echo -e "   ${RED}✗${NC} $f"
    done
    echo ""
    bold "Options:"
    echo -e "  ${GREEN}1)${NC} Keep YOUR version of all conflicted files"
    echo -e "  ${GREEN}2)${NC} Keep REMOTE version of all conflicted files"
    echo -e "  ${RED}3)${NC} Abort — go back to pre-push state (safe)"
    echo ""
    read -p "Choose [1/2/3]: " -r conflict_choice

    case "$conflict_choice" in
        1)
            info "Keeping your local versions..."
            git diff --name-only --diff-filter=U 2>/dev/null | while read -r f; do
                git checkout --ours -- "$f" 2>/dev/null
                git add "$f" 2>/dev/null
                echo -e "   ${GREEN}✓${NC} $f (kept yours)"
            done
            if [[ "$strategy" == "rebase" ]]; then
                GIT_EDITOR=true git rebase --continue 2>/dev/null
            else
                git commit --no-edit 2>/dev/null
            fi
            ok "✅ Conflicts resolved (kept your versions)."
            return 0
            ;;
        2)
            info "Keeping remote versions..."
            git diff --name-only --diff-filter=U 2>/dev/null | while read -r f; do
                git checkout --theirs -- "$f" 2>/dev/null
                git add "$f" 2>/dev/null
                echo -e "   ${GREEN}✓${NC} $f (kept remote)"
            done
            if [[ "$strategy" == "rebase" ]]; then
                GIT_EDITOR=true git rebase --continue 2>/dev/null
            else
                git commit --no-edit 2>/dev/null
            fi
            ok "✅ Conflicts resolved (kept remote versions)."
            return 0
            ;;
        *)
            if [[ "$strategy" == "rebase" ]]; then
                git rebase --abort 2>/dev/null
            else
                git merge --abort 2>/dev/null
            fi
            echo ""
            warn "↩️  Aborted. You're back to where you were."
            warn "Your commit is safe locally. Push later when ready."
            return 1
            ;;
    esac
}

# ── Execute chosen sync strategy ─────────────────────────────────────

case "$sync_choice" in
    1)
        echo ""
        info "📥 Rebasing onto origin/main..."
        if git pull --rebase origin main; then
            ok "✅ Rebase succeeded."
        else
            resolve_conflicts "rebase" || exit 0
        fi
        ;;
    2)
        echo ""
        info "📥 Merging origin/main..."
        if git pull --no-rebase origin main; then
            ok "✅ Merge succeeded."
        else
            resolve_conflicts "merge" || exit 0
        fi
        ;;
    *)
        echo ""
        ok "✅ Committed locally. Push later with: git push origin ${PUSH_REFSPEC}"
        exit 0
        ;;
esac

# Retry push after sync
echo ""
ok "🚀 Retrying push..."
if git push origin "$PUSH_REFSPEC"; then
    echo ""
    ok "✅ Done! Changes committed and pushed."
else
    echo ""
    err "❌ Push still failed."
    warn "Your commits are safe locally. Try 'git status' to debug."
    exit 1
fi
