# OntoMonster User Guide

This guide shows you how to use OntoMonster. Follow the steps in order. Each section has screenshots to help you.

> **What is OntoMonster?**  
> OntoMonster is a local tool. It helps you build ontologies that align with BFO and CCO. It has autofill from reference ontologies, a consistency scan, and validated export.

## Contents

1. [Start the application](#1-start-the-application)
2. [Use the menu](#2-use-the-menu)
3. [Select your organization and workspace](#3-select-your-organization-and-workspace)
4. [Create and edit terms](#4-create-and-edit-terms)
5. [Import data from files](#5-import-data-from-files)
6. [Use autofill for standard terms](#6-use-autofill-for-standard-terms)
7. [View the relationship map](#7-view-the-relationship-map)
8. [Browse reference ontologies](#8-browse-reference-ontologies)
9. [Check for problems](#9-check-for-problems)
10. [Export your ontology](#10-export-your-ontology)

---

## 1. Start the application

When you start OntoMonster, you see the startup screen.

![Startup screen](img/guide/start_loading.png)

**You have two choices:**

### Choice A: Load an existing project

1. Click the folder icon next to **Reference Ontology Location**.
2. Select the folder that has your reference ontologies (BFO, CCO, and others).
3. Click the folder icon next to **Internal Ontology Location**.
4. Select the folder that has your work.
5. Click **Load**.

The reference location is the input. It has the standard ontologies.  
The internal location is the output. It has the ontologies you build.

### Choice B: Start a new project

1. Click **Start a new project**.
2. Select where you want to create the folders.
3. OntoMonster creates the folder structure for you.

**What happens next:**

A progress bar shows while OntoMonster:
- Restores your saved work
- Builds the reference cache
- Loads the interface

---

## 2. Use the menu

The menu has all the commands to work with OntoMonster. Learn where commands are before you start working.

![Menu dropdown](img/guide/II_menu_dropdown.png)

### Menu groups

Commands are grouped by type:

**New:**
- New workspace
- New term

**Edit:**
- Rename workspace
- Move workspace
- Edit term details

**Import:**
- Import data from file
- Import previous work (saved state)

**Export:**
- Export workspace
- Export consolidate bundle

**Delete:**
- Delete workspace
- Delete term

**Revert:**
- Revert to last save
- Discard changes

**Exit:**
- Save and exit
- Exit without saving

### Keyboard shortcuts

Some commands have keyboard shortcuts. These show next to the command name in the menu.

**Throughout this guide:**

When instructions say "Click Menu > Import > Import data...", this means:
1. Click **Menu** at the top
2. Click **Import**
3. Click **Import data...**

---

## 3. Select your organization and workspace

After the app loads, you see the main screen.

![Organization and workspace selectors](img/guide/ws_select.png)

### What are organizations?

An organization (org) is a folder in your Internal Ontology Location. Each org has its own workspaces. When you change the org, the app shows only that org's workspaces.

### What are workspaces?

A workspace is one ontology project. You can have many workspaces in one org.

### How to select

1. Click the **Org** dropdown.
2. Select an organization from the list.
3. Click the **Workspace** dropdown.
4. Select a workspace from the list.

### How to create, rename, or delete

Use the Menu at the top of the screen:
- To create a workspace: **Menu > New > New workspace**
- To rename a workspace: **Menu > Edit > Rename workspace**
- To delete a workspace: **Menu > Delete > Delete workspace**
- To move to another org: **Menu > Edit > Move workspace**

### About saving

When you save, OntoMonster stores your work in `<org>/save/`. Each org has one save folder. Your work is safe even if you have not exported yet.

---

## 4. Create and edit terms

Terms are the basic units in your ontology. Each term has a label, definition, and relationships.

![Term editor panel](img/04_term_editor.png)

### How to select a term

1. Look at the **My Terms** list on the left.
2. Click a term name.
3. The inspector panel shows the term details on the right.

### What you see in the inspector

- **Category**: Shows if the term is a class or an instance
- **Label**: The name of the term
- **Definition**: What the term means
- **Relationships**: How the term connects to other terms

### How to change the category

![Category selector](img/guide/edit_category_row.png)

A term can be:
- **subclass of**: A type of class (example: "Dog" is a subclass of "Animal")
- **type**: An instance of a class (example: "Fido" is a type of "Dog")

To change the category:
1. Click the category dropdown.
2. Select **subclass of** or **type**.

### How to edit term details

1. Click in the **Label** field. Type the new label.
2. Click in the **Definition** field. Type the definition.
3. To add a relationship, click **Add relationship**.
4. Select a predicate from the dropdown.
5. Type a value or use autofill (see Section 5).

Your changes save automatically.

---

## 5. Import data from files

You can add terms to your ontology from data files. OntoMonster reads these formats:
- CSV and TSV
- Excel (`.xlsx`)
- XML
- JSON
- Parquet
- HuggingFace data cards (Markdown)

### Before you import

![Workspace before import](img/guide/upload_00_dashboard_before.png)

The workspace shows the terms you have now.

### How to import

1. Click **Menu > Import > Import data...**.
2. Select your file.
3. The file opens in the import screen.

---

### For spreadsheets and Excel files

#### Step 1: View your data

![Inspector with raw cells](img/guide/upload_01_inspector_open.png)

The **Inspect & label** screen shows your file as a grid. Each cell shows its raw content.

**If your file has multiple sheets:**

![Sheet picker](img/guide/upload_06_second_sheet.png)

Use the sheet picker at the top. Mark each sheet separately.

#### Step 2: Mark the headers

![Mark variables](img/guide/upload_02_mark_variables.png)

1. Click each header cell.
2. Click **Variable**.
3. The cell becomes dark.

Headers become the field names in your ontology.

#### Step 3: Mark the data

![Mark values](img/guide/upload_03_mark_values.png)

1. Click each data cell.
2. Click **Value**.
3. The cell becomes light.

The preview at the bottom shows each field with its values.

#### Step 4: Use Block mode (optional)

![Block mode dialog](img/guide/upload_04_block_mode.png)

Block mode lets you mark a rectangle of cells at once.

1. Click **Block mode**.
2. Drag a rectangle around the cells.
3. Choose where the headers are:
   - Top row
   - Left column
   - Both
   - None
4. Type a group name.
5. Click **Add block**.

![Block added](img/guide/upload_05_block_added.png)

The block outline shows in orange.

#### Step 5: Use Link mode (optional)

Link mode is for key-value pairs that are not in a table.

1. Click **Link mode**.
2. Click a key cell.
3. Click a value cell.
4. OntoMonster links them together.

#### Step 6: Analyze the data

1. Click **Use labeling -> analyze**.
2. OntoMonster sends the labeled table to the parser.
3. The file name changes to show `.labeled.csv`.

---

### Review and add to the ontology

![Review draft screen](img/guide/upload_07_review_draft.png)

The **Review draft** screen shows:
- File format
- Number of terms
- Number of fields
- Number of edges (relationships)
- Notes about the import

Each term shows with its fields and datatypes.

#### Structure options

Three options are on by default:

1. **Subclass hierarchy**: Creates parent-child relationships
2. **Grouping classes**: Groups related terms
3. **Each field as a term**: Makes each column a term

OntoMonster suggests a class for numbers and dates. You choose the class for other fields.

#### Match to existing terms

![Match existing terms](img/guide/upload_08_match_existing.png)

If a new term is similar to an existing term:

1. Click the term.
2. OntoMonster shows existing terms ranked by similarity.
3. Choose one or click **Keep as new term**.

This prevents duplicates.

#### Choose the workspace

![Choose workspace](img/guide/upload_09_new_workspace_choice.png)

1. Use the workspace dropdown to select an existing workspace.
2. Or click **New workspace** and type a name.
3. The button changes to **Create workspace**.
4. Click the button to import.

---

### After you import

![Dashboard after import](img/guide/upload_10_dashboard_after.png)

The new terms appear in **My Terms**. Each term shows a triangle icon. This means it has work to do.

**What you see:**
- Each field has a placeholder class: `cco:InformationContentEntity`
- A row points to the parent term
- An empty row holds the datatype

#### Check the import

![Scan after import](img/guide/upload_11_scan_after.png)

1. Click **Scan** (see Section 7).
2. The scan shows:
   - References that do not resolve (placeholder classes)
   - Terms that have no definition
3. Fix each problem before you export.

---

### For other file formats

#### XML and JSON

| XML | JSON |
|---|---|
| ![XML review](img/guide/upload_12_review_xml.png) | ![JSON review](img/guide/upload_13_review_json.png) |

XML and JSON files go directly to **Review draft**. The structure becomes terms and relationships.

#### Parquet files

![Parquet review](img/guide/upload_14_review_parquet.png)

Parquet files include type information. OntoMonster uses those types for the fields.

#### Data cards

![Data card review](img/guide/upload_15_review_card.png)

Data cards are Markdown files with metadata. OntoMonster:
- Reads the field types
- Fills in the definitions automatically

Data cards are the only format where definitions import with the data.

---

## 6. Use autofill for standard terms

When you type a value in a relationship, OntoMonster suggests matches from your reference ontologies.

![Autofill suggestions](img/guide/edit_autofill.png)

### How autofill works

OntoMonster searches all loaded reference ontologies. It matches by:
- Label
- Code
- Alternative labels

### How to use autofill

1. Click in a relationship value field.
2. Start to type.
3. Suggestions appear in a dropdown.
4. Click a suggestion to select it.
5. OntoMonster adds the ontology prefix automatically.

### Example with CCO loaded

![Autofill with CCO](img/guide/edit_autofill_cco.png)

When you load CCO (Common Core Ontologies), the values resolve to real CCO terms. The prefix shows which ontology each term comes from.

**Why use autofill:**
- Ensures you use standard terms
- Prevents spelling errors
- Makes your ontology interoperable
- Saves typing time

---

## 7. View the relationship map

The map shows your terms and how they connect.

### View modes

| Hierarchy view | Star view | Bloom view |
|---|---|---|
| ![Hierarchy](img/guide/III_map_focus.png) | ![Star](img/guide/map_star_button.png) | ![Bloom](img/guide/III_map_bloom.png) |

**Hierarchy view** (Focus mode):
- Shows parent-child relationships
- Terms are in levels
- Good for seeing the structure

**Star view**:
- Shows one term in the center
- All connections radiate out
- Good for seeing one term's relationships

**Bloom view**:
- Shows all terms spread out
- Good for seeing the full network

### How to change the view

Click the buttons at the top of the map:
- **Focus** / **Bloom**: Changes the layout style
- **Hierarchy** / **Star**: Changes how connections show

### Map controls

![Maximized map](img/guide/III_map_maximized.png)

Use the controls to adjust:
- **Size**: Makes nodes bigger or smaller
- **Spacing**: Adds space between nodes
- **Edge length**: Changes how long the connecting lines are
- **Edge weight**: Makes connecting lines thicker or thinner
- **Colors**: Changes the color scheme

**To see the full map:**
1. Click **Maximize**.
2. The map fills the screen.
3. Click **Minimize** to return.

---

## 8. Browse reference ontologies

The **Glossary** lets you browse all loaded reference ontologies.

### How to open the Glossary

![Reference menu](img/guide/II_about_dropdown.png)

1. Click **Reference** in the top menu.
2. Click **Glossary**.

### What you see

![Glossary view](img/guide/about_glossary.png)

The Glossary shows:
- **Term**: The preferred label
- **Synonyms**: Alternative labels
- **URI**: The unique identifier
- **Definition**: What the term means

**Groups:**
- Terms are grouped by subject domain (biology, chemistry, upper ontology)
- Each group has a color
- Click a group name to collapse or expand

**Columns:**
- Columns have a fixed width
- Long text ends with `...` (ellipsis)
- Point at text to see the full version

### How to search

![Glossary search](img/guide/about_glossary_search.png)

1. Click in the search box at the top.
2. Type your search term.
3. The Glossary filters as you type.

**Search looks in:**
- Term labels
- Synonyms
- Definitions

Use the Glossary to:
- Find standard terms to use
- Check the correct spelling
- See what terms exist in each ontology
- Learn the URI for a term

---

## 9. Check for problems

Before you export, check your ontology for problems.

![Scan window](img/guide/II_scan_window.png)

### What Scan checks

**Scan** looks for these problems:
- **Orphan terms**: Terms with no connections
- **Placeholder terms**: Terms with temporary classes
- **Empty fields**: Terms missing label or definition
- **Broken references**: Links to terms that do not exist
- **Invented references**: Terms not in loaded ontologies
- **Datatype mismatches**: Wrong type for a value
- **Duplicates**: Two terms with the same label
- **Class/individual conflicts**: Term marked as both
- **Missing OWL types**: Terms with no type specified

### How to scan

1. Click **Scan** in the toolbar.
2. Wait for the scan to finish.
3. The results appear in groups.
4. Each problem shows:
   - The term name
   - What is wrong
   - Where to fix it

### How to fix problems

1. Click a problem in the list.
2. OntoMonster selects that term.
3. Fix the problem in the inspector.
4. Scan again to check.

**Tip:** Fix all problems before you export. Export fails if references do not resolve.

---

## 10. Export your ontology

When your ontology is ready, export it to files.

### How to export

1. Click **Menu > Export > Export workspace**.
2. OntoMonster checks for problems.
3. If problems exist, the export stops.
4. If no problems exist, the export runs.

### Export results

| Export stopped | Export successful |
|---|---|
| ![Export blocked](img/guide/export_blocked.png) | ![Export done](img/guide/export_success.png) |

**If export stops:**
- A list shows the problems
- Fix each problem
- Export again

**If export succeeds:**
- A message confirms success
- Files appear in your org folder

### Files created

OntoMonster creates five files for each workspace:

**OWL formats** (Web Ontology Language):
- `<workspace>.ttl` - Turtle format (human-readable)
- `<workspace>.rdf` - RDF/XML format (legacy)
- `<workspace>.jsonld` - JSON-LD format (JSON-based)

**Schema format**:
- `<workspace>.yaml` - LinkML schema definition

**Validation format**:
- `<workspace>_shacl.ttl` - SHACL shapes (for validation)

### Where files go

Files go to: `<org>_workspace/` folder in your Internal Ontology Location.

OntoMonster also updates the `<org>_consolidate/` bundle. This bundle combines all workspaces in the org.


