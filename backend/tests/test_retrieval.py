import pytest

from sqlagent.retrieval import SchemaRetriever, tokenize


@pytest.fixture(scope="module")
def retriever(catalog):
    return SchemaRetriever(catalog, top_tables=5)


def test_tokenize_normalizes_plurals_and_snake_case():
    assert tokenize("Top categories by ss_net_paid") == ["category", "ss", "net", "paid"]


def test_linked_tables_are_kept_and_unrelated_lexical_hits_dropped(retriever):
    res = retriever.retrieve("Which states have the most customers?", ["customer", "customer_address"])
    assert set(res.tables) == {"customer", "customer_address"}


def test_bridge_table_added_for_linked_tables(retriever):
    res = retriever.retrieve("revenue by income band", ["store_sales", "income_band"])
    assert "household_demographics" in res.added_for_joins
    assert "date_dim" in res.tables


def test_never_bridges_through_calendar(retriever):
    res = retriever.retrieve("customers and their stores", ["customer", "store"])
    assert "date_dim" not in res.added_for_joins


def test_bm25_adds_strong_adjacent_column_match(retriever):
    res = retriever.retrieve("Which promotions that ran on TV drove the most units?", ["store_sales"])
    assert "promotion" in res.tables


def test_fallback_without_linked_tables(retriever):
    res = retriever.retrieve("lowest stock of electronics by warehouse")
    assert {"inventory", "warehouse", "item"} <= set(res.tables)
