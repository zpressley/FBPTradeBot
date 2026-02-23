# API & Transaction Handling Rules

**CRITICAL: All API endpoints that modify data and commit to git MUST follow these rules**

## Rule 0: All Website Actions MUST Commit to Git

**THE FUNDAMENTAL RULE: If a button on the website doesn't result in a git commit, it does NOTHING useful.**

### Why This Rule Exists

**The website (fbp-hub) is READ-ONLY.** It displays data from the bot repo (fbp-trade-bot) via GitHub Actions that sync files.

**If an API endpoint doesn't commit to git:**
1. Changes are only saved locally on the Render server
2. Next git pull overwrites the local changes → data lost
3. Website never sees the changes (because they're not in git)
4. User clicks button → sees success message → nothing actually happened

### The Workflow That MUST Happen

```
User clicks button on website
        ↓
Website calls API endpoint in fbp-trade-bot
        ↓
API modifies data files locally
        ↓
API commits and pushes to GitHub (fbp-trade-bot repo)
        ↓
GitHub Action triggers in fbp-trade-bot
        ↓
GitHub Action syncs changed files to fbp-hub repo
        ↓
Website shows updated data on next page load
```

**IF THE GIT COMMIT IS MISSING:**
```
User clicks button on website
        ↓
Website calls API endpoint in fbp-trade-bot
        ↓
API modifies data files locally
        ↓
❌ NO GIT COMMIT
        ↓
User sees success message
        ↓
Website still shows old data (git has no changes)
        ↓
Next deployment pulls from git → local changes overwritten
        ↓
Change is completely lost
```

### Endpoints That MUST Commit to Git

**ANY endpoint called by a website button:**
- ✅ Player management (add, edit, delete)
- ✅ Trade submissions and approvals
- ✅ Buy-in purchases and refunds
- ✅ Draft picks
- ✅ Roster changes
- ✅ Contract updates
- ✅ WizBucks transactions
- ✅ KAP/PAD submissions

**The ONLY exceptions:**
- 🔍 Read-only endpoints (GET requests for displaying data)
- 📊 Status checks and health endpoints

### How to Implement

**Every data-modifying endpoint MUST include:**

```python
@router.post("/api/something")
async def modify_data(request: Request):
    # 1. Validate request
    # 2. Load data
    # 3. Make changes
    # 4. Save to files
    
    # 5. CRITICAL: Commit and push to git
    try:
        git_commit_and_push(
            ["data/file1.json", "data/file2.json"],
            "Descriptive commit message"
        )
        print("✅ Changes committed to git")
    except Exception as e:
        # Rollback changes if git fails
        print(f"❌ Git failed, rolling back: {e}")
        restore_original_data()
        raise HTTPException(
            status_code=500,
            detail="Changes NOT saved - git commit failed. Please try again."
        )
    
    # 6. Return success ONLY if git succeeded
    return {"success": True, "message": "Changes saved"}
```

### Testing Checklist

**Before deploying ANY new endpoint:**

- [ ] Does the endpoint modify data files?
- [ ] If yes, does it call `git_commit_and_push()`?
- [ ] Does it handle git failures with rollback?
- [ ] Does it return error to user if git fails?
- [ ] Have you tested what happens if git push fails?
- [ ] Does the website show updated data after the action?

**If any answer is NO → The endpoint is BROKEN and will lose data**

---

## Rule 1: Transactional Semantics

**ALL data-modifying API endpoints MUST use transaction-like semantics:**

```python
# ✅ CORRECT PATTERN
@router.post("/api/something")
async def modify_data(request: Request):
    # 1. Load original data for rollback
    original_data = load_json(DATA_FILE)
    
    try:
        # 2. Make changes to data
        modified_data = make_changes(original_data)
        
        # 3. Save to disk
        save_json(DATA_FILE, modified_data)
        print("💾 Saved changes")
        
        # 4. CRITICAL: Commit and push (this can fail!)
        try:
            git_commit_and_push([DATA_FILE], "Commit message")
            print("✅ Git committed and pushed")
        except Exception as git_error:
            # 5. ROLLBACK on git failure
            print(f"❌ Git failed: {git_error}")
            print("🔄 Rolling back...")
            save_json(DATA_FILE, original_data)
            print("✅ Rollback complete")
            
            # 6. Return clear error to user
            raise HTTPException(
                status_code=500,
                detail=f"Transaction failed and rolled back. {git_error}. Changes were NOT saved. Please try again."
            )
        
        # 7. Return success ONLY if git succeeded
        return {
            "success": True,
            "message": "Changes committed to git successfully"
        }
        
    except HTTPException:
        raise  # Re-raise formatted errors
    except Exception as e:
        # Rollback on unexpected errors
        save_json(DATA_FILE, original_data)
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {e}. Changes rolled back."
        )
```

**❌ NEVER DO THIS:**
```python
# This will lose data if git fails!
save_json(DATA_FILE, modified_data)
git_commit_and_push([DATA_FILE], "Message")  # If this fails, data is lost on next pull!
return {"success": True}  # User thinks it worked!
```

---

## Rule 2: Comprehensive Logging

**ALL operations MUST log to console with emoji indicators:**

```python
print("🔄 Starting operation X")
print("  📝 Generated ID: 123")
print("  💾 Saved to file.json")
print("  ✅ Git committed and pushed")
print("  📢 Discord notification sent")
print("✅ Operation complete")
```

**Error logging:**
```python
print("❌ Git operation failed: {error}")
print("🔄 Rolling back...")
print("⚠️ Warning: non-critical failure")
```

**Emoji Reference:**
- `🔄` - Starting operation
- `📝` - Data generation/assignment
- `💾` - File save
- `✅` - Success
- `❌` - Critical failure
- `⚠️` - Warning/non-critical
- `📢` - Notification sent
- `🔄` - Rollback

---

## Rule 3: Git Operations MUST Raise Exceptions

**The `git_commit_and_push()` function MUST:**
1. Capture stderr output
2. Raise exceptions on failure (not just log)
3. Provide clear error messages

```python
def git_commit_and_push(files, message):
    """
    CRITICAL: Raises exception on failure - caller MUST handle rollback!
    """
    try:
        result = subprocess.run(
            ["git", "add", *files], 
            check=True, 
            capture_output=True, 
            text=True
        )
        
        result = subprocess.run(
            ["git", "commit", "-m", message], 
            check=True, 
            capture_output=True, 
            text=True
        )
        
        result = subprocess.run(
            ["git", "push"], 
            check=True, 
            capture_output=True, 
            text=True
        )
        
        print(f"✅ Git push: {message}")
        
    except subprocess.CalledProcessError as e:
        error_msg = f"Git failed: {e.stderr if e.stderr else str(e)}"
        print(f"❌ {error_msg}")
        raise RuntimeError(error_msg) from e  # ✅ MUST RAISE
```

**❌ NEVER silently catch and log:**
```python
except Exception as e:
    print(f"Git failed: {e}")  # ❌ Silent failure!
    # No raise = caller thinks it succeeded!
```

---

## Rule 4: Clear User-Facing Error Messages

**API responses MUST tell the user exactly what happened:**

**✅ Success:**
```json
{
  "success": true,
  "upid": 8436,
  "message": "Player Tatsuya Imai successfully added with UPID 8436 and committed to git"
}
```

**✅ Failure:**
```json
{
  "detail": "Transaction failed and rolled back. Git commit/push failed: network timeout. Player was NOT added. Please try again or contact admin."
}
```

**❌ Bad error messages:**
```json
{
  "success": true  // ❌ Lying to user!
}
```
```json
{
  "detail": "Error"  // ❌ No context!
}
```

---

## Rule 5: Discord Notifications MUST Show Git Status

**All Discord notifications MUST indicate git commit status:**

```python
discord_msg = f"""➕ **New Player Added**

👤 Player: **{player_name}**
⚾ Team: {team}
🆔 UPID: {next_upid}
👤 Admin: {admin}
✅ Status: COMMITTED TO GIT  # ✅ CRITICAL!
💾 Source: Website Admin Portal
"""
```

**If git failed, don't send notification** (because we rolled back)

---

## Rule 6: Render Server Logs Must Be Visible

**All operations MUST be visible in Render logs:**

1. **Use print statements** (they appear in Render console)
2. **Include timestamps** for debugging
3. **Log each step** of the operation
4. **Log success AND failure**

**Example Render log output:**
```
🔄 Starting add-player transaction for Tatsuya Imai by WAR
  📝 Assigned UPID: 8436
  💾 Saved to combined_players.json
  💾 Saved to upid_database.json
  💾 Saved to player_log.json
  ✅ Git committed and pushed
  ✅ Synced to website
  📢 Discord notification sent
✅ Transaction complete: Player Tatsuya Imai added successfully with UPID 8436
```

**OR on failure:**
```
🔄 Starting add-player transaction for Tatsuya Imai by WAR
  📝 Assigned UPID: 8436
  💾 Saved to combined_players.json
  💾 Saved to upid_database.json
  💾 Saved to player_log.json
  ❌ Git commit/push failed: network timeout
  🔄 Rolling back all changes...
  ✅ Rollback complete
```

---

## Rule 7: Test Failure Paths

**Before deploying ANY data-modifying endpoint:**

1. Test what happens if git commit fails
2. Test what happens if git push fails  
3. Test what happens if network drops
4. Verify rollback works
5. Verify user gets clear error message
6. Verify no data is left in inconsistent state

---

## Endpoints That MUST Follow These Rules

**Current endpoints:**
- ✅ `/api/admin/add-player` - Fixed with transactional semantics
- ⚠️ `/api/admin/bulk-graduate` - Needs review
- ⚠️ `/api/admin/bulk-update-contracts` - Needs review
- ⚠️ `/api/admin/bulk-release` - Needs review
- ✅ `/api/buyin/purchase` - Already correct (uses wallet, not git)
- ✅ `/api/buyin/refund` - Already correct (uses wallet, not git)

**Trade endpoints:**
- Review all trade processing endpoints
- Review PAD/KAP submission endpoints
- Review any endpoint that calls `git_commit_and_push()`

---

## Why This Matters

**Without these rules:**
- User adds player → API returns success → git push fails silently
- Local files have the change → next git pull overwrites them → data lost
- User thinks player was added → player doesn't exist → confusion
- No log entries → impossible to debug

**With these rules:**
- User adds player → git push fails → immediate rollback
- User gets clear error: "Transaction failed, player NOT added, try again"
- Server logs show exact failure point
- No data is left in inconsistent state
- Easy to debug and fix

---

## Rule 8: NEVER Use Backup Files Without Explicit Permission

**CRITICAL: Using backup/archived data files can cause catastrophic data loss**

### The Problem

**Feb 20, 2026 incident:** A script to add ADP rankings loaded a backup copy of `combined_players.json` from Feb 2, added rankings, and committed it. This overwrote 2+ weeks of changes:
- 13+ player updates lost (Feb 15-17)
- Multiple player log entries wiped out
- Admin changes disappeared
- No way to recover without manual git cherry-picking

**Root cause:** The backup file was from BEFORE recent changes, so committing it **rewound the database** to an earlier state.

---

### The Rule

**NEVER load data from:**
- ❌ Backup files (`.backup`, `_backup`, `_old`, etc.)
- ❌ Archived files (`/archive/`, `/historical/`, `/backups/`)
- ❌ Timestamped copies (`file_20260202.json`)
- ❌ Any file that is not the CURRENT production file

**ALWAYS load data from:**
- ✅ Current production files in `data/` directory
- ✅ `data/combined_players.json` (not `combined_players_backup.json`)
- ✅ Files tracked in git's main branch

---

### Before Using ANY Data File

**Ask these questions:**
1. Is this the CURRENT production file?
2. When was this file last modified?
3. Is this file tracked in the main git branch?
4. Does this filename contain "backup", "old", "archive", or a date?

**If ANY answer is wrong → DO NOT USE THE FILE**

---

### When Backups ARE Allowed

**Backups may ONLY be used when:**
1. **Explicit written permission** from admin/owner
2. **Data recovery scenario** with documented plan
3. **Testing/development** in isolated environment (not production)
4. **Creating new backups** (saving current state for safety)

**Example of CORRECT backup usage:**
```python
# ✅ Creating a backup for safety
import shutil
shutil.copy('data/combined_players.json', 'data/combined_players.backup.json')
print("✅ Backup created for safety")

# ✅ Loading from CURRENT file
players = load_json('data/combined_players.json')  # Current production file
```

**Example of INCORRECT backup usage:**
```python
# ❌ NEVER DO THIS!
players = load_json('data/combined_players.backup.json')  # OLD DATA!
modify_players(players)
save_json('data/combined_players.json', players)  # OVERWRITES CURRENT WITH OLD!
```

---

### Scripts That Modify Data Files

**ALL scripts that modify data files MUST:**

1. **Start with current production files**
```python
# ✅ CORRECT
players = load_json('data/combined_players.json')

# ❌ WRONG
players = load_json('data/backup/combined_players_20260202.json')
```

2. **Verify file freshness**
```python
import os
from datetime import datetime, timedelta

file_path = 'data/combined_players.json'
modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
age = datetime.now() - modified_time

if age > timedelta(days=7):
    print(f"⚠️ WARNING: File is {age.days} days old!")
    print("⚠️ This may not be the current production file.")
    # Decide whether to proceed or abort
```

3. **Log which file is being used**
```python
print(f"📂 Loading data from: {file_path}")
print(f"📅 File last modified: {modified_time}")
players = load_json(file_path)
print(f"📊 Loaded {len(players)} players")
```

4. **Check git status before committing**
```python
import subprocess

# Check if file has uncommitted changes
result = subprocess.run(
    ['git', 'status', '--porcelain', 'data/combined_players.json'],
    capture_output=True,
    text=True
)

if result.stdout.strip():
    print("⚠️ File has uncommitted changes - review before proceeding")
```

---

### Preventing Future Incidents

**Code review checklist for data-modifying scripts:**
- [ ] Does the script load from current production files?
- [ ] Does the script check file modification dates?
- [ ] Does the script log which files it's using?
- [ ] Does the script verify it's not loading a backup?
- [ ] Are all file paths explicitly the production paths?
- [ ] Is there any string matching "backup", "old", "archive" in paths?

**If ANY item fails → Script must be revised before deployment**

---

### Emergency Data Recovery

**If you discover data was lost due to backup file usage:**

1. **STOP immediately** - Do not make any more commits
2. **Identify the bad commit** - Find the commit that used backup data
3. **Document the scope** - Which data was overwritten?
4. **Recovery options:**
   - Git revert the bad commit
   - Cherry-pick individual good commits
   - Manual recovery from git history
5. **Get explicit approval** before any recovery action
6. **Test in separate branch** before applying to main

---

## Related Documents

- `CRITICAL_RULES.md` - Wallet and balance handling rules
- `docs/FILE_STRUCTURE_GUIDE.md` - Data file documentation
- `docs/WIZBUCKS_WALLET_SYSTEM.md` - WizBucks transaction handling
