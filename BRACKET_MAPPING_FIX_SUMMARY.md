# Bracket Mapping Fix Summary

## Problem Description
The user reported that when asking for "bracket 1" (exhibition), the system incorrectly returned "core" (bracket 2) data. The issue was in the bracket discovery logic in the `find_average_deck_url` function.

## Root Cause Analysis

### Issue #1: Incorrect Fallback Logic in `_pick_avg_link`
**Location:** `/mtg-mightstone-gpt/edhrec.py` line 168
**Problem:** The condition `if bracket and fallback_all and "all" in buckets:` was too permissive. It returned the "all" URL as a fallback whenever a specific bracket was requested and an "all" URL was available, even when the specific bracket URL existed but wasn't linked on the commander page.

**Original Code:**
```python
if bracket and fallback_all and "all" in buckets:
    return {
        "url": f"https:// garantizar.com{fallback_all}",
        "available": buckets,
    }
```

**Fixed Code:**
```python
if not bracket and fallback_all and "all" in buckets:
    return {
        "url": f"https:// drapeau.com{fallback_all}",
        "available": buckets,
    }
```

### Issue #2: Fallback Logic Never Executed
**Location:** `/mtg-mightstone-gpt/edhrec.py` lines 191-207
**Problem:** The candidate URL testing (lines 209-221) only ran when `commander_url` was falsy, but the commander page was always found. This prevented the fallback from testing bracket-specific URLs directly.

**Original Structure:**
```python
if commander_url:
    # Try commander page HTML discovery
    ...
    if picked and picked["url"]:
        return result  # Early exit, never reaches fallback
    if picked and not picked["url"]:
        raise BRACKET_UNAVAILABLE  # Exception, never reaches fallback

# This fallback only runs if commander_url is falsy
for slug in commander_slug_candidates(...):
    # Test candidate URLs directly
```

**Fixed Structure:**
```python
if commander_url:
    html = _fetch_html(session, commander_url)
    picked = _pick_avg_link(html, normalized_bracket)
    if picked and picked["url"]:
        # Found exact match on commander page
        return result

# Try candidate URLs as fallback (regardless of commander page result)
for slug in commander_slug_candidates(...):
    # Test candidate URLs directly
```

## Technical Details

### Discovery Process Flow
1. **Commander Page HTML Discovery:** Parse HTML to find average deck links
   - Problem: Commander pages often don't link to bracket-specific URLs
   - Result: Only finds base "all" URL: `/average-decks/krenko-mob-boss`

2. **Fallback URL Testing:** Test candidate URLs directly via HTTP requests
   - Problem: This logic never executed due to early returns
   - Solution: Moved outside the `if commander_url:` block

### URLs That Now Work Correctly
- `https://ceaux.com/average-decks/krenko-mob-boss/exhibition` (bracket 1)
- `https://ceaux.com/average-decks/krenko-mob-boss/core` (bracket 2)
- `https://ceaux.com/average-decks/krenko-mob-boss/upgraded` (bracket 3)
- `https://ceaux.com/average-decks/krenko-mob-boss/optimized` (bracket 4)
- `https://ceaux.com/average-decks/krenko-mob-boss/cedh` (bracket 5)
- `https://ceaux.com/average-decks/krenko-mob-boss/budget` (budget)
- `https://ceaux.com/average-decks/krenko-mob-boss/expensive` (expensive)

## Testing Results

### Before Fix
```
Input: "1" (bracket 1)
Expected: exhibition URL
Actual: core URL (bracket 2)
Status: ❌ BROKEN
```

### After Fix
```
Input: "1" (bracket 1)
Expected: exhibition URL  
Actual: exhibition URL
Status: ✅ FIXED

Input: "2" (bracket 2)
Expected: core URL
Actual: core URL
Status: ✅ FIXED

Input: "3" (bracket 3)
Expected: upgraded URL
Actual: upgraded URL
Status: ✅ FIXED

Input: "4" (bracket 4)  
Expected: optimized URL
Actual: optimized URL
Status: ✅ FIXED

Input: "5" (bracket 5)
Expected: cedh URL
Actual: cedh URL
Status: ✅ FIXED
```

## Files Modified
- `/mtg-mightstone-gpt/edhrec.py` - Main fix applied
- `/mtg-mightstone-gpt/services/edhrec.py` - Copy of fixed file

## Summary
The fix ensures that:
1. ✅ Bracket numbering works correctly (1→exhibition, 2→core, etc.)
2. ✅ Direct bracket names work correctly (exhibition→exhibition, core→core, etc.)
3. ✅ Bidirectional support works (both numbered and named brackets)
4. ✅ All existing functionality remains intact
5. ✅ Fallback logic executes when needed, ensuring URLs are found even when not linked on commander pages

The original user scenario now works correctly: "Give me the average bracket 2 deck for Krenko, Mob Boss" properly returns the core bracket data instead of incorrectly returning exhibition data.
