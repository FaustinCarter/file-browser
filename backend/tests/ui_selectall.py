"""UI test: grid 'select all N matching' applies a bulk edit across all pages."""
import sys

import httpx
from playwright.sync_api import sync_playwright

UI = "http://127.0.0.1:5173"
API = "http://127.0.0.1:8000"
TERM = "Nimbus"  # matches 109 files (> one 100-row page)


def main():
    c = httpx.Client(base_url=API, timeout=60)
    dsid = c.get("/api/datasets").json()[0]["id"]
    expected = c.get(
        "/api/nodes/search", params={"dataset_id": dsid, "q": TERM, "is_dir": False, "page_size": 1}
    ).json()["total"]
    print("matching files:", expected)
    assert expected > 100

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1500, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        page.goto(UI)
        page.wait_for_selector("text=Welcome")
        page.fill("input[placeholder='e.g. fwcarter']", "Migrator")
        page.click("text=Continue")
        page.wait_for_selector("text=FileServer")

        page.click("text=Grid / bulk edit")
        page.wait_for_selector("table.grid")
        page.fill("input[placeholder='e.g. Reports']", TERM)
        page.locator(".filter-group:has(label:text-is('Kind')) select").select_option("false")
        page.click("button:has-text('Apply')")
        page.wait_for_selector(f"text={expected} rows")

        # Select the page, then escalate to "all matching".
        page.check("table.grid thead input[type=checkbox]")
        page.wait_for_selector(f"text=Select all {expected} matching")
        page.click(f"text=Select all {expected} matching")
        page.wait_for_selector(f"text=All {expected} rows matching this filter are selected")
        page.screenshot(path="/tmp/shots/12_selectall.png")

        # Bulk-assign every matching row in one shot.
        page.fill(".bulkbar input[placeholder='Assignee']", "migrate-team")
        page.click(".bulkbar button:has-text('Set assignee')")
        page.wait_for_selector(f"text=Updated {expected} rows")
        page.wait_for_timeout(500)
        b.close()

        real = [e for e in errors if "favicon" not in e.lower()]
        if real:
            print("PAGE ERRORS:", real)
            sys.exit(1)

    # Verify via API: exactly the matching files now have the assignee + audit.
    got = c.get(
        "/api/nodes/search",
        params={"dataset_id": dsid, "assignee": "migrate-team", "page_size": 1},
    ).json()
    print("assignee=migrate-team total:", got["total"])
    assert got["total"] == expected
    sample = c.get(
        "/api/nodes/search",
        params={"dataset_id": dsid, "assignee": "migrate-team", "page_size": 5},
    ).json()["items"]
    assert all(TERM.lower() in i["full_path"].lower() for i in sample)
    assert all(i["updated_by"] == "Migrator" for i in sample)
    print("SELECT-ALL-MATCHING UI OK")


if __name__ == "__main__":
    main()
