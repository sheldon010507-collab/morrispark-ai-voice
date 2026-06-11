from __future__ import annotations

import re
from dataclasses import dataclass


PRICE_WORDS = {
    "price",
    "pricing",
    "cost",
    "rent",
    "rate",
    "deposit",
    "discount",
    "lease",
    "contract",
    "term",
    "terms",
}

AVAILABILITY_WORDS = {
    "available",
    "availability",
    "space",
    "spaces",
    "unit",
    "office",
    "workshop",
    "yard",
    "parking",
    "mot",
}

BOOKING_PATTERNS = [
    r"\bbook\b",
    r"\bviewing\b",
    r"\bvisit\b",
    r"\bcall me\b",
    r"\bcontact me\b",
    r"\bi'?m interested\b",
    r"\bi am interested\b",
    r"\bspeak to\b",
]

LOCATION_WORDS = {"where", "location", "address", "road", "m74", "glasgow"}
FACILITY_WORDS = {"parking", "wifi", "wi-fi", "internet", "cctv", "shower", "cafe", "security", "access"}


@dataclass(frozen=True)
class Intent:
    needs_rag: bool
    is_sensitive: bool
    is_booking: bool
    label: str


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def detect_intent(text: str) -> Intent:
    clean = normalize(text)
    tokens = set(re.findall(r"[a-z0-9£]+", clean))

    is_booking = any(re.search(pattern, clean) for pattern in BOOKING_PATTERNS)
    is_sensitive = bool(tokens & PRICE_WORDS)
    is_availability = bool(tokens & AVAILABILITY_WORDS)
    is_location = bool(tokens & LOCATION_WORDS)
    is_facility = bool(tokens & FACILITY_WORDS)
    needs_rag = is_booking or is_sensitive or is_availability or is_location or is_facility

    if is_booking:
        label = "booking"
    elif is_sensitive:
        label = "pricing_or_terms"
    elif is_availability:
        label = "availability"
    elif is_location:
        label = "location"
    elif is_facility:
        label = "facilities"
    else:
        label = "general"

    return Intent(
        needs_rag=needs_rag,
        is_sensitive=is_sensitive,
        is_booking=is_booking,
        label=label,
    )
