"""diagram HTML → PNG (svg 만, 투명 배경). networkidle 은 Google Fonts 가 느리면 30초 타임아웃이라 fonts.ready 로 기다린다."""
from playwright.sync_api import sync_playwright
import sys, pathlib
src, out = sys.argv[1], sys.argv[2]
scale = float(sys.argv[3]) if len(sys.argv) > 3 else 2
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(device_scale_factor=scale)
    page.goto(f"file://{pathlib.Path(src).resolve()}", wait_until="load")
    try:
        page.wait_for_function("document.fonts.status === 'loaded'", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(500)
    page.locator("svg").first.screenshot(path=out, omit_background=True)
    browser.close()
