"""Поиск → сохранённая подборка → ответы → два вида сравнения (WebKit).

Сценарий сбрасывает базу целевого сервера. Используйте отдельную тестовую базу:
GOULASH_DB=/tmp/goulash-e2e.db uv run uvicorn app.main:app --port 18080
GOULASH_E2E_URL=http://localhost:18080/ uv run --with playwright python scripts/e2e_collections.py
"""

import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("GOULASH_E2E_URL", "http://localhost:18080/").rstrip("/") + "/"
OUT = Path("data/collections-shots")
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.webkit.launch()
    errors = []
    for width in (1440, 390):
        page = browser.new_page(
            viewport={"width": width, "height": 1000 if width == 1440 else 844}
        )
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "console",
            lambda m: (
                errors.append(m.text) if "Alpine Expression Error" in m.text else None
            ),
        )
        page.request.post(URL + "api/demo/reset")
        page.goto(URL)
        page.locator(".examples button").first.click()
        page.wait_for_selector("article.card")
        for name in ["Уральская сыроварня", "ТД «Молочный путь»"]:
            page.locator("article.card", has_text=name).locator(
                "input[type=checkbox]"
            ).check()
        page.get_by_role("button", name="Моя подборка", exact=True).click()
        expect(
            page.locator(".collection-body:visible .collection-member")
        ).to_have_count(2)
        assert page.evaluate("document.documentElement.scrollWidth") == width
        page.screenshot(path=OUT / f"{width}-collection.png")
        opened = page.locator(".collection-body:visible")
        opened.get_by_role("button", name="Сравнить поставщиков", exact=True).click()
        expect(page.locator("dialog[open]")).to_contain_text(
            "Сохранённые данные каталога"
        )
        expect(page.locator("dialog[open]")).to_contain_text("485 ₽/кг")
        page.keyboard.press("Escape")
        for index, price, terms in [
            (0, "900", "НДС включён, доставка 1000 ₽"),
            (1, "300", "Самовывоз, отсрочка 14 дней"),
        ]:
            row = opened.locator(".collection-member").nth(index)
            row.get_by_role("button", name="Внести ответ", exact=True).click()
            page.locator(".member-editor input[type=number]").fill(price)
            page.locator(".member-editor textarea").fill(terms)
            page.get_by_role("button", name="Сохранить ответ", exact=True).click()
            expect(page.locator(".member-editor")).to_have_count(0)
        opened.get_by_role("button", name="Сравнить предложения", exact=False).click()
        expect(page.locator("dialog[open]")).to_contain_text("900")
        expect(page.locator("dialog[open]")).to_contain_text("300")
        expect(page.locator("dialog[open]")).not_to_contain_text("485 ₽/кг")
        page.screenshot(path=OUT / f"{width}-offers.png")
        page.keyboard.press("Escape")
        page.reload()
        page.wait_for_selector(".collection-summary")
        page.locator(".collection-summary").first.click()
        opened = page.locator(".collection-body:visible")
        expect(opened).to_contain_text("900 ₽/кг")
        expect(opened).to_contain_text("НДС включён, доставка 1000 ₽")
        # Preparing a letter must not regress an existing answer to sent/selected.
        opened.get_by_role(
            "button", name="Подготовить запрос", exact=True
        ).first.click()
        expect(page.locator("dialog[open]")).to_contain_text("Ответ получен")
        page.keyboard.press("Escape")
        # A new search keeps the previous collection and creates its own saved need.
        opened.get_by_role("button", name="Повторить этот поиск").click()
        page.wait_for_selector("article.card")
        page.locator("article.card").first.locator("input[type=checkbox]").check()
        page.get_by_role("button", name="Моя подборка", exact=True).click()
        expect(page.locator(".collection")).to_have_count(
            6
        )  # three varied samples, migrated sample, original search, repeated search
        # Real companies use the same collection workflow with their evidence links intact.
        page.get_by_role("button", name="Реальный пример", exact=True).click()
        page.wait_for_selector(".real-supplier")
        page.get_by_role("button", name="Сохранить выбранных в подборку").click()
        expect(
            page.locator(".collection-body:visible .collection-member")
        ).to_have_count(3)
        page.locator(".collection-body:visible").get_by_role(
            "button", name="Сравнить поставщиков", exact=True
        ).click()
        expect(page.locator("dialog[open] .collection-sources a").first).to_be_visible()
        page.keyboard.press("Escape")
        assert page.evaluate("document.documentElement.scrollWidth") == width
        print(
            f"{width}px: сохранение, перенос старых КП, ответы, сравнения и реальные компании — OK"
        )
        page.close()
    browser.close()
    assert not errors, errors
