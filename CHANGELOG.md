# Changelog

This file lists all notable changes to OntoMonster. The project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- Reduced startup noise. Diagnostic messages moved to DEBUG level.
  Set ONTO_MONSTER_LOG=debug to see them. Default console shows only
  the startup URL and errors.
- Faster warm starts. Catalog build skips entirely when all files are
  unchanged and sidecars exist. No loading overlay in this case.
- Parallel parsing. New or changed reference files parse in parallel
  (up to 6 workers). Largest files start first.
- Progressive polling. UI polls at 500ms, increases to 1000ms after
  5 seconds.
- Light status endpoint. After refcache build, UI calls status with
  light=true to skip org scan.

### Fixed
- Removed /api/refcache/progress from uvicorn access log to reduce
  noise.

## [1.0.0] - 2026-11-05

First public release. OntoMonster is a local tool that helps you build 
ontologies that align with BFO and CCO. It has autofill from reference 
ontologies, a consistency scan, and validated export. Everything runs on 
your computer.

### Projects and structure

- The system has three parts: the app (`onto-monster`), the input repo
  `reference-ontologies` (the ontologies that you standardize with) and the
  output repo `internal-ontology` (the ontologies that you build).
- At start, select **Load existing** (give the two locations) or **Start a new
  project** (create the folder structure). A progress overlay shows while the
  app restores your work and builds the reference cache.
- The app organizes the output by org and then by workspace. When you select an
  org, the app loads only the workspaces of that org. Orgs never mix. The app
  finds workspaces in nested folders, in the flat `<org>_workspace/` folder
  and in drafts that have no exported file.
- A workspace is a set of flat files in `<org>_workspace/`. An export writes
  `<workspace>.ttl`, `.rdf`, `.jsonld`, `.yaml` and `.shacl.ttl` there. A new
  export overwrites the same files.
- Each org has one save folder: `<org>/save/`. At load, the app collects the
  drafts from every earlier save location. **Import previous work** loads a
  save folder from another place.
- **Move workspace** moves a workspace to another org. It works for a
  workspace with a folder, with files, or with only a draft.
- Each org has a `<org>_consolidate/` bundle. It replaces the earlier
  `universal/` folder.

### Authoring

- The term inspector has the category (subclass of, or type), the label, the
  definition and the relationships.
- Autofill matches values against all loaded reference ontologies by label,
  code and alt label. When you select a class, the app adds its prefix.
- At load, the app removes the own-namespace prefix from your terms. It does
  not change references. The namespace stays blank until you set it.

### References and namespaces

- The app reads the namespaces of the references from the loaded files. It
  does not guess them. This removes the double OBO namespace (`BFO_BFO_`) at
  its source.
- Scan does not flag the structural predicates of onto-monster (`om:valueType`,
  `om:valueSet`, `om:valueSetRef`, `om:closedEnum` and `om:enumValue`).
- Scan does not flag a relation as invented when the ontology has no
  properties loaded.
- The Glossary shows the loaded ontologies, grouped by the subject domain in
  `reference-ontologies/sources.yaml`. Each group can collapse and has a color.
  The columns have a fixed width. A long alt label ends with an ellipsis. The
  URIs show in full.

### Quality and export

- **Scan** finds orphan and placeholder terms, empty fields, broken or
  invented references, datatype mismatches, duplicates, conflicts between
  class and individual, and missing OWL types.
- **Export** writes five files: OWL (`.ttl`, `.rdf` and `.jsonld`), LinkML
  (`.yaml`) and SHACL (`.shacl.ttl`). If a reference does not resolve, the
  export stops and shows a list of problems. The export conforms to
  ISO/IEC 21838-1, and LinkML validates it.

### Map and UI

- The map has the views Focus and Bloom, and the layouts Hierarchy and Star.
  It has controls for size, spacing and edges, and a Maximize button.
- The UI has light and dark themes. The menu has these groups: New, Edit,
  Import, Export, Delete, Revert and Exit.

### Installation and distribution

- **Simple command**: Just type `ontomonster` to run
- **Two installation methods**:
  * `pip install -e .` for editable install
  * `pixi install` for reproducible environments
- **Cross-platform support**: Windows, Linux, macOS
  * Auto-detects Python in PATH
  * Platform-specific launchers (run.sh, run.bat) for development
- **Auto port cleanup**: Automatically closes port 8000 if already in use
- **Browser auto-open**: Opens browser in 0.5 seconds
- **Entry point configured**: `[project.scripts]` in pyproject.toml

### Community health files

- `CODE_OF_CONDUCT.md` - Contributor Covenant 2.0
- `CONTRIBUTING.md` - Comprehensive contribution guidelines
- `LICENSE` and `NOTICE` - BSD-3-Clause with LLNL disclaimer
- `.github/RELEASE_CHECKLIST.md` - Release procedures and best practices
- LLNL Release Number: LLNL-CODE-2026540
- All source files have `SPDX-License-Identifier: BSD-3-Clause`

### Documentation

- **Professional README**: With logo, installation options, features
- **User Guide** (`docs/GUIDE.md`): ASD-STE-100 compliant with 32 screenshots
  * Reorganized workflow: Menu first, export last
  * Complete data import section with visual workflow
  * All 16 upload screenshots showing real usage
- **Developer docs** (`docs/MAINTAINING.md`): Screenshot procedures, standards
- **Architecture** (`docs/architecture.md`): Design philosophy and capabilities
- **Usage guide** (`docs/usage.md`): Quick reference
- **Template structure** (`template-structure/`): Example folder organization
- Automated screenshot generation: `dev/capture_docs.py`
- The guide shows how to bring in data from files (CSV, Excel, XML, JSON, Parquet, data cards)

### Code quality

- Python code conforms to PEP 8 (79 columns for code, 72 for comments)
- Documentation follows ASD-STE-100 simplified technical English
- Fixed Windows encoding issue (replaced → with ->)
- Fixed import paths (relative imports)
- Fixed state file naming consistency
- Browser opens faster (0.5s instead of 1.3s)
