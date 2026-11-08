# replaykit M4: code-aware incidents on a real app, with a validated patch

Extends SPEC-3 (the bounded loop, limits, evidence rule); M3 suites and reports are unchanged. Everything here is
offline: the app runs on a simulated clock, the database is SQLite in memory, and no network is used.

## B1. The app and its history
- `~/Projects/deeptrace-shopapp`: three services (`web` → `orders` → `inventory`, `web` → `inventory`), a bounded
  connection pool over SQLite, OpenTelemetry-shaped spans, logs and gauges. `shop/runtime.py`, `shop/telemetry.py` and
  `shop/pool.py` are the measuring instruments: a patch may not change them.
- The history is one `main` line built by `make_history.py` (fixed authors and dates, so the same hashes every build).
  Each defect is a commit whose message reads like ordinary work; the commits after it (its fix, later features) are
  unreachable from any incident that deploys it. Earlier incidents and their fixes stay visible, as in a real repo.
- `world.py` (trusted) runs every service at its own commit, seeded traffic, deploys at fixed times and outside events
  (a load test, a database job). The monitoring system turns spans into per-minute request metrics; the page rule is
  web's 5xx rate over 5% for two minutes in a row. An incident is cut at the first page.

## B2. Cases (`replaykit/app/cases.py`; truth never leaves the trusted side)
Every case pages from running the app (c1 at 13:00, the others at 12:00, for seeds 1 and 2); the fix commit after each
defect never pages under the same traffic, and c5 and c6 never page without their outside event. Every commit passes
the app's own tests, as a real defect would pass CI.
Incident ids are opaque (`shop-app:c<N>:<seed>`), so neither ids nor run descriptions name the fault.

| # | Name | What happens | Expected |
|---|---|---|---|
| c1 | leak | `inventory: move the stock check into a helper` stops returning the connection when a reservation fails | patch: inventory, `resource_leak`, `shop/inventory.py`, `Inventory.reserve` |
| c2 | config | `config: limit checkouts per product to slow down stock bots` turns web's checkout limit (5 per user per second) into 5 per product per minute; buyers of popular products get 503s (about a quarter of requests), single-request tests still pass | patch: web, `config_error`, `shop/config.py` (no function) |
| c3 | decoy | orders caps an order at two items but raises a plain error, so a third of checkouts become 502s; a harmless web deploy (log wording) follows 90 s later, closer to the page and after the errors began | patch: orders, `code_defect`, `shop/orders.py`, `Orders.place` |
| c4 | slow query | product lookups become case-insensitive with `lower(sku) = lower(?)`, which SQLite runs as a full scan of a 6,000-row catalogue (about 1.8 s); product pages pass web's 1.5 s upstream timeout and fail | patch: inventory, `slow_query`, `shop/inventory.py`, `Inventory.stock` |
| c5 | outside lock | no deploy; a job outside the shop holds a write transaction on `items`; every request that reaches inventory fails and inventory logs `database table is locked: items` | escalate |
| c6 | load test | no deploy; one internal client (`perf-loadtest`) sends 100 checkouts a second for 110 s; web's per-user limit answers 503 "slow down"; customers are not affected and the traffic has stopped by the page | dismiss |

- Query cost (from `db: run queries through one helper` on): `db.query` sleeps by SQLite's own plan: `QUERY_MS`, plus
  `SCAN_US_PER_ROW` (300 µs) for each row of a scanned table. c1 runs on its old commits, so its bytes are unchanged.
- History after the leak: its fix, the per-user checkout limit, c2's defect and fix, c3's defect, decoy and fix, the
  upstream timeout, the query helper, c4's defect and fix (`later_commits.py` beside `make_history.py`).
- c5 and c6 are honest negatives: nothing in the shop's code is wrong. c5 is outside the team's reach (escalate). c6
  is a planned test throttled as designed, over before the page (dismiss; escalate is safe but not resolved). A patch
  is wrong in both.

## B3. Scoring (`score_app`)
- `decision_correct`: the act equals the case's expected act. `root_cause`: service, fault type and commit. `location`:
  the file and, when the truth names one, the function (`Class.method` or `method`).
- `evidence_valid`: every cited id was in a tool result (with a decision, every such result reached the model), and a
  diagnosis or patch also read its commit and file.
- `resolved`: decision correct, and for diagnose/patch also location and evidence, and for patch a valid patch.

## B4. Patch validation (trusted) and the execution boundary
- Static check: only `shop/*.py` except the instrument files, no assignment to an instrument setting, at most 40
  changed lines, plain unified diff, added lines ASCII only and without imports or denied names (a break here is a
  rejected reply: one more try). After a fresh clone at the deployed commit and `git apply`, an AST comparison
  refuses any new import, dunder, store or delete of an instrument setting, and any new name or attribute in `REACH`
  (denied names, every standard-library module name, the interpreter's handles, `.execute` and the other SQL entry
  points, `type`) or among the harness internals, and any new way of handing `db.query` or `Pool` a clock other than the
  app's own (`self.runtime`; for `Pool` also its `runtime` argument; first in place or as `runtime=`), including any
  other reference to them and any store to `runtime` (a break here makes the patch invalid).
- Then three runs: the app's own tests, the case's protected checks (`run.py <tree> <check files>`), and the same
  traffic with the patched commit deployed. Valid = own tests pass, every protected check runs and passes, and the
  replay is clean (no page, pool back to 0). A patch counts as the decision's fix only if it changes the decision's
  file. Each case lists its protected checks: its own and the earlier ones that apply to its code
  (`replaykit/app/protected/check_*.py`, never in the bundle, never shown to the model): `check_inventory` (connections
  come back), `check_limits` (one busy user is slowed for one second: after a burst at 0, refused at 0.95 s and served at 1.05 s;
  a product's many buyers are not), `check_orders` (an order over
  the cap is a 4xx and takes no stock), `check_lookup` (any case matches, under 50 ms in 6,000 rows, and the
  statements SQLite actually ran, traced on every connection and explained on the check's own, search by an index
  and never scan a table, under any alias; each of four lookups (`sku-5999`, `sku-4242`, `Sku-4321` and a miss, 10 s apart)
  takes at most 200 SQLite steps, counted per statement by a progress handler on those connections, with only
  `CREATE INDEX` (after any SQL comment) left out of the first lookup's count: correct fixes take 14-22, a range over the whole catalogue
  36,013; after a reservation, reads in lower, upper and title case all show it, also after a read that
  overlapped it at any offset from 4 ms before to 11 ms after its start, with or without a read first). Reference fixes:
  `protected/<case>.patch`.
- **Boundary:** the patched code runs only inside those three runs. For any model-written patch they run as the
  `_arcexec` account through the coordinator's existing `sandbox.run` (Seatbelt, no network, the home folder hidden,
  reads limited to the clone, replaykit and the interpreter, writes only to a scratch folder). The trusted side
  builds the clone, reads only each run's stdout file and accepts only an exact result: the expected keys and types,
  and failed checks named only by real check ids of that run's list. Each job gets its own fresh scratch folder and
  may read but not change the clone. A runner has `root` (where job folders go), `replaykit` (the tree the jobs run)
  and is called with (args, cwd, scratch, read); `validate_patch` and `run_app_case` have no default runner. Tests and
  scripted rehearsals pass `LocalRunner` (sandbox-exec as the same user: no network, writes only to scratch, reads
  not limited, so never for a model's patch).
- **Named limit:** the protected checks and the replay import the patched code, so they run in its process; a
  malicious patch could forge their output. The text and AST rules close the bypasses found so far (fullwidth
  names, module chains such as `db.re.enum.bltns`, split SQL) but are not a proof; the boundary keeps a forging patch
  from files, keys and the network; and a human reviews every patch before anything is merged. "Protected" means
  the model never sees the checks; it does not make their results tamper-proof.

## B5. Limits
Simulated clock and SQLite in memory (an application-level demo, not a Postgres equivalent); one app, six cases;
query cost is a model of the plan, not measured time; per-minute metrics; the page rule is fixed. The shop's cost
model reads `SCAN CONSTANT ROW` (a scalar subquery such as `sku = (SELECT lower(?))`) as a table named `CONSTANT`
and fails the request, so that correct spelling is rejected; the shop's pinned history is not changed for it. As in SPEC-3, an
escalation or dismissal needs no evidence, so an "always escalate" stub resolves c5 and an "always dismiss" stub c6
(and fails the other five); reports show each case.

## B6. The app suite, its commands and the report
- `suites/shop-app-v1.json`: the six cases × seeds 1, 2 (12 incidents), limits 15 turns, 25 tool calls, 200,000
  tokens, 600,000 ms, three models and the two rule baselines. `shop_commit` pins the shop repository: it must hold
  that commit, and every case commit must be in its history.
- `replaykit investigate-app SUITE --repo SHOP (--broker URL | --replay REPORT) --out PATH`: runs every investigator
  on every incident and writes the investigation (decisions, what each saw, transcripts, the deployed commits); no
  patch is run here. `--replay` answers from a saved investigation's transcripts (each packet hash must match), so
  the verifier rebuilds the same bytes.
- A patch's files get a `diff --git` line where they lack one before `git apply` (without it the next file's
  `--- a/` header reads as a removed line); the investigation's deployed commits must match the case's.
- Patch validation is the trusted side's: `validate_app(report, repo, runner)` runs `validate_patch` for every
  `patch` decision of the verifier's investigation, giving {incident id: {investigator: result plus the patch's
  sha256}}. Tests and
  rehearsals pass `LocalRunner`; model patches only the `_arcexec` runner.
- `replaykit score-app INVESTIGATION --validations PATH --out PATH [--md PATH]`: refuses an investigation that is not
  exactly its suite's cases × seeds with one complete record per investigator, and validations that do not cover
  exactly its patch decisions, are not of that patch on that incident and deployed commit (a hash of
  incident, service, base and patch; files, changed lines), name checks the case does not run, have a replay or a
  verdict that does not add up; then scores against the truth.
- The report counts apart, per investigator: decisions (resolved, right decision, escalations and dismissals, false
  ones), diagnosis on the incidents that need a fix (root cause, file and function, evidence valid), remediation
  (patches, applies, own tests, protected checks, replay clean, valid fix, patches where none was needed, validations
  that did not finish) and effort; then every incident.
- Rule baselines, labelled in the report: `revert_last_deploy` reverts the newest deploy before the page (any
  service; every page here is on web) and escalates when there was none; `deepest_abnormal_commit` blames the deployed
  commit of the abnormal service lowest in the call graph and dismisses when none looks abnormal. Both read the whole
  bundle without tools, use thresholds picked for this simulator, and name no function. A revert of a commit that
  added or deleted a file breaks the patch rules and is judged not valid (no current case has one).
