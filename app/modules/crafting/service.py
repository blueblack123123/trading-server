import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.core.config import settings
from app.modules.crafting.models import CraftAnalysisResult, CraftAnalysisRun
from app.modules.crafting.schemas import (
    CraftAnalysisResponse,
    CraftAnalysisResultResponse,
)
from app.modules.history.models import MarketItem, SaleAggregate

DEFAULT_MIN_MARGIN_PERCENT = Decimal("20")
RECIPE_FILENAME = "hideout_recipes.json"


@dataclass(frozen=True)
class RecipeComponent:
    item_id: str
    amount: int


@dataclass(frozen=True)
class HideoutRecipe:
    index: int
    result: RecipeComponent
    ingredients: tuple[RecipeComponent, ...]
    category: str | None
    subcategory: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PricePoint:
    price: Decimal
    amount: int
    sale_count: int


@dataclass(frozen=True)
class IngredientDecision:
    item_id: str
    item_name: str | None
    amount: int
    market_price: Decimal | None
    craft_price: Decimal | None
    chosen_price: Decimal | None
    decision: str
    ingredients: tuple["IngredientDecision", ...] = ()


@dataclass(frozen=True)
class CostDecision:
    item_id: str
    market_price: Decimal | None
    craft_price: Decimal | None
    chosen_price: Decimal | None
    decision: str
    recipe: HideoutRecipe | None = None
    ingredients: tuple[IngredientDecision, ...] = ()


@dataclass(frozen=True)
class RecipeAnalysis:
    recipe: HideoutRecipe
    item_name: str | None
    sell_price: Decimal | None
    craft_cost: Decimal | None
    direct_craft_cost: Decimal | None
    market_buy_price: Decimal | None
    profit: Decimal | None
    margin_percent: Decimal | None
    is_profitable: bool
    recommendation: str
    status: str
    ingredients: tuple[IngredientDecision, ...]


async def run_craft_analysis(
    session: AsyncSession,
    *,
    period_hours: int | None = None,
    min_margin_percent: Decimal | None = None,
    recipe_path: Path | None = None,
    now: datetime | None = None,
) -> CraftAnalysisRun:
    period_hours = period_hours or settings.craft_analysis_period_hours
    min_margin_percent = min_margin_percent or Decimal(
        str(settings.craft_analysis_min_margin_percent)
    )
    current = now or datetime.now(UTC)
    recipes = load_hideout_recipes(recipe_path or _recipe_path())
    item_ids = _recipe_item_ids(recipes)
    prices = await read_average_prices(session, item_ids, current, period_hours)
    item_names = await read_item_names(session, item_ids)

    analyzer = CraftAnalyzer(recipes, prices, item_names)
    analyses = [analyzer.analyze_recipe(recipe, min_margin_percent) for recipe in recipes]
    return await save_analysis(session, analyses, period_hours, min_margin_percent)


async def read_latest_analysis(session: AsyncSession) -> CraftAnalysisResponse | None:
    run = await session.scalar(
        select(CraftAnalysisRun).order_by(CraftAnalysisRun.created_at.desc()).limit(1)
    )
    if run is None:
        return None

    rows = (
        await session.scalars(
            select(CraftAnalysisResult)
            .where(CraftAnalysisResult.run_id == run.id)
            .order_by(
                desc(CraftAnalysisResult.is_profitable),
                desc(CraftAnalysisResult.margin_percent).nulls_last(),
                CraftAnalysisResult.item_name,
                CraftAnalysisResult.item_id,
            )
        )
    ).all()

    return CraftAnalysisResponse(
        run_id=run.id,
        created_at=run.created_at,
        period_hours=run.period_hours,
        min_margin_percent=run.min_margin_percent,
        recipes_count=run.recipes_count,
        analyzed_count=run.analyzed_count,
        profitable_count=run.profitable_count,
        missing_price_count=run.missing_price_count,
        results=[
            CraftAnalysisResultResponse(
                item_id=row.item_id,
                item_name=row.item_name,
                category=row.category,
                subcategory=row.subcategory,
                output_amount=row.output_amount,
                sell_price=row.sell_price,
                craft_cost=row.craft_cost,
                direct_craft_cost=row.direct_craft_cost,
                market_buy_price=row.market_buy_price,
                profit=row.profit,
                margin_percent=row.margin_percent,
                is_profitable=row.is_profitable,
                recommendation=row.recommendation,
                status=row.status,
                ingredients=row.ingredients,
                recipe=row.recipe,
            )
            for row in rows
        ],
    )


async def read_average_prices(
    session: AsyncSession,
    item_ids: set[str],
    now: datetime,
    period_hours: int,
) -> dict[str, PricePoint]:
    if not item_ids:
        return {}

    start = now - timedelta(hours=period_hours)
    statement = (
        select(
            SaleAggregate.item_id,
            (func.sum(SaleAggregate.weighted_price_sum) / func.sum(SaleAggregate.amount_sum)).label(
                "price"
            ),
            func.sum(SaleAggregate.amount_sum).label("amount"),
            func.sum(SaleAggregate.sale_count).label("sale_count"),
        )
        .where(
            SaleAggregate.item_id.in_(item_ids),
            SaleAggregate.resolution == "hour",
            SaleAggregate.bucket_start >= start,
            SaleAggregate.bucket_start < now,
        )
        .group_by(SaleAggregate.item_id)
    )
    rows = await session.execute(statement)
    return {
        str(row.item_id): PricePoint(
            price=Decimal(row.price),
            amount=int(row.amount),
            sale_count=int(row.sale_count),
        )
        for row in rows
        if row.price is not None and int(row.amount or 0) > 0
    }


async def read_item_names(session: AsyncSession, item_ids: set[str]) -> dict[str, str]:
    if not item_ids:
        return {}
    rows = await session.execute(
        select(MarketItem.id, MarketItem.name).where(MarketItem.id.in_(item_ids))
    )
    return {str(row.id): str(row.name) for row in rows}


async def save_analysis(
    session: AsyncSession,
    analyses: list[RecipeAnalysis],
    period_hours: int,
    min_margin_percent: Decimal,
) -> CraftAnalysisRun:
    run = CraftAnalysisRun(
        period_hours=period_hours,
        min_margin_percent=min_margin_percent,
        recipes_count=len(analyses),
        analyzed_count=sum(analysis.status == "ok" for analysis in analyses),
        profitable_count=sum(analysis.is_profitable for analysis in analyses),
        missing_price_count=sum(analysis.status != "ok" for analysis in analyses),
    )
    session.add(run)
    await session.flush()

    session.add_all(
        [
            CraftAnalysisResult(
                run_id=run.id,
                item_id=analysis.recipe.result.item_id,
                item_name=analysis.item_name,
                category=analysis.recipe.category,
                subcategory=analysis.recipe.subcategory,
                output_amount=analysis.recipe.result.amount,
                sell_price=analysis.sell_price,
                craft_cost=analysis.craft_cost,
                direct_craft_cost=analysis.direct_craft_cost,
                market_buy_price=analysis.market_buy_price,
                profit=analysis.profit,
                margin_percent=analysis.margin_percent,
                is_profitable=analysis.is_profitable,
                recommendation=analysis.recommendation,
                status=analysis.status,
                ingredients=[
                    _ingredient_to_dict(ingredient) for ingredient in analysis.ingredients
                ],
                recipe=analysis.recipe.raw,
            )
            for analysis in analyses
        ]
    )
    await session.commit()
    await session.refresh(run)
    await prune_old_analysis_runs(session)
    return run


async def prune_old_analysis_runs(session: AsyncSession) -> int:
    keep_runs = (
        select(CraftAnalysisRun.id)
        .order_by(CraftAnalysisRun.created_at.desc())
        .limit(settings.craft_analysis_keep_runs)
    )
    result = await session.execute(
        delete(CraftAnalysisRun).where(CraftAnalysisRun.id.not_in(keep_runs))
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def load_hideout_recipes(path: Path) -> list[HideoutRecipe]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    recipes: list[HideoutRecipe] = []
    for index, raw_recipe in enumerate(payload.get("recipes", [])):
        if not isinstance(raw_recipe, dict):
            continue
        results = _parse_components(raw_recipe.get("result"))
        ingredients = _parse_components(raw_recipe.get("ingredients"))
        if len(results) != 1 or not ingredients:
            continue
        recipes.append(
            HideoutRecipe(
                index=index,
                result=results[0],
                ingredients=tuple(ingredients),
                category=_translation(raw_recipe.get("category")),
                subcategory=_translation(raw_recipe.get("subcategory")),
                raw=raw_recipe,
            )
        )
    return recipes


class CraftAnalyzer:
    def __init__(
        self,
        recipes: list[HideoutRecipe],
        prices: dict[str, PricePoint],
        item_names: dict[str, str],
    ) -> None:
        self.recipes_by_result: dict[str, list[HideoutRecipe]] = defaultdict(list)
        for recipe in recipes:
            self.recipes_by_result[recipe.result.item_id].append(recipe)
        self.prices = prices
        self.item_names = item_names
        self.cost_cache: dict[str, CostDecision] = {}

    def analyze_recipe(
        self,
        recipe: HideoutRecipe,
        min_margin_percent: Decimal,
    ) -> RecipeAnalysis:
        sell_price = self._market_price(recipe.result.item_id)
        craft_cost = self._recipe_unit_cost(recipe, use_optimal_ingredients=True)
        direct_craft_cost = self._recipe_unit_cost(recipe, use_optimal_ingredients=False)
        ingredients = self._ingredient_decisions(recipe)

        profit = None
        margin_percent = None
        is_profitable = False
        recommendation = "no_price"
        status = "missing_price"
        if sell_price is not None and craft_cost is not None and craft_cost > 0:
            profit = sell_price - craft_cost
            margin_percent = profit / craft_cost * Decimal("100")
            is_profitable = margin_percent >= min_margin_percent
            recommendation = "craft" if is_profitable else "skip"
            status = "ok"
        elif craft_cost is None:
            status = "missing_ingredient_price"

        return RecipeAnalysis(
            recipe=recipe,
            item_name=self.item_names.get(recipe.result.item_id),
            sell_price=sell_price,
            craft_cost=craft_cost,
            direct_craft_cost=direct_craft_cost,
            market_buy_price=sell_price,
            profit=profit,
            margin_percent=margin_percent,
            is_profitable=is_profitable,
            recommendation=recommendation,
            status=status,
            ingredients=ingredients,
        )

    def _optimal_cost(self, item_id: str, stack: frozenset[str]) -> CostDecision:
        if item_id in self.cost_cache:
            return self.cost_cache[item_id]

        market_price = self._market_price(item_id)
        if item_id in stack:
            return CostDecision(
                item_id=item_id,
                market_price=market_price,
                craft_price=None,
                chosen_price=market_price,
                decision="buy" if market_price is not None else "unavailable",
            )

        craft_price = None
        craft_recipe = None
        craft_ingredients: tuple[IngredientDecision, ...] = ()
        for recipe in self.recipes_by_result.get(item_id, []):
            recipe_cost = self._recipe_unit_cost(
                recipe,
                use_optimal_ingredients=True,
                stack=stack | {item_id},
            )
            if recipe_cost is None:
                continue
            if craft_price is None or recipe_cost < craft_price:
                craft_price = recipe_cost
                craft_recipe = recipe
                craft_ingredients = self._ingredient_decisions(
                    recipe,
                    stack=stack | {item_id},
                )

        if market_price is not None and (craft_price is None or market_price <= craft_price):
            decision = CostDecision(
                item_id=item_id,
                market_price=market_price,
                craft_price=craft_price,
                chosen_price=market_price,
                decision="buy",
                recipe=craft_recipe,
            )
        elif craft_price is not None:
            decision = CostDecision(
                item_id=item_id,
                market_price=market_price,
                craft_price=craft_price,
                chosen_price=craft_price,
                decision="craft",
                recipe=craft_recipe,
                ingredients=craft_ingredients,
            )
        else:
            decision = CostDecision(
                item_id=item_id,
                market_price=market_price,
                craft_price=craft_price,
                chosen_price=None,
                decision="unavailable",
            )

        self.cost_cache[item_id] = decision
        return decision

    def _recipe_unit_cost(
        self,
        recipe: HideoutRecipe,
        *,
        use_optimal_ingredients: bool,
        stack: frozenset[str] = frozenset(),
    ) -> Decimal | None:
        total = Decimal("0")
        for ingredient in recipe.ingredients:
            if use_optimal_ingredients:
                ingredient_cost = self._optimal_cost(ingredient.item_id, stack).chosen_price
            else:
                ingredient_cost = self._market_price(ingredient.item_id)
            if ingredient_cost is None:
                return None
            total += ingredient_cost * ingredient.amount

        if recipe.result.amount <= 0:
            return None
        return total / Decimal(recipe.result.amount)

    def _ingredient_decisions(
        self,
        recipe: HideoutRecipe,
        stack: frozenset[str] = frozenset(),
    ) -> tuple[IngredientDecision, ...]:
        decisions: list[IngredientDecision] = []
        for ingredient in recipe.ingredients:
            cost = self._optimal_cost(ingredient.item_id, stack)
            decisions.append(
                IngredientDecision(
                    item_id=ingredient.item_id,
                    item_name=self.item_names.get(ingredient.item_id),
                    amount=ingredient.amount,
                    market_price=cost.market_price,
                    craft_price=cost.craft_price,
                    chosen_price=cost.chosen_price,
                    decision=cost.decision,
                    ingredients=cost.ingredients,
                )
            )
        return tuple(decisions)

    def _market_price(self, item_id: str) -> Decimal | None:
        point = self.prices.get(item_id)
        return point.price if point is not None else None


def _recipe_path() -> Path:
    if settings.craft_analysis_recipe_path:
        return Path(settings.craft_analysis_recipe_path)
    database_path = Path(settings.exbo_database_path)
    candidates = [
        database_path / RECIPE_FILENAME,
        database_path / "ru" / RECIPE_FILENAME,
        database_path / "global" / RECIPE_FILENAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    found = next(database_path.glob(f"**/{RECIPE_FILENAME}"), None)
    if found is not None:
        return found
    return candidates[0]


def _recipe_item_ids(recipes: list[HideoutRecipe]) -> set[str]:
    item_ids: set[str] = set()
    for recipe in recipes:
        item_ids.add(recipe.result.item_id)
        item_ids.update(ingredient.item_id for ingredient in recipe.ingredients)
    return item_ids


def _parse_components(value: object) -> list[RecipeComponent]:
    if not isinstance(value, list):
        return []
    components: list[RecipeComponent] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("item") or "").strip()
        amount = int(item.get("amount") or 0)
        if item_id and amount > 0:
            components.append(RecipeComponent(item_id=item_id, amount=amount))
    return components


def _translation(value: object, language: str = "ru") -> str | None:
    if not isinstance(value, dict):
        return None
    lines = value.get("lines")
    if isinstance(lines, dict):
        translated = lines.get(language) or lines.get("en")
        if translated:
            return str(translated)
    return None


def _ingredient_to_dict(item: IngredientDecision) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "item_name": item.item_name,
        "amount": item.amount,
        "market_price": _decimal_to_str(item.market_price),
        "craft_price": _decimal_to_str(item.craft_price),
        "chosen_price": _decimal_to_str(item.chosen_price),
        "decision": item.decision,
        "ingredients": [_ingredient_to_dict(child) for child in item.ingredients],
    }


def _decimal_to_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
