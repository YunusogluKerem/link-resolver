"""Reading the source Excel file.

The file is a database export (`select id, links from app1_story`), and
`links` is a Postgres array column. So every cell looks like `{https://a}` and
sometimes `{"https://a,b"}`: the array is quoted when the URL contains a comma
or a space. In the sample every array has one element, but the format allows
several, so the parser returns a list.
"""

from collections.abc import Iterator
from pathlib import Path

from openpyxl import load_workbook

ID_HEADERS = {"id", "content_id"}
LINK_HEADERS = {"links", "link"}


def parse_links_cell(value: object) -> list[str]:
    """Turn one `links` cell into a list of clean URLs."""
    if value is None:
        return []
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        items = _split_pg_array(text[1:-1])
    else:
        items = [text]  # plain URL, in case the file is ever exported differently
    return [item.strip() for item in items if item.strip()]


def _split_pg_array(body: str) -> list[str]:
    """Split the inside of a Postgres array literal on commas outside quotes.

    Unquoted NULL means a missing element and is dropped.
    """
    items: list[str] = []
    current: list[str] = []
    in_quotes = was_quoted = False
    i = 0

    def flush() -> None:
        item = "".join(current)
        if was_quoted or item.strip().upper() != "NULL":
            items.append(item)

    while i < len(body):
        char = body[i]
        if in_quotes:
            if char == "\\" and i + 1 < len(body):  # \" and \\ are escapes
                current.append(body[i + 1])
                i += 2
                continue
            if char == '"':
                in_quotes = False
            else:
                current.append(char)
        elif char == '"':
            in_quotes = was_quoted = True
        elif char == ",":
            flush()
            current, was_quoted = [], False
        else:
            current.append(char)
        i += 1
    flush()
    return items


def parse_content_id(value: object) -> int | None:
    """Excel may hand the id back as int, float or text; anything else is invalid."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def read_rows(path: Path | str, sheet: str | None = None) -> Iterator[tuple[object, object]]:
    """Yield the raw (id, links) cells of every data row.

    Columns are located by header name, not position, so a reordered export
    still works. `read_only` streams the file instead of loading it whole.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook[sheet] if sheet else workbook.worksheets[0]
        rows = worksheet.iter_rows(values_only=True)
        header = [str(cell).strip().lower() if cell is not None else "" for cell in next(rows, ())]
        id_col = _find_column(header, ID_HEADERS)
        link_col = _find_column(header, LINK_HEADERS)
        for row in rows:
            yield _cell(row, id_col), _cell(row, link_col)
    finally:
        workbook.close()


def _find_column(header: list[str], names: set[str]) -> int:
    for index, name in enumerate(header):
        if name in names:
            return index
    raise ValueError(f"Column {sorted(names)} not found in header row {header}")


def _cell(row: tuple, index: int) -> object:
    return row[index] if index < len(row) else None
