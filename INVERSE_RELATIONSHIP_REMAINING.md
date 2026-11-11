# Inverse Relationship Support - Remaining Work

## Completed (Part 1)
✅ Loader stores inv as full IRIs (not curies)
✅ Helper functions for IRI/curie resolution
✅ Read-only chips in predicate picker and relationship rows
✅ CSS styling for inverse chips

## Completed (Part 2)
✅ Own Property Inverse Field
✅ Optional "inverse of" field for property terms (ObjectProperty/DatatypeProperty/AnnotationProperty)
✅ Field picker showing loaded properties + workspace properties
✅ Bidirectional updates when setting inverse
✅ Stored as pred="om:inverseOf", val="pfx:property_code"
✅ Hidden from normal relationship rows (filtered by isHiddenTypeRow)
✅ CSS styling for inverse control (.invctrl, .invlbl, .invsel)
⚠️ Export pending (Part 6)

## Completed (Part 3)
✅ Show Incoming Relations as Derived Rows
✅ Default ON (no toggle yet, can be added later)
✅ Lists incoming relations on object term view
✅ Format: "predicate_name ←" with source term as value
✅ Marked as derived (grayed out, read-only, italic style)
✅ NOT saved to parquet (only visual, not in t.children)
✅ Map inverse pair drawing unchanged
✅ CSS styling for derived rows (.derived-row)

## Completed (Part 4)
✅ Write Switch for Inverse Assertions
✅ Checkbox/switch on each relationship row
✅ Only shown when predicate has declared inverse (not symmetric)
✅ Default: OFF
✅ When ON:
  ✅ Class-to-class: writes `B subClassOf (inverse some A)` with warning
  ✅ Individuals: writes direct assertion `y inverse x`
  ✅ Switch state stored on row (field: writeInverse)
  ✅ Updates/deletes written inverse when source changes/deleted
✅ Generated inverse rows shown as read-only with visual indicator
✅ Generated rows filtered out before save (_cleanTermsForSave)
✅ Never leaves orphan inverses (cascade delete)
✅ Warning shown for class restrictions (stronger claim message)

## Completed (Part 5)
✅ Scan Checks (src/ontomonster/validator.py)
✅ Duplicate: Both directions written manually (warning)
✅ Info: Relation with no declared inverse (information only)
✅ Orphan: Written inverse whose source row is gone (error)
✅ Function: scan_inverse_issues(project, props)
✅ Report: print_scan_report(issues)

## Completed (Part 6)
✅ Export Updates
✅ **OWL Export** (src/ontomonster/exporter.py domain_ttl):
  ✅ Property terms with om:inverseOf: add owl:inverseOf triple
  ✅ Relationships with writeInverse=true: write inverse restrictions
  ✅ Properties declared as owl:ObjectProperty/DatatypeProperty
✅ **LinkML Export** (src/ontomonster/exporter.py build_schema):
  ✅ Add "inverse" field to slot definitions when property has inverse
  ✅ Inverse field references the inverse slot name
✅ **SHACL**: No changes (per requirements)

## All Parts Complete

All six parts of inverse relationship support are now implemented:
- Part 1: Read-only chips ✅
- Part 2: Own property inverse field ✅
- Part 3: Derived incoming rows ✅
- Part 4: Write switch for assertions ✅
- Part 5: Scan checks ✅
- Part 6: Export updates ✅

### Testing Checklist
1. Load CCO, check describes/described by shows chip
2. Derived rows appear on object term, not saved
3. Write switch OFF: writes nothing
4. Write switch ON (class pair): writes restriction + shows warning
5. Write switch ON (individuals): writes assertion
6. Delete source: removes written inverse
7. Symmetric property: writes nothing extra
8. Own property with inverse: exports owl:inverseOf
9. Export without switch: byte-identical to current

### Files to Modify
- src/ontomonster/static/index.html (UI, derived rows, write logic)
- src/ontomonster/exporter.py (OWL and LinkML export)
- src/ontomonster/validator.py (scan checks)
- CHANGELOG.md (document feature)

### Data Structure Changes
Property term children add optional field:
```javascript
{
  pred: "om:inverseOf",  // new predicate for properties
  val: "pfx:property_code"  // the inverse property
}
```

Relationship row adds optional field:
```javascript
{
  id: "...",
  pred: "describes",
  val: "Dataset",
  writeInverse: true  // new field for write switch
}
```
