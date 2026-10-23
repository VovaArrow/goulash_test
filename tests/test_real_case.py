from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.real_case import RealSupplier, load_real_case
from .test_api import client


def test_case_has_five_real_companies_and_traceable_facts(client):
    result = client.get('/api/real-case')
    assert result.status_code == 200
    case = result.json()
    assert len(case['suppliers']) == 5
    assert case['checked_at'] == '2026-10-22'
    for supplier in case['suppliers']:
        assert 'score' not in supplier and 'inn' not in supplier
        sources = {s['id']: s for s in supplier['sources']}
        assert supplier['contact_source_id'] in sources
        for source in sources.values():
            assert source['url'].startswith('https://') and '.example' not in source['url']
        assert len(supplier['company_facts']) >= 8
        for fact in supplier['facts'] + supplier['company_facts']:
            assert not fact['value'] or fact['source_ids']
            assert set(fact['source_ids']) <= sources.keys()
        assert {f['key'] for f in supplier['company_facts']} >= {'legal_name', 'inn', 'ogrn', 'registration', 'legal_status', 'activity', 'finance', 'identity'}


def test_case_explains_stock_geography_and_retail_price(client):
    suppliers = {s['id']: s for s in client.get('/api/real-case').json()['suppliers']}
    samovar = suppliers['samovar']
    assert samovar['decision'] == 'unavailable'
    assert 'розничная' in next(f['value'] for f in samovar['facts'] if f['key'] == 'price')
    assert suppliers['gurman']['decision'] == 'outside_region'
    assert 'Тулу' in suppliers['gostpp']['reason']


def test_unsupported_evidence_rejected():
    supplier = load_real_case().suppliers[0].model_dump(mode='json')
    broken = deepcopy(supplier)
    broken['facts'][0]['source_ids'] = []
    with pytest.raises(ValidationError):
        RealSupplier.model_validate(broken)
    broken = deepcopy(supplier)
    broken['facts'][0]['source_ids'] = ['invented-source']
    with pytest.raises(ValidationError):
        RealSupplier.model_validate(broken)
    broken = deepcopy(supplier)
    broken['company_facts'][0]['source_ids'] = []
    with pytest.raises(ValidationError):
        RealSupplier.model_validate(broken)
    broken = deepcopy(supplier)
    broken['company_facts'][0]['key'] = broken['facts'][0]['key']
    with pytest.raises(ValidationError):
        RealSupplier.model_validate(broken)


def test_company_profiles_keep_matching_financial_periods_and_limits(client):
    suppliers = {s['id']: s for s in client.get('/api/real-case').json()['suppliers']}
    def field(sid, key):
        return next(f for f in suppliers[sid]['company_facts'] + suppliers[sid]['facts'] if f['key'] == key)
    assert field('prodservice', 'inn')['value'] == '6670170834'
    assert field('gurman', 'inn')['value'] == '7728383263'
    assert field('gostpp', 'ogrn')['value'] == '312715432600101'
    assert field('samovar', 'ogrn')['value'] == '314667911800082'
    assert '2025' in field('mozaretti', 'finance')['label']
    assert 'убыток' in field('mozaretti', 'finance')['value']
    assert '2024' in field('mozaretti', 'product_history')['value']
    assert 'текущей продукции не подтверждены' in field('mozaretti', 'product_history')['value']
    assert '771501001' in field('mozaretti', 'legal_address')['value']
    assert '263601001' in field('mozaretti', 'legal_address')['value']
    assert field('gostpp', 'finance')['value'] is None
    assert field('gostpp', 'finance')['missing_reason']
    assert 'не подтверждены' in field('prodservice', 'deferral')['value']


def test_draft_uses_real_supplier_and_does_not_record_sending(client):
    before = client.get('/api/kp').json()
    draft = client.post('/api/real-case/gostpp/draft').json()
    assert '200 кг' in draft['body'] and 'Екатеринбург' in draft['body']
    assert 'Туле' in draft['body'] and '[название вашей компании]' in draft['body']
    assert '.example' not in draft['body'] and 'Сушкоф' not in draft['body']
    assert client.get('/api/kp').json() == before
    assert client.post('/api/real-case/missing/draft').status_code == 404


def test_draft_does_not_use_address_from_another_city(client):
    profile = client.get('/api/profile').json()
    profile.update(city='almaty', address='Алматы, ул. Тестовая, 15')
    client.put('/api/profile', json=profile)
    draft = client.post('/api/real-case/prodservice/draft').json()
    assert profile['address'] not in draft['body']
    assert 'Екатеринбург, точный адрес согласуем' in draft['body']


def test_draft_uses_user_profile_after_demo_contacts_are_replaced(client):
    profile = client.get('/api/profile').json()
    profile.update(company='Тестовая сеть', email='buyer@test.ru', contact_name='Анна', phone='+79000000000', address='Екатеринбург, адрес кухни')
    client.put('/api/profile', json=profile)
    body = client.post('/api/real-case/mozaretti/draft').json()['body']
    assert 'Тестовая сеть' in body and 'buyer@test.ru' in body and profile['address'] in body
