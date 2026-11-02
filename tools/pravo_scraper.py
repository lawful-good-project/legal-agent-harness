#!/usr/bin/env python

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

# --- Настройки ---------------------------------------------------------------

HOST = "publication.pravo.gov.ru"
USER_AGENT = "LegalCoursePravoScraper/1.0"

# Защита государственного портала от случайной перегрузки.
MIN_DELAY = 0.5
MAX_RETRY_AFTER = 60

MIB = 1024 * 1024
MAX_PDF_BYTES = 64 * MIB
MAX_JSON_BYTES = 8 * MIB
MAX_TEXT_BYTES = 8 * MIB
MAX_ROBOTS_BYTES = 256 * 1024
PDFTOTEXT_TIMEOUT = 60
OCR_DPI = 200
OCR_PAGE_TIMEOUT = 60
OCR_RENDER_TIMEOUT = 600
MAX_OCR_PAGES = 300

RESOLVE_MAX_PAGES = 3
SEARCH_PAGE_SIZE = 100
NAME_SEARCH_PAGE_SIZE = 200
MIN_SUBJECT_WORDS = 3
QUOTE_RADIUS = 160
TITLE = "заголовок"

# Заголовки сводных актов: сами изменяемые акты названы только в тексте.
OMNIBUS_TITLES = (
    "отдельные законодательные акты",
    "некоторые законодательные акты",
    "отдельные акты",
    "некоторые акты",
)

# Фильтр периода API и поле карточки, по которому его можно проверить.
PERIOD_FILTERS = (
    ("PublishDate", "publishDateShort"),
    ("DocumentDate", "documentDate"),
)

# --- Шаблоны реквизитов ------------------------------------------------------

EO_NUMBER = re.compile(r"[0-9]{16}\Z")
DASHES = re.compile(r"[–—]")
QUOTES = re.compile(r"[«»„“”]")
REASON_CLAUSE = re.compile(r"\bв\s+(?:связи|целях)\b[^\"]{0,80}$")
SUBJECT_TITLE = re.compile(r"(?:об\s+утверждении|об?)\s+(\S+)\s+(.+)")
AMENDING_TITLE = re.compile(r"о\s+(?:внесении|признании|приостановлении)\b")
MONTH_NAMES = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
MONTHS = {name: number for number, name in enumerate(MONTH_NAMES, 1)}

# Границы слова обязательны: без них «указанным» читается как указ,
# а «законодательства» как закон.
ACT_WORD = (
    r"\b(?:постановлени\w*|распоряжени\w*|"
    r"(?:указ|приказ|закон)(?:а|у|ом|е|ы|ов|ами)?\b)"
)
FEDERAL_ACT = (
    r"\bфедеральн(?:ый|ого|ому|ым|ом|ые|ых|ыми)\s+"
    r"(?:конституционн(?:ый|ого|ому|ым|ом|ые|ых|ыми)\s+)?"
    r"закон(?:а|у|ом|е|ы|ов|ами)?\b"
)
# Реквизиты не должны перетекать через другой вид акта или конец предложения,
# иначе «закона. Утверждено приказом ... № 5» станет законом № 5.
LABEL = (
    rf"(?:{FEDERAL_ACT}|{ACT_WORD})"
    rf"(?:(?!{ACT_WORD}|(?<!г)\.\s)[^«»\"\n;]){{0,180}}?"
)
REFERENCE = re.compile(
    rf"(?P<label>{LABEL})\s+от\s+"
    r"(?P<date>[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}|"
    r"[0-9]{1,2}\s+(?:" + "|".join(MONTHS) + r")\s+[0-9]{4})"
    r"\s*(?:года|г\.)?\s*(?:№|N)\s*"
    r"(?P<number>(?=\S*[0-9])[0-9А-Яа-яA-Za-z][0-9А-Яа-яA-Za-z/@–—-]*)",
    re.IGNORECASE,
)
# Порядок задаёт приоритет. Только формы действия: «дополнительные»,
# «за исключением» и «уточненный» поправками не являются.
PREDICATES = tuple(
    (re.compile(pattern, re.IGNORECASE), value)
    for pattern, value in (
        (r"призна\w*\s+утративш\w*\s+силу", "признана_утратившей_силу"),
        (r"излож\w*\s+в\s+новой\s+редакции", "изложена_в_новой_редакции"),
        (r"\bдополн(?:ить|ив|ен[аоы]?|ени[ия])\b", "дополнена"),
        (r"\bисключ(?:ить|ив|[её]н[аоы]?)\b", "исключена"),
        (r"\bввести\b", "введена"),
        (r"\bуточн(?:ить|ив|[её]н[аоы]?)\b", "уточнена"),
        (
            (
                r"\b(?:внес\w*.{0,80}изменени\w*"
                r"|изменени\w*,?\s+которые\W+вносятся|изменить)"
            ),
            "изменена",
        ),
    )
)
# Зачин поправки в сводном акте: «Статья 3 В пункте 1 статьи 66 Федерального
# закона от ...». Глагол стоит после длинной скобки с источниками публикации.
AMENDING_LEAD = re.compile(
    r"(?:\bвнести\s+в\b[^;]{0,200}"
    r"|(?:\bстатья\s+[0-9]+(?:\.[0-9]+)?\s+|(?:^|\s)[0-9]+\.\s*)в\s+"
    r"(?:(?:абзац|пункт|подпункт|част|стать|наименовани|приложени|преамбул|"
    r"перечн|правил|положени|порядк|таблиц|раздел|форм)\w*\b[^;]{0,200})?)$",
    re.IGNORECASE,
)
OWN_ARTICLE_TAIL = re.compile(r"\s+(?:внести|в)\b", re.IGNORECASE)
COORDINATE = re.compile(
    r"\b(?:стать[яиюе]|(?:под)?пункт[а-я]*)\s+"
    r"(?:[0-9]+(?:\.[0-9]+)*|[«\"'][а-я][»\"'])",
    re.IGNORECASE,
)


class ScraperError(Exception):
    """Ожидаемая ошибка загрузки или контракта портала."""


# --- Даты --------------------------------------------------------------------


def iso_day(value):
    return date.fromisoformat(value[:10]).isoformat()


def api_day(value):
    """ISO-дата в формат API: даты ГГГГ-ММ-ДД портал молча игнорирует."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Ожидалась дата ГГГГ-ММ-ДД")
    return date.fromisoformat(value).strftime("%d.%m.%Y")


def reference_date(value):
    if "." in value:
        day, month, year = map(int, value.split("."))
    else:
        day, month, year = value.casefold().split()
        day, month, year = int(day), MONTHS[month], int(year)
    return date(year, month, day).isoformat()


# --- HTTP-клиент -------------------------------------------------------------


def check_url(url, scheme):
    parsed = urlsplit(url)
    if (
        parsed.scheme != scheme
        or parsed.hostname != HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ScraperError(
            "Разрешён только выбранный протокол и publication.pravo.gov.ru"
        )


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, urlsplit(req.full_url).scheme)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    """Последовательный клиент портала с паузами, повторами и лимитами."""

    def __init__(
        self,
        base_url=f"https://{HOST}",
        timeout=30,
        delay=1,
        retries=2,
        max_bytes=MAX_PDF_BYTES,
        opener=None,
        sleep=time.sleep,
        use_proxy=True,
    ):
        check_url(base_url, urlsplit(base_url).scheme)
        if base_url not in (f"https://{HOST}", f"http://{HOST}"):
            raise ScraperError("Неверный адрес портала")
        if not all(math.isfinite(x) for x in (timeout, delay)):
            raise ScraperError("timeout и delay должны быть конечными числами")
        if timeout <= 0 or delay < MIN_DELAY or not 0 <= retries <= 5 or max_bytes <= 0:
            raise ScraperError("Неверные ограничения запросов")
        self.base_url, self.timeout, self.delay = base_url, timeout, delay
        self.retries, self.max_bytes = retries, max_bytes
        handlers = [SameOriginRedirect()]
        if not use_proxy:
            handlers.append(ProxyHandler({}))
        self.opener = opener or build_opener(*handlers)
        self.sleep = sleep
        self.robots = None
        self.requests = 0

    def document_url(self, eo_number):
        return f"{self.base_url}/document/{eo_number}"

    def get(self, path, params=None, limit=None):
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        self._check_robots(path, url)
        cap = min(limit or self.max_bytes, self.max_bytes)
        for attempt in range(self.retries + 1):
            if self.requests:
                self.sleep(self.delay)
            self.requests += 1
            try:
                return self._fetch(url, path, cap)
            except HTTPError as exc:
                wait = self._retry_wait(exc, path, attempt)
            except (OSError, http.client.HTTPException):
                if attempt == self.retries:
                    raise ScraperError(
                        f"Не удалось загрузить {path}: проверьте доступ к порталу"
                    ) from None
                wait = 2**attempt
            self.sleep(wait)
        raise AssertionError("Недостижимая ветвь")

    def _check_robots(self, path, url):
        if self.robots is None and path != "/robots.txt":
            body = self.get("/robots.txt", limit=MAX_ROBOTS_BYTES)
            robots = RobotFileParser()
            robots.parse(body.decode("utf-8").splitlines())
            self.robots = robots
        if self.robots is not None and not self.robots.can_fetch(USER_AGENT, url):
            raise ScraperError(f"robots.txt запрещает загрузку {path}")

    def _fetch(self, url, path, cap):
        request = Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            check_url(response.geturl(), urlsplit(self.base_url).scheme)
            length = response.headers.get("Content-Length")
            if length and int(length) > cap:
                raise ScraperError(f"Ответ {path} превышает лимит {cap} байт")
            body = response.read(cap + 1)
        if len(body) > cap:
            raise ScraperError(f"Ответ {path} превышает лимит {cap} байт")
        # read(n) молча возвращает обрыв соединения как короткий ответ.
        if length and len(body) != int(length):
            raise http.client.IncompleteRead(body, int(length) - len(body))
        return body

    def _retry_wait(self, error, path, attempt):
        """Пауза перед повтором; для неповторяемых ошибок — ScraperError."""
        temporary = error.code == 429 or 500 <= error.code < 600
        retry_after = error.headers.get("Retry-After", "")
        error.close()
        if not temporary or attempt == self.retries:
            raise ScraperError(f"HTTP {error.code} при загрузке {path}") from None
        if not retry_after.isdigit():
            return 2**attempt
        if int(retry_after) > MAX_RETRY_AFTER:
            raise ScraperError(
                f"Портал просит подождать {retry_after} с; сбор остановлен"
            ) from None
        return int(retry_after)

    def json(self, path, params=None):
        try:
            result = json.loads(
                self.get(path, params, limit=MAX_JSON_BYTES).decode("utf-8-sig")
            )
        except (ValueError, UnicodeError):
            raise ScraperError(f"Портал вернул некорректный JSON: {path}") from None
        if not isinstance(result, dict):
            raise ScraperError(f"Ожидался JSON-объект: {path}")
        return result

    def document(self, eo_number):
        if not EO_NUMBER.fullmatch(eo_number):
            raise ScraperError("Номер опубликования должен содержать 16 цифр")
        result = self.json("/api/Document", {"eoNumber": eo_number})
        if result.get("eoNumber") != eo_number:
            raise ScraperError("Карточка API не соответствует запрошенному номеру")
        return result

    def listing(self, params, max_pages):
        """Страницы выдачи: пары (новые карточки, выдача закончилась)."""
        seen = set()
        for page in range(1, max_pages + 1):
            result = self.json("/api/Documents", {**params, "Index": page})
            items, pages = result.get("items"), result.get("pagesTotalCount")
            if not isinstance(items, list) or type(pages) is not int or pages < 0:
                raise ScraperError(
                    "Изменился контракт /api/Documents: items/pagesTotalCount"
                )
            if result.get("currentPage") != page:
                raise ScraperError("API вернул другую страницу")
            fresh = []
            for item in items:
                if not isinstance(item, dict) or not EO_NUMBER.fullmatch(
                    str(item.get("eoNumber", ""))
                ):
                    raise ScraperError("В списке отсутствует корректный eoNumber")
                check_period(params, item)
                if item["eoNumber"] not in seen:
                    fresh.append(item)
                    seen.add(item["eoNumber"])
            if items and not fresh:
                raise ScraperError("API повторил страницу; обход остановлен")
            yield fresh, page >= pages
            if page >= pages:
                break
            if not items:
                raise ScraperError("API вернул пустую промежуточную страницу")


def check_period(params, item):
    """API может молча игнорировать фильтр: сверяем фактическую дату карточки."""
    for prefix, field in PERIOD_FILTERS:
        start, end = params.get(prefix + "From"), params.get(prefix + "To")
        if not start and not end:
            continue
        actual = date.fromisoformat(iso_day(item[field]))
        if (start and actual < datetime.strptime(start, "%d.%m.%Y").date()) or (
            end and actual > datetime.strptime(end, "%d.%m.%Y").date()
        ):
            raise ScraperError("API вернул документ вне заданного периода")


# --- Извлечение реквизитов ---------------------------------------------------


def normalize_number(value):
    return DASHES.sub("-", str(value)).strip().rstrip("-")


def label_key(label):
    """Вид акта и орган из подписи; регистр и варианты тире не различаются."""
    label = " ".join(DASHES.sub("-", label.casefold().replace("ё", "е")).split())
    if label.startswith("федеральн"):
        kind = "федеральный конституционный закон"
        return (
            kind if "конституционн" in label else "федеральный закон",
            "Российская Федерация",
        )
    for stem, kind in (
        ("постановлени", "постановление"),
        ("распоряжени", "распоряжение"),
        ("приказ", "приказ"),
        ("указ", "указ"),
        ("закон", "закон"),
    ):
        if label.startswith(stem):
            return kind, re.sub(r"^\S+\s*", "", label)
    return label, ""


def target_key(target):
    return (
        target["document_type"],
        target["authority"],
        target["document_date"],
        target["number"].casefold(),
    )


def document_key(raw):
    """Реквизиты самой карточки в том же виде, что и у извлечённых ссылок."""
    match = REFERENCE.search((raw.get("complexName") or "").split("\n")[0])
    if not match:
        return None
    kind, issuer = label_key(match["label"])
    number = normalize_number(raw["number"]).casefold()
    return kind, issuer, iso_day(raw["documentDate"]), number


def is_own_reference(key, own_key):
    """В шапке скана орган стоит отдельно: «ПОСТАНОВЛЕНИЕ от ... № 1»."""
    if not own_key:
        return False
    kind, issuer, doc_date, number = key
    own_kind, own_issuer, own_date, own_number = own_key
    return (kind, doc_date, number) == (own_kind, own_date, own_number) and (
        issuer in ("", own_issuer)
    )


def find_predicate(context, nearest=False):
    """Тип изменения: по приоритету PREDICATES либо ближайший к концу контекста."""
    hits = [
        (match.end(), value)
        for pattern, value in PREDICATES
        for match in pattern.finditer(context)
    ]
    if not hits:
        return None
    return max(hits, key=lambda hit: hit[0])[1] if nearest else hits[0][1]


def body_predicate(before, quote):
    """Зачин «Внести в» или «Статья 3 В пункте» точнее соседних глаголов."""
    return "изменена" if AMENDING_LEAD.search(before) else find_predicate(quote)


def is_own_article(match):
    """«Статья 1 Внести в ...» — статья изменяющего акта, а не изменяемого."""
    tail = match.string[match.end() :]
    return match.group(0).casefold().startswith("статья") and bool(
        OWN_ARTICLE_TAIL.match(tail)
    )


def find_coordinate(before, context):
    """Координата действия обычно стоит перед реквизитами: берём ближайшую."""
    norms = [
        match for match in COORDINATE.finditer(before) if not is_own_article(match)
    ]
    norm = norms[-1] if norms else COORDINATE.search(context)
    return norm.group(0) if norm and not is_own_article(norm) else None


def make_reference(target, predicate, norm, source_url, coordinate, quote):
    return {
        "target": target,
        "relation": "amendment_candidate" if predicate else "cites",
        "predicate": predicate,
        "norm_coordinate": norm,
        "effective_from": None,
        "effective_to": None,
        "evidence": {
            "source_url": source_url,
            "coordinate": coordinate,
            "quote": quote,
        },
        "resolution": "not_requested",
        "candidate_eo_numbers": [],
        "target_eo_number": None,
        "target_url": None,
        "needs_expert_approval": True,
    }


def extract_references(text, coordinate, source_url, own_key=None):
    """Реквизиты и кандидаты в поправки; не юридически подтверждённые связи."""
    references = []
    is_title = coordinate == TITLE
    # Переносы строк OCR не должны разрывать реквизиты.
    text = " ".join(text.split())
    for match in REFERENCE.finditer(text):
        try:
            doc_date = reference_date(match["date"])
        except ValueError:
            continue
        kind, issuer = label_key(match["label"])
        number = normalize_number(match["number"])
        if is_own_reference((kind, issuer, doc_date, number.casefold()), own_key):
            continue
        start = max(0, match.start() - QUOTE_RADIUS)
        before = text[start : match.start()]
        quote = text[start : match.end() + QUOTE_RADIUS]
        # В заголовке учитываем только текст ДО ссылки: название вложенного
        # акта само может содержать «О внесении изменений». Действие относится
        # к ближайшему акту: «О признании утратившим силу А и внесении изменений в Б».
        action_context = before if is_title else quote
        predicate = (
            find_predicate(before, nearest=True)
            if is_title
            else body_predicate(before, quote)
        )
        references.append(
            make_reference(
                {
                    "document_type": kind,
                    "authority": issuer,
                    "document_date": doc_date,
                    "number": number,
                },
                predicate,
                find_coordinate(before, action_context),
                source_url,
                coordinate,
                quote,
            )
        )
    return references


def unique_references(references):
    """Убирает точные повторы, сохраняя упоминания на разных страницах."""
    unique = {}
    for reference in references:
        key = json.dumps(
            [reference["target"], reference["relation"], reference["evidence"]],
            sort_keys=True,
        )
        unique[key] = reference
    return list(unique.values())


# --- Поиск актов на портале --------------------------------------------------


class Resolver:
    """Находит упомянутый акт по реквизитам; при сомнениях URL не выдаёт."""

    def __init__(self, client):
        self.client, self.cache = client, {}

    def resolve(self, reference):
        target = reference["target"]
        key = target_key(target)
        if key not in self.cache:
            self.cache[key] = self._search(target, key)
        status, matches = self.cache[key]
        reference["resolution"] = status
        reference["candidate_eo_numbers"] = [item["eoNumber"] for item in matches]
        if status == "matched_by_requisites":
            eo_number = matches[0]["eoNumber"]
            reference["target_eo_number"] = eo_number
            reference["target_url"] = self.client.document_url(eo_number)

    def _search(self, target, key):
        day = api_day(target["document_date"])
        params = {
            "DocumentDateFrom": day,
            "DocumentDateTo": day,
            "Number": target["number"],
            "NumberSearchType": 0,
            "PageSize": SEARCH_PAGE_SIZE,
        }
        matches, complete = [], False
        for items, complete in self.client.listing(params, RESOLVE_MAX_PAGES):
            matches.extend(item for item in items if document_key(item) == key)
        if not complete:
            return "search_truncated", matches
        if not matches:
            return "not_found", matches
        return ("matched_by_requisites" if len(matches) == 1 else "ambiguous"), matches


def date_variants(day):
    """Написания даты в заголовках актов, без повторов."""
    month = MONTH_NAMES[day.month - 1]
    return list(
        dict.fromkeys(
            (
                day.strftime("%d.%m.%Y"),
                f"{day.day} {month} {day.year}",
                f"{day.day:02d} {month} {day.year}",
            )
        )
    )


def amends_by_title(item, key):
    return any(
        target_key(reference["target"]) == key
        and reference["relation"] == "amendment_candidate"
        for reference in extract_references(item.get("name") or "", TITLE, "")
    )


def find_amendments(client, seeds, max_pages, limit):
    """Ограниченный поиск входящих поправок по дате в заголовке, затем сверка.

    Name — рабочий параметр API, ComplexName портал молча игнорирует. Акты без
    реквизитов в заголовке не находятся, полнота истории не гарантируется.
    """
    found, complete = {}, True
    for raw in seeds:
        key = document_key(raw)
        if key is None:
            complete = False
            continue
        for value in date_variants(date.fromisoformat(key[2])):
            params = {"Name": value, "PageSize": SEARCH_PAGE_SIZE}
            for items, search_complete in client.listing(params, max_pages):
                for item in items:
                    if amends_by_title(item, key):
                        found[item["eoNumber"]] = item
                if len(found) >= limit:
                    return list(found.values())[:limit], False
            complete = complete and search_complete
    return list(found.values()), complete


def normalize_title(text):
    text = QUOTES.sub('"', text.casefold().replace("ё", "е"))
    return " ".join(DASHES.sub("-", text).split())


def law_title(raw):
    """Название закона из карточки; у законов о поправках своего имени нет."""
    title = " ".join((raw.get("complexName") or "").split("\n")[1:]).strip()
    title = title.strip(' "«»')
    if not title or AMENDING_TITLE.match(normalize_title(title)):
        return None
    return title


def is_federal_law(key):
    return key is not None and key[0].startswith("федеральный")


def title_predicate(before):
    """Действие перед упоминанием акта в заголовке; None, если это не поправка.

    Нечётные кавычки: акт назван внутри названия другого акта.
    «В связи с принятием закона» называет повод, а не изменяемый акт.
    """
    if before.lstrip('"').count('"') % 2 or REASON_CLAUSE.search(before):
        return None
    return find_predicate(before, nearest=True)


def named_amendment(item, title):
    """Тип изменения, если заголовок изменяет закон с этим названием."""
    name = normalize_title(item.get("name") or "")
    pattern = r"\bзакон\w*\s+\"" + re.escape(normalize_title(title)) + '"'
    for match in re.finditer(pattern, name):
        predicate = title_predicate(name[: match.start()])
        if predicate:
            return predicate
    return None


def title_subject(title):
    """Предмет акта: «Об утверждении Правил X» даёт ("правил", "X")."""
    match = SUBJECT_TITLE.fullmatch(normalize_title(title))
    if not match:
        return None
    word, rest = match.groups()
    rest = re.split(r"\s+и\s+об?\s+", rest)[0].strip(' ".')
    return (word, rest) if len(rest.split()) >= MIN_SUBJECT_WORDS else None


def subject_amendment(item, subject):
    """Тип изменения, если заголовок изменяет документ с тем же предметом.

    «О перечне X» изменяют актом «О внесении изменений в перечень X».
    """
    word, rest = subject
    name = normalize_title(item.get("name") or "")
    position = name.find(f" {rest}")
    if position < 0:
        return None
    before = name[:position]
    # Слово в другом падеже: сверяем основу, «перечне» и «перечень», «правил» и «правила».
    if not before.split() or not before.split()[-1].startswith(word[:-2]):
        return None
    return title_predicate(before)


def named_search(raw, key, title):
    """Параметры поиска по названию и проверка совпадения для одного акта."""
    params = {
        "DocumentTypes": raw["documentTypeId"],
        "PageSize": NAME_SEARCH_PAGE_SIZE,
    }
    if is_federal_law(key):
        return {**params, "Name": title}, lambda item: named_amendment(item, title)
    subject = title_subject(title)
    if subject is None:
        return None, None
    params = {
        **params,
        "Name": subject[1],
        "SignatoryAuthorityId": raw["signatoryAuthorityId"],
    }
    return params, lambda item: subject_amendment(item, subject)


def find_named_amendments(client, seeds, max_pages, limit):
    """Поправки, которые заголовок называет именем закона или предметом акта.

    Совпадение названия слабее совпадения реквизитов, поэтому такие связи
    помечаются matched_by_title.
    """
    found, complete = {}, True
    for raw in seeds:
        key, title = document_key(raw), law_title(raw)
        if key is None:
            complete = False
            continue
        if title is None:
            continue
        params, predicate_of = named_search(raw, key, title)
        if params is None:
            continue
        for items, search_complete in client.listing(params, max_pages):
            for item in items:
                predicate = predicate_of(item)
                newer = iso_day(item["documentDate"]) >= key[2]
                if predicate and newer and item["eoNumber"] != raw["eoNumber"]:
                    found.setdefault(item["eoNumber"], (item, []))[1].append(
                        (raw, predicate)
                    )
            if len(found) >= limit:
                return found, False
        complete = complete and search_complete
    return found, complete


def omnibus_candidates(client, raw, scan, max_pages):
    """Сводные акты того же вида и органа, вышедшие после публикации акта."""
    params = {
        "DocumentTypes": raw["documentTypeId"],
        "SignatoryAuthorityId": raw["signatoryAuthorityId"],
        "PublishDateFrom": api_day(scan.date_from or iso_day(raw["publishDateShort"])),
        "PageSize": NAME_SEARCH_PAGE_SIZE,
    }
    if scan.date_to:
        params["PublishDateTo"] = api_day(scan.date_to)
    candidates, complete = {}, True
    for phrase in OMNIBUS_TITLES:
        for items, search_complete in client.listing(
            {**params, "Name": phrase}, max_pages
        ):
            for item in items:
                if item["eoNumber"] != raw["eoNumber"]:
                    candidates.setdefault(item["eoNumber"], item)
        complete = complete and search_complete
    return candidates, complete


def amends_in_text(text, key):
    return any(
        target_key(reference["target"]) == key
        and reference["relation"] == "amendment_candidate"
        for page in text.split("\f")
        for reference in extract_references(page, "страница", "")
    )


def scan_omnibus(client, seeds, scan, texts, max_pages):
    """Читает текст сводных актов и ищет в нём реквизиты заданных актов."""
    found, stats = {}, {"candidates": 0, "scanned": 0, "skipped": 0}
    complete, budget = True, scan.budget
    for raw in seeds:
        key = document_key(raw)
        if key is None:
            complete = False
            continue
        candidates, search_complete = omnibus_candidates(client, raw, scan, max_pages)
        complete = complete and search_complete
        stats["candidates"] += len(candidates)
        for eo_number, item in candidates.items():
            if eo_number not in texts:
                if budget <= 0:
                    complete = False
                    break
                budget -= 1
            text = texts.read(item)
            if text is None:
                stats["skipped"] += 1
                complete = False
                continue
            stats["scanned"] += 1
            if amends_in_text(text, key):
                found[eo_number] = item
    return found, complete, stats


# --- Файлы и текст -----------------------------------------------------------


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(data)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def write_json(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    atomic_bytes(path, text.encode("utf-8"))


def run_pdftotext(pdftotext, pdf_path):
    with tempfile.TemporaryDirectory() as task_dir:
        text_path = Path(task_dir) / "document.txt"
        command = [
            pdftotext,
            "-enc",
            "UTF-8",
            "-layout",
            str(pdf_path),
            str(text_path),
        ]
        try:
            subprocess.run(
                command,
                shell=False,
                check=True,
                timeout=PDFTOTEXT_TIMEOUT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if text_path.stat().st_size > MAX_TEXT_BYTES:
                raise ScraperError("Извлечённый текст превышает лимит 8 МиБ")
            return text_path.read_text(encoding="utf-8-sig")
        except (OSError, subprocess.SubprocessError):
            raise ScraperError(
                "Не удалось запустить pdftotext или обработать PDF (лимит 60 секунд)"
            ) from None


def run_ocr(ocr, pdf_path):
    """Распознаёт скан постранично; страницы разделяются form feed."""
    tesseract, language = ocr
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise ScraperError("Для OCR нужен pdftoppm из Poppler")
    with tempfile.TemporaryDirectory() as task_dir:
        prefix = Path(task_dir) / "page"
        pages = []
        try:
            subprocess.run(
                [pdftoppm, "-r", str(OCR_DPI), "-gray", str(pdf_path), str(prefix)],
                shell=False,
                check=True,
                timeout=OCR_RENDER_TIMEOUT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for image in sorted(Path(task_dir).glob("page*.pgm")):
                result = subprocess.run(
                    [tesseract, str(image), "stdout", "-l", language],
                    shell=False,
                    check=True,
                    timeout=OCR_PAGE_TIMEOUT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                pages.append(result.stdout.decode("utf-8", "replace"))
        except (OSError, subprocess.SubprocessError):
            raise ScraperError(
                "Не удалось распознать PDF: проверьте tesseract и языковую модель"
            ) from None
    return "\f".join(pages)


def read_text(pdf_path, eo_number, text_dir, pdftotext, ocr=None):
    """Текст акта и способ получения: TXT, затем текстовый слой, затем OCR."""
    if text_dir:
        path = text_dir / f"{eo_number}.txt"
        if path.exists():
            if path.stat().st_size > MAX_TEXT_BYTES:
                raise ScraperError("TXT превышает лимит 8 МиБ")
            return path.read_text(encoding="utf-8-sig"), "provided_text"
    if pdftotext and pdf_path:
        text = run_pdftotext(pdftotext, pdf_path)
        if text.strip() or not ocr:
            return text, "pdftotext"
    if ocr and pdf_path:
        return run_ocr(ocr, pdf_path), "ocr"
    return "", "not_requested"


class TextStore:
    """Тексты актов на запуск: сводный акт распознаётся один раз."""

    def __init__(self, client, text_dir, pdftotext, ocr):
        self.client, self.text_dir = client, text_dir
        self.pdftotext, self.ocr = pdftotext, ocr
        self.cache = {}

    def __contains__(self, eo_number):
        return eo_number in self.cache

    def get(self, eo_number):
        return self.cache.get(eo_number)

    def read(self, item):
        """Текст для сканирования или None, если акт превышает лимиты."""
        eo_number = item["eoNumber"]
        if eo_number not in self.cache:
            self.cache[eo_number] = self._load(item)
        cached = self.cache[eo_number]
        return cached[0] if cached else None

    def _load(self, item):
        length = item.get("pdfFileLength") or 0
        has_txt = self.text_dir and (self.text_dir / f"{item['eoNumber']}.txt").exists()
        if not has_txt and (
            length > self.client.max_bytes
            or (item.get("pagesCount") or 0) > MAX_OCR_PAGES
        ):
            return None
        eo_number = item["eoNumber"]
        if has_txt:
            return read_text(None, eo_number, self.text_dir, None)
        with tempfile.TemporaryDirectory() as task_dir:
            pdf_path = Path(task_dir) / "document.pdf"
            pdf_path.write_bytes(self.client.get("/file/pdf", {"eoNumber": eo_number}))
            return read_text(pdf_path, eo_number, None, self.pdftotext, self.ocr)


# --- Сбор --------------------------------------------------------------------


def prepare_output(output):
    """Отдельный каталог на запуск: старый manifest не должен выглядеть новым."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ScraperError(
            "Каталог результата должен быть пустым; выберите новый --output"
        )
    return output


class OmnibusScan:
    """Параметры чтения сводных актов: бюджет и период публикации."""

    def __init__(self, budget, date_from=None, date_to=None):
        self.budget, self.date_from, self.date_to = budget, date_from, date_to


def new_manifest(client, query, eo_numbers, discover):
    return {
        "schema_version": 1,
        "source": client.base_url,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "query": query or {},
        "requested_eo_numbers": list(eo_numbers),
        "listing_complete": False,
        "history_complete": False,
        "needs_expert_approval": True,
        "amendment_discovery": {
            "requested": discover,
            "scope": ["title_date_search"],
            "searches_complete": False,
        },
        "documents": [],
        "pdf_skipped": [],
        "edges": [],
    }


def new_record(client, raw):
    eo_number = raw["eoNumber"]
    registration_date = raw.get("jdRegDate")
    return {
        "schema_version": 1,
        "eo_number": eo_number,
        "source_url": client.document_url(eo_number),
        "api_url": f"{client.base_url}/api/Document?eoNumber={eo_number}",
        "title": raw.get("complexName", ""),
        "name": raw.get("name", ""),
        "number": raw.get("number"),
        "document_date": iso_day(raw["documentDate"]),
        "publication_date": iso_day(raw["publishDateShort"]),
        "document_type": raw.get("documentType"),
        "authorities": raw.get("signatoryAuthorities", []),
        "registration_number": raw.get("jdRegNumber"),
        "registration_date": iso_day(registration_date) if registration_date else None,
        "pages_count": raw.get("pagesCount"),
        "pdf": {
            "url": f"{client.base_url}/file/pdf?{urlencode({'eoNumber': eo_number})}",
            "downloaded": False,
        },
        "text_status": "not_requested",
        "text_pages": [],
        "history_complete": False,
        "needs_expert_approval": True,
        "references": [],
        "amended_by": [],
    }


def select_from_listing(client, query, max_pages, limit):
    selected, complete = [], False
    for items, complete in client.listing(query or {}, max_pages):
        selected.extend(item["eoNumber"] for item in items)
        if len(selected) >= limit:
            return selected, complete and len(selected) <= limit
    return selected, complete


def add_discovered(client, selected, manifest, max_pages, limit, scan, texts):
    """Расширяет выборку входящими поправками всеми включёнными способами.

    Возвращает выборку, готовые карточки и связи, найденные только по названию.
    """
    cards = {eo_number: client.document(eo_number) for eo_number in selected[:limit]}
    seeds = list(cards.values())
    by_date, complete = find_amendments(client, seeds, max_pages, limit)
    by_name, name_complete = find_named_amendments(client, seeds, max_pages, limit)
    discovery = manifest["amendment_discovery"]
    discovery["scope"].append("law_name_search")
    found = [item["eoNumber"] for item in by_date] + list(by_name)
    if scan:
        by_text, scan_complete, stats = scan_omnibus(
            client, seeds, scan, texts, max_pages
        )
        discovery["scope"].append("omnibus_text_scan")
        discovery["omnibus_scan"] = stats
        discovery["omnibus_found"] = len(by_text)
        found += list(by_text)
        complete = complete and scan_complete
    selected = list(dict.fromkeys(selected + found))
    discovery.update(
        {
            "searches_complete": (
                complete and name_complete and len(selected) <= limit
            ),
            "candidates_count": len(dict.fromkeys(found)),
        }
    )
    return selected, cards, {eo: links for eo, (_, links) in by_name.items()}


def title_links(record, links):
    """Связи, подтверждённые названием закона, а не реквизитами."""
    references = []
    for raw, predicate in links:
        key = document_key(raw)
        reference = make_reference(
            {
                "document_type": key[0],
                "authority": key[1],
                "document_date": key[2],
                "number": normalize_number(raw["number"]),
            },
            predicate,
            None,
            record["source_url"],
            TITLE,
            record["name"] or "",
        )
        reference.update(
            {
                "resolution": "matched_by_title",
                "candidate_eo_numbers": [raw["eoNumber"]],
                "target_eo_number": raw["eoNumber"],
                "target_url": f"{record['source_url'].rsplit('/', 1)[0]}/{raw['eoNumber']}",
            }
        )
        references.append(reference)
    return references


def save_pdf(client, raw, record, output):
    """Скачивает и проверяет PDF; возвращает путь либо None при пропуске."""
    eo_number = raw["eoNumber"]
    expected = raw.get("pdfFileLength")
    # Один огромный акт не должен обрывать выборку, но пропуск обязан быть виден.
    if expected is not None and expected > client.max_bytes:
        record["pdf"].update({"bytes": expected, "skip_reason": "exceeds_max_bytes"})
        return None
    pdf = client.get("/file/pdf", {"eoNumber": eo_number})
    if not pdf.startswith(b"%PDF-"):
        raise ScraperError("Вместо PDF портал вернул другой формат")
    if expected is not None and len(pdf) != expected:
        raise ScraperError("Размер PDF отличается от pdfFileLength карточки")
    path = output / "pdf" / f"{eo_number}.pdf"
    atomic_bytes(path, pdf)
    record["pdf"].update(
        {
            "downloaded": True,
            "path": f"pdf/{eo_number}.pdf",
            "sha256": hashlib.sha256(pdf).hexdigest(),
            "bytes": len(pdf),
        }
    )
    return path


def add_text(record, text, method, ocr_expected, own_key):
    """Записывает страницы текста и возвращает найденные в них ссылки."""
    record["text_method"] = method
    if not text.strip():
        record["text_status"] = "needs_ocr" if ocr_expected else "not_requested"
        return []
    record["text_status"] = "extracted_unverified"
    references = []
    for page, content in enumerate(text.split("\f"), 1):
        if content.strip():
            record["text_pages"].append({"page": page, "text": content})
            references += extract_references(
                content, f"страница {page}", record["source_url"], own_key
            )
    return references


def link_amendments(client, records, edges):
    """Обратные ссылки внутри выборки; заголовок и текст акта дают одну."""
    by_id = {record["eo_number"]: record for record in records}
    for edge in edges:
        target = by_id.get(edge["target_eo_number"])
        if target is None or edge["relation"] != "amendment_candidate":
            continue
        link = {
            "eo_number": edge["from_eo_number"],
            "url": client.document_url(edge["from_eo_number"]),
            "predicate": edge["predicate"],
            "needs_expert_approval": True,
        }
        if link not in target["amended_by"]:
            target["amended_by"].append(link)


def collect(
    client,
    output,
    eo_numbers=(),
    query=None,
    max_pages=1,
    limit=10,
    download_pdf=True,
    resolve=True,
    text_dir=None,
    pdftotext=None,
    discover=False,
    ocr=None,
    scan=None,
):
    output = prepare_output(output)
    manifest = new_manifest(client, query, eo_numbers, discover)
    manifest_path = output / "manifest.json"
    write_json(manifest_path, manifest)
    resolver = Resolver(client)
    texts = TextStore(client, text_dir, pdftotext, ocr)
    named_links = {}
    records, edges = [], []
    try:
        selected = list(dict.fromkeys(eo_numbers))
        listing_complete = bool(selected)
        if not selected:
            selected, listing_complete = select_from_listing(
                client, query, max_pages, limit
            )
        cards = {}
        if discover:
            if not eo_numbers:
                raise ScraperError("Поиск входящих поправок требует явных eo-number")
            selected, cards, named_links = add_discovered(
                client, selected, manifest, max_pages, limit, scan, texts
            )
        for eo_number in selected[:limit]:
            raw = cards.get(eo_number) or client.document(eo_number)
            record = new_record(client, raw)
            pdf_path = save_pdf(client, raw, record, output) if download_pdf else None
            if "skip_reason" in record["pdf"]:
                manifest["pdf_skipped"].append(eo_number)
            own_key = document_key(raw)
            references = extract_references(
                raw.get("name") or "", TITLE, record["source_url"], own_key
            )
            text, method = texts.get(eo_number) or read_text(
                pdf_path, eo_number, text_dir, pdftotext, ocr
            )
            references += add_text(
                record, text, method, bool(pdftotext or text_dir or ocr), own_key
            )
            references += title_links(record, named_links.get(eo_number, ()))
            record["references"] = unique_references(references)
            for reference in record["references"]:
                if resolve and reference["resolution"] == "not_requested":
                    resolver.resolve(reference)
                edges.append({"from_eo_number": eo_number, **reference})
            records.append(record)
            write_json(output / "documents" / f"{eo_number}.json", record)
            manifest["documents"].append(eo_number)
            write_json(manifest_path, manifest)
        link_amendments(client, records, edges)
        for record in records:
            write_json(output / "documents" / f"{record['eo_number']}.json", record)
        manifest.update(
            {
                "status": "completed",
                "listing_complete": listing_complete and len(selected) <= limit,
                "edges": edges,
                "documents_count": len(records),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception:
        manifest.update(
            {
                "status": "failed",
                "edges": edges,
                "documents_count": len(records),
                "error": "Сбор не завершён; результат частичный",
            }
        )
        write_json(manifest_path, manifest)
        raise
    write_json(manifest_path, manifest)
    return manifest


# --- Командная строка --------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        description="Загрузка НПА и кандидатов в связи изменений с pravo.gov.ru"
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--eo-number",
        action="append",
        help="номер публикации (16 цифр), можно повторить",
    )
    selection.add_argument(
        "--published-from", help="начало периода публикации, ГГГГ-ММ-ДД"
    )
    parser.add_argument(
        "--published-to", help="конец периода, ГГГГ-ММ-ДД (включительно)"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="новый пустой каталог в out/"
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="явно использовать HTTP, если HTTPS недоступен",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="не использовать системные настройки прокси",
    )
    parser.add_argument("--metadata-only", action="store_true", help="не загружать PDF")
    parser.add_argument(
        "--no-resolve", action="store_true", help="не искать ссылки на упомянутые акты"
    )
    parser.add_argument(
        "--find-amendments",
        action="store_true",
        help="искать акты, изменяющие заданные eo-number (по заголовкам)",
    )
    parser.add_argument(
        "--text-dir",
        type=Path,
        help="каталог локальных UTF-8 TXT: НОМЕР_ПУБЛИКАЦИИ.txt",
    )
    parser.add_argument(
        "--pdftotext", help="путь к pdftotext для извлечения текстового слоя PDF"
    )
    parser.add_argument(
        "--ocr",
        metavar="TESSERACT",
        help="путь к tesseract: распознавать сканы (нужен также pdftoppm)",
    )
    parser.add_argument(
        "--ocr-lang", default="rus", help="языковая модель tesseract (по умолчанию rus)"
    )
    parser.add_argument(
        "--scan-omnibus",
        type=int,
        metavar="N",
        default=0,
        help="прочитать не больше N сводных актов при --find-amendments",
    )
    parser.add_argument("--scan-from", help="начало периода сводных актов, ГГГГ-ММ-ДД")
    parser.add_argument("--scan-to", help="конец периода сводных актов, ГГГГ-ММ-ДД")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="максимум загружаемых актов (по умолчанию 10)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="максимум страниц списка (по умолчанию 1)",
    )
    parser.add_argument("--page-size", type=int, choices=[10, 30, 100, 200], default=10)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--delay",
        type=float,
        default=1,
        help=f"пауза между запросами в секундах, не меньше {MIN_DELAY} (по умолчанию 1)",
    )
    parser.add_argument("--retries", type=int, choices=range(6), default=2)
    parser.add_argument(
        "--max-mib", type=int, default=64, help="лимит одного PDF в МиБ"
    )
    return parser


def validate_args(parser, args):
    if not args.delay >= MIN_DELAY:
        parser.error(f"delay должен быть не меньше {MIN_DELAY} секунды")
    if args.limit < 1 or args.max_pages < 1 or args.max_mib < 1:
        parser.error("limit, max-pages, max-mib должны быть положительными")
    if args.eo_number and any(
        not EO_NUMBER.fullmatch(value) for value in args.eo_number
    ):
        parser.error("eo-number должен содержать 16 цифр")
    if args.published_from and not args.published_to:
        parser.error("для published-from требуется published-to")
    if args.published_to and not args.published_from:
        parser.error("published-to используется вместе с published-from")
    if args.metadata_only and args.pdftotext and not args.scan_omnibus:
        parser.error("pdftotext требует загрузки PDF")
    if args.metadata_only and args.ocr and not args.scan_omnibus:
        parser.error("ocr требует загрузки PDF")
    if args.scan_omnibus < 0:
        parser.error("scan-omnibus не может быть отрицательным")
    if args.scan_omnibus and not args.find_amendments:
        parser.error("scan-omnibus работает только с find-amendments")
    if args.scan_omnibus and not (args.ocr or args.pdftotext or args.text_dir):
        parser.error("scan-omnibus требует ocr, pdftotext или text-dir")
    if (args.scan_from or args.scan_to) and not args.scan_omnibus:
        parser.error("scan-from и scan-to работают только с scan-omnibus")
    for value in (args.scan_from, args.scan_to):
        if value:
            try:
                api_day(value)
            except ValueError:
                parser.error("период сводных актов задаётся датами ГГГГ-ММ-ДД")
    if args.find_amendments and (not args.eo_number or args.no_resolve):
        parser.error("find-amendments требует eo-number и разрешения ссылок")


def build_query(parser, args):
    query = {"PageSize": args.page_size, "SortedBy": 5}
    if args.published_from:
        try:
            query.update(
                {
                    "PublishDateFrom": api_day(args.published_from),
                    "PublishDateTo": api_day(args.published_to),
                }
            )
        except ValueError:
            query = None
        if query is None or args.published_from > args.published_to:
            parser.error("период должен содержать даты ГГГГ-ММ-ДД от ранней к поздней")
    return query


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    query = build_query(parser, args)
    try:
        client = Client(
            base_url=f"{'http' if args.http else 'https'}://{HOST}",
            timeout=args.timeout,
            delay=args.delay,
            retries=args.retries,
            max_bytes=args.max_mib * MIB,
            use_proxy=not args.no_proxy,
        )
        manifest = collect(
            client,
            args.output,
            eo_numbers=args.eo_number or (),
            query=query,
            max_pages=args.max_pages,
            limit=args.limit,
            download_pdf=not args.metadata_only,
            resolve=not args.no_resolve,
            text_dir=args.text_dir,
            pdftotext=args.pdftotext,
            discover=args.find_amendments,
            ocr=(args.ocr, args.ocr_lang) if args.ocr else None,
            scan=(
                OmnibusScan(args.scan_omnibus, args.scan_from, args.scan_to)
                if args.scan_omnibus
                else None
            ),
        )
    except (
        ScraperError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        # Не выводим содержимое ответов, локальные пути и stderr внешних утилит.
        message = (
            str(exc)
            if isinstance(exc, ScraperError)
            else "Ошибка файла или формата данных портала"
        )
        print(f"Ошибка: {message}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "documents_count": manifest["documents_count"],
                "references_count": len(manifest["edges"]),
                "history_complete": False,
                "needs_expert_approval": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
