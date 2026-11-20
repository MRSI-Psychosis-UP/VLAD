# Historical R implementation

These files are retained solely to audit the Python translation and regenerate
development reference values in an isolated R environment. They are not called
by VLAD, included in its wheel/container, or installed by its bootstrap.

The active implementations are `src/vlad/reporting/statistics.py` (Randomise),
`mixed.py` (SwE), and the shared Python ingestion/rendering modules. Frozen
numeric reference values and R package versions are in `tests/fixtures/`.

The archived templates contain known input-handling and contrast-selection
limitations described in `docs/python-reporting-migration.md`. Use `vlad report`
to generate reports; it has no R dependency.
