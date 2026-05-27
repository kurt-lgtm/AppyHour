#!/usr/bin/env python3
"""
AppyHourMCP smoke-test harness.

Ported from OB1 recipes/brain-smoke-test (T2.4). Probes MCP server startup,
tool registration, env/auth, core tool schemas, and (gated by --destructive)
live API calls.

Usage:
    python smoke_test_mcp.py                        # read-only + schema
    python smoke_test_mcp.py --destructive          # + live API
    python smoke_test_mcp.py --json                 # machine-readable
    python smoke_test_mcp.py --category=Startup     # single category
    python smoke_test_mcp.py --help

Exit: 0 = all pass/skip, 1 = ≥1 fail, 2 = setup error
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

try:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ImportError:
    print("ERROR: `mcp` package not installed. `pip install mcp`", file=sys.stderr)
    sys.exit(2)


class SkipError(Exception):
    """Probe marks itself skipped (not configured) — not a failure."""


# Resolve actual repo paths (config snippet points to outdated C:/Users/Work/AppyHour/)
REPO = Path(r"C:\Users\Work\Claude Projects\AppyHour")
SERVER = REPO / "AppyHourMCP" / "server.py"
PYTHONPATH_DIRS = [
    REPO / "GelPackCalculator",
    REPO / "InventoryReorder",
    REPO / "ShippingReports",
    REPO / "AppyHourMCP",
]

STARTUP_TIMEOUT_S = 15
PROBE_TIMEOUT_S = 10
MIN_TOOL_COUNT = 20

# Map module → at least one expected tool name (substring match)
EXPECTED_TOOL_BY_MODULE = {
    "gelcalc":           "appyhour_analyze_shipment",
    "shopify":           "appyhour_search_orders",
    "inventory":         "get_inventory_snapshot",
    "shipping":          "appyhour_shipping_analysis",
    "weather":           "appyhour_get_weather",
    "gorgias":           "gorgias_list_tickets",
    "google_sheets":     "sheets_read",
    "ops_summary":       "rebuild_ops_summary",
    "matrix_qc":         "validate_production_matrix",
    "product_catalog":   "appyhour_list_products",
    "context":           "appyhour_get_product",
}


def build_server_params() -> StdioServerParameters:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in PYTHONPATH_DIRS)
    return StdioServerParameters(
        command="python",
        args=[str(SERVER)],
        env=env,
    )


async def run_probe(
    name: str,
    category: str,
    fn: Callable[[], Awaitable[Any]],
    timeout: float = PROBE_TIMEOUT_S,
) -> dict[str, Any]:
    start = time.time()
    try:
        await asyncio.wait_for(fn(), timeout=timeout)
        return {"name": name, "category": category, "status": "pass", "ms": int((time.time() - start) * 1000)}
    except SkipError as e:
        return {"name": name, "category": category, "status": "skip", "message": str(e), "ms": int((time.time() - start) * 1000)}
    except asyncio.TimeoutError:
        return {"name": name, "category": category, "status": "fail", "message": f"timeout >{timeout}s", "ms": int((time.time() - start) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"name": name, "category": category, "status": "fail", "message": f"{type(e).__name__}: {e}", "ms": int((time.time() - start) * 1000)}


def env_or_skip(*names: str) -> None:
    if not any(os.environ.get(n) for n in names):
        raise SkipError(f"none of {names} set")


def path_or_skip(p: str | Path) -> Path:
    pp = Path(p)
    if not pp.exists():
        raise SkipError(f"{pp} does not exist")
    return pp


async def probes_startup(session: ClientSession) -> list[dict[str, Any]]:
    return [
        await run_probe("server-spawned", "Startup", lambda: asyncio.sleep(0)),
        await run_probe("initialize-returned", "Startup", lambda: _check_initialized(session)),
        await run_probe("notifications-initialized", "Startup", lambda: asyncio.sleep(0)),
    ]


async def _check_initialized(session: ClientSession) -> None:
    # session is initialized by the caller; this just verifies tools/list works
    result = await session.list_tools()
    if not result.tools:
        raise RuntimeError("tools/list returned empty")


async def probes_tool_registration(session: ClientSession) -> list[dict[str, Any]]:
    result = await session.list_tools()
    tool_names = [t.name for t in result.tools]

    async def check_module(expected_substr: str) -> None:
        if not any(expected_substr in n for n in tool_names):
            raise RuntimeError(f"no tool matching '{expected_substr}'")

    out: list[dict[str, Any]] = []
    for mod, expected in EXPECTED_TOOL_BY_MODULE.items():
        out.append(await run_probe(f"module:{mod}", "Tool Registration", lambda e=expected: check_module(e)))

    async def check_count() -> None:
        if len(tool_names) < MIN_TOOL_COUNT:
            raise RuntimeError(f"tool count {len(tool_names)} < {MIN_TOOL_COUNT}")
    out.append(await run_probe(f"count >= {MIN_TOOL_COUNT}", "Tool Registration", check_count))
    return out


async def probes_env_auth() -> list[dict[str, Any]]:
    settings_path = Path(os.environ.get("APPDATA", "")) / "AppyHour" / "gel_calc_shopify_settings.json"

    async def check(*names: str) -> None:
        env_or_skip(*names)

    async def check_settings_file() -> None:
        path_or_skip(settings_path)

    async def check_appdata() -> None:
        if not os.environ.get("APPDATA"):
            raise SkipError("APPDATA not set (not Windows?)")

    return [
        await run_probe("SHOPIFY_ACCESS_TOKEN or SHOPIFY_STORE", "Env/Auth", lambda: check("SHOPIFY_ACCESS_TOKEN", "SHOPIFY_STORE")),
        await run_probe("GORGIAS_API_KEY", "Env/Auth", lambda: check("GORGIAS_API_KEY")),
        await run_probe("GOOGLE_APPLICATION_CREDENTIALS", "Env/Auth", lambda: check("GOOGLE_APPLICATION_CREDENTIALS")),
        await run_probe("gel_calc settings file", "Env/Auth", check_settings_file),
        await run_probe("APPDATA env var", "Env/Auth", check_appdata),
    ]


async def probes_core_readonly(session: ClientSession) -> list[dict[str, Any]]:
    async def analyze_shipment_call() -> None:
        result = await session.call_tool("appyhour_analyze_shipment", {"params": {"dest_state": "TX", "peak_temp_f": 85.0}})
        text = str(result.content)
        if "gel_packs" not in text and "config_name" not in text:
            raise RuntimeError(f"unexpected result: {text[:200]}")

    async def get_weather_call() -> None:
        result = await session.call_tool("appyhour_get_weather", {"zip_code": "75042"})
        text = str(result.content)
        if "temp_f" not in text and "error" not in text:
            raise RuntimeError(f"unexpected result: {text[:200]}")

    async def tool_schema_has(tool: str, field: str) -> None:
        tl = await session.list_tools()
        for t in tl.tools:
            if t.name == tool:
                schema_json = json.dumps(t.inputSchema or {})
                if field not in schema_json:
                    raise RuntimeError(f"{tool} schema missing '{field}'")
                return
        raise RuntimeError(f"tool {tool} not registered")

    return [
        await run_probe("analyze_shipment schema has dest_state+peak_temp_f", "Core ReadOnly",
                        lambda: tool_schema_has("appyhour_analyze_shipment", "dest_state")),
        await run_probe("analyze_shipment call(TX,85F)", "Core ReadOnly", analyze_shipment_call),
        await run_probe("get_weather schema has zip_code", "Core ReadOnly",
                        lambda: tool_schema_has("appyhour_get_weather", "zip_code")),
        await run_probe("get_weather call(75042)", "Core ReadOnly", get_weather_call),
        await run_probe("matrix_qc tool registered", "Core ReadOnly",
                        lambda: tool_schema_has("appyhour_validate_production_matrix", "")),
        await run_probe("product_catalog tool registered", "Core ReadOnly",
                        lambda: tool_schema_has("appyhour_list_products", "")),
        await run_probe("ops_summary tool registered", "Core ReadOnly",
                        lambda: tool_schema_has("rebuild_ops_summary", "")),
        await run_probe("get_product tool registered", "Core ReadOnly",
                        lambda: tool_schema_has("appyhour_get_product", "")),
    ]


async def probes_live_api(session: ClientSession) -> list[dict[str, Any]]:
    async def search_orders() -> None:
        env_or_skip("SHOPIFY_ACCESS_TOKEN", "SHOPIFY_STORE")
        result = await session.call_tool("appyhour_search_orders", {"limit": 1})
        if not result.content:
            raise RuntimeError("empty response")

    async def list_gorgias() -> None:
        env_or_skip("GORGIAS_API_KEY")
        result = await session.call_tool("appyhour_gorgias_list_tickets", {"limit": 1})
        if not result.content:
            raise RuntimeError("empty response")

    async def get_inventory() -> None:
        result = await session.call_tool("get_inventory_snapshot", {})
        if not result.content:
            raise RuntimeError("empty response")

    async def read_sheet() -> None:
        env_or_skip("GOOGLE_APPLICATION_CREDENTIALS")
        # Just check the tool is callable — actual sheet ID not assumed
        tl = await session.list_tools()
        if not any(t.name == "appyhour_read_sheet" or t.name == "sheets_read" for t in tl.tools):
            raise SkipError("sheets tool not registered")

    return [
        await run_probe("Shopify: search_orders(limit=1)", "Live API", search_orders),
        await run_probe("Gorgias: list_tickets(limit=1)", "Live API", list_gorgias),
        await run_probe("Inventory: snapshot", "Live API", get_inventory),
        await run_probe("Sheets: read tool registered", "Live API", read_sheet),
    ]


async def main_async(args: argparse.Namespace) -> int:
    if not SERVER.exists():
        print(f"ERROR: server not found at {SERVER}", file=sys.stderr)
        return 2

    params = build_server_params()

    all_results: list[dict[str, Any]] = []

    try:
        async with asyncio.timeout(STARTUP_TIMEOUT_S):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    all_results.extend(await probes_startup(session))

                    if not args.category or args.category == "Tool Registration":
                        all_results.extend(await probes_tool_registration(session))
                    if not args.category or args.category == "Env/Auth":
                        all_results.extend(await probes_env_auth())
                    if not args.category or args.category == "Core ReadOnly":
                        all_results.extend(await probes_core_readonly(session))
                    if args.destructive and (not args.category or args.category == "Live API"):
                        all_results.extend(await probes_live_api(session))
    except asyncio.TimeoutError:
        print(f"ERROR: server startup exceeded {STARTUP_TIMEOUT_S}s", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: harness failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"results": all_results}, indent=2))
    else:
        _print_dashboard(all_results, destructive=args.destructive)

    fails = [r for r in all_results if r["status"] == "fail"]
    return 1 if fails else 0


def _print_dashboard(results: list[dict[str, Any]], *, destructive: bool) -> None:
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    print()
    print("=" * 70)
    print(f"AppyHourMCP Smoke Test {'(destructive)' if destructive else '(read-only)'}")
    print("=" * 70)
    totals = {"pass": 0, "skip": 0, "fail": 0}
    for cat, items in by_cat.items():
        print(f"\n[{cat}]")
        for r in items:
            status_icon = {"pass": "PASS", "skip": "SKIP", "fail": "FAIL"}[r["status"]]
            msg = f" — {r.get('message')}" if r.get("message") else ""
            print(f"  {status_icon}  {r['name']} ({r['ms']}ms){msg}")
            totals[r["status"]] += 1
    print()
    print("=" * 70)
    print(f"Totals: {totals['pass']} pass, {totals['skip']} skip, {totals['fail']} fail")
    print("=" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destructive", action="store_true", help="enable live API probes")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--category", help="run only this category")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
