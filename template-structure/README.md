# Template Structure

This folder shows the recommended directory layout for organizing your ontology projects with OntoMonster.

## Structure

```
myorg/                          # Your organization name
├── myorg_consolidate/          # Domain-wide consolidated ontology
│   └── myorg_consolidate.ttl   # Single file combining all domain ontologies
├── myorg_workspace/            # Project-specific ontology files
│   ├── experiment1.ttl         # Smaller files for individual tasks
│   └── analysis2.ttl           # Each workspace is its own file
└── save/                       # OntoMonster internal state (auto-managed)
```

## What Goes Where

**`myorg_consolidate/`** - Domain-wide ontology
- ONE consolidated file for your entire domain ontology
- Combines all workspace exports
- Use this when you need the complete picture

**`myorg_workspace/`** - Project-specific ontologies
- SMALLER files for individual tasks (experiments, analyses, datasets)
- Each file = one workspace
- Work on these individually, export when ready

**`save/`** - Internal state
- OntoMonster auto-manages this
- Draft work and UI state

## Export Formats

You choose which format(s) to export:
- **Turtle** (`.ttl`) - default RDF format, human-readable
- **RDF/XML** (`.rdf`) - legacy RDF format
- **JSON-LD** (`.jsonld`) - JSON-based RDF
- **LinkML** (`.yaml`) - schema definition language
- **SHACL** (`.shacl.ttl`) - validation shapes

## Usage

1. Create a folder for your organization (replace `myorg` with your actual org name)
2. When you start OntoMonster, point it to this folder as your Internal Ontology Location
3. OntoMonster creates the structure automatically
4. Choose your export format(s) when you export
