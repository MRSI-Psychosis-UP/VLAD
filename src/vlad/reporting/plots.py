"""Headless report figures and population-level mixed-model predictions."""

from __future__ import annotations

import io
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _svg(figure) -> str:
    buffer = io.StringIO()
    figure.savefig(buffer, format="svg", bbox_inches="tight", metadata={"Date": None})
    plt.close(figure)
    return buffer.getvalue()


def design_figure(frame: pd.DataFrame, correlation: bool = False) -> str:
    numeric = frame.select_dtypes(include="number")
    fig, ax = plt.subplots(figsize=(max(5, min(15, len(numeric.columns) * .6)), 4))
    if not len(numeric.columns):
        ax.text(.5, .5, "No numeric design columns", ha="center", va="center")
        ax.axis("off")
    elif correlation:
        values = numeric.corr().to_numpy()
        picture = ax.imshow(values, vmin=-1, vmax=1, cmap="coolwarm", aspect="auto")
        ax.set_xticks(range(len(numeric.columns)), numeric.columns, rotation=60, ha="right")
        ax.set_yticks(range(len(numeric.columns)), numeric.columns)
        fig.colorbar(picture, ax=ax, label="Pearson correlation")
    else:
        span = (numeric.max() - numeric.min()).replace(0, 1)
        normalised = (numeric - numeric.min()) / span
        ax.imshow(normalised.to_numpy(), aspect="auto", cmap="viridis", interpolation="nearest")
        ax.set_xticks(range(len(numeric.columns)), numeric.columns, rotation=60, ha="right")
        ax.set_ylabel("Design row (original order)")
    ax.set_title("Design correlation" if correlation else "Design matrix (column-normalised)")
    return _svg(fig)


def randomise_figure(data, region, statistics: dict) -> str:
    frame = data.combined
    fig, ax = plt.subplots(figsize=(7.5, 5))
    if data.group_column:
        groups = [pd.to_numeric(frame.loc[frame[data.group_column] == level, region.column], errors="coerce").dropna().to_numpy() for level in data.group_levels]
        for index, values in enumerate(groups):
            if not len(values):
                continue
            ax.boxplot([values], positions=[index], widths=.45, showfliers=False)
            jitter = np.random.default_rng(42 + index).uniform(-.13, .13, len(values))
            ax.scatter(index + jitter, values, alpha=.7, s=25)
        ax.set_xticks(range(len(groups)), data.group_levels)
        ax.set_xlabel("Group")
    else:
        name = data.main_variables[0]
        x = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)
        y = pd.to_numeric(frame[region.column], errors="coerce").to_numpy(dtype=float)
        valid = np.isfinite(x) & np.isfinite(y)
        ax.scatter(x[valid], y[valid], alpha=.7, s=30)
        if valid.sum() >= 3 and np.ptp(x[valid]) > 0:
            from scipy import stats

            fit = stats.linregress(x[valid], y[valid])
            grid = np.linspace(x[valid].min(), x[valid].max(), 100)
            predicted = fit.intercept + fit.slope * grid
            residual = y[valid] - fit.intercept - fit.slope * x[valid]
            sem = np.sqrt(np.sum(residual**2) / (valid.sum() - 2) *
                          (1 / valid.sum() + (grid - x[valid].mean())**2 / np.sum((x[valid] - x[valid].mean())**2)))
            margin = stats.t.ppf(.975, valid.sum() - 2) * sem
            ax.plot(grid, predicted, color="#126c8a")
            ax.fill_between(grid, predicted - margin, predicted + margin, alpha=.15, color="#126c8a")
        ax.set_xlabel(name)
    ax.set_ylabel(region.metabolite)
    p = statistics.get("p")
    annotation = f"{statistics.get('method', '')}" + (f"; p={p:.4g}" if p is not None else "")
    ax.set_title(region.label + "\n" + annotation, fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    return _svg(fig)


def _typical_grid(frame: pd.DataFrame, model, count: int) -> pd.DataFrame:
    values = {}
    for name, kind in model.predictor_types.items():
        if kind == "categorical":
            values[name] = [model.categorical_levels[name][0]] * count
        else:
            values[name] = [pd.to_numeric(frame[name], errors="coerce").mean()] * count
    return pd.DataFrame(values, index=range(count))


def mixed_prediction_grid(frame: pd.DataFrame, model, predictor: str) -> tuple[pd.DataFrame, str, str | None]:
    """Vary a predictor while holding covariates at means/reference levels.

    Existing product columns remain actual design columns. If the selected
    product joins a numeric and categorical term, recompute all available
    products on the prediction grid, as in the original SwE report.
    """
    parts = re.split(r"_x_|:|\*", predictor)
    if len(parts) == 2 and all(name in model.predictor_types for name in parts):
        continuous = [name for name in parts if model.predictor_types[name] == "continuous"]
        categorical = [name for name in parts if model.predictor_types[name] == "categorical"]
        if len(continuous) == len(categorical) == 1:
            x, group = continuous[0], categorical[0]
            x_values = pd.to_numeric(frame[x], errors="coerce").dropna()
            levels = model.categorical_levels[group]
            count = 100 * len(levels)
            grid = _typical_grid(frame, model, count)
            grid[x] = np.tile(np.linspace(x_values.min(), x_values.max(), 100), len(levels))
            grid[group] = np.repeat(levels, 100)
            for name in model.predictor_types:
                pair = re.split(r"_x_|:|\*", name)
                if len(pair) == 2 and all(term in grid for term in pair):
                    factors = []
                    for term in pair:
                        numeric = pd.to_numeric(grid[term], errors="coerce")
                        if numeric.isna().all():
                            numeric = grid[term].map({level: i for i, level in enumerate(model.categorical_levels[term])})
                        factors.append(numeric)
                    grid[name] = factors[0] * factors[1]
            return grid, x, group
    if model.predictor_types[predictor] == "categorical":
        levels = model.categorical_levels[predictor]
        grid = _typical_grid(frame, model, len(levels))
        grid[predictor] = levels
    else:
        values = pd.to_numeric(frame[predictor], errors="coerce").dropna()
        grid = _typical_grid(frame, model, 100)
        grid[predictor] = np.linspace(values.min(), values.max(), 100)
    return grid, predictor, None


def mixed_figure(data, region, model, predictor: str | None) -> tuple[str, list[dict]]:
    required = [region.column, data.subject_column, *model.predictor_types]
    frame = data.combined.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
    fig, ax = plt.subplots(figsize=(8, 5))
    if predictor is None:
        ax.scatter(np.arange(len(frame)), frame[region.column], alpha=.7)
        ax.set_xlabel("Design row")
        predictions = []
    else:
        grid, x, group = mixed_prediction_grid(frame, model, predictor)
        predictions_frame = model.predict(grid)
        predicted = pd.concat([grid, predictions_frame], axis=1)
        numeric_x = model.predictor_types[x] == "continuous"
        x_levels = model.categorical_levels.get(x, [])
        positions = {level: index for index, level in enumerate(x_levels)}
        if numeric_x:
            for _, rows in frame.groupby(data.subject_column, sort=False):
                rows = rows.sort_values(x)
                ax.plot(pd.to_numeric(rows[x], errors="coerce"), rows[region.column], alpha=.18, color="gray", linewidth=.7)
            ax.scatter(pd.to_numeric(frame[x], errors="coerce"), frame[region.column], alpha=.55, s=20, color="#126c8a")
        else:
            jitter = np.random.default_rng(42).uniform(-.1, .1, len(frame))
            ax.scatter(frame[x].map(positions).astype(float) + jitter, frame[region.column], alpha=.5, s=20)
            ax.set_xticks(range(len(x_levels)), [str(level) for level in x_levels])
        pieces = predicted.groupby(group, sort=False, observed=True) if group else [(None, predicted)]
        for label, rows in pieces:
            if numeric_x:
                ax.plot(rows[x].to_numpy(dtype=float), rows["mean"].to_numpy(), label=str(label) if label is not None else "Population mean")
                ax.fill_between(rows[x].to_numpy(dtype=float), rows["ci_low"].to_numpy(), rows["ci_high"].to_numpy(), alpha=.15)
            else:
                ax.errorbar([positions[value] for value in rows[x]], rows["mean"],
                            yerr=np.vstack([rows["mean"] - rows["ci_low"], rows["ci_high"] - rows["mean"]]), fmt="o", capsize=4, color="#123b56")
        if group:
            ax.legend(title=group)
        ax.set_xlabel(x)
        predictions = predicted.to_dict(orient="records")
    ax.set_ylabel(region.metabolite)
    ax.set_title(region.label + "\nML random intercept; population-mean 95% intervals", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    return _svg(fig), predictions
