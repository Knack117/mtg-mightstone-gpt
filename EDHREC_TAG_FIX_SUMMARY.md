# EDHREC Tag Extraction Fix - Integration Summary

## ✅ What Was Fixed

EDHREC changed their website structure in 2025, breaking tag extraction. The tags moved from:
- **OLD**: `props.pageProps.commander.metadata.tagCloud`
- **NEW**: `props.pageProps.data.panels.links[]`

## 📝 Changes Made

### Updated File: `utils/edhrec_commander.py`

Added two new helper functions and updated two existing functions:

#### New Helper Functions:
1. **`_extract_tags_from_new_structure()`** - Extracts tag names from the new EDHREC structure
2. **`_extract_tags_with_counts_from_new_structure()`** - Extracts tags with deck counts from new structure

#### Updated Functions:
1. **`extract_commander_tags_from_json()`** - Now tries NEW structure first, falls back to OLD
2. **`extract_commander_tags_with_counts_from_json()`** - Now tries NEW structure first, falls back to OLD

## 🔄 Backward Compatibility

✅ All existing unit tests still pass (9/9 tests)
✅ Old data structure still supported (fallback mechanism)
✅ No breaking changes to function signatures
✅ Existing code continues to work

## 🎯 Test Results

- **Unit Tests**: ✅ 9/9 passed
- **Live Extraction**: ✅ Successfully extracts **168 tags** from EDHREC
- **Integration**: ✅ Works with existing `fetch_commander_summary()` function

## 📖 How to Use

Your existing code doesn't need changes! The functions work the same way:

```python
from utils.edhrec_commander import (
    extract_commander_tags_from_json,
    extract_commander_tags_with_counts_from_json
)

# Extract just tag names
tags = extract_commander_tags_from_json(next_data_json)
# Returns: ['Legends', 'Historic', 'Cascade', ...]

# Extract tags with deck counts
tags_with_counts = extract_commander_tags_with_counts_from_json(next_data_json)
# Returns: [
#   {'tag': 'Legends', 'deck_count': None},
#   {'tag': 'Historic', 'deck_count': None},
#   ...
# ]
```

### Using with Your Service Layer

Your existing `services/edhrec.py` functions like `fetch_commander_summary()` automatically use the updated extraction:

```python
from services.edhrec import fetch_commander_summary

result = fetch_commander_summary("Jodah, the Unifier")
print(f"Found {len(result['tags'])} tags")  # 168 tags!
```

## 🔍 How It Works

The updated functions follow this logic:

1. **Try NEW structure** (current EDHREC format)
   - Navigate to `props.pageProps.data.panels.links`
   - Find sections starting with `header="Tags"`
   - Collect tags from that section and following empty-header sections
   - Stop when hitting a new named section

2. **Fallback to OLD structure** (for backward compatibility)
   - Navigate to `props.pageProps.commander.metadata.tagCloud`
   - Use the original extraction logic

3. **Return normalized results**
   - Deduplicate tags
   - Filter structural/noise tags
   - Preserve order

## 📁 Files Modified

- ✏️ **utils/edhrec_commander.py** - Updated with new extraction logic
- ✅ **tests/test_commander_tags.py** - All tests still passing (no changes needed)

## 🧪 Test Files

- `test_integration.py` - Quick integration test for updated functions
- `test_tag_scraper.py` - Full service-layer test (uses `fetch_commander_summary`)
- `fixed_tag_extractor.py` - Original standalone fix (can be removed if desired)

## ⚡ Quick Test

Run this to verify everything works:

```bash
# Run unit tests
python -m pytest tests/test_commander_tags.py -v

# Test live extraction
python test_integration.py

# Test with full service layer
python test_tag_scraper.py
```

## 🎉 Result

**Before Fix**: 0 tags extracted from live EDHREC  
**After Fix**: 168 tags extracted from live EDHREC ✨

No code changes needed in your application - it just works! 🚀
