from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

# These are conversational scaffolding words, not product attributes.  The
# public simulator uses similar phrasing, but removing them is also useful for
# normal shopping conversations.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "im", "in", "is", "it", "me", "my", "of", "on", "or", "please",
    "some", "that", "the", "this", "to", "want", "with", "would", "you",
    "looking", "look", "need", "find", "get", "something", "still", "exploring",
    "key", "requirement", "additional", "preference", "preferences", "actually",
    "ignore", "earlier", "options", "option", "quite", "right", "yet", "ask",
    "one", "specific", "attribute", "regarding", "important", "matters", "matter",
    "use", "judgment", "judgement", "product", "products", "around", "approximately",
    "category", "material", "color", "colour", "size", "style", "brand", "budget",
    "feature", "features", "use_case", "case", "preference", "preferences",
}

ATTRIBUTE_NAMES = (
    "material",
    "other",
    "feature",
    "color",
    "style",
    "use_case",
    "budget",
    "size",
    "brand",
    "category",
)

MATERIALS = (
    "cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk",
    "rayon", "fabric", "denim", "suede", "canvas", "linen", "fleece",
    "rubber", "plastic", "metal", "alloy", "steel", "stainless", "wood",
)

COLORS = (
    "black", "white", "blue", "red", "pink", "green", "yellow", "brown",
    "gray", "grey", "purple", "orange",
)

SIZES = (
    "xxxs", "xxs", "xs", "small", "medium", "large", "xl", "xxl", "xxxl",
)

USE_CASES = (
    "running", "hiking", "gym", "work", "winter", "summer", "outdoor",
    "travel", "vacation", "walking", "beach", "party", "casual", "formal",
)

OVERRIDE_RE = re.compile(
    r"\b(?:actually|instead|rather|change|changed|switch|switched|replace|"
    r"ignore|forget|no longer|i mean)\b",
    re.IGNORECASE,
)

NO_PREFERENCE_RE = re.compile(
    r"\bi\s+don['’]?t\s+have\s+(?:a|an)\s+(?:additional\s+)?preference\b"
    r"|\bno\s+preference\b",
    re.IGNORECASE,
)

BOUNDARY_NO_PREFERENCE_RE = re.compile(
    r"\bi\s+don['’]?t\s+have\s+a\s+preference\s+for\b",
    re.IGNORECASE,
)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _terms(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            token.lower()
            for token in TOKEN_RE.findall(text)
            if len(token) > 1 and token.lower() not in STOPWORDS
        )
    )


def _price(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _extract_budget(text: str) -> float | None:
    message = text.lower()
    patterns = (
        r"(?:under|below|less\s+than|up\s+to|max(?:imum)?|at\s+most)\s+"
        r"\$?\s*(\d+(?:\.\d+)?)",
        r"\$\s*(\d+(?:\.\d+)?)",
        r"(?:budget|price)\s*(?:of|around|about|is|:)?\s*\$?\s*"
        r"(\d+(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return float(match.group(1))
    return None


def _latest_word(text: str, values: tuple[str, ...]) -> str | None:
    lowered = text.lower()
    matches: list[tuple[int, str]] = []
    for value in values:
        for match in re.finditer(rf"\b{re.escape(value)}\b", lowered):
            matches.append((match.start(), value))
    if not matches:
        return None
    return max(matches)[1]


def _extract_color(text: str) -> str | None:
    if NO_PREFERENCE_RE.search(text):
        return None
    return _latest_word(text, COLORS)


def _extract_material(text: str) -> str | None:
    if NO_PREFERENCE_RE.search(text):
        return None
    return _latest_word(text, MATERIALS)


def _extract_size(text: str) -> str | None:
    if NO_PREFERENCE_RE.search(text):
        return None
    size = _latest_word(text, SIZES)
    if size:
        return size
    match = re.search(r"\bsize\s*([0-9]+)\b", text.lower())
    return match.group(1) if match else None


def _extract_use_case(text: str) -> str | None:
    if NO_PREFERENCE_RE.search(text):
        return None
    return _latest_word(text, USE_CASES)


def _extract_category_phrase(text: str) -> str | None:
    match = re.search(
        r"\blooking\s+for\s+(.+?)(?=,\s*but\b|,\s*a\s+key\b|[.!?]|$)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    phrase = match.group(1).strip(" ,;:")
    return phrase or None


def _is_override(text: str) -> bool:
    return bool(OVERRIDE_RE.search(text))


def _content_terms(text: str) -> list[str]:
    """Extract product-bearing words from a customer message.

    The evaluator's replies contain useful values after a colon, while the
    surrounding sentence is only dialog scaffolding.  Removing that scaffolding
    keeps generic words such as ``preference`` and ``attribute`` from competing
    with actual catalog terms.
    """
    if NO_PREFERENCE_RE.search(text):
        return []

    cleaned = text
    cleaned = re.sub(
        r"\b(?:i['’]?m|i\s+am)\s+looking\s+for\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bbut\s+i['’]?m\s+still\s+exploring\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\ba\s+key\s+requirement\s+is\s*:\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bactually,?\s*ignore\s+my\s+earlier\s+preference\.?(?:\s*what\s+i\s+need\s+is)?\s*:??",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bfor\s+that,?\s*what\s+matters\s+is\s*:\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bthose\s+options\s+are\s+not\s+quite\s+right\s+yet.*$",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )

    category_phrase = _extract_category_phrase(text)
    if category_phrase:
        cleaned = cleaned.replace(category_phrase, " ")

    return _terms(cleaned)[:24]


def _quote_terms(terms: list[str]) -> str:
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


class Agent:
    """Conversational BM25 agent with active state and query routing."""

    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        self.catalog_path = Path(catalog_path)
        self.connection = sqlite3.connect(":memory:")
        self._sessions: dict[str, dict] = {}
        self.prices: dict[str, float | None] = {}
        self._build_index()

    def _build_index(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, "
            "store, description, tokenize='unicode61 remove_diacritics 2')"
        )

        batch: list[tuple[str, str, str, str, str, str, str]] = []
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line in handle:
                product = json.loads(line)
                parent_asin = str(product["parent_asin"])
                self.prices[parent_asin] = _price(product.get("price"))
                batch.append(
                    (
                        parent_asin,
                        _text(product.get("title")),
                        _text(product.get("categories")),
                        _text(product.get("features")),
                        _text(product.get("details")),
                        _text(product.get("store")),
                        _text(product.get("description")),
                    )
                )
                if len(batch) >= 1000:
                    cursor.executemany(
                        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                    batch.clear()
        if batch:
            cursor.executemany(
                "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
        self.connection.commit()

    def reset(self, session_id: str, user_profile: dict) -> None:
        self._sessions[session_id] = {
            "history": [],
            "category_terms": [],
            "category_text": "",
            "content_terms": [],
            "stale_terms": [],
            "slots": {},
            "user_profile": user_profile or {},
            "asked_attributes": [],
            "last_asked_attribute": None,
            "repeat_attribute": None,
            "intent": "unknown",
            "override_count": 0,
        }

    def _update_state(self, session: dict, message: str) -> None:
        override = _is_override(message)
        category_phrase = _extract_category_phrase(message)
        if category_phrase:
            category_terms = _terms(category_phrase)
            if category_terms:
                session["category_terms"] = category_terms
                session["category_text"] = category_phrase

        if override:
            # The category usually remains valid across an intent change, but
            # old terms should only become weak evidence.  Keeping a decayed
            # copy is useful when an old preference is compatible with the new
            # one, while current terms still get the strongest score.
            session["stale_terms"] = list(
                dict.fromkeys(
                    session.get("stale_terms", []) + session["content_terms"]
                )
            )[-32:]
            session["content_terms"] = []
            session["slots"] = {}
            session["asked_attributes"] = []
            session["last_asked_attribute"] = None
            session["repeat_attribute"] = None
            session["override_count"] += 1
            session["intent"] = "buying"

        if "still exploring" in message.lower() or NO_PREFERENCE_RE.search(message):
            if session["intent"] == "unknown":
                session["intent"] = "browsing"
        elif _content_terms(message):
            session["intent"] = "buying"

        content = _content_terms(message)
        if content:
            session["content_terms"] = list(
                dict.fromkeys(session["content_terms"] + content)
            )[-48:]

        budget = _extract_budget(message)
        if budget is not None:
            session["slots"]["budget"] = budget
        for name, value in (
            ("color", _extract_color(message)),
            ("material", _extract_material(message)),
            ("size", _extract_size(message)),
            ("use_case", _extract_use_case(message)),
        ):
            if value is not None:
                session["slots"][name] = value

        last_asked = session.get("last_asked_attribute")
        if last_asked and BOUNDARY_NO_PREFERENCE_RE.search(message):
            session["repeat_attribute"] = last_asked
        elif session.get("repeat_attribute") and not BOUNDARY_NO_PREFERENCE_RE.search(message):
            session["repeat_attribute"] = None

        session["history"].append(message)

    def _profile_terms(self, session: dict) -> list[str]:
        profile = session.get("user_profile") or {}
        values: list[str] = []
        for tag in profile.get("preference_tags", []):
            values.extend(_terms(str(tag)))
        return list(dict.fromkeys(values))[:16]

    def _recommendations(self, session: dict, top_k: int) -> list[dict]:
        category_terms = list(session.get("category_terms") or [])
        content_terms = list(session.get("content_terms") or [])
        stale_terms = list(session.get("stale_terms") or [])
        profile_terms = self._profile_terms(session)

        query_terms = list(
            dict.fromkeys(category_terms + stale_terms + content_terms)
        )
        if not content_terms:
            query_terms = list(dict.fromkeys(query_terms + profile_terms))
        if not query_terms:
            return []

        result_limit = max(0, min(10, int(top_k)))
        if result_limit == 0:
            return []

        budget = session.get("slots", {}).get("budget")
        fetch_limit = result_limit
        if budget is not None:
            # Fetch a wider pool before moving known over-budget products down.
            fetch_limit = max(50, result_limit * 10)

        weights = (0.0, 5.0, 3.5, 5.0, 4.0, 0.8, 1.0)
        and_expression = " AND ".join(
            f'"{term.replace(chr(34), chr(34) * 2)}"'
            for term in query_terms
        )
        and_rows = self.connection.execute(
            "SELECT parent_asin FROM products WHERE products MATCH ? "
            "ORDER BY bm25(products, ?, ?, ?, ?, ?, ?, ?), parent_asin "
            "LIMIT ?",
            (and_expression, *weights, fetch_limit),
        ).fetchall()

        # A strict conjunction is very effective when the customer has supplied
        # several compatible constraints.  If it is too selective, preserve its
        # candidates first and fill the remaining slots with an OR retrieval.
        ranked_rows = list(and_rows)
        if len(ranked_rows) < result_limit:
            or_rows = self.connection.execute(
                "SELECT parent_asin FROM products WHERE products MATCH ? "
                "ORDER BY bm25(products, ?, ?, ?, ?, ?, ?, ?), parent_asin "
                "LIMIT ?",
                (" OR ".join(
                    f'"{term.replace(chr(34), chr(34) * 2)}"'
                    for term in query_terms
                ), *weights, fetch_limit),
            ).fetchall()
            seen = {str(row[0]) for row in ranked_rows}
            for row in or_rows:
                if str(row[0]) in seen:
                    continue
                ranked_rows.append(row)
                seen.add(str(row[0]))
                if len(ranked_rows) >= fetch_limit:
                    break

        if budget is not None:
            in_budget: list[tuple[object]] = []
            unknown_price: list[tuple[object]] = []
            over_budget: list[tuple[object]] = []
            for row in ranked_rows:
                product_price = self.prices.get(str(row[0]))
                if product_price is None:
                    unknown_price.append(row)
                elif product_price <= float(budget):
                    in_budget.append(row)
                else:
                    over_budget.append(row)
            ranked_rows = in_budget + unknown_price + over_budget

        return [
            {"parent_asin": str(row[0])}
            for row in ranked_rows[:result_limit]
        ]

    def _next_attribute(self, session: dict) -> str | None:
        repeat = session.get("repeat_attribute")
        if repeat:
            session["repeat_attribute"] = None
            return repeat

        asked = set(session.get("asked_attributes") or [])
        for attribute in ATTRIBUTE_NAMES:
            if attribute in asked:
                continue
            if attribute == "category" and session.get("category_terms"):
                continue
            session.setdefault("asked_attributes", []).append(attribute)
            return attribute
        return None

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        if session_id not in self._sessions:
            raise RuntimeError("reset must be called before respond")

        session = self._sessions[session_id]
        self._update_state(session, user_message)
        recommendations = self._recommendations(session, top_k)
        next_attribute = self._next_attribute(session)
        session["last_asked_attribute"] = next_attribute

        if next_attribute is None:
            message = "Here are the strongest matches for your current preferences."
        else:
            message = f"To narrow this down, what matters most about the {next_attribute}?"

        return {
            "message": message,
            "ask_attribute": next_attribute,
            "recommendations": recommendations,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
            },
        }
