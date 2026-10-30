"""Offline regressions for the standalone Colab publishing paths."""
import ast
import io
import json
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

NOTEBOOK = Path(__file__).resolve().parents[1] / 'notebooks/law_extract_fixed.ipynb'


def definitions():
    cells = json.loads(NOTEBOOK.read_text(encoding='utf-8'))['cells']
    source = '\n'.join(''.join(c['source']) for c in cells if c['cell_type'] == 'code' and not ''.join(c['source']).startswith('%'))
    tree = ast.parse(source)
    form = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FORM_HTML' for t in n.targets))
    publish = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'publish')
    return form, publish


@pytest.mark.parametrize('read_status', [200, 404])
def test_browser_submission(read_status):
    if not shutil.which('node'):
        pytest.skip('Node.js is required for the browser JavaScript regression')
    form, _ = definitions()
    converter = form[form.index('  function toUniversalRecord('):form.index('  function buildReview()')]
    handler = form[form.index('  $("send").addEventListener'):form.index('\n})();')]
    script = r'''
const assert = require('node:assert/strict');
const elements = Object.fromEntries(['send','hfToken','reviewId','expert','comments'].map(k => [k, {value:''}]));
elements.hfToken.value = 'hf_' + 'testonly'; elements.reviewId.value = 'rv-test'; elements.comments.value = 'Проверено';
const $ = k => elements[k];
const DATA = {review_id:'rv-test',document:{filename:'акт.txt'},method:'test'};
const REPO = 'example/dataset', RECORDS = [{norm_id:'ГК/1259',predicate:'изменена',change_excerpt:'Цитата'}];
const review = {comments:'Проверено', records:RECORDS};
const buildReview = () => ({review}); const markError = () => {};
let status = '', callback, posted;
const setStatus = message => {status = message;};
const window = {localStorage:{setItem(){}}};
elements.send.addEventListener = (event, fn) => {callback = fn;};
const fetch = async (url, options) => {
 if (options.method !== 'POST') return {ok:READ_STATUS===200,status:READ_STATUS,text:async()=>'{"old":"данные"}\n'};
 posted = options;
 assert.equal(options.headers['Content-Type'], 'application/x-ndjson');
 const rows = options.body.trim().split('\n').map(JSON.parse);
 assert.equal(rows[0].key, 'header'); assert.ok(rows[0].value.summary);
 assert.equal(rows[1].key, 'file'); assert.equal(rows[1].value.path,'records.jsonl');
 assert.equal(rows[1].value.encoding,'base64');
 const content = Buffer.from(rows[1].value.content,'base64').toString('utf8');
 const records = content.trim().split('\n').map(JSON.parse);
 assert.equal(records.at(-1).review.note_ru,'Проверено');
 assert.equal(records.at(-1).evidence_quote,'Цитата');
 if (READ_STATUS===200) assert.equal(records[0].old,'данные');
 assert.ok(!content.includes(elements.hfToken.value));
 return {ok:true,json:async()=>({commitUrl:'https://huggingface.co/datasets/example/dataset/discussions/1'})};
};
CONVERTER
HANDLER
(async()=>{
 await callback();
 assert.ok(posted, status); assert.ok(status.includes('/discussions/1'), status);
 assert.equal(elements.send.disabled,false);
 assert.equal(elements.hfToken.value,'');
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('READ_STATUS', str(read_status)).replace('CONVERTER', converter).replace('HANDLER', handler)
    result = subprocess.run(['node', '-'], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('read_status', [200, 404])
def test_python_submission(read_status):
    _, function = definitions()
    ns = {'CFG': {'repo':'example/dataset'}, 'json':json, 'urllib':__import__('urllib'),
          'base64':__import__('base64'), 'to_records_jsonl':lambda review,nick:'{"new":"Цитата"}\n'}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<publish>', 'exec'), ns)
    requests = []

    class Response(io.BytesIO):
        status = 200

    def urlopen(request, timeout):
        if isinstance(request, str) or request.get_method() == 'GET':
            if read_status != 200:
                raise urllib.error.HTTPError('https://huggingface.co/test', read_status, 'test', {}, io.BytesIO())
            return Response('{"old":"данные"}\n'.encode())
        requests.append(request)
        assert request.get_header('Content-type') == 'application/x-ndjson'
        rows = [json.loads(line) for line in request.data.splitlines()]
        assert rows[0]['key'] == 'header'
        assert rows[1]['key'] == 'file'
        assert rows[1]['value']['encoding'] == 'base64'
        content = __import__('base64').b64decode(rows[1]['value']['content']).decode()
        records = [json.loads(line) for line in content.splitlines()]
        assert records[-1] == {'new':'Цитата'}
        if read_status == 200:
            assert records[0] == {'old':'данные'}
        return Response(b'{"commitUrl":"https://huggingface.co/discussions/1"}')

    with patch.object(urllib.request, 'urlopen', urlopen):
        ok, message = ns['publish']({}, 'test-token')
    assert ok == (read_status in (200, 404))
    assert len(requests) == (1 if ok else 0)
    if ok:
        assert '/discussions/1' in message
