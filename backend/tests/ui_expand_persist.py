"""UI test: tree expansion state survives applying and clearing a filter."""
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
    child = c.get("/api/tree/children", params={"dataset_id": dsid, "parent_id": finance["id"]}).json()["children"][0]
    child_name = child["name"]
    print("Finance first child:", child_name)

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

        # Expand Finance by clicking its twisty.
        finance_row = page.locator(".tree-row", has_text="Finance").first
        finance_row.locator(".twisty").click()
        # Its child should now be visible.
        page.wait_for_selector(f".tree-row:has-text('{child_name}')")
        print("child visible after expand:", page.locator(f".tree-row:has-text('{child_name}')").count())

        # Apply a type filter, then clear it.
        page.click("text=All file types")
        page.fill("input[placeholder='Search types…']", "PPTX")
        page.click(".typefilter-row >> text=PPTX File")
        page.keyboard.press("Escape")
        page.wait_for_timeout(900)
        page.click("text=Reset filters")
        page.wait_for_timeout(1000)

        # Finance must STILL be expanded: its twisty shows ▾ and the child is back.
        finance_row2 = page.locator(".tree-row", has_text="Finance").first
        twisty = finance_row2.locator(".twisty").inner_text()
        child_visible = page.locator(f".tree-row:has-text('{child_name}')").count()
        print(f"after reset -> Finance twisty='{twisty}', child rows={child_visible}")
        page.screenshot(path="/tmp/shots/09_persist.png")

        b.close()
        real = [e for e in errors if "favicon" not in e.lower()]
        if real:
            print("PAGE ERRORS:", real)
            sys.exit(1)

        assert twisty.strip() == "▾", "Finance collapsed after clearing filter"
        assert child_visible >= 1, "Finance's child not visible after clearing filter"
    print("EXPANSION PERSISTENCE OK")


if __name__ == "__main__":
    main()
