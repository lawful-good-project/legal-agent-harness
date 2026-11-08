"""Отзыв эксперта публикуется без секретов и e-mail (P41). Сеть не используется.

Regression: README-VALIDATION.md обещает, что экспертные записи «всегда
редактируются» и e-mail вырезается, но `review_record` сохранял комментарий
эксперта и фрагменты документа как есть. Раньше «пишите expert@example.com»
уходило в журнал и в публичный датасет, теперь там «пишите [REDACTED]», а
реквизиты поправки (норма, тип изменения, даты, sha256 документа) сохраняются.
"""

import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from legal_agent_harness.validation_app import (  # noqa: E402
    append_review,
    extract_graph,
    publish_hf,
    review_record,
)

# Собирается в коде, чтобы в репозитории не было строки, похожей на токен.
FAKE_TOKEN = "hf_" + "A1b2" * 8
DOCUMENT = (
    "Статью 1252 изложить в новой редакции с 2017-10-01.\n"
    "Статью 1259 дополнить пунктом 3 (исполнитель ivanov@example.com).\n"
)
COMMENT = f"Проверил. Вопросы: expert@example.com, токен {FAKE_TOKEN}"
LEAKS = ("expert@example.com", "ivanov@example.com", FAKE_TOKEN)
RECORD_FIELDS = (
    "norm_id",
    "predicate",
    "subject",
    "object",
    "effective_from",
    "effective_to",
    "confidence",
    "coordinate",
)


def _graph():
    return extract_graph("act.txt", DOCUMENT.encode("utf-8"), "Госдума", "RU")


def _review(graph):
    return review_record(graph, 7, COMMENT, "review-test")


def test_regression_comment_has_no_email_or_token():
    review = _review(_graph())
    assert review["comments"] == "Проверил. Вопросы: [REDACTED], токен [REDACTED]"


def test_document_fragments_have_no_email():
    review = _review(_graph())
    blob = json.dumps(review, ensure_ascii=False)
    for leak in LEAKS:
        assert leak not in blob


def test_email_at_length_limit_is_not_cut_in_half():
    # Раньше обрезка до 2000 символов шла до редакции и оставляла «ivanov@ex».
    review = review_record(
        _graph(), 7, "x" * 1990 + " ivanov@example.com", "review-test"
    )
    assert len(review["comments"]) <= 2000
    assert "ivanov" not in review["comments"]


def test_review_keeps_legal_metadata():
    graph = _graph()
    review = _review(graph)
    assert review["document"] == graph["document"]
    assert len(review["document"]["sha256"]) == 64
    assert len(review["records"]) == len(graph["records"]) == 2
    for got, want in zip(review["records"], graph["records"]):
        for field in RECORD_FIELDS:
            assert got[field] == want[field]
    assert review["records"][0]["norm_id"] == "document/article/1252"
    assert review["records"][0]["predicate"] == "изложена_в_новой_редакции"
    assert review["records"][0]["effective_from"] == "2017-10-01"
    assert [e["relation"] for e in review["edges"]] == [
        e["relation"] for e in graph["edges"]
    ]
    assert review["score"] == 7
    assert review["expert_approved"] is True


def test_review_does_not_modify_graph():
    graph = _graph()
    _review(graph)
    assert "ivanov@example.com" in graph["records"][1]["change_description"]


def test_journal_and_published_payload_are_clean(tmp_path, monkeypatch):
    review = _review(_graph())
    journal = tmp_path / "reviews.jsonl"
    append_review(journal, review)
    sent = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        sent.append(request.data.decode("utf-8"))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    ok, _ = publish_hf(review, "hf_" + "testonly")
    assert ok and len(sent) == 1
    for text in (journal.read_text(encoding="utf-8"), sent[0]):
        for leak in LEAKS:
            assert leak not in text
        assert "document/article/1252" in text
        assert review["document"]["sha256"] in text
