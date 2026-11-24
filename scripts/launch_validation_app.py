#!/usr/bin/env python3
"""Минимальный запуск контура. Основное API доступно как библиотека."""
import argparse, json, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from legal_agent_harness.validation_app import extract_graph, build_case, review_record, append_review, publish_hf

def main(argv=None):
    p=argparse.ArgumentParser(description="Экспертная проверка извлечения поправок")
    p.add_argument("document"); p.add_argument("--authority",default="не указан"); p.add_argument("--jurisdiction",default="OTHER")
    p.add_argument("--score",type=int); p.add_argument("--comments",default=""); p.add_argument("--journal",default="validation/reviews.jsonl")
    p.add_argument("--hf-token",default=""); p.add_argument("--publish",action="store_true")
    a=p.parse_args(argv); data=Path(a.document).read_bytes(); graph=extract_graph(Path(a.document).name,data,a.authority,a.jurisdiction)
    print(json.dumps(graph,ensure_ascii=False,indent=2))
    if a.score is not None:
        # Имя файла может содержать ПДн и уходит в путь файла на HF — как в веб-форме, берём uuid.
        review=review_record(graph,a.score,a.comments,"review-"+uuid.uuid4().hex[:12])
        append_review(a.journal,review)
        if a.publish: print(publish_hf(review,a.hf_token))
    return 0
if __name__ == "__main__": raise SystemExit(main())
