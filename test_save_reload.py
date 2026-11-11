# SPDX-License-Identifier: BSD-3-Clause
"""Test save and reload to reproduce lost progress issue.

Run with real browser and real backend. Requires playwright:
    pip install playwright
    playwright install chromium
"""
import json
import subprocess
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, expect
    import pandas as pd
except ImportError:
    print("Install dependencies: pip install playwright pandas pyarrow")
    print("Then run: playwright install chromium")
    exit(1)


def test_basic_save_reload():
    """Test 1: Load project, edit definition, save, reload, verify."""
    print("\n=== Test 1: Basic Save and Reload ===")

    # Start server in background.
    print("Starting server...")
    server = subprocess.Popen(
        ["python", "-m", "ontomonster", "--no-browser"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    time.sleep(3)  # Wait for server to start.

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        try:
            # Navigate to app.
            page.goto("http://127.0.0.1:8000")
            page.wait_for_load_state("networkidle")
            time.sleep(1)

            # Check if project chooser or main app.
            print("Looking for project interface...")

            # Look for a term or create workspace.
            # Try to find existing workspace or create test one.
            new_ws_btn = page.locator('button:has-text("New workspace")')
            if new_ws_btn.count() > 0:
                print("Creating new workspace...")
                new_ws_btn.click()
                # This might need actual implementation details.

            # Find first term or create one.
            new_term_btn = page.locator('.newterm, button:has-text("New term")')
            if new_term_btn.count() > 0:
                print("Creating new term...")
                new_term_btn.click()
                time.sleep(0.5)

            # Find definition field and edit it.
            def_field = page.locator('textarea[data-val], input[data-val]').first
            if def_field.count() > 0:
                print("Editing definition...")
                test_text = "Test definition for save/reload test"
                def_field.fill(test_text)
                time.sleep(0.5)

                # Click Save button.
                save_btn = page.locator('button:has-text("Save")')
                if save_btn.count() > 0:
                    print("Clicking Save...")
                    save_btn.click()
                    time.sleep(3)  # Wait for save to complete.

                    # Check working.parquet.
                    print("Checking working.parquet...")
                    # Find the org directory.
                    project_dir = Path.cwd()
                    output_dir = project_dir / "output"
                    if output_dir.exists():
                        orgs = [d for d in output_dir.iterdir() if d.is_dir()]
                        if orgs:
                            org_dir = orgs[0]
                            parquet_path = org_dir / "save" / "working.parquet"
                            if parquet_path.exists():
                                df = pd.read_parquet(parquet_path)
                                print(f"Parquet has {len(df)} row(s)")
                                print(f"Columns: {df.columns.tolist()}")
                                if len(df) > 0:
                                    terms_json = df.iloc[0]['terms']
                                    terms = json.loads(terms_json)
                                    print(f"First workspace has {len(terms)} term(s)")

                                    # Check if our edit is in there.
                                    found = False
                                    for term in terms:
                                        for child in term.get('children', []):
                                            if test_text in str(child.get('val', '')):
                                                found = True
                                                print(f"✓ Edit found in parquet!")
                                                break
                                        if found:
                                            break

                                    if not found:
                                        print("✗ Edit NOT found in parquet!")
                                        return False
                                else:
                                    print("✗ Parquet is empty!")
                                    return False
                            else:
                                print("✗ working.parquet not found!")
                                return False

                    # Now reload page.
                    print("Reloading page...")
                    page.reload()
                    page.wait_for_load_state("networkidle")
                    time.sleep(2)

                    # Check if definition still shows.
                    def_field = page.locator('textarea[data-val], input[data-val]').first
                    if def_field.count() > 0:
                        current_value = def_field.input_value()
                        if test_text in current_value:
                            print("✓ Edit persisted after reload!")
                            return True
                        else:
                            print(f"✗ Edit lost after reload! Current: {current_value}")
                            return False
                    else:
                        print("✗ Cannot find definition field after reload!")
                        return False
                else:
                    print("✗ Save button not found!")
                    return False
            else:
                print("✗ Definition field not found!")
                return False

        finally:
            browser.close()
            server.terminate()
            server.wait(timeout=5)

    return False


if __name__ == "__main__":
    result = test_basic_save_reload()
    print(f"\nTest result: {'PASSED' if result else 'FAILED'}")
    exit(0 if result else 1)
