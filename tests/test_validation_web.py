import importlib.util
import http.client
import json
import os
import threading
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "validation_web", ROOT / "scripts" / "validation_web.py"
)
validation_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validation_web)


def _multipart(fields, files):
    boundary = "----test" + uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")
    for name, (filename, data) in files.items():
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        )
        body += header.encode("utf-8") + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    return f"multipart/form-data; boundary={boundary}", body


def test_does_not_use_cgi():
    assert "cgi" not in vars(validation_web)


def test_parse_form_fields_and_binary_file():
    blob = os.urandom(4096) + b"\r\n--\r\n\r\n\n\r"
    content_type, body = _multipart(
        {"authority": "Госдума", "comments": ""},
        {"document": ("акт №1.docx", blob)},
    )
    fields, files = validation_web.parse_form(content_type, body)
    assert fields == {"authority": "Госдума"}
    assert files == {"document": ("акт №1.docx", blob)}


def test_parse_form_ignores_non_multipart():
    assert validation_web.parse_form("application/x-www-form-urlencoded", b"a=1") == ({}, {})


ACT = ("act.txt", "Статья 1252 изменить".encode("utf-8"))


def _request(tmp_path, body, headers):
    """POST в запущенный сервер; журнал пишется в tmp_path/validation/reviews.jsonl."""
    server = validation_web.HTTPServer(("127.0.0.1", 0), validation_web.Handler)
    server.RequestHandlerClass.log_message = lambda *args: None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root, journal = validation_web.ROOT, validation_web.JOURNAL
    validation_web.ROOT = tmp_path
    validation_web.JOURNAL = tmp_path / "validation" / "reviews.jsonl"
    try:
        port = server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/", body, headers)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        validation_web.ROOT, validation_web.JOURNAL = root, journal
        server.shutdown()
        server.server_close()


def _post(tmp_path, fields, files):
    content_type, body = _multipart(fields, files)
    return _request(tmp_path, body, {"Content-Type": content_type})


def test_post_saves_review_to_journal(tmp_path):
    status, page = _post(
        tmp_path, {"score": "7", "authority": "Госдума"}, {"document": ACT}
    )
    assert status == 200
    assert "Извлечение" in page
    journal = tmp_path / "validation" / "reviews.jsonl"
    review = json.loads(journal.read_text(encoding="utf-8"))
    assert review["score"] == 7
    assert review["document"]["filename"] == "act.txt"
    assert review["document"]["authority"] == "Госдума"


def test_post_without_token_reports_saved_review_not_error(tmp_path):
    status, page = _post(tmp_path, {"score": "5"}, {"document": ACT})
    assert status == 200
    assert "Оценка сохранена в журнал validation/reviews.jsonl" in page
    assert "публикация не выполнялась: HF-токен не указан" in page
    assert "неверный формат" not in page


def test_post_with_malformed_token_reports_publish_status(tmp_path):
    status, page = _post(
        tmp_path, {"score": "5", "hf_token": "not-a-token"}, {"document": ACT}
    )
    assert status == 200
    assert "Оценка сохранена в журнал" in page
    assert "HF-токен не задан или имеет неверный формат" in page


def test_post_without_document_returns_error_page(tmp_path):
    status, page = _post(tmp_path, {"score": "5"}, {})
    assert status == 400
    assert "Не выбран файл документа" in page
    assert not (tmp_path / "validation").exists()


def test_post_errors_explain_what_is_wrong(tmp_path):
    cases = [
        ({"score": "abc"}, ACT, "Оценка должна быть целым числом от 0 до 10"),
        ({"score": "11"}, ACT, "Оценка должна быть целым числом от 0 до 10"),
        (
            {"score": "5"},
            ("x.exe", b"MZ"),
            "Не удалось обработать файл x.exe: Поддерживаются PDF",
        ),
        ({"score": "5"}, ("a.docx", b"not a zip"), "Не удалось обработать файл a.docx:"),
    ]
    for fields, document, message in cases:
        status, page = _post(tmp_path, fields, {"document": document})
        assert status == 400
        assert message in page
        assert "Error" not in page
    assert not (tmp_path / "validation").exists()


def test_journal_is_in_repository_root_by_default():
    assert validation_web.JOURNAL == ROOT.resolve() / "validation" / "reviews.jsonl"


def test_journal_does_not_depend_on_working_directory(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cwd = os.getcwd()
    os.chdir(elsewhere)
    try:
        status, page = _post(tmp_path, {"score": "5"}, {"document": ACT})
    finally:
        os.chdir(cwd)
    assert status == 200
    assert "Оценка сохранена в журнал validation/reviews.jsonl" in page
    assert (tmp_path / "validation" / "reviews.jsonl").exists()
    assert not (elsewhere / "validation").exists()


def test_oversized_request_is_rejected_before_reading_body(tmp_path):
    # Заявлен размер больше лимита, а тело короткое: если бы сервер стал читать
    # тело целиком, он ждал бы недостающие байты до таймаута клиента.
    content_type, body = _multipart({"score": "5"}, {"document": ACT})
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(validation_web.MAX_REQUEST_BYTES + 1),
    }
    started = time.monotonic()
    status, page = _request(tmp_path, body, headers)
    assert status == 413
    assert "Файл больше 20 МБ" in page
    assert time.monotonic() - started < 5
    assert not (tmp_path / "validation").exists()


def test_invalid_content_length_is_rejected(tmp_path):
    status, page = _request(tmp_path, b"", {"Content-Length": "abc"})
    assert status == 400
    assert "Некорректный заголовок Content-Length" in page


def test_error_page_keeps_entered_values_but_not_token(tmp_path):
    fields = {
        "authority": "Государственная Дума ФС РФ",
        "jurisdiction": "US",
        "score": "11",
        "comments": "Дата вступления в силу неверна",
        "hf_token": "hf_doNotEchoThisToken",
    }
    status, page = _post(tmp_path, fields, {"document": ACT})
    assert status == 400
    assert "value='Государственная Дума ФС РФ'" in page
    assert "value='US'" in page
    assert "name='score' value='11'" in page
    assert ">Дата вступления в силу неверна</textarea>" in page
    assert "hf_doNotEchoThisToken" not in page


def test_success_page_keeps_authority_and_resets_score_and_comments(tmp_path):
    fields = {"authority": "Госдума", "jurisdiction": "RU", "score": "6", "comments": "ок"}
    status, page = _post(tmp_path, fields, {"document": ACT})
    assert status == 200
    assert "value='Госдума'" in page
    assert "name='score' value='' required" in page
    assert ">ок</textarea>" not in page
