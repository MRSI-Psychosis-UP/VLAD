"""Explicit statistics used by the Python translations of the VLAD reports.

The defaults mirror ggstatsplot/statsExpressions and gtsummary: unequal-variance
parametric tests for regional plots, and rank/exact tests for cohort tables.
Every result names its method; undefined results are JSON null, never NaN.
"""

from __future__ import annotations

from itertools import combinations
import math
import warnings

import numpy as np
import pandas as pd
from scipy import optimize, special, stats


def _number(value):
    try:
        return float(value) if np.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _levels(values: pd.Series, requested=None) -> list:
    observed = list(pd.unique(values.dropna()))
    if requested is not None:
        wanted = list(requested)
        return [value for value in wanted if value in observed] + [value for value in observed if value not in wanted]
    if isinstance(values.dtype, pd.CategoricalDtype):
        return [value for value in values.cat.categories if value in observed]
    try:
        return sorted(observed)
    except TypeError:
        return sorted(observed, key=str)


def _summary(values) -> dict:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    n = len(array)
    mean = float(np.mean(array)) if n else None
    sd = float(np.std(array, ddof=1)) if n > 1 else None
    se = sd / math.sqrt(n) if sd is not None else None
    half = stats.t.ppf(0.975, n - 1) * se if se is not None else None
    return {"n": n, "mean": mean, "sd": sd, "se": se,
            "ci_low": _number(mean - half) if half is not None else None,
            "ci_high": _number(mean + half) if half is not None else None,
            "median": float(np.median(array)) if n else None,
            "q1": float(np.quantile(array, 0.25)) if n else None,
            "q3": float(np.quantile(array, 0.75)) if n else None}


def _ncp_t(observed: float, df: float, target: float) -> float:
    span = max(10.0, abs(observed) * 2)
    for _ in range(12):
        low, high = -span, span
        if stats.nct.cdf(observed, df, low) >= target >= stats.nct.cdf(observed, df, high):
            return float(optimize.brentq(lambda ncp: stats.nct.cdf(observed, df, ncp) - target, low, high))
        span *= 2
    raise ValueError("Noncentral t interval could not be bracketed")


def _ncp_f_lower(observed: float, df_num: float, df_denom: float, target: float = 0.95) -> float:
    if stats.f.cdf(observed, df_num, df_denom) <= target:
        return 0.0
    high = max(10.0, observed * df_num * 2)
    while stats.ncf.cdf(observed, df_num, df_denom, high) > target:
        high *= 2
        if high > 1e12:
            raise ValueError("Noncentral F interval could not be bracketed")
    return float(optimize.brentq(lambda ncp: stats.ncf.cdf(observed, df_num, df_denom, ncp) - target, 0.0, high))


def _holm(values: list[float]) -> list[float]:
    order = np.argsort(values)
    corrected = np.empty(len(values))
    previous = 0.0
    for rank, index in enumerate(order):
        previous = max(previous, min(1.0, (len(values) - rank) * values[index]))
        corrected[index] = previous
    return corrected.tolist()


def analyse_continuous(x, y) -> dict:
    """Pearson's correlation, Fisher-z interval, and an OLS fitted line."""
    first = pd.to_numeric(pd.Series(x), errors="coerce").to_numpy(dtype=float)
    second = pd.to_numeric(pd.Series(y), errors="coerce").to_numpy(dtype=float)
    if len(first) != len(second):
        raise ValueError("Correlation variables must have the same number of rows")
    keep = np.isfinite(first) & np.isfinite(second)
    first, second = first[keep], second[keep]
    n = len(first)
    result = {"method": "Pearson correlation (two-sided)", "n": n,
              "n_missing": int(len(keep) - n), "statistic_name": "t", "statistic": None,
              "df": n - 2 if n >= 3 else None, "p": None, "estimate": None,
              "ci_low": None, "ci_high": None, "confidence_level": 0.95,
              "regression": None, "warnings": []}
    if n < 3:
        result["warnings"].append("Pearson inference requires at least three complete observations.")
        return result
    if np.ptp(first) == 0 or np.ptp(second) == 0:
        result["warnings"].append("Correlation is undefined for a constant variable.")
        return result
    coefficient, p = stats.pearsonr(first, second)
    coefficient = float(np.clip(coefficient, -1, 1))
    result.update(estimate=coefficient, p=_number(p))
    if abs(coefficient) < 1:
        result["statistic"] = float(coefficient * math.sqrt((n - 2) / (1 - coefficient ** 2)))
    else:
        result["warnings"].append("Perfect correlation gives an unbounded t statistic.")
    if n > 3:
        if abs(coefficient) == 1:
            result.update(ci_low=coefficient, ci_high=coefficient)
        else:
            z = np.arctanh(coefficient)
            half = stats.norm.ppf(0.975) / math.sqrt(n - 3)
            result.update(ci_low=float(np.tanh(z - half)), ci_high=float(np.tanh(z + half)))
    else:
        result.update(ci_low=-1.0, ci_high=1.0)
    fitted = stats.linregress(first, second)
    half = stats.t.ppf(0.975, n - 2) * fitted.stderr
    result["regression"] = {"method": "Ordinary least squares", "slope": _number(fitted.slope),
                            "intercept": _number(fitted.intercept), "r_squared": coefficient ** 2,
                            "p": _number(fitted.pvalue), "stderr": _number(fitted.stderr),
                            "intercept_stderr": _number(fitted.intercept_stderr),
                            "ci_low": _number(fitted.slope - half), "ci_high": _number(fitted.slope + half)}
    return result


def analyse_groups(values, groups, levels=None) -> dict:
    """Welch t/ANOVA with unbiased effects and Games–Howell/Holm comparisons."""
    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    labels = pd.Series(groups)
    if len(numeric) != len(labels):
        raise ValueError("Group labels and regional values must have equal row counts")
    keep = np.isfinite(numeric.to_numpy(dtype=float)) & labels.notna().to_numpy()
    numeric, labels = numeric[keep].reset_index(drop=True), labels[keep].reset_index(drop=True)
    ordered = _levels(labels, levels)
    arrays = [numeric[labels == value].to_numpy(dtype=float) for value in ordered]
    summaries = [{"name": str(value), **_summary(array)} for value, array in zip(ordered, arrays)]
    result = {"method": "Welch two-sample t-test" if len(arrays) == 2 else "Welch one-way ANOVA",
              "n": int(sum(len(array) for array in arrays)), "n_missing": int(len(keep) - sum(keep)),
              "statistic": None, "statistic_name": "t" if len(arrays) == 2 else "F",
              "df": None, "df_num": None, "df_denom": None, "p": None,
              "confidence_level": 0.95, "groups": summaries, "difference": None,
              "effect_size": None, "pairwise": [], "warnings": []}
    if len(arrays) < 2 or any(len(array) < 2 for array in arrays):
        result["warnings"].append("At least two groups with two complete observations each are required.")
        return result
    ns = np.array([len(array) for array in arrays], dtype=float)
    means = np.array([np.mean(array) for array in arrays])
    variances = np.array([np.var(array, ddof=1) for array in arrays])
    if not np.all(np.isfinite(means)) or not np.all(np.isfinite(variances)):
        result["warnings"].append("Group moments exceed floating-point range; inference is unavailable.")
        return result
    if len(arrays) > 2 and np.any(variances == 0):
        result["warnings"].append("Welch ANOVA is undefined when a group has zero variance.")
        return result
    if len(arrays) == 2:
        v = variances / ns
        se2 = float(v.sum())
        if se2 == 0:
            result["warnings"].append("Welch inference is undefined when both groups have zero variance.")
            return result
        df = float(se2 ** 2 / np.sum(v ** 2 / (ns - 1)))
        difference = float(means[0] - means[1])
        se = math.sqrt(se2)
        t = difference / se
        half = float(stats.t.ppf(0.975, df) * se)
        result.update(statistic=t, df=df, df_denom=df, p=float(2 * stats.t.sf(abs(t), df)))
        result["difference"] = {"group1": str(ordered[0]), "group2": str(ordered[1]),
                                "estimate": difference, "ci_low": difference - half, "ci_high": difference + half}
        scale = math.sqrt(float(variances.mean()))
        correction = math.exp(special.gammaln(df / 2) - math.log(math.sqrt(df / 2)) - special.gammaln((df - 1) / 2)) if df > 1 else 0.0
        effect = {"method": "Hedges g (average unpooled SD; Welch df correction)",
                  "estimate": difference / scale * correction, "ci_low": None, "ci_high": None,
                  "confidence_level": 0.95, "ci_method": "Noncentral t, two-sided"}
        try:
            hn = 2 * (ns[1] * variances[0] + ns[0] * variances[1]) / (ns[0] * ns[1] * variances.sum())
            effect.update(ci_low=_ncp_t(t, df, 0.975) * math.sqrt(hn) * correction,
                          ci_high=_ncp_t(t, df, 0.025) * math.sqrt(hn) * correction)
        except (ValueError, FloatingPointError):
            result["warnings"].append("The effect-size confidence interval could not be estimated.")
        result["effect_size"] = effect
    else:
        k = len(arrays)
        weights = ns / variances
        total = weights.sum()
        center = np.sum(weights * means) / total
        adjustment = float(np.sum((1 - weights / total) ** 2 / (ns - 1)))
        df_num = k - 1
        df_denom = float((k ** 2 - 1) / (3 * adjustment))
        f_value = float(np.sum(weights * (means - center) ** 2) / (k - 1) /
                        (1 + 2 * (k - 2) / (k ** 2 - 1) * adjustment))
        result.update(statistic=f_value, df_num=df_num, df_denom=df_denom,
                      p=float(stats.f.sf(f_value, df_num, df_denom)))
        omega = max(0.0, (f_value - 1) * df_num / (f_value * df_num + df_denom + 1))
        effect = {"method": "Omega squared (Welch F conversion)", "estimate": omega,
                  "ci_low": None, "ci_high": 1.0, "confidence_level": 0.95,
                  "ci_method": "Noncentral F, one-sided lower bound"}
        try:
            transformed = (omega / df_num) / ((1 - omega) / df_denom)
            noncentral = _ncp_f_lower(transformed, df_num, df_denom)
            effect["ci_low"] = noncentral / (noncentral + df_denom)
        except (ValueError, FloatingPointError):
            result["warnings"].append("The omega-squared lower confidence bound could not be estimated.")
        result["effect_size"] = effect
    # The R pipeline applies Holm even though Games–Howell already uses a range
    # distribution. Preserve that explicit extra multiplicity correction.
    raw_p = []
    for i, j in combinations(range(len(arrays)), 2):
        v1, v2 = variances[i] / ns[i], variances[j] / ns[j]
        se = math.sqrt(float(v1 + v2))
        if se == 0:
            continue
        df = float((v1 + v2) ** 2 / (v1 ** 2 / (ns[i] - 1) + v2 ** 2 / (ns[j] - 1)))
        difference = float(means[i] - means[j])
        # PMCMRplus reports the signed range statistic as group2 minus group1.
        q = -difference / (se / math.sqrt(2))
        p_value = float(stats.studentized_range.sf(abs(q), len(arrays), df))
        half = float(stats.studentized_range.ppf(0.95, len(arrays), df) * se / math.sqrt(2))
        raw_p.append(p_value)
        result["pairwise"].append({"method": "Games–Howell", "p_adjustment": "Holm",
                                   "group1": str(ordered[i]), "group2": str(ordered[j]),
                                   "difference": difference, "statistic": q, "df": df,
                                   "statistic_name": "q (group2 minus group1)",
                                   "p": p_value, "p_adjusted": None,
                                   "ci_low": difference - half, "ci_high": difference + half,
                                   "ci_method": "Studentized-range simultaneous interval; no additional Holm interval adjustment"})
    for row, p_adjusted in zip(result["pairwise"], _holm(raw_p)):
        row["p_adjusted"] = p_adjusted
    return result


class _EnumerationLimit(Exception):
    pass


def _fisher_rxc(table: np.ndarray, max_tables: int = 100000) -> dict:
    """Two-sided fixed-margin probability ordering, as in Fisher's exact test."""
    row_totals, col_totals = table.sum(axis=1), table.sum(axis=0)
    constant = float(special.gammaln(row_totals + 1).sum() + special.gammaln(col_totals + 1).sum() - special.gammaln(table.sum() + 1))
    observed_log = float(constant - special.gammaln(table + 1).sum())
    if np.all(row_totals == 1) or np.all(col_totals == 1):
        return {"method": "Fisher–Freeman–Halton exact test (two-sided)", "statistic": None,
                "p": 1.0, "note": "Every fixed-margin table has equal probability when all row or column margins equal one."}
    if table.size > 10000:
        return {"method": "Unavailable", "statistic": None, "p": None,
                "note": "Categorical table has more than 10000 cells; an exact comparison was not attempted."}
    selected = []
    count = 0

    def allocations(total, remaining, position=0, prefix=()):
        if position == len(remaining) - 1:
            if 0 <= total <= remaining[position]:
                yield prefix + (total,)
            return
        low = max(0, total - int(sum(remaining[position + 1:])))
        high = min(total, int(remaining[position]))
        for value in range(low, high + 1):
            yield from allocations(total - value, remaining, position + 1, prefix + (value,))

    def visit(row, remaining, factorial_sum):
        nonlocal count
        if row == len(row_totals) - 1:
            if remaining.sum() != row_totals[row]:
                return
            count += 1
            if count > max_tables:
                raise _EnumerationLimit
            log_probability = constant - factorial_sum - float(special.gammaln(remaining + 1).sum())
            if log_probability <= observed_log + 1e-7:
                selected.append(log_probability)
            return
        for values in allocations(int(row_totals[row]), remaining):
            values = np.asarray(values, dtype=int)
            visit(row + 1, remaining - values, factorial_sum + float(special.gammaln(values + 1).sum()))

    try:
        visit(0, col_totals.astype(int), 0.0)
        return {"method": "Fisher–Freeman–Halton exact test (two-sided)", "statistic": None,
                "p": min(1.0, float(np.exp(special.logsumexp(selected)))) if selected else 0.0,
                "tables_enumerated": count}
    except (_EnumerationLimit, RecursionError):
        # Exact enumeration is combinatorial; make the fallback and uncertainty
        # visible instead of silently substituting a chi-square approximation.
        simulations = 19999
        rng = np.random.default_rng(42831)
        remaining = simulations
        extreme = 0
        batch = max(1, min(simulations, 200000 // table.size))
        while remaining:
            size = min(remaining, batch)
            simulated = stats.random_table.rvs(row_totals, col_totals, size=size, random_state=rng)
            log_probs = constant - special.gammaln(simulated + 1).sum(axis=(1, 2))
            extreme += int(np.sum(log_probs <= observed_log + 1e-7))
            remaining -= size
        p = float((1 + extreme) / (simulations + 1))
        return {"method": "Fisher–Freeman–Halton fixed-margin Monte Carlo test (two-sided)",
                "statistic": None, "p": p, "simulations": simulations, "seed": 42831,
                "monte_carlo_standard_error": math.sqrt(p * (1 - p) / (simulations + 1)),
                "note": "Exact enumeration exceeded 100000 tables; fixed-margin Monte Carlo was used."}


def _table_test(series: pd.Series, groups: pd.Series, continuous: bool) -> dict:
    keep = series.notna() & groups.notna()
    series, groups = series[keep], groups[keep]
    levels = _levels(groups)
    empty = {"method": "Unavailable", "statistic": None, "p": None}
    if len(levels) < 2:
        return {**empty, "note": "At least two nonempty groups are required."}
    if continuous:
        arrays = [pd.to_numeric(series[groups == level], errors="coerce").to_numpy(dtype=float) for level in levels]
        arrays = [array[np.isfinite(array)] for array in arrays]
        if any(not len(array) for array in arrays):
            return {**empty, "note": "A group has no complete numeric observations."}
        if len(arrays) == 2:
            pooled = np.concatenate(arrays)
            use_exact = all(len(array) < 50 for array in arrays) and len(np.unique(pooled)) == len(pooled)
            test = stats.mannwhitneyu(*arrays, alternative="two-sided", method="exact" if use_exact else "asymptotic", use_continuity=True)
            return {"method": "Wilcoxon rank-sum test (exact)" if use_exact else "Wilcoxon rank-sum test (normal approximation with continuity correction)",
                    "statistic": _number(test.statistic), "p": _number(test.pvalue)}
        if len(np.unique(np.concatenate(arrays))) == 1:
            return {**empty, "method": "Kruskal–Wallis test", "note": "All complete observations are identical."}
        test = stats.kruskal(*arrays)
        return {"method": "Kruskal–Wallis test", "statistic": _number(test.statistic), "df": len(arrays) - 1, "p": _number(test.pvalue)}
    table = pd.crosstab(series, groups).to_numpy(dtype=int)
    table = table[np.any(table, axis=1)][:, np.any(table, axis=0)]
    if min(table.shape) < 2:
        return {**empty, "note": "A categorical variable has fewer than two observed levels."}
    chi, p, df, expected = stats.chi2_contingency(table, correction=False)
    if np.all(expected >= 5):
        return {"method": "Pearson chi-square test (no continuity correction)", "statistic": float(chi), "df": int(df), "p": float(p)}
    if table.shape == (2, 2):
        fisher = stats.fisher_exact(table, alternative="two-sided")
        return {"method": "Fisher exact test (two-sided)", "statistic": _number(fisher.statistic), "statistic_name": "odds ratio", "p": float(fisher.pvalue)}
    return _fisher_rxc(table)


def describe_design(frame: pd.DataFrame, group: str | None = None, exclude: list[str] | None = None) -> dict:
    """Cohort table with means/SD, category counts, missingness, and named tests.

    gtsummary treats numeric variables with fewer than ten distinct observations
    as categorical by default. This threshold concerns display and table tests;
    regional analyses retain their explicitly selected continuous/group mode.
    """
    if group is not None and group not in frame.columns:
        raise ValueError(f"Grouping column {group!r} is absent from the design")
    excluded = set(exclude or []) | ({group} if group is not None else set())
    grouping = frame[group] if group else None
    group_levels = _levels(grouping) if grouping is not None else []
    result = {"n": len(frame), "group": group, "group_levels": [str(value) for value in group_levels],
              "group_counts": {str(value): int((grouping == value).sum()) for value in group_levels},
              "group_missing": int(grouping.isna().sum()) if grouping is not None else 0,
              "variables": [], "warnings": []}
    for name in frame.columns:
        if name in excluded:
            continue
        series = frame[name]
        continuous = bool(pd.api.types.is_numeric_dtype(series.dtype) and not pd.api.types.is_bool_dtype(series.dtype) and series.nunique(dropna=True) >= 10)
        if continuous:
            numeric = pd.to_numeric(series, errors="coerce")
            numeric = numeric.where(np.isfinite(numeric.to_numpy(dtype=float)))
            series = numeric
        levels = _levels(series) if not continuous else []

        def summarise(selected):
            if continuous:
                return _summary(selected)
            total = int(selected.notna().sum())
            return {"n": total, "levels": [{"level": str(level), "n": int((selected == level).sum()),
                                              "denominator": total,
                                              "percent": float((selected == level).sum() / total * 100) if total else None}
                                             for level in levels]}

        variable = {"name": str(name), "type": "continuous" if continuous else "categorical",
                    "n": int(series.notna().sum()), "missing": int(series.isna().sum()),
                    "overall": summarise(series), "groups": {}, "test": None}
        if grouping is not None:
            for level in group_levels:
                selected = series[grouping == level]
                variable["groups"][str(level)] = {**summarise(selected), "missing": int(selected.isna().sum())}
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    variable["test"] = _table_test(series, grouping, continuous)
            except (ValueError, FloatingPointError, OverflowError) as exc:
                variable["test"] = {"method": "Unavailable", "statistic": None, "p": None, "note": str(exc)}
                result["warnings"].append(f"Could not compare {name}: {exc}")
        result["variables"].append(variable)
    return result
