#!/usr/bin/env python3
import sys
import uuid
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from legal_agent_harness.validation_app import (  # noqa: E402
    append_review,
    extract_graph,
    publish_hf,
    render_form,
    review_record,
)

# Журнал всегда в корне репозитория, независимо от каталога запуска.
JOURNAL = ROOT / "validation" / "reviews.jsonl"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Запас на текстовые поля формы и служебные заголовки multipart.
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024


def journal_label():
    try:
        return JOURNAL.relative_to(ROOT).as_posix()
    except ValueError:
        return str(JOURNAL)


def parse_form(content_type, body):
    message = BytesParser(policy=HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    fields, files = {}, {}
    if not message.is_multipart():
        return fields, files

    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name is None:
            continue

        data = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            files[name] = (part.get_filename(), data)
        elif data:
            fields.setdefault(name, data.decode("utf-8", "replace"))

    return fields, files


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send(render_form())

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            return self._send(render_form(message="Некорректный заголовок Content-Length"), 400)

        # Размер проверяется до чтения тела: слишком большой запрос не читается в память.
        if length > MAX_REQUEST_BYTES:
            self.close_connection = True
            return self._send(render_form(message="Файл больше 20 МБ"), 413)

        body = self.rfile.read(length)
        form, files = parse_form(self.headers.get("Content-Type", ""), body)

        if "document" not in files:
            return self._send(render_form(message="Не выбран файл документа"), 400)

        filename, data = files["document"]
        if len(data) > MAX_UPLOAD_BYTES:
            return self._send(render_form(message="Файл больше 20 МБ"), 400)

        try:
            score = int(form.get("score", "0"))
        except ValueError:
            return self._send(
                render_form(message="Оценка должна быть целым числом от 0 до 10"), 400
            )

        try:
            graph = extract_graph(
                filename,
                data,
                form.get("authority", "не указан"),
                form.get("jurisdiction", "RU"),
            )
        except Exception as exc:
            return self._send(
                render_form(message=f"Не удалось обработать файл {filename}: {exc}"), 400
            )

        try:
            review = review_record(
                graph, score, form.get("comments", ""), "review-" + uuid.uuid4().hex[:12]
            )
        except ValueError as exc:
            return self._send(render_form(graph, str(exc)), 400)

        try:
            append_review(JOURNAL, review)
        except OSError as exc:
            return self._send(
                render_form(graph, f"Не удалось записать журнал {journal_label()}: {exc}"), 500
            )

        token = form.get("hf_token", "")
        if token:
            status = publish_hf(review, token)[1]
        else:
            status = "публикация не выполнялась: HF-токен не указан"

        return self._send(
            render_form(graph, f"Оценка сохранена в журнал {journal_label()}; {status}")
        )

    def _send(self, body, status=200):
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print("Откройте http://127.0.0.1:%d" % port)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
