from app.services.data import get_customer, get_invoices, search_kb, get_customer_name


def test_get_customer_found():
    assert get_customer("C-100")["name"] == "Northwind Bakery"


def test_get_customer_unknown_returns_none():
    assert get_customer("C-999") is None


def test_get_invoices_returns_only_own():
    ids = {i.invoice_id for i in get_invoices("C-100")}
    assert ids == {"INV-1001", "INV-1002"}


def test_get_invoices_empty_for_customer_without_invoices():
    assert get_invoices("C-200") == []


def test_search_kb_finds_password_article():
    assert search_kb("I forgot my password")[0]["article_id"] == "KB-1"


def test_search_kb_no_match():
    assert search_kb("pizza") == []

def test_get_customer_name_found():
    assert get_customer_name("C-100") == "Northwind Bakery"