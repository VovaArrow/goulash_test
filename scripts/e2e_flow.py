"""Сквозной сценарий в WebKit (движок Safari): поиск → карточка → сравнение → запрос КП → вкладка запросов.

Запуск при работающем сервере (make run):
    uv run --with playwright python -m playwright install webkit
    uv run --with playwright python scripts/e2e_flow.py
Скриншоты — в data/e2e-shots/. Сценарий сбрасывает демо-данные в начале и в конце.
"""
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "data/e2e-shots")
OUT.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:8000/"
errors = []


def card(page, name):
    return page.locator("article.card", has_text=name).first


with sync_playwright() as p:
    browser = p.webkit.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.request.post(URL + "api/demo/reset")
    page.goto(URL)
    page.wait_for_selector(".examples button")
    page.screenshot(path=OUT / "01-hero.png")

    page.locator(".examples button").first.click()
    page.wait_for_selector("article.card")
    page.wait_for_timeout(1200)
    first = page.locator("article.card h3").first.inner_text()
    print("first card:", first)
    assert "Уральская сыроварня" in first
    page.screenshot(path=OUT / "02-results.png")
    print("hidden notice:", page.locator(".notice h4").first.inner_text())

    card(page, "Уральская сыроварня").get_by_role("button", name="Подробнее").click()
    page.wait_for_selector("dialog.drawer[open]")
    page.wait_for_timeout(300)
    page.screenshot(path=OUT / "03-detail.png")
    page.fill("#note-input", "Тест: <b>не жирный</b>")
    page.get_by_role("button", name="Добавить").click()
    expect(page.locator(".note").first).to_contain_text("<b>не жирный</b>")
    page.locator("dialog.drawer .dlg-body").evaluate("el => el.parentElement.parentElement.scrollTop = 99999")
    page.screenshot(path=OUT / "04-detail-bottom.png")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    for name in ["Уральская сыроварня", "ТД «Молочный путь»"]:
        card(page, name).locator(".cmp-toggle input").check()
    page.wait_for_selector(".selbar")
    page.screenshot(path=OUT / "05-selbar.png")
    page.locator(".selbar").get_by_role("button", name="Сравнить").click()
    page.wait_for_selector("dialog[open] .conclusion")
    concl = page.locator(".conclusion").inner_text()
    print("conclusion:", concl)
    assert "300 кг" in concl
    page.screenshot(path=OUT / "06-compare.png")

    page.get_by_role("button", name="Запросить КП у этих поставщиков").click()
    page.wait_for_selector("dialog[open] .kp-row")
    body = page.locator("dialog[open] textarea").input_value()
    assert "Сушкоф и пицца" in body and "200 кг" in body
    page.screenshot(path=OUT / "07-kp.png")
    rows = page.locator("dialog[open] .kp-row")
    for i in range(rows.count()):
        rows.nth(i).locator("select").select_option("copy")
        rows.nth(i).get_by_role("button", name="Скопировать").click()
        expect(rows.nth(i).locator(".sent-mark")).to_be_visible()
    page.screenshot(path=OUT / "08-kp-sent.png")
    page.get_by_role("button", name="Готово").click()

    page.get_by_role("button", name="Запросы КП").first.click()
    page.wait_for_selector(".kp-card")
    n = page.locator(".kp-card").count()
    print("requests:", n)
    assert n == 5
    page.screenshot(path=OUT / "09-requests.png")
    top = page.locator(".kp-card").first
    top.locator("select").select_option("received")
    top.locator("input[type=number]").fill("470")
    top.locator("input[type=number]").press("Tab")
    page.wait_for_timeout(500)
    page.reload()
    page.get_by_role("button", name="Запросы КП").first.click()
    page.wait_for_selector(".kp-card")
    top = page.locator(".kp-card").first
    assert top.locator("select").input_value() == "received", top.locator("select").input_value()
    assert top.locator("input[type=number]").input_value() == "470"
    print("persisted: ok")

    page.get_by_role("button", name="Как это работает").click()
    page.wait_for_timeout(200)
    page.screenshot(path=OUT / "10-about.png", full_page=True)

    m = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    m.on("pageerror", lambda e: errors.append(str(e)))
    m.goto(URL)
    m.wait_for_selector(".examples button")
    m.screenshot(path=OUT / "11-mobile-hero.png")
    m.locator(".examples button").first.click()
    m.wait_for_selector("article.card")
    m.wait_for_timeout(1000)
    m.screenshot(path=OUT / "12-mobile-results.png")
    m.locator("article.card").first.get_by_role("button", name="Подробнее").click()
    m.wait_for_selector("dialog.drawer[open]")
    m.screenshot(path=OUT / "13-mobile-detail.png")
    print("scroll width mobile:", m.evaluate("document.documentElement.scrollWidth"))

    page.request.post(URL + "api/demo/reset")
    browser.close()

print("ERRORS:", errors or "none")
