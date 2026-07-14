"""UI test: a folder flag with an effective-value filter (JIRA) active stamps
only the matching files -- the audit-hazard fix (previously a flag/JIRA/
assignee filter was silently ignored and the whole subtree was rewritten)."""
import sys

import httpx
from playwright.sync_api import sync_playwright

UI = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"


def main():
    c = httpx.Client(base_url=API, timeout=60)
    # Fresh dataset so prior test runs don't leak annotations.
    for d in c.get("/api/datasets").json():
        c.delete(f"/api/datasets/{d['id']}")
    with open("/home/user/file-browser/sample_data/fake_fileserver.csv", "rb") as fh:
        dsid = c.post("/api/datasets", files={"file": ("f.csv", fh, "text/csv")},
                      data={"name": "Fake Server"}).json()["id"]

    def folder(name):
        r = c.get("/api/nodes/search", params={
            "dataset_id": dsid, "q": name, "is_dir": True, "page_size": 50}).json()
        return next(i for i in r["items"] if i["name"] == name)

    legal = folder("Legal")

    # Give some (not all) files under Legal a JIRA ticket via the API.
    pptx_total = c.get("/api/nodes/search", params={
        "dataset_id": dsid, "under_node_id": legal["id"], "is_dir": False,
        "types": ["PPTX File"], "page_size": 1}).json()["total"]
    all_total = c.get("/api/nodes/search", params={
        "dataset_id": dsid, "under_node_id": legal["id"], "is_dir": False,
        "page_size": 1}).json()["total"]
    assert 0 < pptx_total < all_total, (pptx_total, all_total)
    c.post("/api/nodes/bulk-annotation", json={
        "node_id": legal["id"], "files_only": True, "types": ["PPTX File"],
        "values": {"jira_ticket": "MIG-UI-1"}})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(UI)
        page.wait_for_selector("text=Welcome")
        page.fill("input[placeholder='e.g. fwcarter']", "ui-eff")
        page.click("text=Continue")
        page.wait_for_selector("text=FileServer")

        # Filter the tree by JIRA = MIG-UI-1 (an effective-value filter ONLY).
        page.locator(".filter-group", has_text="JIRA").locator("select").select_option("MIG-UI-1")
        page.wait_for_timeout(800)

        # Select Legal; the scoped-edit banner must appear (filter recognised).
        page.click("text=Legal")
        page.wait_for_selector("text=matching file(s)")

        # Toggle No Transfer on the folder -- must scope to the ticketed files.
        row = page.locator(".form-row", has_text="No Transfer?")
        row.locator("input[type=checkbox]").click()
        page.wait_for_timeout(1200)
        browser.close()

        assert not errors, errors

    marked = c.get("/api/nodes/search", params={
        "dataset_id": dsid, "under_node_id": legal["id"], "is_dir": False,
        "no_transfer": "yes", "page_size": 1}).json()["total"]
    own = c.get(f"/api/nodes/{legal['id']}").json()["own"]["no_transfer"]
    print(f"files marked under Legal: {marked} (expected {pptx_total} of {all_total})")
    print(f"Legal folder own no_transfer: {own} (expected None)")
    assert marked == pptx_total, f"expected only the {pptx_total} ticketed files, got {marked}"
    assert own is None, "folder's own value must stay untouched (scoped path)"
    print("SCOPED EFF-FILTER FLAG UI OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("FAIL:", e)
        sys.exit(1)
