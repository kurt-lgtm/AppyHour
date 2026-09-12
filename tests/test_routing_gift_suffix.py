"""Gift twins cannot block or broaden routing. No Shopify, credentials or history DB."""
import ast
from pathlib import Path

import pytest

from appyhour_lib.routing_scope import without_gift_redemption_twins


def test_exclude_a_only_preserve_identity_and_numeric_gifts(capsys):
    orders = [{"name": name, "tags": ["gift redemption"]} for name in
              ["#182723", "#182723A", "#182724B", " #182725a ", "#182726"]]
    result = without_gift_redemption_twins(orders)
    assert result == [orders[0], orders[2], orders[4]]
    assert result[0] is orders[0]
    assert orders[1]["name"] == "#182723A"
    assert "Dropped gift redemption: #182723A" in capsys.readouterr().out


def test_message_bounded_and_normal_cohort_quiet(capsys):
    assert without_gift_redemption_twins([{"name": "#182723"}])
    assert capsys.readouterr().out == ""
    assert without_gift_redemption_twins([{"name": f"#{i}A"} for i in range(25)]) == []
    output = capsys.readouterr().out
    assert "+5 more" in output and "#24A" not in output


def _function(filename, name, namespace):
    # Execute the actual reader without importing box_simulation's unrelated local
    # settings/utility imports. The code under test makes no local authority reads.
    tree = ast.parse((Path(__file__).parents[1] / filename).read_text(encoding="utf-8-sig"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("reader", ["box", "matrix"])
def test_reader_skips_twin_before_deep_fetch_and_continues_pagination(capsys, reader):
    calls = []
    pages = [
        {"orders": {"edges": [{"node": {"name": "#182723A", "lineItems": {"pageInfo": {"hasNextPage": True}}}}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "page2"}}},
        {"orders": {"edges": [{"node": {"name": "#182723", "lineItems": {"pageInfo": {"hasNextPage": False}}}}],
                    "pageInfo": {"hasNextPage": False}}},
    ]
    def graphql(base, headers, query, variables, resource=None):
        assert query == "cohort", "excluded twin must not fetch extra line items"
        calls.append((variables, resource))
        return pages.pop(0)
    if reader == "box":
        fetch = _function("box_simulation.py", "fetch_all_orders", {
            "cohort_query": lambda tag: tag, "QUERY": "cohort", "DEEP_QUERY": "deep",
            "shopify_graphql": graphql,
        })
        result = fetch("unused", {}, "test", live=True)
        assert all(c[1] == "orders-live" for c in calls)
    else:
        fetch = _function("matrix_commander.py", "_fetch_orders_graphql", {
            "MATRIX_ORDERS_QUERY": "cohort", "MATRIX_LINE_ITEMS_QUERY": "deep",
            "_shopify_graphql_matrix": graphql,
        })
        result = fetch("test", "unused", {})
    assert [o["name"] for o in result] == ["#182723"]
    assert len(calls) == 2 and calls[1][0]["cursor"] == "page2"
    assert "Dropped gift redemption" in capsys.readouterr().out


def test_all_twins_matrix_stops_cleanly_before_product_or_file_work(capsys):
    generate = _function("matrix_commander.py", "generate_matrix_xlsx", {
        "Optional": __import__("typing").Optional,
        "_get_shopify_auth": lambda: ("unused", {}),
        "_fetch_orders_graphql": lambda *a: [{"name": "#182723A"}],
        "_RED": "", "_RESET": "",
        "load_mfg_translations": lambda: pytest.fail("no products to resolve"),
    })
    assert generate("test") == ""
    assert "Dropped gift redemption" in capsys.readouterr().out
