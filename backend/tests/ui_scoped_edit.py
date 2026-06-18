"""UI test: editing a folder flag with a filter active stamps only matching files."""
import sys

import httpx
from playwright.sync_api import sync_playwright

UI = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"


def main():
    c = httpx.Client(base_url=API, timeout=30)
    dsid = c.get("/api/datasets").json()[0]["id"]

    def folder(name):
        r = c.get("/api/nodes/search", params={"dataset_id": dsid, "q": name, "is_dir": True, "page_size": 50}).json()
        return next(i for i in r["items"] if i["name"] == name)

    legal = folder("Legal")

    # Files under Legal, split by type, BEFORE the edit.
    def files_under(node_id, type_=None):
        p = {"dataset_id": dsid, "under_node_id": node_id, "is_dir": False, "page_size": 1000}
        if type_:
            p["types"] = [type_]
        return c.get("/api/nodes/search", params=p).json()["items"]

    pptx_before = files_under(legal["id"], "PPTX File")
    non_pptx = [f for f in files_under(legal["id"]) if f["file_type"] != "PPTX File"]
    assert pptx_before, "test data has no PPTX under Legal"
    assert non_pptx, "test data has no non-PPTX under Legal"
    print(f"Legal subtree: {len(pptx_before)} PPTX, {len(non_pptx)} other files")

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1500, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        page.goto(UI)
        page.wait_for_selector("text=Welcome")
        page.fill("input[placeholder='e.g. Faustin']", "Tester")
        page.click("text=Continue")
        page.wait_for_selector("text=FileServer")

        # Select Legal
        page.click(".tree-row >> text=Legal")
        page.wait_for_selector("text=Read-only (from CSV)")

        # Apply PPTX type filter
        page.click("text=All file types")
        page.fill("input[placeholder='Search types…']", "PPTX")
        page.click(".typefilter-row >> text=PPTX File")
        page.keyboard.press("Escape")

        # The editable section should switch to scoped mode.
        page.wait_for_selector("text=filter active: matching files only")
        page.wait_for_selector("text=matching file(s)")
        page.screenshot(path="/tmp/shots/06_scoped.png")

        # Click the scoped "Set ✓" button.
        page.click("button:has-text('Set ✓ on')")
        page.wait_for_selector("text=Updated")  # toast
        page.wait_for_timeout(600)
        b.close()

        real_errors = [e for e in errors if "favicon" not in e.lower()]
        if real_errors:
            print("PAGE ERRORS:", real_errors)
            sys.exit(1)

    # Verify via API.
    kept_pptx = sum(
        1 for f in files_under(legal["id"], "PPTX File")
        if c.get(f"/api/nodes/{f['id']}").json()["effective"]["keep"] is True
    )
    touched_other = [
        f for f in non_pptx
        if c.get(f"/api/nodes/{f['id']}").json()["effective"]["keep"] is not None
    ]
    legal_own = c.get(f"/api/nodes/{legal['id']}").json()["own"]["keep"]

    print(f"PPTX now kept: {kept_pptx}/{len(pptx_before)}")
    print(f"non-PPTX touched: {len(touched_other)} (expected 0)")
    print(f"Legal folder own keep: {legal_own} (expected None)")

    assert kept_pptx == len(pptx_before), "not all PPTX were kept"
    assert touched_other == [], "non-PPTX files were incorrectly modified"
    assert legal_own is None, "folder itself should not be modified"
    print("SCOPED EDIT OK — only matching files updated")


if __name__ == "__main__":
    main()
