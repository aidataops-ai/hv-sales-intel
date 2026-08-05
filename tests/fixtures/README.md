# Test fixtures

## `jobspy_rows.json`

51 real job-board records (30 Indeed, 21 LinkedIn) sampled from the 2026-08-04
collection runs, in the shape `jobspy.scrape_jobs(...).to_dict("records")`
returns. The sample is stratified to be adversarial rather than representative:
it over-weights the cases that should be *rejected* — hospital systems, DSOs,
staffing agencies, clinical roles — plus remote-flagged, salaried and
confidential (no employer name) postings.

Two pandas quirks are preserved deliberately, because both have bitten this
pipeline before:

- Missing numerics are `float('nan')`, not `None`. `nan != nan` is the check.
  JSON has no NaN literal, so they are stored as the string `"__NAN__"` and
  converted back by `tests/test_job_boards.py::_load_rows`.
- Missing employer names arrive as NaN too, which stringifies to the literal
  `"nan"` — an employer named "nan" would otherwise pass every downstream
  emptiness check.

Using these instead of live board calls keeps the collector tests offline and
deterministic. Regenerate only if the board response shape changes.
