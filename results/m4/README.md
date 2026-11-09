# The M4 run

The 12-incident run behind the results in the main README. Checksums in `SHA256SUMS`.

| file | what it is |
|---|---|
| `recorded.json` | every model answer as recorded during the run; `--replay` feeds these back with no network |
| `report.json` | the investigation report: each incident's decision, transcript, tokens and cost |
| `validations.json` | the trusted validation of each of the 29 patches (applies, own tests, protected checks, replay), bound to the patch's hash |
| `score.json`, `score.md` | the official score |
| `side-check/` | the six patches with three trailing context lines added, the report with only those replaced (plus two officially valid patches as controls) and their validations; not part of the official score |

The investigation ran twice: once live and once rebuilt from the recorded answers by an untouched copy of the code.
The two reports were identical, otherwise the run would have counted as invalid. `make reproduce` repeats that
rebuild and the scoring.
