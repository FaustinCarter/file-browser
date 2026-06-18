"""UI test: assignee field, audit (updated_by), and assignee/unassigned filters."""
import sys

import httpx
from playwright.sync_api import sync_playwright

UI = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"


def main():
    c = httpx.Client(base_url=API, timeout=60)
    for d in c.get("/api/datasets").json():
        c.delete(f"/api/datasets/{d['id']}")
    with open("/home/user/file-browser/sample_data/fake_fileserver.csv", "rb") as fh:
        dsid = c.post("/api/datasets", files={"file": ("f.csv", fh, "text/csv")},
                      data={"name": "Fake Server"}).json()["id"]

    def folder(name):
        r = c.get("/api/nodes/search", params={"dataset_id": dsid, "q": name, "is_dir": True, "page_size": 50}).json()
        return next(i for i in r["items"] if i["name"] == name)

    finance = folder("Finance")

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1500, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        page.goto(UI)
        page.wait_for_selector("text=Welcome")
        page.fill("input[placeholder='e.g. Faustin']", "Carol")
        page.click("text=Continue")
        page.wait_for_selector("text=FileServer")

        # Assign Finance to "alice" via the Assignee field.
        page.click(".tree-row >> text=Finance")
        page.wait_for_selector("text=Assignee")
        assignee_input = page.locator(".form-row:has(label:text-is('Assignee')) input").first
        assignee_input.fill("alice")
        assignee_input.press("Enter")
        page.wait_for_selector("text=Last updated by Carol")  # audit footer
        page.screenshot(path="/tmp/shots/10_assignee.png")

        # Verify backend: Finance own assignee=alice, updated_by=Carol.
        fin = c.get(f"/api/nodes/{finance['id']}").json()
        assert fin["own"]["assignee"] == "alice", fin["own"]
        assert fin["updated_by"] == "Carol", fin
        print("Finance assignee:", fin["own"]["assignee"], "updated_by:", fin["updated_by"])

        # Grid: filter Assignee = alice -> only Finance subtree files.
        page.click("text=Grid / bulk edit")
        page.wait_for_selector("table.grid")
        sel = page.locator(".filter-group:has(label:text-is('Assignee')) select")
        sel.select_option("alice")
        page.click("text=Apply")
        page.wait_for_function(
            "Array.from(document.querySelectorAll('table.grid tbody tr td:nth-child(3)'))"
            ".every(td => td.textContent.includes('\\\\Finance\\\\'))"
            " && document.querySelectorAll('table.grid tbody tr').length > 0"
        )
        n_alice = page.locator("table.grid tbody tr").count()
        print("rows for assignee=alice (all under Finance):", n_alice)
        page.screenshot(path="/tmp/shots/11_grid_assignee.png")

        # Filter Assignee = Unassigned -> none should be under Finance.
        sel.select_option(value="__none__")
        page.click("text=Apply")
        page.wait_for_timeout(900)
        under_finance = page.evaluate(
            "Array.from(document.querySelectorAll('table.grid tbody tr td:nth-child(3)'))"
            ".filter(td => td.textContent.includes('\\\\Finance\\\\')).length"
        )
        print("unassigned rows under Finance (expect 0):", under_finance)
        assert under_finance == 0

        b.close()
        real = [e for e in errors if "favicon" not in e.lower()]
        if real:
            print("PAGE ERRORS:", real)
            sys.exit(1)

    print("ASSIGNEE/AUDIT/FILTER UI OK")


if __name__ == "__main__":
    main()
