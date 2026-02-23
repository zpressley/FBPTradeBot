# API Git Commit Audit

**Date:** 2026-02-23  
**Rule:** All website actions MUST commit to git (Rule 0 in API_TRANSACTION_RULES.md)

## Summary

| Endpoint | Modifies Data? | Commits to Git? | Status | Notes |
|----------|----------------|-----------------|--------|-------|
| **api_admin_bulk.py** |
| POST /api/admin/bulk-graduate | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 226 |
| POST /api/admin/bulk-update-contracts | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 277 |
| POST /api/admin/bulk-release | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 336 |
| POST /api/admin/add-player | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 456, transactional |
| POST /api/admin/enrich-player | ✅ Yes | ❓ Unknown | ⚠️ NEEDS REVIEW | Need to check implementation |
| **api_buyin.py** |
| POST /api/buyin/purchase | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 270, added 2026-02-23 |
| POST /api/buyin/refund | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 386, added 2026-02-23 |
| **api_draft_pick_request.py** |
| POST /api/draft/prospect/pick-request | ❌ No | ❌ No | ✅ COMPLIANT | Only shows Discord confirmation |
| POST /api/draft/prospect/validate-pick | ❌ No | ❌ No | ✅ COMPLIANT | Read-only validation |
| POST /api/draft/prospect/pick-confirm | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Delegates to DraftManager.make_pick() |
| **api_settings.py** |
| POST /api/settings/team-colors | ✅ Yes | ⚠️ Best-effort | ⚠️ NEEDS FIX | Line 121, silent failure |
| **api_trade.py** |
| POST /api/trade/submit | ✅ Yes | ⚠️ Via callback | ⚠️ NEEDS REVIEW | Uses `_commit_fn` callback |
| POST /api/trade/accept | ✅ Yes | ⚠️ Via callback | ⚠️ NEEDS REVIEW | Uses `_commit_fn` callback |
| POST /api/trade/reject | ✅ Yes | ⚠️ Via callback | ⚠️ NEEDS REVIEW | Uses `_commit_fn` callback |
| POST /api/trade/withdraw | ✅ Yes | ⚠️ Via callback | ⚠️ NEEDS REVIEW | Uses `_commit_fn` callback |
| **draft/draft_manager.py** |
| make_pick() | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 687, async commit |
| undo_last_pick() | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 743, async commit |
| start_draft() | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 784, async commit |
| pause_draft() | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 800, async commit |
| resume_draft() | ✅ Yes | ✅ Yes | ✅ COMPLIANT | Line 816, async commit |

---

## ⚠️ Issues Found

### 1. api_settings.py - Team Colors (CRITICAL)

**File:** `api_settings.py`  
**Endpoint:** `POST /api/settings/team-colors`  
**Lines:** 97-125

**Problem:**
```python
# Best-effort persistence back to GitHub so fbp-hub can sync it.
try:
    if _commit_fn is not None:
        _commit_fn([TEAM_COLORS_PATH], f"Team colors: {manager_team}")
except Exception as exc:
    print("⚠️ Team colors git commit/push failed:", exc)
```

**Issue:** 
- Commits via callback function that may not be set
- Catches all exceptions and silently continues
- Returns success to user even if git commit fails
- No rollback on git failure

**Impact:** 
- User changes team colors → sees success
- Git commit fails silently → changes not in git
- Website never shows updated colors
- Next deploy overwrites local changes → data lost

**Fix Required:**
```python
# Save file
_save_json(TEAM_COLORS_PATH, data)

# CRITICAL: Commit to git (not optional!)
try:
    if _commit_fn is None:
        raise HTTPException(
            status_code=500,
            detail="Git commit system not initialized. Changes NOT saved."
        )
    _commit_fn([TEAM_COLORS_PATH], f"Team colors: {manager_team}")
except Exception as exc:
    # Rollback on failure
    original_data = _load_json(TEAM_COLORS_PATH, {})
    _save_json(TEAM_COLORS_PATH, original_data)
    print(f"❌ Git commit failed, rolled back: {exc}")
    raise HTTPException(
        status_code=500,
        detail=f"Failed to save team colors: {exc}. Changes NOT saved."
    )

return {"success": True, "team": manager_team, "colors": colors}
```

---

### 2. api_trade.py - Trade Operations (NEEDS REVIEW)

**File:** `api_trade.py`  
**Endpoints:** All POST endpoints  
**Issue:** Uses callback pattern with `trade_store._maybe_commit()`

**Current Implementation:**
```python
def _maybe_commit(message: str, file_paths: Optional[list[str]] = None) -> None:
    global _warned_commit_fn_missing
    if _commit_fn is None:
        # High-signal: if commit wiring is missing, trades will apply locally
        # but never push to GitHub. Only log once per process.
        if not _warned_commit_fn_missing:
            _warned_commit_fn_missing = True
            try:
                print(
                    "⚠️ TRADE_COMMIT_FN_NOT_SET",
                    {"message_head": (message or "").splitlines()[0] if message else ""},
                )
            except Exception:
                pass
        return
    try:
        paths = file_paths or ["data/trades.json"]
        _commit_fn(paths, message)
    except Exception as exc:
        print(f"⚠️ Trade commit/push skipped: {exc}")
```

**Issues:**
1. Silently continues if `_commit_fn` is not set
2. Catches git exceptions and continues
3. No rollback on failure
4. User sees success even if git fails

**Recommendation:**
- Audit `trade_store.py` to add transactional semantics
- Raise exceptions on git failures
- Add rollback logic for failed commits
- Ensure user gets error if git fails

---

### 3. api_admin_bulk.py - Enrich Player (UNKNOWN)

**File:** `api_admin_bulk.py`  
**Endpoint:** `POST /api/admin/enrich-player`  
**Line:** 530

**Status:** Need to review implementation to verify git commit

---

## ✅ Compliant Endpoints

### api_admin_bulk.py

All bulk operations correctly commit:
- `bulk_graduate()` - Line 226: `git_commit_and_push([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)`
- `bulk_update_contracts()` - Line 277: `git_commit_and_push([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)`
- `bulk_release()` - Line 336: `git_commit_and_push([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)`
- `add_player()` - Line 456: Transactional with rollback on git failure ✅

### api_buyin.py

Both endpoints correctly commit (added 2026-02-23):
- `purchase_buyin()` - Line 270: `git_commit_and_push([...], commit_msg)` with exception handling
- `refund_buyin()` - Line 386: `git_commit_and_push([...], commit_msg)` with exception handling

**Note:** Raises exceptions on git failure, which is correct behavior

### api_draft_pick_request.py

All endpoints are compliant:
- `pick-request` - Read-only (just shows Discord confirmation)
- `validate-pick` - Read-only (validation only)
- `pick-confirm` - Delegates to `DraftManager.make_pick()` which commits

### draft/draft_manager.py

All draft operations commit asynchronously:
- `make_pick()` - Line 687: `_commit_draft_files_async(unique, msg)`
- `undo_last_pick()` - Line 743: `_commit_draft_files_async(unique, "Draft undo")`
- `start_draft()` - Line 784: `_commit_draft_files_async([self.state_file], ...)`
- `pause_draft()` - Line 800: `_commit_draft_files_async([self.state_file], ...)`
- `resume_draft()` - Line 816: `_commit_draft_files_async([self.state_file], ...)`

**Note:** Draft manager uses async commit pattern, warnings on failure but doesn't fail the operation

---

## Recommendations

### Priority 1 - Critical (Fix Immediately)

1. **Fix `api_settings.py` team colors endpoint**
   - Add proper error handling
   - Raise exception if git fails
   - Add rollback logic
   - Don't return success if git fails

### Priority 2 - High (Review & Fix)

2. **Audit `trade_store.py` and `api_trade.py`**
   - Review all trade operations
   - Add transactional semantics
   - Ensure git failures raise exceptions
   - Add rollback logic

3. **Review `api_admin_bulk.py` enrich-player endpoint**
   - Verify it commits to git
   - Add to audit results

### Priority 3 - Medium (Improve)

4. **Improve draft manager error handling**
   - Consider failing the operation if git commit fails
   - Currently just logs warning and continues

---

## Testing Checklist

For each endpoint that modifies data:

- [ ] Does it save changes to local files?
- [ ] Does it call `git_commit_and_push()` (or equivalent)?
- [ ] Does it handle git failures by raising exceptions?
- [ ] Does it roll back local changes if git fails?
- [ ] Does it return error to user if git fails?
- [ ] Have you tested what happens when git push fails?
- [ ] Does the website show updated data after the action?

---

## See Also

- `docs/API_TRANSACTION_RULES.md` - Rule 0: All Website Actions MUST Commit to Git
- `docs/CRITICAL_RULES.md` - Wallet and balance handling
