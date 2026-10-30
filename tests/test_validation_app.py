import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from legal_agent_harness.validation_app import extract_graph, review_record, append_review

@pytest.mark.parametrize("article", ["Статью 1259", "Статья 1259", "ст. 1259", "ст 1259"])
def test_extracts_article_number(article):
    graph = extract_graph("act.txt", f"{article} дополнить пунктом 3.".encode("utf-8"))
    assert graph["records"][0]["norm_id"] == "document/article/1259"
    assert graph["edges"][0]["to"] == "norm:1259"
    assert graph["records"][0]["predicate"] == "дополнена"

@pytest.mark.parametrize("wording, predicate", [
    ("изложить в следующей редакции", "изложена_в_новой_редакции"),
    ("ИЗЛОЖИТЬ  В  СЛЕДУЮЩЕЙ  РЕДАКЦИИ", "изложена_в_новой_редакции"),
    ("изложить", "изложена_в_новой_редакции"),
    ("изменить", "изменена"),
])
def test_extracts_new_wording_predicate(wording, predicate):
    graph = extract_graph("act.txt", f"Статья 1259 {wording}.".encode("utf-8"))
    assert graph["records"][0]["predicate"] == predicate
    assert graph["edges"][0]["relation"] == predicate

@pytest.mark.parametrize("text, article", [
    # 303-ФЗ от 31.07.2020, ст. 7, п. 12:
    # https://www.consultant.ru/document/cons_doc_LAW_358791/6a73a7e61adc45fc3dd224c0e7194a1392c8b071/
    ("12) статью 11 изложить в следующей редакции:", "11"),
    # 137-ФЗ от 27.07.2006, ст. 1, пп. 5, 58:
    # https://rulaws.ru/laws/Federalnyy-zakon-ot-27.07.2006-N-137-FZ/
    ("5) статью 6.1 изложить в следующей редакции:", "6.1"),
    ("58) статью 81 изложить в следующей редакции:", "81"),
])
def test_extracts_real_amendment_fragments(text, article):
    graph = extract_graph("act.txt", text.encode("utf-8"))
    assert graph["records"][0]["norm_id"] == f"document/article/{article}"
    assert graph["records"][0]["predicate"] == "изложена_в_новой_редакции"
    assert graph["edges"][0]["to"] == f"norm:{article}"
    assert graph["edges"][0]["relation"] == "изложена_в_новой_редакции"

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
