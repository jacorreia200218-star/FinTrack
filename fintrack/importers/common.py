"""Utilitários partilhados pelos importadores: leitura de ficheiros, números e datas."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path


def normalize(text: str) -> str:
    """Minúsculas, sem acentos e sem espaços a mais — para comparar cabeçalhos."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def read_rows(content: bytes, filename: str = "") -> list[list[str]]:
    """Lê CSV (qualquer separador/encoding) ou XLSX e devolve uma lista de linhas."""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        return _read_xlsx(content)

    text = None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    assert text is not None  # latin-1 nunca falha

    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[cell.strip() for cell in row] for row in reader]


def _read_xlsx(content: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(values_only=True):
        cells = []
        for v in row:
            if v is None:
                cells.append("")
            elif isinstance(v, datetime):
                cells.append(v.strftime("%Y-%m-%d"))
            elif isinstance(v, date):
                cells.append(v.isoformat())
            else:
                cells.append(str(v).strip())
        rows.append(cells)
    return rows


def parse_amount(value: str) -> float | None:
    """Converte '1.234,56', '1,234.56', '-12,50 €', '(12.50)' em float."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d,.\-+]", "", s)
    if not s or s in "-+":
        return None
    if "," in s and "." in s:
        # O último separador é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # '12,50' -> decimal (formato PT); '1,234,567' -> separador de milhares
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        result = float(s)
    except ValueError:
        return None
    return -abs(result) if negative else result


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%d-%m-%y",
    "%d/%m/%y",
)


def parse_date(value: str) -> date | None:
    if not value:
        return None
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # ISO com fração de segundos, etc.
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def find_col(header: list[str], *candidates: str, exact: bool = False) -> int | None:
    """Índice da primeira coluna cujo cabeçalho corresponde a um dos candidatos."""
    norm = [normalize(h) for h in header]
    for cand in candidates:
        c = normalize(cand)
        for i, h in enumerate(norm):
            if (h == c) if exact else (h == c or h.startswith(c)):
                return i
    return None


def cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return row[idx]


def file_label(path_or_name: str | Path) -> str:
    return Path(path_or_name).name
