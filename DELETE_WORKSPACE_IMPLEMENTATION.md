# Delete Workspace - Implementation Plan

## Problem
Delete workspace only removes draft from save/ but leaves exported files in <org>_workspace/, causing workspace to reappear on reload.

## Solution
Comprehensive delete that removes workspace from ALL locations and uses trash folder.

## Implementation

### Files to Remove
1. **Save entries**:
   - `<org>/save/working.parquet` (row for this workspace)
   - `<org>/save/history.parquet` (all versions)
   - Nested: `<org>/**/save/working.parquet` and `history.parquet`
   - Pooled: `<project>/save/working.parquet` and `history.parquet`

2. **Exported files** (exact stem match):
   - `<org>_workspace/<slug>.ttl`
   - `<org>_workspace/<slug>.rdf`
   - `<org>_workspace/<slug>.jsonld`
   - `<org>_workspace/<slug>.yaml`
   - `<org>_workspace/<slug>.shacl.ttl`
   - `<org>/<slug>.ttl`, `.rdf`, `.jsonld`, `.yaml`, `.shacl.ttl`
   - `<org>/<slug>/` folder if exists

3. **Never delete**:
   - Files outside org folder
   - `<org>_consolidate/`
   - Similar names (chm-2 when deleting chm)
   - Save folder itself

### Trash Folder Structure
```
<org>/save/trash/<yyyymmdd-hhmmss>_<slug>/
├── save_data.json          # Parquet rows as JSON
├── <slug>.ttl             # Exported files moved here
├── <slug>.rdf
├── <slug>.jsonld
├── <slug>.yaml
├── <slug>.shacl.ttl
└── manifest.json          # List of what was moved
```

### Algorithm
1. Find all files/entries for workspace
2. Show confirmation with complete list
3. Create trash folder
4. Move files to trash (not delete)
5. Remove parquet entries
6. Verify workspace not discoverable
7. Rebuild consolidate in background
8. Return manifest of moved items

### New Function
```python
def comprehensive_delete_workspace(
    pr: Project,
    org: str,
    slug: str
) -> dict:
    \"\"\"Delete workspace from ALL locations, move to trash.\"\"\"
    ...
```

## Testing
1. Exported workspace → 5 files in trash
2. Draft-only → draft in trash
3. Nested save → removed
4. Similar name (chm vs chm-2) → untouched
5. Consolidate updated
6. Workspace not listed after restart
7. Files can be restored manually

## Files to Modify
1. src/ontomonster/app.py - comprehensive delete
2. src/ontomonster/parquet_store.py - delete from specific parquet
3. CHANGELOG.md - document fix
