"""UI test for the No-Transfer rename, folder rollup checkbox, and hide filter."""
import sys

import httpx
from playwright.sync_api import sync_playwright

UI = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"


def main():
    c = httpx.Client(base_url=API, timeout=60)
    # Fresh dataset for a predictable Legal folder.
    for d in c.get("/api/datasets").json():
        c.delete(f"/api/datasets/{d['id']}")
    with open("/home/user/file-browser/sample_data/fake_fileserver.csv", "rb") as fh:
        ds = c.post("/api/datasets", files={"file": ("f.csv", fh, "text/csv")},
                    data={"name": "Fake Server"}).json()
    dsid = ds["id"]

    def folder(name):
        r = c.get("/api/nodes/search", params={"dataset_id": dsid, "q": name, "is_dir": True, "page_size": 50}).json()
        return next(i for i in r["items"] if i["name"] == name)

    legal = folder("Legal")

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

        # Rename check: detail panel uses "No Transfer?", not "Keep".
        page.click(".tree-row >> text=Legal")
        page.wait_for_selector("text=No Transfer?")
        assert page.locator("text=Keep?").count() == 0, "old 'Keep?' label still present"

        # Folder rollup shows 0 marked initially.
        nt_label = page.locator("label:has-text('No Transfer?')").first
        assert "0/" in nt_label.inner_text(), nt_label.inner_text()

        # Check the No Transfer box (no filter) -> marks the whole subtree.
        nt_label.locator("input[type=checkbox]").click()
        page.wait_for_timeout(900)
        page.screenshot(path="/tmp/shots/07_notransfer.png")

        leg = c.get(f"/api/nodes/{legal['id']}").json()
        assert leg["own"]["no_transfer"] is True, leg["own"]
        assert leg["no_transfer_marked"] == leg["total_files"], leg
        print(f"Legal marked {leg['no_transfer_marked']}/{leg['total_files']} (own={leg['own']['no_transfer']})")

        # Now hide marked rows via the tree filter -> Legal should disappear.
        nt_select = page.locator(".filter-group:has(label:text-is('No Transfer')) select")
        nt_select.select_option("no")
        page.wait_for_timeout(900)
        page.wait_for_selector(".tree-row:has-text('Legal')", state="detached", timeout=8000)
        legal_rows = page.locator(".tree-row", has_text="Legal").count()
        print(f"Legal tree rows after hide-marked: {legal_rows}")
        assert legal_rows == 0, "fully-marked Legal folder should be hidden"
        page.screenshot(path="/tmp/shots/08_hidden.png")

        b.close()
        real = [e for e in errors if "favicon" not in e.lower()]
        if real:
            print("PAGE ERRORS:", real)
            sys.exit(1)

    print("NO-TRANSFER UI OK")


if __name__ == "__main__":
    main()
