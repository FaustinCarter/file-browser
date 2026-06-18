"""Playwright UI smoke test. Drives the real frontend against the live backend."""
import sys

from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:5173"
SHOTS = "/tmp/shots"


def run():
    import os
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        page.goto(BASE)

        # 1. Name prompt
        page.wait_for_selector("text=Welcome")
        page.fill("input[placeholder='e.g. Faustin']", "Faustin")
        page.click("text=Continue")

        # 2. Tree explorer loads with root
        page.wait_for_selector("text=FileServer")
        page.wait_for_selector("text=files")  # folder file counts visible
        page.screenshot(path=f"{SHOTS}/01_tree.png")

        # 3. Expand the root (it's default expanded) and click Finance
        page.wait_for_selector("text=Finance")
        page.click("text=Finance")
        page.wait_for_selector("text=Read-only (from CSV)")
        page.wait_for_selector("text=File types here")
        page.screenshot(path=f"{SHOTS}/02_detail.png")

        # capture the root's unfiltered file count text
        root_row = page.locator(".tree-row", has_text="FileServer").first
        unfiltered = root_row.inner_text()

        # 4. Apply a PPTX type filter
        page.click("text=All file types")
        page.fill("input[placeholder='Search types…']", "PPTX")
        page.click("text=PPTX File")
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
        page.screenshot(path=f"{SHOTS}/03_filtered.png")
        root_row2 = page.locator(".tree-row", has_text="FileServer").first
        filtered = root_row2.inner_text()
        print("ROOT unfiltered:", " ".join(unfiltered.split()))
        print("ROOT pptx-filtered:", " ".join(filtered.split()))

        # 5. Switch to grid view, wait for real data rows (not the loading state)
        page.click("text=Reset filters")
        page.click("text=Grid / bulk edit")
        page.wait_for_selector("table.grid tbody tr td")
        page.wait_for_function(
            "document.querySelectorAll('table.grid tbody tr').length > 1"
        )
        total_rows = page.locator("table.grid tbody tr").count()
        print("GRID rows on first page:", total_rows)
        page.screenshot(path=f"{SHOTS}/04_grid.png")

        # 6. Filter grid to folders (target the 'Kind' select specifically)
        kind = page.locator("select", has=page.locator("option", has_text="Folders"))
        kind.select_option(label="Folders")
        page.click("text=Apply")
        # every visible row should now be a folder (📁 in the Name cell)
        page.wait_for_function(
            "Array.from(document.querySelectorAll('table.grid tbody tr td:nth-child(2)'))"
            ".every(td => td.textContent.includes('📁')) "
            "&& document.querySelectorAll('table.grid tbody tr').length > 0"
        )
        rows = page.locator("table.grid tbody tr").count()
        print("GRID folder rows on page:", rows)

        # 7. Select all on page, bulk-set Keep
        page.check("table.grid thead input[type=checkbox]")
        page.wait_for_selector("text=selected")
        page.click("text=No Xfer ✓")
        page.wait_for_timeout(500)
        page.screenshot(path=f"{SHOTS}/05_grid_bulk.png")

        browser.close()

        real_errors = [e for e in errors if "favicon" not in e.lower()]
        if real_errors:
            print("CONSOLE/PAGE ERRORS:", real_errors)
            sys.exit(1)
        print("UI SMOKE OK")


if __name__ == "__main__":
    run()
