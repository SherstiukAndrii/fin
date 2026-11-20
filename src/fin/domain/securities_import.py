"""Privacy-preserving offline import of supported Ukrainian securities PDFs.

Only normalized security facts leave the PDF parser. Depositor identity, tax
numbers, account numbers, signatures and PDF bytes are never saved or logged.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO

from pypdf import PdfReader

from fin.domain.money import Money

_MAX_PDF_BYTES = 8 * 1024 * 1024
_ISIN = re.compile(r"\bUA\d{10}\b")
_DAY = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")


@dataclass(frozen=True, slots=True)
class SecuritiesEntry:
    """One closing position or one dated security movement, not cash."""

    source_hash: str
    event_key: str
    custodian: str
    isin: str
    as_of: date
    kind: str  # closing / movement
    quantity: int  # signed delta for movement, absolute quantity for closing
    nominal_per_unit: Money | None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", self.source_hash):
            raise ValueError("Invalid document fingerprint")
        if not re.fullmatch(r"[a-f0-9]{64}", self.event_key):
            raise ValueError("Invalid security fingerprint")
        if not re.fullmatch(r"UA\d{10}", self.isin):
            raise ValueError("Invalid Ukrainian ISIN")
        if not self.custodian or len(self.custodian) > 200:
            raise ValueError("Invalid securities institution")
        if type(self.as_of) is not date:
            raise TypeError("Security record needs a date")
        if self.kind not in ("closing", "movement"):
            raise ValueError("Unsupported security record kind")
        if type(self.quantity) is not int or abs(self.quantity) > 10**12:
            raise ValueError("Invalid security quantity")
        if (self.kind == "closing" and self.quantity < 0) or (
            self.kind == "movement" and self.quantity == 0
        ):
            raise ValueError("Invalid security quantity")
        if self.nominal_per_unit is not None and self.nominal_per_unit.amount <= 0:
            raise ValueError("Nominal per unit must be positive")


def _day(text: str) -> date:
    return date(int(text[6:]), int(text[3:5]), int(text[:2]))


def _number(text: str) -> Decimal:
    return Decimal(re.sub(r"\s+", "", text).replace(",", "."))


def _institution(text: str) -> str:
    if "СМАРТ БОНД.ЮА" in text:
        return "СМАРТ БОНД.ЮА"
    if "ІНВЕСТИЦІЙНИЙ КАПІТАЛ УКРАЇНА" in text:
        return "ІНВЕСТИЦІЙНИЙ КАПІТАЛ УКРАЇНА"
    if "Сенс Банк" in text or "СЕНС БАНК" in text:
        return "Сенс Банк"
    raise ValueError("Неизвестный формат депозитарной выписки")


def _fingerprint(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def parse_securities_text(text: str, source_hash: str) -> SecuritiesEntry:
    """Only three recognized statement layouts; reject ambiguous inputs."""
    if not any(word in text.casefold() for word in ("виписка", "выписка")):
        raise ValueError("Нет заголовка выписки")
    provider = _institution(text)
    isins = set(_ISIN.findall(text))
    if len(isins) != 1:
        raise ValueError("Нужна выписка с одним определённым ISIN")
    isin = isins.pop()
    block = text.split(isin, 1)[1]
    if "ВИПИСКА ПРО СТАН РАХУНКУ" in text.upper():
        observed = re.search(
            r"(?P<qty>\d+)\s+"
            r"(?P<face>(?:\d{1,3}(?: \d{3})+|\d+)[,.]\d{2})\s+"
            r"(?P<currency>[A-Z]{3})\s+"
            r"(?P<total>(?:\d{1,3}(?: \d{3})+|\d+)[,.]\d{2})\s+"
            r"(?P=currency)",
            block,
        )
        statement_date = re.search(r"\bНА\s+(\d{2}\.\d{2}\.\d{4})\b", text.upper())
        if not observed or not statement_date:
            raise ValueError("Не удалось проверить конечный остаток и номинал")
        quantity = int(observed["qty"])
        face = Money(_number(observed["face"]), observed["currency"])
        total = Money(_number(observed["total"]), face.currency)
        if face * quantity != total:
            raise ValueError("Номинал и итоговая сумма в выписке не совпадают")
        as_of, kind = _day(statement_date[1]), "closing"
        nominal = face
        ref = "closing"
    elif "Кінцевий залишок:" in block:
        closing = re.search(r"Кінцевий залишок:\s*(\d+)", block)
        period = re.search(
            r"за період з\s+\d{2}\.\d{2}\.\d{4}\s+по\s+"
            r"(\d{2}\.\d{2}\.\d{4})",
            text,
            flags=re.IGNORECASE,
        )
        face_match = re.search(r"вартість ЦП:\s*(\d[\d\s.,]*)\s+([A-Z]{3})", block)
        if not closing or not period or not face_match:
            raise ValueError("Не удалось проверить остаток и дату выписки")
        quantity, as_of, kind = int(closing[1]), _day(period[1]), "closing"
        nominal = Money(_number(face_match[1]), face_match[2])
        ref = "closing"
    elif "Зарахування ЦП" in block or "Списання ЦП" in block:
        row = re.search(r"(?m)^\s*(\d+)\s*\n\s*(Зарахування|Списання) ЦП", block)
        dated = re.search(r"(?m)^\s*(\d{2}\.\d{2}\.\d{4})\s*$", block)
        if not row or not dated:
            raise ValueError("Не удалось проверить дату и количество операции")
        quantity = int(row[1]) * (1 if row[2] == "Зарахування" else -1)
        as_of, kind, nominal = _day(dated[1]), "movement", None
        # A contract reference distinguishes two same-day trades of equal size.
        contract = re.search(r"Дог\.\s*№\s*([^\s]+)", block)
        ref = contract[1] if contract else source_hash
    else:
        raise ValueError("Выписка не содержит распознаваемого остатка или операции")
    event_key = _fingerprint(provider, isin, as_of.isoformat(), kind, quantity, ref)
    return SecuritiesEntry(source_hash, event_key, provider, isin, as_of, kind, quantity, nominal)


def parse_securities_pdf(payload: bytes) -> SecuritiesEntry:
    """Parse in memory. Never store the PDF or expose depositor fields."""
    # Signed statements may contain a short CMS envelope before the PDF header.
    if not isinstance(payload, bytes) or b"%PDF-" not in payload[:1024]:
        raise ValueError("Ожидается PDF-файл")
    if not 0 < len(payload) <= _MAX_PDF_BYTES:
        raise ValueError("PDF слишком большой (максимум 8 МБ)")
    try:
        reader = PdfReader(BytesIO(payload), strict=False)
        if not 1 <= len(reader.pages) <= 8:
            raise ValueError("Поддерживаются выписки до 8 страниц")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Не удалось прочитать PDF") from exc
    if not text.strip():
        raise ValueError("В PDF нет текстового слоя: скан пока не поддерживается")
    return parse_securities_text(text, hashlib.sha256(payload).hexdigest())
