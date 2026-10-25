"""Автономный контур экспертной проверки извлечения поправок (stdlib-only)."""
from __future__ import annotations
import datetime as dt, hashlib, html, json, os, re, secrets, urllib.request, urllib.error, uuid, zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

PREDICATES = {"изменена", "дополнена", "введена", "изложена_в_новой_редакции", "признана_утратившей_силу", "исключена", "уточнена"}
MAX_UPLOAD = 20 * 1024 * 1024
FORM_DEFAULTS = {"authority": "не указан", "jurisdiction": "RU", "score": "", "comments": ""}

def _text_from_bytes(name: str, data: bytes) -> str:
    ext = Path(name).suffix.lower()
    if ext in (".txt", ".md", ".csv", ".xml"):
        return data.decode("utf-8", "replace")
    if ext in (".html", ".htm"):
        return html.unescape(re.sub(r"<[^>]+>", " ", data.decode("utf-8", "replace")))
    if ext == ".rtf":
        s = data.decode("utf-8", "replace")
        s = re.sub(r"\\'[0-9a-fA-F]{2}", "", s)
        return re.sub(r"\\[a-z]+\d*|[{}]", " ", s)
    if ext == ".docx":
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return html.unescape(re.sub(r"<[^>]+>", " ", xml))
    if ext == ".pdf":
        # Без сторонних библиотек извлекаем читаемые PDF-строки; OCR не обещается.
        s = data.decode("latin-1", "ignore")
        s = re.sub(r"\\[0-9]+", " ", s)
        return "\n".join(re.findall(r"[\x20-\x7eА-Яа-яЁё№«»„“”–—]{3,}", s))
    raise ValueError("Поддерживаются PDF, TXT, HTML, DOCX и RTF")

def _date(text: str) -> str | None:
    m = re.search(r"(?<!\d)(20\d{2})[.\-/](\d{1,2})(?:[.\-/](\d{1,2}))?", text)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m and m.group(3) else (f"{m.group(1)}-{int(m.group(2)):02d}" if m else None)

def extract_graph(filename: str, data: bytes, authority: str = "не указан", jurisdiction: str = "OTHER") -> Dict[str, Any]:
    text = _text_from_bytes(filename, data)
    digest = hashlib.sha256(data).hexdigest()
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    date = _date(text)
    edges: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    pred_re = re.compile(r"(изложить|изменить|дополнить|ввести|исключить|признать[^.]{0,50}утративш|уточнить)", re.I)
    art_re = re.compile(r"(?:стать[яю]|ст\.?)[\s№]*(\d+(?:\.\d+)?)", re.I)
    for i, line in enumerate(lines):
        am = art_re.search(line)
        pm = pred_re.search(line)
        if not (am or pm): continue
        article = am.group(1) if am else "не определена"
        raw = (pm.group(1).lower() if pm else "изменить")
        predicate = "дополнена" if "дополн" in raw else "введена" if "вве" in raw else "исключена" if "исключ" in raw else "признана_утратившей_силу" if "утратив" in raw else "уточнена" if "уточ" in raw else "изменена"
        if raw == "изложить":
            predicate = "изложена_в_новой_редакции"
        node_act, node_norm = f"act:{digest[:12]}", f"norm:{article}"
        records.append({"norm_id": f"document/article/{article}", "predicate": predicate, "subject": f"статья {article}", "object": filename, "effective_from": date, "effective_to": None, "change_description": line[:500], "confidence": 0.55 if not am or not pm else 0.75, "coordinate": f"строка {i+1}"})
        edges.append({"from": node_act, "to": node_norm, "relation": predicate, "evidence": line[:500], "coordinate": f"строка {i+1}"})
    if not records:
        records.append({"norm_id": "document/article/не определена", "predicate": "изменена", "subject": "не определено", "object": filename, "effective_from": date, "effective_to": None, "change_description": "Автоматическое извлечение не нашло явной формулы изменения.", "confidence": 0.1, "coordinate": "не определена"})
    return {"schema_version": 1, "document": {"filename": filename, "sha256": digest, "authority": authority, "jurisdiction": jurisdiction, "effective_date": date}, "nodes": [{"id": f"act:{digest[:12]}", "type": "amending_act", "label": filename}] + [{"id": f"norm:{r['norm_id'].split('/')[-1]}", "type": "norm", "label": r['subject']} for r in records], "edges": edges, "records": records, "text_stats": {"lines": len(lines), "characters": len(text)}}

def build_case(graph: Dict[str, Any]) -> Dict[str, Any]:
    r = graph["records"][0]; d = graph["document"]
    return {"case_id": "upload-" + uuid.uuid4().hex[:12], "source": {"authority": d["authority"], "jurisdiction": d["jurisdiction"], "doc_type": "amending_act", "identifier": d["filename"], "coordinate": r["coordinate"], "effective_date": d["effective_date"]}, "expected": {k: r[k] for k in ("norm_id", "predicate", "subject", "object", "effective_from", "effective_to", "change_description", "confidence")}}

def review_record(graph: Dict[str, Any], score: int, comments: str, session_id: str) -> Dict[str, Any]:
    if not isinstance(score, int) or not 0 <= score <= 10: raise ValueError("Оценка должна быть целым числом от 0 до 10")
    public = {"session_id": session_id, "created_at": dt.date.today().isoformat(), "score": score, "comments": comments[:2000], "document": {k: graph["document"][k] for k in ("filename", "sha256", "authority", "jurisdiction", "effective_date")}, "records": graph["records"], "edges": graph["edges"], "expert_approved": True}
    return public

def append_review(path: str | Path, review: Dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f: f.write(json.dumps(review, ensure_ascii=False) + "\n")

def render_form(result: Dict[str, Any] | None = None, message: str = "", values: Dict[str, str] | None = None) -> str:
    # Значения из values подставляются обратно в форму; токен не подставляется никогда.
    v = {k: html.escape(str((values or {}).get(k, d)), quote=True) for k, d in FORM_DEFAULTS.items()}
    result_html = ""
    if result:
        result_html = "<h2>Извлечение</h2><pre>" + html.escape(json.dumps(result, ensure_ascii=False, indent=2)) + "</pre>"
    return "<!doctype html><meta charset='utf-8'><title>Проверка НПА</title><h1>Экспертная проверка</h1><p>Учебная проверка, не юридическая консультация. Токен не сохраняется.</p><p>" + html.escape(message) + "</p><form method='post' enctype='multipart/form-data'><label>HF API token (только на время запроса) <input type='password' name='hf_token' autocomplete='off'></label><br><label>Орган <input name='authority' value='" + v["authority"] + "'></label><label> Юрисдикция <input name='jurisdiction' value='" + v["jurisdiction"] + "'></label><br><input type='file' name='document' required accept='.pdf,.txt,.html,.htm,.docx,.rtf'><br><label>Оценка 0–10 <input type='number' min='0' max='10' name='score' value='" + v["score"] + "' required></label><br><textarea name='comments' maxlength='2000' placeholder='Комментарии (необязательно)'>" + v["comments"] + "</textarea><br><button>Извлечь, сохранить и отправить</button></form>" + result_html

def publish_hf(review: Dict[str, Any], token: str, repo_id: str = "lawful-good-project/legal-agent-calibrations") -> Tuple[bool, str]:
    if not token or not re.match(r"^hf_[A-Za-z0-9]+$", token): return False, "HF-токен не задан или имеет неверный формат"
    payload = json.dumps(review, ensure_ascii=False).encode("utf-8")
    path = f"validation/{review['session_id']}.json"
    # Hugging Face commit API принимает multipart operations; сохраняем токен только в Authorization.
    boundary = "----legalagent" + secrets.token_hex(8)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=operations\r\n\r\n" + json.dumps([{"key": path, "value": payload.decode("utf-8")}], ensure_ascii=False) + f"\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(f"https://huggingface.co/api/datasets/{repo_id}/commit/main", data=body, method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp: return True, f"Опубликовано: {resp.status}"
    except Exception as exc: return False, f"Публикация не выполнена ({type(exc).__name__}); локальная запись сохранена"
