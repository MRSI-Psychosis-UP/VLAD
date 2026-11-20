# Python reporting implementation

Both former R Markdown reports are now implemented in `src/vlad/reporting/`.
The Bash workflow and `./vlad report` execute Python only. Installation, report
rendering, the wheel and the current Docker image require neither R nor Pandoc.
Historical R sources are archived under `reference/r/` for development auditing;
they are excluded from the runtime container and wheel.

## Run a report

```bash
./vlad report --kind randomise --results-dir /study/Results/Population \
  --analysis-name Population --matrix-name Model --permutations 500 \
  --export-dir /study/reports --modality mrsi
```

Use `--kind swe` for the longitudinal report, `--confirmation` for confirmation
filenames, and `--date YYYY-MM-DD` for a fixed export date. `./vlad run` selects
the report type automatically. `scripts/compat/create_report.py` and the
direct-file entry `src/vlad/reporting/run.py` support source checkouts and existing Python/pyenv
environments as well as the installed CLI. Compatibility wrappers accept the
same arguments after the move into `scripts/compat/`.

## Implemented calculations

| Report feature | Python implementation |
| --- | --- |
| Design summary | Means/SD, categorical counts/denominators/percentages, missing counts; identifiers and redundant group indicators excluded |
| Demographic comparisons | Wilcoxon for two-group continuous data, Kruskal–Wallis for more groups; uncorrected Pearson chi-square when expected counts permit, Fisher otherwise |
| Continuous regional summary | Pearson correlation, t test, Fisher-z confidence interval, linear regression and mean-response interval |
| Two-group regional summary | Welch t test, Welch df, mean difference/CI and unpooled Hedges g with noncentral-t CI |
| Multi-group regional summary | Welch ANOVA, omega² with noncentral-F lower interval, Games–Howell comparisons followed by Holm adjustment |
| SwE regional models | Gaussian random-intercept maximum likelihood, treatment-coded factors and explicit product columns |
| SwE inference | Analytic variance-parameter Hessian/Jacobian and Satterthwaite t df/p-values; normal-Wald coefficient intervals |
| SwE model summaries | Random/residual variance, marginal likelihood, marginal/conditional R², population predictions and t intervals |
| Reporting and exports | Self-contained HTML, Feather tables, SVG figures, statistical JSON, input hashes, versions and diagnostics |

The mixed model uses the same ML random-intercept specification as the old
report, independently of FSL SwE's sandwich estimator. It does not add random
slopes, REML or Kenward–Roger inference. Low-cardinality numeric factor coercion
matches the former SwE policy (at most six integer levels, or any two levels),
applied before response-specific complete-case removal. Missing model rows are
removed jointly and reported. Rank-deficient or unidentified models fail with
an explanation rather than silently changing their fixed effects.

Large sparse categorical tables use exact fixed-margin enumeration where
feasible. When the enumeration limit is exceeded, the summary labels its
reproducible fixed-margin Monte Carlo calculation, sample count, seed and Monte
Carlo standard error in the JSON. Oversized tables are diagnosed rather than
allocating unbounded work. These diagnostics are distinct from an estimable
nonsignificant test.

The implementations follow the original methods and their documented numerical
definitions, including the
[lmerTest Satterthwaite notes](https://github.com/runehaubo/lmerTestR/blob/master/pkg_notes/Satterthwaite_for_LMMs.Rmd),
[SciPy statistics](https://docs.scipy.org/doc/scipy/reference/stats.html), and
[Arrow Feather interchange](https://arrow.apache.org/docs/python/feather.html).

## Compatible inputs and outputs

The design remains `<matrix>.csv` containing tab-separated values. The existing
`To_analyse_with_R_<analysis>_<matrix>.txt` file remains a list of regional means
for compatibility; its name does not invoke R. The spelling without `_with_R`
is also accepted. Regional means retain their existing six-part double-underscore
filenames. Main-variable selection still comes from
`main_variable_<matrix>.txt` for Randomise.

Default HTML names remain `<analysis>_<matrix>_<modality>.html`, with `-swe`
and/or `-confirmation` suffixes as appropriate. The workflow copies the
standalone HTML into the parent Results directory. Feather remains
`<analysis>_<matrix>_<date>.feather` (SwE adds `_swe`). SVG basenames follow the
old reports. A JSON file beside each HTML report records every numerical result,
method, interval convention, package version, input hash and prediction grid.

Subject and visit labels are retained as strings when loading, preserving IDs
such as `001`. SwE's coerced factors are exported as Arrow categorical columns.
An equal row count cannot establish subject identity: regional means are still
aligned positionally to the original design/4D ordering, and reports state this
assumption. Use `--subject-column` and `--visit-column` when identifiers have
unusual names. A SwE subject file can supply missing subject IDs.

When an FSL `.mat` is present, it establishes the contrast column order by
matching the exported numerical design columns. Ambiguous matches or mismatched
sizes require `--design-columns` in exact FSL order; the Python code never
truncates a contrast to fit a different design. `--design-columns` names the
actual exported columns, not an automatically expanded formula.

## Deliberate corrections from the historical R reports

- Single-row contrasts are parsed as matrix data, fixing the old readr filename
  ambiguity. Both signs of a contrast are retained.
- SwE reports the complete signed linear contrast alongside all model
  coefficients. The old report selected only the first nonzero predictor and
  ignored its sign. An ambiguous multilevel factor contrast is explicitly
  unavailable until its coefficient mapping is supplied; useful coefficient
  summaries remain in a partial report with a nonzero command status.
- Bad row counts, duplicate result columns/paths, malformed headers and
  incompatible contrast/design mappings fail clearly. A single mean is never
  recycled across subjects.
- Empty result lists produce a design-only report and merged-data export.
  Missing pictures are identified, and missing design figures are regenerated.
- HTML escapes labels and embeds assets. Copying the HTML does not leave it
  dependent on paths inside an analysis directory.
- Failed regional models leave an explicit partial report and a nonzero exit
  status. Fit warnings, dropped rows, singular fits and failed comparisons are
  visible rather than silently omitted.

Regional follow-up statistics concern regions already selected by voxelwise
inference. Reports identify them as secondary exploratory summaries; they are
not an independent confirmation of the original selection.

## Validation

`tests/fixtures/randomise_reference.json` and `mixed_reference.json` contain
frozen synthetic R reference values and package versions. R was used only to
capture those development references. The tests consume JSON and require no R
installation.

The statistical tests compare Pearson estimates/intervals; Welch t/F/df/p;
Hedges g/omega² and intervals; Games–Howell plus Holm p-values; demographic
Wilcoxon/Fisher tests; and mixed-model coefficients, covariance, variances,
log-likelihood, Satterthwaite df/p, R², signed contrasts and predictions. Mixed
model comparisons include continuous, categorical and product-interaction
fixtures. Numeric tolerances are explicit in the tests: tight floating-point
comparisons for closed-form statistics, looser tolerances for optimiser-derived
R reference fits and interval inversions.

Pipeline checks exercise all report modes, confirmation, empty/invalid inputs,
path quoting, headless plotting, HTML escaping/embedded assets, Feather/SVG/JSON
exports, and Bash failure propagation. Sentinel R/Pandoc executables fail the
test if reporting attempts to invoke them.

```bash
./scripts/check.sh
./vlad doctor --profile reports
```

These references establish the tested synthetic cases, not universal equality
for every cohort. No real study dataset is included; validate subject ordering
and a known cohort before interpreting production results with the new backend.
