"""Реальный сценарий в WebKit. Сохраняет подборки, сообщения не отправляет.

GOULASH_E2E_URL=http://localhost:8000/ uv run --with playwright python scripts/e2e_real_case.py
"""
import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get('GOULASH_E2E_URL', 'http://localhost:8000/').rstrip('/') + '/'
OUT = Path('data/real-case-shots')
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.webkit.launch()
    errors = []
    for width in (1440, 1024, 390):
        page = browser.new_page(viewport={'width': width, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(URL)
        page.locator('.real-entry').click()
        expect(page.locator('.real-supplier')).to_have_count(5)
        assert page.evaluate('location.hash') == '#real-case'
        assert page.evaluate('document.documentElement.scrollWidth') == width
        for supplier in page.locator('.real-supplier').all():
            assert supplier.locator('.real-source-links a').count() > 0
            expect(supplier.locator('.real-company')).to_contain_text('ИНН')
            expect(supplier.locator('.real-company')).to_contain_text('Дата регистрации')
        expect(page.locator('.real-supplier', has=page.locator('#real-prodservice'))).to_contain_text('6670170834')
        expect(page.locator('.real-supplier', has_text='Mozaretti')).to_contain_text('чистый убыток 18,4 млн')
        expect(page.locator('.real-supplier', has_text='Mozaretti')).to_contain_text('25.10.2024')
        expect(page.locator('.real-supplier', has_text='Лавка Самоваръ').locator('.real-decision')).to_have_text('Нет в наличии')
        expect(page.locator('.real-table thead th')).to_have_count(4)
        page.screenshot(path=OUT / f'{width}-overview.png')
        # Three selected by default; a fourth must not enter comparison.
        page.locator('.real-supplier').nth(3).locator('input[type=checkbox]').click()
        expect(page.locator('.real-table thead th')).to_have_count(4)
        expect(page.locator('.real-supplier input:checked')).to_have_count(3)
        # Preparing a draft must not record an actual sending.
        requests_before = page.request.get(URL + 'api/kp').json()
        page.locator('.real-supplier').first.get_by_role('button', name='Подготовить запрос').click()
        expect(page.locator('#real-draft')).to_be_visible()
        draft = page.locator('#real-draft textarea').input_value()
        assert '200 кг' in draft and 'Profi Cheese' in draft and 'ЗМЖ' in draft
        assert page.request.get(URL + 'api/kp').json() == requests_before
        page.screenshot(path=OUT / f'{width}-draft.png')
        page.reload()
        expect(page.locator('.real-supplier')).to_have_count(5)
        page.get_by_role('button', name='Поиск', exact=True).click()
        expect(page.locator('.hero')).to_be_visible()
        print(f'{width}px: источники, сравнение, черновик и ссылка проверены')
        page.close()
    browser.close()
    assert not errors, errors
