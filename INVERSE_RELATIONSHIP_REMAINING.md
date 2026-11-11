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

## Remaining Implementation

### Part 4: Write Switch (Inverse Assertions)
- Add checkbox/switch on each relationship row
- Only available when predicate has declared inverse
- Default: OFF
- When ON:
  - Class-to-class: write `B subClassOf (inverse some A)` with warning
  - Individuals: write direct assertion `y inverse x`
  - Store switch state on the row (new field: writeInverse)
  - Update/delete written inverse when source changes/deleted
- Never leave orphan inverses

### Part 5: Scan Checks
- Both directions written manually (duplicate - flag as warning)
- Relation with no declared inverse (info only, ignorable)
- Written inverse whose source row is gone (orphan - error)

### Part 6: Export Updates
**OWL Export** (src/ontomonster/exporter.py domain_ttl):
- For property terms with "inverse of" field: add owl:inverseOf triple
- For relationships with writeInverse=true:
  - Class-to-class: write restriction with inverse
  - Individuals: write inverse assertion

**LinkML Export** (src/ontomonster/exporter.py build_schema):
- Add "inverse" field to slot definitions when property has inverse

**SHACL**: No changes (per requirements)

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
