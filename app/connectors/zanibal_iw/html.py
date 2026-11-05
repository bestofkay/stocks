"""Tiny stdlib HTML extractor for IW portal pages (tables, forms, alerts).

IW pages are server-rendered ASP.NET MVC; we only need table cells, form
fields and alert text, so a full DOM library is unnecessary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text or "")).strip()


@dataclass
class Table:
    id: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)  # tbody (or headerless) rows
    heading: str = ""  # nearest preceding h2-h5 text

    def has_headers(self, *names: str) -> bool:
        low = [h.lower() for h in self.headers]
        return all(any(n.lower() in h for h in low) for n in names)

    def col(self, name: str) -> int | None:
        """Exact header match first (e.g. 'Price' vs 'Avg Unit Price'), then substring."""
        name = name.lower().strip()
        low = [h.lower().strip() for h in self.headers]
        if name in low:
            return low.index(name)
        for i, h in enumerate(low):
            if name in h:
                return i
        return None


@dataclass
class Select:
    name: str
    options: list[tuple[str, str, bool]] = field(default_factory=list)  # (value, text, selected)


@dataclass
class Form:
    action: str = ""
    method: str = "get"
    inputs: dict[str, str] = field(default_factory=dict)  # name -> value (hidden/text/number/date/password)
    input_types: dict[str, str] = field(default_factory=dict)
    selects: dict[str, Select] = field(default_factory=dict)
    submits: list[str] = field(default_factory=list)  # submit button labels

    def fields(self) -> dict[str, str]:
        out = dict(self.inputs)
        for name, sel in self.selects.items():
            chosen = next((v for v, _, s in sel.options if s), sel.options[0][0] if sel.options else "")
            out.setdefault(name, chosen)
        return out


@dataclass
class Page:
    tables: list[Table] = field(default_factory=list)
    forms: list[Form] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    title: str = ""
    text: str = ""

    def table_with(self, *headers: str) -> Table | None:
        return next((t for t in self.tables if t.has_headers(*headers)), None)

    def tables_with(self, *headers: str) -> list[Table]:
        return [t for t in self.tables if t.has_headers(*headers)]

    def form_with_field(self, name: str) -> Form | None:
        return next((f for f in self.forms if name in f.inputs or name in f.selects), None)


_ALERT_CLASSES = ("alert", "validation-summary-errors", "field-validation-error", "text-danger")


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self._tables: list[Table] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_is_th = False
        self._in_thead = False
        self._in_tfoot = False
        self._row_has_td = False
        self._form: Form | None = None
        self._select: Select | None = None
        self._option: list | None = None
        self._textarea: str | None = None
        self._heading: list[str] | None = None
        self._last_heading = ""
        self._alert_depth = 0
        self._alert_buf: list[str] = []
        self._title: list[str] | None = None
        self._skip = 0  # script/style
        self._text: list[str] = []
        self._depth = 0

    # -- helpers
    def _attrs(self, attrs):
        return {k: (v or "") for k, v in attrs}

    def handle_starttag(self, tag, attrs):
        a = self._attrs(attrs)
        if tag in ("script", "style"):
            self._skip += 1
            return
        self._depth += 1
        if self._alert_depth:
            self._alert_depth += 1
        elif any(c in a.get("class", "").split() for c in _ALERT_CLASSES):
            self._alert_depth = 1
            self._alert_buf = []
        if tag == "title":
            self._title = []
        elif tag in ("h2", "h3", "h4", "h5"):
            self._heading = []
        elif tag == "table":
            self._tables.append(Table(id=a.get("id", ""), heading=self._last_heading))
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tfoot":
            self._in_tfoot = True
        elif tag == "tr" and self._tables:
            if self._row is not None:  # previous <tr> never closed (seen on IW summary tables)
                self.handle_endtag("tr")
                self._depth += 1
            self._row = []
            self._row_has_td = False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._cell_is_th = tag == "th"
            if tag == "td":
                self._row_has_td = True
        elif tag == "form":
            self._form = Form(action=a.get("action", ""), method=(a.get("method") or "get").lower())
        elif tag == "input" and self._form is not None:
            name, typ = a.get("name", ""), (a.get("type") or "text").lower()
            if typ in ("submit", "button"):
                self._form.submits.append(a.get("value", ""))
            elif name and (typ not in ("radio", "checkbox") or "checked" in a):
                self._form.inputs[name] = a.get("value", "")
                self._form.input_types[name] = typ
        elif tag == "button" and self._form is not None and (a.get("type") or "submit") == "submit":
            self._form.submits.append(a.get("value", "") or "button")
        elif tag == "select" and self._form is not None:
            self._select = Select(name=a.get("name", ""))
        elif tag == "option" and self._select is not None:
            self._option = [a.get("value"), [], "selected" in a]
        elif tag == "textarea" and self._form is not None:
            self._textarea = a.get("name", "")
            self._form.inputs[self._textarea] = ""
            self._form.input_types[self._textarea] = "textarea"

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        self._depth -= 1
        if tag == "title" and self._title is not None:
            self.page.title = clean("".join(self._title))
            self._title = None
        elif tag in ("h2", "h3", "h4", "h5") and self._heading is not None:
            self._last_heading = clean("".join(self._heading))
            self._heading = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._tables:
            t = self._tables[-1]
            if self._in_thead or (not t.headers and not t.rows and not self._row_has_td and self._row):
                if not t.headers:
                    t.headers = self._row
            elif self._in_tfoot:
                pass  # totals row
            elif self._row:
                t.rows.append(self._row)
            self._row = None
        elif tag == "thead":
            self._in_thead = False
        elif tag == "tfoot":
            self._in_tfoot = False
        elif tag == "table" and self._tables:
            self.page.tables.append(self._tables.pop())
        elif tag == "option" and self._option is not None and self._select is not None:
            text = clean("".join(self._option[1]))
            value = self._option[0] if self._option[0] is not None else text
            self._select.options.append((value, text, self._option[2]))
            self._option = None
        elif tag == "select" and self._select is not None and self._form is not None:
            if self._select.name:
                self._form.selects[self._select.name] = self._select
            self._select = None
        elif tag == "textarea":
            self._textarea = None
        elif tag == "form" and self._form is not None:
            self.page.forms.append(self._form)
            self._form = None
        if self._alert_depth:
            self._alert_depth -= 1
            if self._alert_depth == 0:
                msg = clean("".join(self._alert_buf))
                if msg and msg not in self.page.alerts:
                    self.page.alerts.append(msg)

    def handle_data(self, data):
        if self._skip:
            return
        if self._title is not None:
            self._title.append(data)
        if self._heading is not None:
            self._heading.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._option is not None:
            self._option[1].append(data)
        if self._textarea and self._form is not None:
            self._form.inputs[self._textarea] += data
        if self._alert_depth:
            self._alert_buf.append(data)
        self._text.append(data)


def parse_page(html: str) -> Page:
    p = _Parser()
    p.feed(html or "")
    p.close()
    while p._tables:  # unclosed tables
        p.page.tables.append(p._tables.pop())
    if p._form is not None:
        p.page.forms.append(p._form)
    p.page.text = clean(" ".join(p._text))
    return p.page
