from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "can",
    "do",
    "for",
    "from",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "where",
    "with",
    "you",
}


@dataclass(frozen=True)
class KnowledgeHit:
    source: str
    text: str
    score: int


class KnowledgeBase:
    def __init__(self, knowledge_dir: Path):
        self.knowledge_dir = knowledge_dir
        self.core_facts = self._read_json("core_facts.json")
        self.availability = self._read_json("availability.json")
        self.guardrails = self._read_json("guardrails.json")
        self.faq = self._read_text("faq.md")
        self.fragments = self._build_fragments()

    def _read_json(self, filename: str) -> dict[str, Any]:
        return json.loads((self.knowledge_dir / filename).read_text(encoding="utf-8"))

    def _read_text(self, filename: str) -> str:
        return (self.knowledge_dir / filename).read_text(encoding="utf-8")

    def _build_fragments(self) -> list[KnowledgeHit]:
        fragments: list[KnowledgeHit] = []

        facts = self.core_facts
        fragments.append(KnowledgeHit("core_facts", f"Morris Park is at {facts['address']}.", 0))
        fragments.append(KnowledgeHit("core_facts", "Morris Park is conveniently located next to the M74.", 0))
        fragments.append(KnowledgeHit("core_facts", "Facilities include " + ", ".join(facts["facilities"]) + ".", 0))
        contact = facts["contact"]
        fragments.append(KnowledgeHit("core_facts", f"Contact: {contact['phone']} or {contact['email']}.", 0))

        for item in self.availability["public_availability"]:
            text = item["label"]
            if item.get("price"):
                text += f" is publicly listed {item['price']}"
            if item.get("notes"):
                text += f". {item['notes']}"
            fragments.append(KnowledgeHit("availability", text, 0))

        for section in re.split(r"\n## ", self.faq):
            section = section.strip()
            if section and not section.startswith("# Morris"):
                fragments.append(KnowledgeHit("faq", section.replace("\n", " "), 0))

        for category, rules in self.guardrails.items():
            for rule in rules:
                fragments.append(KnowledgeHit(f"guardrails:{category}", rule, 0))

        return fragments

    def persona_prompt(self) -> str:
        contact = self.core_facts["contact"]
        facilities = ", ".join(self.core_facts["facilities"])
        availability = "; ".join(
            item["label"] + (f" ({item['price']})" if item.get("price") else "")
            for item in self.availability["public_availability"]
        )
        rules = " ".join(self.guardrails["pricing"] + self.guardrails["availability"] + self.guardrails["voice_style"])
        return (
            "You are the full-duplex AI voice guide for Morris Park in Glasgow. "
            "Keep listening while speaking; if the visitor interrupts, stop and answer the new intent. "
            f"Known facts: Morris Park is at {self.core_facts['address']}. "
            "It is conveniently located next to the M74. "
            f"Facilities include {facilities}. "
            f"Public availability includes {availability}. "
            f"Contact: {contact['phone']}, {contact['email']}. "
            f"Rules: {rules}"
        )

    def search(self, query: str, limit: int = 4) -> list[KnowledgeHit]:
        query_terms = self._terms(query)
        scored: list[KnowledgeHit] = []
        for fragment in self.fragments:
            terms = self._terms(fragment.text + " " + fragment.source)
            score = len(query_terms & terms)
            if "price" in query_terms or "cost" in query_terms or "rent" in query_terms:
                if "guardrails:pricing" in fragment.source or "£450" in fragment.text:
                    score += 4
            if "available" in query_terms or "availability" in query_terms or "space" in query_terms:
                if fragment.source == "availability":
                    score += 3
            if "parking" in query_terms:
                if "parking" in terms:
                    score += 3
            if score > 0:
                scored.append(KnowledgeHit(fragment.source, fragment.text, score))

        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:limit]

    def context_for(self, query: str) -> str:
        hits = self.search(query)
        if not hits:
            return ""
        bullets = "\n".join(f"- {hit.text}" for hit in hits)
        return f"Relevant Morris Park facts:\n{bullets}"

    def _terms(self, text: str) -> set[str]:
        return {
            term
            for term in re.findall(r"[a-z0-9£]+", text.lower())
            if len(term) > 1 and term not in STOP_WORDS
        }


def guarded_reply(query: str, kb: KnowledgeBase) -> str:
    context = kb.context_for(query)
    if not context:
        return "I can help with Morris Park spaces, facilities, location, or booking a viewing."

    clean = query.lower()
    if any(word in clean for word in ["price", "cost", "rent", "deposit", "lease", "contract", "discount"]):
        return (
            "I can only confirm public pricing. The public listing mentions MOT vehicle parking from "
            "£450 per month. For exact rent, lease terms, deposits, or current availability, I can pass "
            "your enquiry to the Morris Park team."
        )
    if "where" in clean or "address" in clean or "location" in clean:
        return "Morris Park is at 37 Rosyth Road, Glasgow G5 0YE, conveniently located next to the M74."
    if "parking" in clean:
        return "Yes, Morris Park lists on-site parking. It is also close to the M74, which is useful for teams and visitors travelling by car."
    if "available" in clean or "space" in clean or "office" in clean or "unit" in clean:
        return (
            "The public availability page lists a 610 sq ft work zone, a 473 sq ft two-room space with "
            "reception area, yard space, and MOT vehicle parking from £450 per month. Real-time availability "
            "should be confirmed with the Morris Park team."
        )
    if "wifi" in clean or "wi-fi" in clean or "internet" in clean or "facilities" in clean:
        return "Facilities include high-speed Wi-Fi, on-site parking, CCTV security, shower facilities, and an on-site cafe."
    return "Here is what I found: " + " ".join(line.removeprefix("- ") for line in context.splitlines()[1:3])
