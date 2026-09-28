import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.modules.crafting.models import CraftAnalysisResult, CraftAnalysisRun
from app.modules.crafting.service import (
    CraftAnalyzer,
    HideoutRecipe,
    PricePoint,
    RecipeComponent,
    _liquidity_status,
    _recipe_path,
    filter_disabled_feature_recipes,
    read_average_prices,
    read_latest_analysis,
)


def test_craft_analyzer_prefers_buying_ingredient_when_market_is_cheaper() -> None:
    recipes = [
        _recipe("combat_pea", 1, [("raw_pea", 2)]),
        _recipe("pea_soup", 1, [("combat_pea", 3)]),
    ]
    prices = {
        "raw_pea": PricePoint(Decimal("10"), amount=1, sale_count=1),
        "combat_pea": PricePoint(Decimal("15"), amount=1, sale_count=1),
        "pea_soup": PricePoint(Decimal("200"), amount=1, sale_count=1),
    }
    analyzer = CraftAnalyzer(
        recipes,
        prices,
        {
            "combat_pea": "Боевой горох",
            "pea_soup": "Гороховый суп",
            "raw_pea": "Горох",
        },
    )

    result = analyzer.analyze_recipe(recipes[1], Decimal("20"))

    assert result.craft_cost == Decimal("45")
    assert result.is_profitable is True
    assert result.market_amount == 1
    assert result.market_sale_count == 1
    assert result.liquidity_score == Decimal("1")
    assert result.liquidity_status == "low"
    assert result.ingredients[0].decision == "buy"
    assert result.ingredients[0].craft_price == Decimal("20")


def test_craft_analyzer_can_craft_missing_market_ingredient() -> None:
    recipes = [
        _recipe("crafted_only", 2, [("raw_pea", 4)]),
        _recipe("pea_soup", 1, [("crafted_only", 3)]),
    ]
    prices = {
        "raw_pea": PricePoint(Decimal("10"), amount=1, sale_count=1),
        "pea_soup": PricePoint(Decimal("70"), amount=1, sale_count=1),
    }

    result = CraftAnalyzer(recipes, prices, {}).analyze_recipe(recipes[1], Decimal("20"))

    assert result.craft_cost == Decimal("60")
    assert result.ingredients[0].decision == "craft"
    assert result.ingredients[0].chosen_price == Decimal("20")
    assert result.ingredients[0].craft_output_amount == 2


def test_read_average_prices_uses_saved_hourly_aggregates() -> None:
    session = AsyncMock()
    session.execute.return_value = [
        SimpleNamespace(
            item_id="item-1",
            price=Decimal("125"),
            amount=3,
            sale_count=2,
        )
    ]

    result = asyncio.run(
        read_average_prices(
            session,
            {"item-1"},
            datetime(2026, 9, 27, 12, tzinfo=UTC),
            24,
        )
    )

    statement = str(session.execute.await_args.args[0])
    assert "sale_aggregates" in statement
    assert result["item-1"].price == Decimal("125")
    assert result["item-1"].amount == 3


def test_liquidity_status_uses_daily_sale_thresholds() -> None:
    assert _liquidity_status(Decimal("0")) == "none"
    assert _liquidity_status(Decimal("1")) == "low"
    assert _liquidity_status(Decimal("5")) == "medium"
    assert _liquidity_status(Decimal("20")) == "high"


def test_filter_disabled_feature_recipes_excludes_water_collector_recipe() -> None:
    water_collector_recipe = _recipe(
        "clean_water",
        1,
        [("plastic_bottle", 1)],
        features=["water_collector"],
    )
    kitchen_recipe = _recipe(
        "clean_water",
        5,
        [("water_carrier", 10), ("plastic_bottle", 5)],
        features=["gauze_filter", "kitchen_items"],
    )

    result = filter_disabled_feature_recipes(
        [water_collector_recipe, kitchen_recipe],
        frozenset({"water_collector"}),
    )

    assert result == [kitchen_recipe]


def test_read_latest_analysis_returns_all_saved_results() -> None:
    session = AsyncMock()
    session.scalar.return_value = CraftAnalysisRun(
        id=3,
        created_at=datetime(2026, 9, 27, 12, tzinfo=UTC),
        period_hours=24,
        min_margin_percent=Decimal("20"),
        recipes_count=1,
        analyzed_count=1,
        profitable_count=1,
        missing_price_count=0,
    )
    session.scalars.return_value = SimpleNamespace(
        all=lambda: [
            CraftAnalysisResult(
                run_id=3,
                item_id="pea_soup",
                item_name="Гороховый суп",
                category="Кулинария",
                subcategory="Усиление",
                output_amount=1,
                sell_price=Decimal("200"),
                craft_cost=Decimal("45"),
                direct_craft_cost=Decimal("60"),
                market_buy_price=Decimal("200"),
                profit=Decimal("155"),
                margin_percent=Decimal("344.4444"),
                market_amount=3,
                market_sale_count=2,
                liquidity_score=Decimal("2"),
                liquidity_status="low",
                is_profitable=True,
                recommendation="craft",
                status="ok",
                ingredients=[],
                recipe={},
            )
        ]
    )

    result = asyncio.run(read_latest_analysis(session))

    assert result is not None
    assert result.run_id == 3
    assert len(result.results) == 1
    assert result.results[0].item_name == "Гороховый суп"


def test_recipe_path_prefers_ru_database_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "stalzone-database"
    recipe_path = root / "ru" / "hideout_recipes.json"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text('{"recipes": []}', encoding="utf-8")
    monkeypatch.setattr(settings, "exbo_database_path", str(root))
    monkeypatch.setattr(settings, "craft_analysis_recipe_path", "")

    assert _recipe_path() == recipe_path


def _recipe(
    result_item_id: str,
    result_amount: int,
    ingredients: list[tuple[str, int]],
    features: list[str] | None = None,
) -> HideoutRecipe:
    return HideoutRecipe(
        index=0,
        result=RecipeComponent(result_item_id, result_amount),
        ingredients=tuple(RecipeComponent(item_id, amount) for item_id, amount in ingredients),
        category="Кулинария",
        subcategory="Усиление",
        raw={
            "result": [{"item": result_item_id, "amount": result_amount}],
            "ingredients": [{"item": item_id, "amount": amount} for item_id, amount in ingredients],
            "requirements": {"features": features or []},
        },
    )
