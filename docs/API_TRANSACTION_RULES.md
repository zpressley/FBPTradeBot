# API & Transaction Handling Rules

**CRITICAL: All API endpoints that modify data and commit to git MUST follow these rules**

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

## Related Documents

- `CRITICAL_RULES.md` - Wallet and balance handling rules
- `docs/FILE_STRUCTURE_GUIDE.md` - Data file documentation
- `docs/WIZBUCKS_WALLET_SYSTEM.md` - WizBucks transaction handling
