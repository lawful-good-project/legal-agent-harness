import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from legal_agent_harness.validation_app import extract_graph, review_record, append_review

def test_extracts_directed_amendment_graph(tmp_path):
    data = "Федеральный закон. Статья 1252 дополнить с 2020-01-01".encode("utf-8")
    graph = extract_graph("act.txt", data, "Duma", "RU")
    assert graph["edges"][0]["from"].startswith("act:")
    assert graph["edges"][0]["to"] == "norm:1252"
    assert graph["records"][0]["predicate"] == "дополнена"
    assert graph["records"][0]["effective_from"] == "2020-01-01"

def test_review_is_bounded_and_append_only(tmp_path):
    graph = extract_graph("act.txt", b"Article 1 changed")
    review = review_record(graph, 10, "Checked", "session-1")
    assert review["expert_approved"] is True
    assert review["score"] == 10
    path = tmp_path / "reviews.jsonl"
    append_review(path, review)
    append_review(path, review)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2

def test_review_rejects_invalid_score():
    graph = extract_graph("act.txt", b"Article 1 changed")
    for score in (-1, 11):
        try:
            review_record(graph, score, "", "session-1")
        except ValueError:
            pass
        else:
            assert False

def test_review_redacts_secrets_in_comments_and_document_text():
    token = "hf_" + "a" * 30
    data = "Статья 1252 изменить. Контакт: ivan@example.com".encode("utf-8")
    graph = extract_graph("act.txt", data)
    review = review_record(graph, 5, f"пишите на expert@example.com, токен {token}", "session-1")
    blob = json.dumps(review, ensure_ascii=False)
    assert "expert@example.com" not in blob
    assert "ivan@example.com" not in blob
    assert token not in blob
    assert "[REDACTED]" in review["comments"]

def test_review_keeps_document_checksum_and_session_id():
    graph = extract_graph("act.txt", b"Article 1 changed")
    review = review_record(graph, 7, "", "review-act")
    assert review["document"]["sha256"] == graph["document"]["sha256"]
    assert len(review["document"]["sha256"]) == 64
    assert review["session_id"] == "review-act"
