"""Проверка доступности источников, без изменения даты и фактов сохранённого сценария.

uv run python scripts/check_real_sources.py
Результат: data/real-source-check.json. Содержимое страниц не сохраняется.
"""
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.real_case import load_real_case


def check(source):
    item = {'id': source.id, 'url': str(source.url), 'checked_at': datetime.now(timezone.utc).isoformat()}
    try:
        request = Request(str(source.url), headers={'User-Agent': 'GoulashPrototypeSourceCheck/0.1'})
        with urlopen(request, timeout=20) as response:
            body = response.read(4 * 1024 * 1024 + 1)
            item.update(status=response.status, final_url=response.url,
                        sha256=hashlib.sha256(body).hexdigest())
        text = body.decode('utf-8', errors='replace')
        title = re.search(r'<title[^>]*>(.*?)</title>', text, re.S | re.I)
        item['title'] = re.sub(r'\s+', ' ', title.group(1)).strip() if title else None
        item['truncated'] = len(body) > 4 * 1024 * 1024
    except (OSError, HTTPError, URLError) as error:
        item['error'] = str(error)
    return item


if __name__ == '__main__':
    sources = [source for supplier in load_real_case().suppliers for source in supplier.sources]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(check, sources))
    output = Path('data/real-source-check.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    for result in results:
        print(result['id'], result.get('status', 'ERROR'), result.get('error', ''))
    print(f'Результат: {output}. Доступность страницы не подтверждает актуальность условий.')
