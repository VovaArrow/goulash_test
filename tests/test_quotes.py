import json
from unittest.mock import patch

import pytest
from app.ai.quotes import QuoteFields, _grounded, extract_quote
from .test_api import client


def test_rules_extract_price_with_units(monkeypatch):
    monkeypatch.delenv('GOULASH_OLLAMA_MODEL', raising=False)
    r = extract_quote('Моцарелла: 1 250,50 руб/кг, доставка по пятницам.')
    assert r.price_rub == 1250.5 and r.unit == 'кг' and r.engine == 'rules'
    assert r.evidence['price_rub'] in 'Моцарелла: 1 250,50 руб/кг, доставка по пятницам.'


@pytest.mark.parametrize('text', ['Цена 500 тенге/кг', 'Цена по запросу', 'Моцарелла 470 руб/кг, пармезан 900 руб/кг'])
def test_ambiguous_or_foreign_price_not_invented(monkeypatch, text):
    monkeypatch.delenv('GOULASH_OLLAMA_MODEL', raising=False)
    assert extract_quote(text).price_rub is None


def test_grounding_rejects_invented_value():
    with pytest.raises(ValueError):
        _grounded(QuoteFields(price_rub=100, evidence={'price_rub': '500 руб/кг'}), '500 руб/кг')


def test_ollama_validated_response_and_failure_fallback(monkeypatch):
    monkeypatch.setenv('GOULASH_OLLAMA_MODEL', 'test')
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps({'message': {'content': json.dumps({'price_rub': 470, 'unit': 'кг', 'evidence': {'price_rub': '470 руб/кг', 'unit': '470 руб/кг'}})}}).encode()
    with patch('app.ai.quotes.urlopen', return_value=Response()):
        assert extract_quote('470 руб/кг').engine == 'ollama'
    with patch('app.ai.quotes.urlopen', side_effect=OSError('unavailable')):
        result = extract_quote('470 руб/кг')
        assert result.engine == 'rules_fallback' and result.price_rub == 470


def test_extract_is_preview_and_rejects_mismatched_unit(client, monkeypatch):
    monkeypatch.delenv('GOULASH_OLLAMA_MODEL', raising=False)
    before = client.get('/api/kp').json()
    result = client.post('/api/kp/1/extract', json={'text': 'Моцарелла: 400 руб/л'}).json()
    assert result['price_rub'] is None and 'единицу' in result['warning']
    assert client.get('/api/kp').json() == before
