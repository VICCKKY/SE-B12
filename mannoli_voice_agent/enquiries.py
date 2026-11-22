"""Validate and store Mannoli call enquiries (no third-party dependencies)."""
from __future__ import annotations

import csv
import re
import secrets
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
LANGUAGES = {"ta", "en", "hi", "ml", "kn", "te", "other"}
ENQUIRY_TYPES = {"retail", "bulk_or_gifting", "custom_design", "dealer_or_distribution",
                 "delivery_question", "complaint", "other"}
FIELDS = ["enquiry_id", "received_at_ist", "caller_number", "name", "language", "enquiry_type",
          "products", "quantity", "city_or_pincode", "callback_time", "whatsapp_consent", "notes",
          "status"]
MAX_LEN = 200
_lock = threading.Lock()


class EnquiryError(ValueError):
    """Bad tool arguments; the message is safe to read back to the agent."""


def _clean(value, field: str, required: bool = False) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()[:MAX_LEN]
    if required and not text:
        raise EnquiryError(f"{field} is missing. Ask the caller for it.")
    # Stop spreadsheet formula injection when the CSV is opened in Excel / Sheets.
    if text[:1] in ("=", "+", "-", "@"):
        text = "'" + text
    return text


def _caller_number(value) -> str:
    digits = re.sub(r"[^\d+]", "", str(value or ""))
    return digits if 10 <= len(digits.lstrip("+")) <= 13 else ""


def save_enquiry(args: dict, csv_path: Path, caller_number: str | None = None) -> dict:
    """Validate tool arguments, append one row, and return the reply for the agent."""
    if not isinstance(args, dict):
        raise EnquiryError("Arguments must be an object.")
    language = str(args.get("language", "")).strip().lower()
    if language not in LANGUAGES:
        raise EnquiryError("language must be one of: " + ", ".join(sorted(LANGUAGES)))
    enquiry_type = str(args.get("enquiry_type", "")).strip()
    if enquiry_type not in ENQUIRY_TYPES:
        raise EnquiryError("enquiry_type must be one of: " + ", ".join(sorted(ENQUIRY_TYPES)))
    consent = args.get("whatsapp_consent")
    if not isinstance(consent, bool):
        raise EnquiryError("whatsapp_consent must be true or false. Ask the caller.")

    now = datetime.now(IST)
    row = {
        "enquiry_id": "MN-" + now.strftime("%y%m%d") + "-" + secrets.token_hex(2).upper(),
        "received_at_ist": now.strftime("%Y-%m-%d %H:%M"),
        "caller_number": _caller_number(caller_number),
        "name": _clean(args.get("name"), "name", required=True),
        "language": language,
        "enquiry_type": enquiry_type,
        "products": _clean(args.get("products"), "products", required=True),
        "quantity": _clean(args.get("quantity"), "quantity"),
        "city_or_pincode": _clean(args.get("city_or_pincode"), "city_or_pincode"),
        "callback_time": _clean(args.get("callback_time"), "callback_time"),
        "whatsapp_consent": "yes" if consent else "no",
        "notes": _clean(args.get("notes"), "notes"),
        "status": "new",
    }
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        new_file = not csv_path.exists() or csv_path.stat().st_size == 0
        with csv_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    return {
        "ok": True,
        "enquiry_id": row["enquiry_id"],
        "say": "Enquiry saved. Tell the caller the team will call back to confirm price and delivery. "
               "Do not promise a time or price.",
    }
