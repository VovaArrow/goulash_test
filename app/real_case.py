"""Срез реальных источников: отдельный контракт без выдуманных реквизитов и баллов."""
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, HttpUrl, model_validator

from .models import KPDraft, Profile


class Source(BaseModel):
    id: str
    title: str
    url: HttpUrl
    checked_at: date


class Fact(BaseModel):
    key: str
    label: str
    value: str | None
    source_ids: list[str]
    missing_reason: str | None = None


class RealSupplier(BaseModel):
    id: str
    name: str
    kind: str
    decision: Literal['shortlist', 'clarify', 'unavailable', 'outside_region']
    decision_label: str
    reason: str
    caveats: list[str]
    phone: str
    email: str | None
    contact_source_id: str
    sources: list[Source]
    facts: list[Fact]
    company_facts: list[Fact]

    @model_validator(mode='after')
    def evidence_is_resolvable(self):
        ids = {s.id for s in self.sources}
        if len(ids) != len(self.sources) or self.contact_source_id not in ids:
            raise ValueError('Контакты должны иметь уникальный существующий источник')
        facts = self.facts + self.company_facts
        if len({f.key for f in facts}) != len(facts):
            raise ValueError('Поля поставщика должны иметь уникальные ключи')
        for fact in facts:
            if fact.value is not None and not fact.source_ids:
                raise ValueError('Опубликованному факту нужен источник')
            if not set(fact.source_ids) <= ids:
                raise ValueError('Неизвестный источник факта')
        return self


class RealCase(BaseModel):
    id: str
    title: str
    query: str
    checked_at: date
    method: str
    conclusion: str
    suppliers: list[RealSupplier]

    @model_validator(mode='after')
    def unique_suppliers(self):
        if len({s.id for s in self.suppliers}) != len(self.suppliers):
            raise ValueError('Повторяющийся поставщик')
        return self


@lru_cache(maxsize=1)
def load_real_case() -> RealCase:
    return RealCase.model_validate_json(
        Path(__file__).with_name('fixtures').joinpath('real_case.json').read_text(encoding='utf-8')
    )


def make_draft(supplier: RealSupplier, profile: Profile) -> KPDraft:
    demo_profile = profile.email.lower().endswith('.example')
    company = '[название вашей компании]' if demo_profile else profile.company
    contact = '[ваше имя]' if demo_profile else profile.contact_name or '[ваше имя]'
    phone = '[ваш телефон]' if demo_profile else profile.phone or '[ваш телефон]'
    email = '[ваш e-mail]' if demo_profile else profile.email or '[ваш e-mail]'
    address = (profile.address if profile.city == 'ekb' and not demo_profile
               else 'Екатеринбург, точный адрес согласуем')
    questions = [
        'цену в ₽/кг с НДС и без, срок действия предложения и наличие на ближайший месяц;',
        'минимальную партию, сумму заказа и возможность регулярных поставок по 200 кг в месяц;',
        'график и стоимость доставки, температурный режим и документы на перевозку;',
        'действующую декларацию на конкретный товар и условия оформления ВСД в «Меркурии»;',
        'возможность отсрочки 14 дней и предоставления образца для проработки.',
    ]
    extra = {
        'prodservice': 'Просим предложить именно сыр без ЗМЖ, а не молокосодержащий вариант Profi Cheese.',
        'mozaretti': 'Подскажите, работаете ли вы напрямую или через дистрибьютора на Урале. На сайте опубликован план мероприятий от 25.10.2024 по партии от 10.06.2024: просим сообщить результат мероприятий и прислать документы на текущую продукцию, а также актуальные реквизиты продавца.',
        'gostpp': 'Уточните маршрут со склада в Туле и сохранение холодовой цепи до Екатеринбурга.',
        'samovar': 'На сайте товар отсутствует. Когда следующая поставка? Какой порог бесплатной доставки актуален: 3000 или 4000 ₽?',
        'gurman': 'Сначала просим подтвердить возможность доставки охлаждённого товара в Екатеринбург: на сайте опубликованы условия для Москвы и области.',
    }
    body = '\n'.join([
        f'Добрый день, команда {supplier.name}!', '',
        f'Компания «{company}» подбирает сыр моцарелла для пиццы без ЗМЖ.',
        'Потребность: 200 кг в месяц. Город доставки: Екатеринбург.',
        f'Адрес: {address}.', '', 'Просим прислать коммерческое предложение:',
        *[f'— {q}' for q in questions], '', extra.get(supplier.id, ''), '',
        f'Контакт: {contact}.', f'Телефон: {phone}. E-mail: {email}.',
    ])
    return KPDraft(subject='Запрос КП: моцарелла без ЗМЖ, 200 кг/мес, Екатеринбург', body=body)
