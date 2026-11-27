"""Offline regressions for paragraph-grounded quotes."""
import pytest

from test_notebook_extraction import extraction


PARAGRAPH = 'В статье 10 слова «старый порядок» заменить\nсловами «новый порядок рассмотрения заявлений».'
OTHER_PARAGRAPH = 'В статье 20 исключить положение об обязательном представлении дополнительных документов.'


@pytest.mark.parametrize('quote,removed', [
    ('об обязательном представлении дополнительных документов', True),
    ('исключить положение', True),
    ('несуществующая цитата', True),
    ('заменить словами «новый порядок', False),
    ('заменить\tсловами «новый порядок', False),
    ('старый порядок', False),
    ('', False),
])
def test_quote_belongs_to_confirmed_paragraph(extraction, quote, removed):
    ns, _ = extraction
    raw_record = {
        'norm_id': 'тест/ст.10', 'predicate': 'изменена',
        'subject': 'порядок', 'object': 'тестовый акт',
        'effective_from': None, 'effective_to': None, 'confidence': 0.8,
        'change_excerpt': PARAGRAPH, 'quote': quote,
    }
    doc = {'filename': 'synthetic.txt', 'text': PARAGRAPH + '\n\n' + OTHER_PARAGRAPH}
    hint = {'authority': 'Тест', 'jurisdiction': 'RU'}
    result = ns['validate']({'records': [raw_record]}, doc, hint)
    assert len(result['records']) == 1
    record = result['records'][0]
    assert record['paragraph'] == PARAGRAPH
    assert record['change_excerpt'] == PARAGRAPH
    assert record['quote'] == ('' if removed else quote)
    for key in ('norm_id', 'predicate', 'subject', 'object', 'effective_from', 'effective_to', 'confidence'):
        assert record[key] == raw_record[key]
    assert raw_record['quote'] == quote
    assert result['issues'] == ([
        'запись 1: короткая цитата удалена — она не найдена в абзаце поправки; сверьте текст изменения'
    ] if removed else [])


def test_unconfirmed_record_is_still_rejected(extraction):
    ns, _ = extraction
    result = ns['validate']({'records': [{
        'predicate': 'изменена', 'change_excerpt': 'Несуществующий абзац',
        'quote': 'Несуществующая цитата', 'effective_from': None, 'effective_to': None,
    }]}, {'filename': 'synthetic.txt', 'text': PARAGRAPH},
        {'authority': 'Тест', 'jurisdiction': 'RU'})
    assert result['records'] == []
    assert result['issues'] == ['запись 1: пропущена — фрагмент не найден дословно в тексте']
