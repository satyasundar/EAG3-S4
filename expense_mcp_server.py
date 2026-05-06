"""
Expense Tracker MCP Server
--------------------------
Three tools:
  1. fetch_exchange_rates  -> hits a free FX API (internet)
  2. expense_crud          -> CRUD on expenses.json (local file)
  3. show_expense_dashboard -> reactive Prefab UI (UI)

Run:
    fastmcp dev server.py        # local preview
    fastmcp run server.py        # production
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

import httpx

from fastmcp import FastMCP
from fastmcp.tools import ToolResult

from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Alert,
    Badge,
    Card,
    CardContent,
    Column,
    Else,
    ForEach,
    Grid,
    Heading,
    If,
    Muted,
    Row,
    Select,
    SelectOption,
    Separator,
    Slider,
    Switch,
    Text,
)
from prefab_ui.components.charts import BarChart, ChartSeries, LineChart
from prefab_ui.rx import Rx


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_FILE = Path(__file__).parent / "expenses.json"
FX_API = "https://open.er-api.com/v6/latest/INR"  # free, no key required

CATEGORIES = ["Food", "Transport", "Shopping", "Bills", "Entertainment", "Other"]
SUPPORTED_CURRENCIES = ["INR", "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "SGD"]

mcp = FastMCP("Expense Tracker")


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _empty_store() -> dict:
    return {
        "expenses": [],
        "rates": {},          # currency -> rate relative to INR (1 unit of currency = X INR)
        "rates_updated_at": None,
        "rates_base": "INR",
    }


def _load() -> dict:
    if not DATA_FILE.exists():
        DATA_FILE.write_text(json.dumps(_empty_store(), indent=2))
    try:
        return json.loads(DATA_FILE.read_text())
    except json.JSONDecodeError:
        # corrupted file -> start fresh
        DATA_FILE.write_text(json.dumps(_empty_store(), indent=2))
        return _empty_store()


def _save(store: dict) -> None:
    DATA_FILE.write_text(json.dumps(store, indent=2))


# ---------------------------------------------------------------------------
# Tool 1: fetch_exchange_rates  (internet)
# ---------------------------------------------------------------------------

@mcp.tool
def fetch_exchange_rates() -> dict:
    """
    Fetch live currency exchange rates and cache them locally.

    Uses open.er-api.com (free, no key) with INR as the base currency.
    The cached rates are used by the dashboard to convert all expenses to INR.

    Returns:
        Dict with rates, base currency, and last-updated timestamp.
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(FX_API)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Network error: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Unexpected error: {e}"}

    if payload.get("result") != "success":
        return {"ok": False, "error": "API returned failure", "raw": payload}

    # API gives "1 INR = X foreign", we want the inverse: "1 foreign = Y INR"
    raw_rates = payload.get("rates", {})
    inr_rates = {"INR": 1.0}
    for cur in SUPPORTED_CURRENCIES:
        if cur == "INR":
            continue
        rate = raw_rates.get(cur)
        if rate and rate > 0:
            inr_rates[cur] = round(1.0 / rate, 6)

    store = _load()
    store["rates"] = inr_rates
    store["rates_updated_at"] = datetime.utcnow().isoformat() + "Z"
    store["rates_base"] = "INR"
    _save(store)

    return {
        "ok": True,
        "base": "INR",
        "rates": inr_rates,
        "updated_at": store["rates_updated_at"],
        "currencies_available": list(inr_rates.keys()),
    }


# ---------------------------------------------------------------------------
# Tool 2: expense_crud  (local file CRUD)
# ---------------------------------------------------------------------------

@mcp.tool
def expense_crud(
    action: Literal["create", "read", "update", "delete"],
    expense_id: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    category: Optional[str] = None,
    date: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """
    Create, read, update, or delete expenses in the local expenses.json file.

    Args:
        action: One of "create", "read", "update", "delete".
        expense_id: Required for update/delete. Returned by create.
        amount: Numeric amount in the given currency. Required for create.
        currency: ISO code (INR, USD, EUR, GBP, JPY, AUD, CAD, SGD).
                  Required for create. Default INR.
        category: One of Food, Transport, Shopping, Bills, Entertainment, Other.
        date: ISO date string YYYY-MM-DD. Defaults to today.
        note: Free-text description.

    Returns:
        Dict with the action result and current expense list.
    """
    store = _load()
    expenses: list = store["expenses"]

    if action == "read":
        return {"ok": True, "count": len(expenses), "expenses": expenses}

    if action == "create":
        if amount is None or amount <= 0:
            return {"ok": False, "error": "amount must be a positive number"}
        cur = (currency or "INR").upper()
        if cur not in SUPPORTED_CURRENCIES:
            return {
                "ok": False,
                "error": f"currency must be one of {SUPPORTED_CURRENCIES}",
            }
        cat = category or "Other"
        if cat not in CATEGORIES:
            return {"ok": False, "error": f"category must be one of {CATEGORIES}"}
        d = date or datetime.utcnow().date().isoformat()
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            return {"ok": False, "error": "date must be YYYY-MM-DD"}

        new_expense = {
            "id": uuid.uuid4().hex[:8],
            "amount": float(amount),
            "currency": cur,
            "category": cat,
            "date": d,
            "note": note or "",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        expenses.append(new_expense)
        _save(store)
        return {"ok": True, "created": new_expense, "total_count": len(expenses)}

    if action == "update":
        if not expense_id:
            return {"ok": False, "error": "expense_id required for update"}
        for exp in expenses:
            if exp["id"] == expense_id:
                if amount is not None:
                    if amount <= 0:
                        return {"ok": False, "error": "amount must be positive"}
                    exp["amount"] = float(amount)
                if currency is not None:
                    cur = currency.upper()
                    if cur not in SUPPORTED_CURRENCIES:
                        return {
                            "ok": False,
                            "error": f"currency must be one of {SUPPORTED_CURRENCIES}",
                        }
                    exp["currency"] = cur
                if category is not None:
                    if category not in CATEGORIES:
                        return {
                            "ok": False,
                            "error": f"category must be one of {CATEGORIES}",
                        }
                    exp["category"] = category
                if date is not None:
                    try:
                        datetime.strptime(date, "%Y-%m-%d")
                    except ValueError:
                        return {"ok": False, "error": "date must be YYYY-MM-DD"}
                    exp["date"] = date
                if note is not None:
                    exp["note"] = note
                _save(store)
                return {"ok": True, "updated": exp}
        return {"ok": False, "error": f"expense_id {expense_id} not found"}

    if action == "delete":
        if not expense_id:
            return {"ok": False, "error": "expense_id required for delete"}
        new_list = [e for e in expenses if e["id"] != expense_id]
        if len(new_list) == len(expenses):
            return {"ok": False, "error": f"expense_id {expense_id} not found"}
        store["expenses"] = new_list
        _save(store)
        return {"ok": True, "deleted_id": expense_id, "remaining_count": len(new_list)}

    return {"ok": False, "error": f"unknown action: {action}"}


# ---------------------------------------------------------------------------
# Tool 3: show_expense_dashboard  (Prefab UI)
# ---------------------------------------------------------------------------

def _to_inr(amount: float, currency: str, rates: dict) -> float:
    """Convert a foreign amount to INR using cached rates. Falls back to amount."""
    if currency == "INR":
        return amount
    rate = rates.get(currency)
    if not rate:
        return amount  # graceful fallback if rates not fetched yet
    return round(amount * rate, 2)


def _category_totals(expenses: list, rates: dict) -> list:
    """Aggregate expenses by category, all converted to INR."""
    totals = {cat: 0.0 for cat in CATEGORIES}
    for exp in expenses:
        inr = _to_inr(exp["amount"], exp["currency"], rates)
        totals[exp["category"]] = totals.get(exp["category"], 0.0) + inr
    return [
        {"category": cat, "amount_inr": round(amt, 2)}
        for cat, amt in totals.items()
        if amt > 0
    ]


def _daily_totals(expenses: list, rates: dict, days: int = 30) -> list:
    """Spending per day for the last N days, in INR."""
    today = datetime.utcnow().date()
    bucket = {(today - timedelta(days=i)).isoformat(): 0.0 for i in range(days)}
    for exp in expenses:
        if exp["date"] in bucket:
            bucket[exp["date"]] += _to_inr(exp["amount"], exp["currency"], rates)
    # oldest -> newest for the chart
    return [
        {"date": d, "amount_inr": round(bucket[d], 2)}
        for d in sorted(bucket.keys())
    ]


@mcp.tool(app=True)
def show_expense_dashboard() -> ToolResult:
    """
    Render an interactive expense dashboard with reactive filters.

    The dashboard reads from expenses.json and uses cached exchange rates
    to display all amounts in INR. Filters (category, min amount, currency
    display toggle) update the view live with no server round-trips.
    """
    store = _load()
    expenses = store["expenses"]
    rates = store.get("rates", {"INR": 1.0})
    rates_updated = store.get("rates_updated_at")

    # Pre-compute everything at build time for the LLM summary + charts
    total_inr = sum(_to_inr(e["amount"], e["currency"], rates) for e in expenses)
    by_category = _category_totals(expenses, rates)
    by_day = _daily_totals(expenses, rates, days=14)
    biggest = (
        max(by_category, key=lambda x: x["amount_inr"])["category"]
        if by_category else "—"
    )

    # Add an `amount_inr` field to each expense so the reactive filter slider
    # can compare against a single normalized number.
    enriched_expenses = []
    for e in expenses:
        enriched_expenses.append({
            **e,
            "amount_inr": _to_inr(e["amount"], e["currency"], rates),
        })

    # ---- Build the view -------------------------------------------------
    with Column(gap=4, css_class="p-6") as view:
        Heading("💰 Expense Dashboard")
        if rates_updated:
            Muted(f"Exchange rates last updated: {rates_updated}")
        else:
            Muted("⚠ Exchange rates not fetched yet — non-INR amounts shown as-is.")

        Separator()

        # Empty-state vs populated dashboard
        if not expenses:
            Alert(
                title="No expenses yet",
                description="Add some expenses with the expense_crud tool, then reopen this dashboard.",
                variant="warning",
            )
        else:
            # ---- Summary cards -----------------------------------------
            with Grid(columns=3, gap=4):
                with Card():
                    with CardContent():
                        Muted("Total Spent (INR)")
                        Heading(f"₹{total_inr:,.2f}")
                with Card():
                    with CardContent():
                        Muted("Number of Expenses")
                        Heading(str(len(expenses)))
                with Card():
                    with CardContent():
                        Muted("Biggest Category")
                        Heading(biggest)

            Separator()

            # ---- Filters (reactive) ------------------------------------
            Heading("Filters", level=3)
            with Row(gap=4, align="center"):
                with Select(name="category_filter", label="Category"):
                    SelectOption("All", value="All")
                    for cat in CATEGORIES:
                        SelectOption(cat, value=cat)
                Slider(
                    name="min_amount",
                    label="Minimum amount (INR)",
                    min=0,
                    max=max(int(total_inr) + 1, 1000),
                    step=50,
                )
                Switch(name="hide_small", label="Hide expenses under ₹100")

            Separator()

            # ---- Charts ------------------------------------------------
            with Grid(columns=2, gap=4):
                with Card():
                    with CardContent():
                        Heading("Spending by Category", level=3)
                        if by_category:
                            BarChart(
                                data=by_category,
                                series=[ChartSeries(data_key="amount_inr", label="INR")],
                                x_axis="category",
                            )
                        else:
                            Muted("No data yet")
                with Card():
                    with CardContent():
                        Heading("Last 14 Days", level=3)
                        if by_day:
                            LineChart(
                                data=by_day,
                                series=[ChartSeries(data_key="amount_inr", label="INR")],
                                x_axis="date",
                            )
                        else:
                            Muted("No data yet")

            Separator()

            # ---- Reactive expense list ---------------------------------
            Heading("Expenses", level=3)
            cat_filter = Rx("category_filter")
            min_amt = Rx("min_amount")
            hide_small = Rx("hide_small")

            # with ForEach("expenses") as exp:
            #     # Show this card only if it passes ALL active filters.
            #     # We render every card wrapped in If; non-matching ones collapse.
            #     category_ok = (cat_filter == "All") | (cat_filter == exp.category)
            #     amount_ok = exp.amount_inr >= min_amt
            #     size_ok = hide_small.then(exp.amount_inr >= 100, True)

            #     with If(category_ok & amount_ok & size_ok):
            #         with Card():
            #             with CardContent():
            #                 with Row(gap=4, align="center"):
            #                     Badge(exp.category, variant="default")
            #                     with Column(gap=1):
            #                         Text(
            #                             exp.amount.currency(exp.currency),
            #                             css_class="font-semibold text-lg",
            #                         )
            #                         Muted(exp.date)
            #                     Text(exp.note)
            #                     Muted(f"id: {exp.id}")
            with ForEach("expenses") as exp:
                category_ok = (cat_filter == "All") | (cat_filter == exp["category"])
                amount_ok = exp["amount_inr"] >= min_amt
                size_ok = hide_small.then(exp["amount_inr"] >= 100, True)

                with If(category_ok & amount_ok & size_ok):
                    with Card():
                        with CardContent():
                            with Row(gap=4, align="center"):
                                Badge(exp["category"], variant="default")
                                with Column(gap=1):
                                    Text(
                                        exp["amount_inr"],
                                        css_class="font-semibold text-lg",
                                    )
                                    Muted(exp["date"])
                                Text(exp["note"])
                                Muted(exp["id"])

    # ---- Tool result ----------------------------------------------------
    summary = (
        f"Expense dashboard rendered. {len(expenses)} expenses, "
        f"total ₹{total_inr:,.2f} (INR equivalent). "
        f"Biggest category: {biggest}. "
        f"{'Rates current.' if rates_updated else 'Rates NOT fetched — call fetch_exchange_rates first for accurate INR conversion.'}"
    )

    return ToolResult(
        content=summary,
        structured_content=PrefabApp(
            view=view,
            state={
                "expenses": enriched_expenses,
                "category_filter": "All",
                "min_amount": 0,
                "hide_small": False,
            },
        ),
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
