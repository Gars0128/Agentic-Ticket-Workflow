import json
import re
from pathlib import Path

from app.domain.models import Invoice

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

STOPWORDS = {"your", "with", "that", "this", "have", "from", "were", "what", "please"}


def _load(filename: str) -> list[dict]:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def get_customer(customer_id: str) -> dict | None:
    for customer in _load("customers.json"):
        if customer["customer_id"] == customer_id:
            return customer
    return None

def get_customer_name(customer_id: str) -> str | None:
    customer = get_customer(customer_id)
    return customer["name"] if customer else None



def get_invoices(customer_id: str) -> list[Invoice]:
    return [
        Invoice(**item)
        for item in _load("invoices.json")
        if item["customer_id"] == customer_id
    ]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 4 and w not in STOPWORDS}


def search_kb(query: str) -> list[dict]:
    query_words = _words(query)
    scored = []

    # Rank articles by shared keywords across title and text.
    for article in _load("kb.json"):
        score = len(query_words & _words(article["title"] + " " + article["text"]))
        if score > 0:
            scored.append((score, article))

    # Return up to three matches, best first.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [article for _, article in scored[:3]]
