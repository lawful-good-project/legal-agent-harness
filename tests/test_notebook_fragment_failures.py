"""Offline regressions for complete-document extraction."""
import ast
import json
from types import SimpleNamespace
import urllib.request

import pytest

from test_notebook_extraction import extraction, extraction_tree


@pytest.mark.parametrize('failed,records', [
    ([], []), ([2], []), ([1, 2], []),
    ([2], [{'norm_id': 'тест/ст.1', 'predicate': 'изменена'}]),
])
def test_every_fragment_must_succeed(extraction, monkeypatch, failed, records):
    ns, client = extraction
    text = 'Статья 1. ' + 'Нейтральный текст. ' * 600 + '\nСтатья 2. Конец документа.'
    seen = []

    async def create(**kwargs):
        fragment = json.loads(kwargs['messages'][1]['content'])
        seen.append(fragment)
        if fragment['fragment_index'] in failed:
            raise RuntimeError('Synthetic provider failure')
        content = json.dumps({'source': {}, 'records': records})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client.AsyncClient = lambda: SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))

    def failed_fallback(*args, **kwargs):
        raise RuntimeError('Synthetic fallback failure')

    monkeypatch.setattr(urllib.request, 'urlopen', failed_fallback)
    if failed:
        with pytest.raises(RuntimeError) as error:
            ns['llm_extract'](text, {})
        assert str(error.value) == 'Не обработаны фрагменты ' + ', '.join(map(str, failed)) + ' из 2'
    else:
        raw, truncated = ns['llm_extract'](text, {})
        assert raw['records'] == []
        assert truncated is False
    assert sorted(f['fragment_index'] for f in seen) == [1, 2]
    assert all(f['fragment_count'] == 2 for f in seen)


def test_successful_provider_retry_is_not_a_failed_fragment(extraction):
    ns, client = extraction
    ns['G4F_MODELS_TO_PROBE'] = ['second-fake']
    seen = []

    async def create(**kwargs):
        seen.append(kwargs['model'])
        if kwargs['model'] == 'fake':
            raise RuntimeError('Synthetic failure')
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"records": []}'))])

    client.AsyncClient = lambda: SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))
    assert ns['llm_extract']('Нейтральный текст.', {}) == ({'source': {}, 'records': []}, False)
    assert seen == ['fake', 'second-fake']


@pytest.mark.parametrize('allow_fallback,recover', [(False, False), (True, False), (False, True)])
def test_document_retry_and_explicit_rules_fallback(extraction, allow_fallback, recover):
    ns, _ = extraction
    calls = []
    fallback_calls = []

    def extract(*args):
        calls.append(args)
        if not recover or len(calls) == 1:
            raise RuntimeError('Не обработаны фрагменты 2 из 2')
        return {'records': []}, False

    def rules(*args):
        fallback_calls.append(args)
        return {'records': [], 'method': 'rules-fallback'}

    tree = extraction_tree()
    default = next(n for n in tree.body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == 'ALLOW_RULES_FALLBACK' for t in n.targets))
    assert ast.literal_eval(default.value) is False
    pipeline = next(n for n in tree.body if isinstance(n, ast.If)
                    and isinstance(n.test, ast.Name) and n.test.id == 'DOCS')
    ns.update(llm_extract=extract, rule_extract=rules, EXTRACTED=[],
              ALLOW_RULES_FALLBACK=allow_fallback,
              DOCS=[{'text': 'Нейтральный текст.', 'filename': 'synthetic.txt'}],
              CFG={'authority': 'Тест', 'jurisdiction': 'RU', 'model': 'fake', 'max_chars': 10000},
              build_record=lambda doc, extracted, source: dict(extracted, edges=[]))
    exec(compile(ast.Module(body=[pipeline], type_ignores=[]), '<pipeline>', 'exec'), ns)
    assert len(calls) == 2
    assert len(fallback_calls) == int(allow_fallback and not recover)
    assert len(ns['EXTRACTED']) == int(allow_fallback or recover)
    if ns['EXTRACTED']:
        assert ns['EXTRACTED'][0]['method'] == ('llm:fake' if recover else 'rules-fallback')


