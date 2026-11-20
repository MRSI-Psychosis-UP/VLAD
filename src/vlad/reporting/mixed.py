"""Maximum-likelihood Gaussian random-intercept models, without an R runtime.

The model is ``y = X beta + b_subject + error``, with independent Gaussian
subject intercepts and residuals.  Blockwise Sherman--Morrison identities give
the exact marginal likelihood without forming an n by n covariance matrix.

Fixed-effect t tests use Satterthwaite denominator degrees of freedom: the
delta-method variance of ``L cov(beta) L'`` is evaluated using the observed
Hessian in (relative random SD, residual SD). This is the parameterisation
described by lmerTest's original methodological notes:
https://github.com/runehaubo/lmerTestR/blob/master/pkg_notes/Satterthwaite_for_LMMs.Rmd

Only random intercepts and ML fitting are implemented, matching VLAD's existing
SwE report. Random slopes, REML and Kenward--Roger adjustments are not inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import optimize, stats


def is_categorical(series: pd.Series) -> bool:
    """Match the existing report's factor coercion, with sorted level order."""
    if isinstance(series.dtype, pd.CategoricalDtype):
        return True
    if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return True
    values = pd.to_numeric(series.dropna()).unique()
    if not len(values):
        return False
    integerish = np.all(np.abs(values - np.round(values)) < 1e-8)
    return len(values) == 2 or (len(values) <= 6 and integerish)


def _finite(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _levels(series: pd.Series) -> list[Any]:
    if isinstance(series.dtype, pd.CategoricalDtype):
        return [value.item() if isinstance(value, np.generic) else value
                for value in series.cat.remove_unused_categories().cat.categories]
    values = list(pd.unique(series.dropna()))
    try:
        ordered = sorted(values)
    except TypeError:
        ordered = sorted(values, key=lambda value: str(value))
    return [value.item() if isinstance(value, np.generic) else value for value in ordered]


@dataclass
class MixedModelResult:
    statistics: dict[str, Any]
    predictor_types: dict[str, str]
    categorical_levels: dict[str, list[Any]]
    design_columns: list[str]
    _predictors: list[str] = field(repr=False)
    _beta: np.ndarray = field(repr=False)
    _covariance: np.ndarray = field(repr=False)
    _column_predictors: list[str] = field(repr=False)

    def design_matrix(self, frame: pd.DataFrame) -> np.ndarray:
        """Encode new observations using exactly the fitted treatment coding."""
        columns = [np.ones(len(frame), dtype=float)]
        for name in self._predictors:
            if name not in frame:
                raise ValueError(f"Prediction data lacks predictor {name!r}.")
            series = frame[name]
            if self.predictor_types[name] == "categorical":
                levels = self.categorical_levels[name]
                unknown = series.notna() & ~series.isin(levels)
                if unknown.any():
                    raise ValueError(f"Unknown levels for predictor {name!r}: {series[unknown].unique().tolist()}")
                if series.isna().any():
                    raise ValueError(f"Prediction data has missing values for {name!r}.")
                columns.extend((series == level).to_numpy(dtype=float) for level in levels[1:])
            else:
                values = pd.to_numeric(series, errors="raise").to_numpy(dtype=float)
                if not np.isfinite(values).all():
                    raise ValueError(f"Prediction data has non-finite values for {name!r}.")
                columns.append(values)
        return np.column_stack(columns)

    def predict(self, frame: pd.DataFrame, confidence: float = 0.95) -> pd.DataFrame:
        """Population means and CIs, excluding subject-specific random effects.

        The t interval uses residual model degrees of freedom n - p - 2 (fixed
        coefficients plus the two variance parameters), as in ggeffects' default
        interval for Gaussian lmer models. It is a confidence interval for the
        population mean, not an interval for a new observation.
        """
        if not 0 < confidence < 1:
            raise ValueError("confidence must lie between zero and one.")
        design = self.design_matrix(frame)
        mean = design @ self._beta
        se = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", design, self._covariance, design), 0))
        df = self.statistics["prediction_df"]
        # There is no defined t interval with nonpositive model degrees of
        # freedom. Preserve estimable means/SEs and expose unavailable limits.
        critical = stats.t.ppf((1 + confidence) / 2, df) if df > 0 else math.nan
        return pd.DataFrame({"mean": mean, "se": se, "ci_low": mean - critical * se,
                             "ci_high": mean + critical * se}, index=frame.index)


def fit_random_intercept(
    frame: pd.DataFrame,
    response: str,
    predictors: list[str],
    subject: str,
    contrast: np.ndarray | None = None,
    contrast_terms: list[str] | None = None,
) -> MixedModelResult:
    """Fit the SwE report's ML model and report Satterthwaite inference.

    Predictor types are classified over the full input before jointly removing
    missing rows, matching the R report's global numeric-factor coercion. An
    existing pandas Categorical is honoured; unused levels are then dropped from
    the complete model frame as in R's model.frame. Invalid or rank-deficient
    designs raise ValueError rather than silently changing the fixed effects.

    Contrasts refer to the supplied ``contrast_terms``. A term with multiple
    treatment-coded coefficients is ambiguous and is returned as an explicit
    unsupported contrast; it is never approximated by the first coefficient.
    """
    predictors = list(dict.fromkeys(name for name in predictors if name != "Intercept" and name != subject))
    required = list(dict.fromkeys([response, subject, *predictors]))
    missing_columns = [name for name in required if name not in frame]
    if missing_columns:
        raise ValueError(f"Mixed-model columns were not found: {missing_columns}")
    data = frame[required].copy()
    data[response] = pd.to_numeric(data[response], errors="coerce")
    data = data.replace([np.inf, -np.inf], np.nan)
    # Coercion belongs before complete-case deletion: otherwise a continuous
    # nine-level covariate could become a factor when only six levels remain.
    predictor_types = {name: "categorical" if is_categorical(data[name]) else "continuous"
                       for name in predictors}
    for name, kind in predictor_types.items():
        if kind == "categorical" and not isinstance(data[name].dtype, pd.CategoricalDtype):
            data[name] = pd.Categorical(data[name], categories=_levels(data[name]))
    data = data.dropna()
    warnings: list[str] = []
    if len(data) != len(frame):
        warnings.append(f"Removed {len(frame) - len(data)} row(s) with missing/non-finite model data.")
    if not len(data):
        raise ValueError("No complete observations are available for the mixed model.")

    categorical_levels: dict[str, list[Any]] = {}
    design_columns = ["(Intercept)"]
    column_predictors = ["Intercept"]
    design_parts = [np.ones(len(data))]
    for name in predictors:
        series = data[name]
        categorical = predictor_types[name] == "categorical"
        if categorical:
            levels = _levels(series)
            if len(levels) < 2:
                raise ValueError(f"Categorical predictor {name!r} has fewer than two observed levels.")
            categorical_levels[name] = levels
            for level in levels[1:]:
                design_parts.append((series == level).to_numpy(dtype=float))
                design_columns.append(f"{name}[{level}]")
                column_predictors.append(name)
        else:
            design_parts.append(pd.to_numeric(series, errors="raise").to_numpy(dtype=float))
            design_columns.append(name)
            column_predictors.append(name)
    design = np.column_stack(design_parts)
    y = data[response].to_numpy(dtype=float)
    n, p = design.shape
    if n <= p + 1:
        raise ValueError("A mixed model needs more complete observations than fixed coefficients plus one.")
    if np.linalg.matrix_rank(design) < p:
        raise ValueError("The mixed-model fixed-effects design is rank deficient; check constant/collinear predictors.")
    subjects, group_indices = np.unique(data[subject].astype(str), return_inverse=True)
    groups = len(subjects)
    if groups < 2 or groups >= n:
        raise ValueError("Random-intercept fitting requires at least two subjects and repeated observations.")
    counts = np.bincount(group_indices).astype(float)
    if (counts < 2).any():
        warnings.append("Some subjects have only one complete observation.")
    group_x = np.zeros((groups, p))
    np.add.at(group_x, group_indices, design)
    group_y = np.bincount(group_indices, weights=y)
    xtx = design.T @ design
    xty = design.T @ y

    def evaluate(theta: float) -> tuple[float, np.ndarray, np.ndarray, np.ndarray, float]:
        ratio = theta * theta
        weights = ratio / (1 + counts * ratio)
        information = xtx - group_x.T @ (weights[:, None] * group_x)
        cross = xty - group_x.T @ (weights * group_y)
        try:
            covariance_unscaled = np.linalg.inv(information)
        except np.linalg.LinAlgError:
            return math.inf, np.zeros(p), np.eye(p), np.zeros(n), math.inf
        beta = covariance_unscaled @ cross
        residual = y - design @ beta
        group_residual = np.bincount(group_indices, weights=residual, minlength=groups)
        quadratic = float(residual @ residual - np.sum(weights * group_residual**2))
        if not quadratic > np.finfo(float).tiny:
            return math.inf, beta, covariance_unscaled, residual, quadratic
        deviance = float(np.log1p(counts * ratio).sum() + n * (math.log(2 * math.pi * quadratic / n) + 1))
        return deviance, beta, covariance_unscaled, residual, quadratic

    # Search the nonnegative variance-ratio boundary and every bracketed local
    # minimum of the one-dimensional profile, including unbalanced designs.
    log_grid = np.linspace(-16, 16, 65)
    grid_values = np.array([evaluate(math.exp(value / 2))[0] for value in log_grid])
    candidates = [(evaluate(0.0)[0], 0.0)]
    for index in range(1, len(log_grid) - 1):
        if grid_values[index] <= grid_values[index - 1] and grid_values[index] <= grid_values[index + 1]:
            optimum = optimize.minimize_scalar(
                lambda value: evaluate(math.exp(value / 2))[0],
                bounds=(log_grid[index - 1], log_grid[index + 1]), method="bounded",
                options={"xatol": 1e-11, "maxiter": 500},
            )
            if optimum.success:
                candidates.append((float(optimum.fun), math.exp(float(optimum.x) / 2)))
    if np.argmin(grid_values) in (0, len(log_grid) - 1):
        edge = int(np.argmin(grid_values))
        if edge == len(log_grid) - 1 and grid_values[edge] < candidates[0][0]:
            raise ValueError("The random/residual variance ratio is unbounded within the numerical search range.")
    deviance, theta = min(candidates, key=lambda candidate: candidate[0])
    deviance, beta, unscaled_covariance, residual, quadratic = evaluate(theta)
    if not np.isfinite(deviance):
        raise ValueError("The response has no estimable positive residual variance.")
    sigma2 = quadratic / n
    if sigma2 <= np.finfo(float).eps * max(float(np.var(y)), np.finfo(float).tiny):
        raise ValueError("The response has no positive residual variance at numerical precision.")
    sigma = math.sqrt(sigma2)
    random_variance = theta * theta * sigma2
    covariance = sigma2 * unscaled_covariance
    boundary = theta < 1e-6
    if boundary:
        warnings.append("The random-intercept variance is on the zero boundary (singular fit).")

    # Analytic observed Hessian of profiled-over-beta deviance D(theta, sigma).
    denominator = 1 + counts * theta**2
    group_residual = np.bincount(group_indices, weights=residual, minlength=groups)
    first_weight = 2 * theta / denominator**2
    second_weight = 2 * (1 - 3 * counts * theta**2) / denominator**3
    info_derivative = -group_x.T @ (first_weight[:, None] * group_x)
    beta_derivative = -unscaled_covariance @ (group_x.T @ (first_weight * group_residual))
    q_first = -float(np.sum(first_weight * group_residual**2))
    information = np.linalg.inv(unscaled_covariance)
    q_second = -float(np.sum(second_weight * group_residual**2)) - 2 * float(beta_derivative @ information @ beta_derivative)
    logdet_second = float(np.sum(2 * counts * (1 - counts * theta**2) / denominator**2))
    hessian = np.array([
        [logdet_second + q_second / sigma2, -2 * q_first / sigma**3],
        [-2 * q_first / sigma**3, 6 * quadratic / sigma**4 - 2 * n / sigma2],
    ])
    eigenvalues, eigenvectors = np.linalg.eigh(hessian)
    positive = eigenvalues > 1e-8
    if (eigenvalues < -1e-8).any():
        warnings.append("The variance-parameter Hessian has a negative eigenvalue; inference may be unreliable.")
    if not positive.all():
        warnings.append("The variance-parameter Hessian is singular; a positive-eigenvalue pseudoinverse was used.")
    covariance_varpar = 2 * (eigenvectors[:, positive] / eigenvalues[positive]) @ eigenvectors[:, positive].T
    jacobian_theta = -sigma2 * unscaled_covariance @ info_derivative @ unscaled_covariance
    jacobian_sigma = 2 * sigma * unscaled_covariance

    def inference(weights: np.ndarray) -> dict[str, float | None]:
        variance = float(weights @ covariance @ weights)
        estimate = float(weights @ beta)
        se = math.sqrt(max(variance, 0))
        gradient = np.array([weights @ jacobian_theta @ weights, weights @ jacobian_sigma @ weights])
        variance_of_variance = float(gradient @ covariance_varpar @ gradient)
        df = 2 * variance**2 / variance_of_variance if variance_of_variance > 0 else math.nan
        t_value = estimate / se if se > 0 else math.nan
        p_value = 2 * stats.t.sf(abs(t_value), df) if df > 0 else math.nan
        critical = stats.norm.ppf(0.975)  # R confint(..., method="Wald").
        return {"beta": _finite(estimate), "se": _finite(se), "t": _finite(t_value),
                "df": _finite(df), "p": _finite(p_value),
                "ci_low": _finite(estimate - critical * se), "ci_high": _finite(estimate + critical * se)}

    coefficients = []
    for index, name in enumerate(design_columns):
        row = {"term": name, "predictor": column_predictors[index], **inference(np.eye(p)[index])}
        coefficients.append(row)
    if any(row["df"] is None for row in coefficients):
        warnings.append("Some Satterthwaite degrees of freedom could not be estimated; their p-values are unavailable.")
    fixed_variance = float(np.var(design @ beta, ddof=1))
    total_variance = fixed_variance + random_variance + sigma2
    prediction_df = n - p - 2
    if prediction_df <= 0:
        warnings.append("Population-mean prediction intervals are unavailable: residual model degrees of freedom are nonpositive.")
    result_stats: dict[str, Any] = {
        "method": "Gaussian random-intercept maximum likelihood",
        "inference": "Satterthwaite t tests from the observed variance-parameter Hessian",
        "confidence_intervals": "95% normal-Wald fixed-effect intervals",
        "prediction_intervals": "95% population-mean t intervals, excluding random-effect and residual variation",
        "prediction_df": prediction_df,
        "converged": not bool((eigenvalues < -1e-8).any()),
        "singular": bool(boundary), "nobs": int(n), "n_subjects": int(groups),
        "rows_dropped": int(len(frame) - n), "coefficients": coefficients,
        "design_columns": design_columns, "covariance": covariance.tolist(),
        "random_variance": float(random_variance), "residual_variance": float(sigma2),
        "log_likelihood": float(-0.5 * deviance),
        "r2_marginal": float(fixed_variance / total_variance),
        "r2_conditional": float((fixed_variance + random_variance) / total_variance),
        "warnings": warnings,
    }
    if contrast is not None:
        terms = contrast_terms if contrast_terms is not None else ["Intercept", *predictors]
        values = np.asarray(contrast, dtype=float).reshape(-1)
        if len(values) != len(terms) or not np.isfinite(values).all():
            raise ValueError("Contrast length must match its declared terms and all weights must be finite.")
        weights = np.zeros(p)
        unsupported: list[str] = []
        for term, value in zip(terms, values):
            if value == 0:
                continue
            term = "Intercept" if term == "(Intercept)" else term
            indices = [index for index, name in enumerate(column_predictors) if name == term]
            if len(indices) == 1:
                weights[indices[0]] += value
            else:
                unsupported.append(term)
        if unsupported:
            message = "Contrast terms have no unique fitted coefficient: " + ", ".join(unsupported)
            result_stats["contrast"] = {"supported": False, "reason": message}
            warnings.append(message)
        elif not np.any(weights):
            message = "The requested contrast is identically zero and cannot be tested."
            result_stats["contrast"] = {"supported": False, "reason": message}
            warnings.append(message)
        else:
            result_stats["contrast"] = {"supported": True, "terms": list(terms),
                                        "weights": values.tolist(), **inference(weights)}
    return MixedModelResult(result_stats, predictor_types, categorical_levels,
                            design_columns, predictors, beta, covariance, column_predictors)
