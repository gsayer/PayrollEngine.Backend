# Regression test for Backend issue #15 / VER-336

This branch contains tests only, based on upstream commit
`ecd049ee43a64c130981301192a0f8ab36eed1ed`. It does not change production SQL.

Requires Python 3.10+ and Docker. SQL Server needs a supported native x86-64
Linux container host; the workflow uses Ubuntu 24.04. No Python dependencies.

From the Backend checkout root:

```sh
python3 Database/Tests/result_job_status.py --provider mysql --output mysql.json
python3 Database/Tests/result_job_status.py --provider sqlserver --output sqlserver.json
```

The script also works independently: copy this single Python file anywhere and
pass `--repo /absolute/path/to/PayrollEngine.Backend` to select the checkout under test.
It reads that checkout's actual creation script and procedure signatures.

Each command checks 334 assertions against a new disposable database. On original
upstream SQL it must exit 1 with assertion failures. After a complete correction
it must exit 0 with zero failures. A traceback or `completed: false` in the JSON
means an infrastructure/installation failure, not a successful reproduction.

## Central reproducer

For the same employee, period and result key, insert an older Complete job (id 20,
value 1000), then a newer Draft job (id 21, value 9999), both without a forecast
label. Request consolidated results with `jobStatus = Complete (3)`.

Expected: job 20. Original implementation returns job 21. The assertion
`newer_draft_must_not_replace_complete` checks this on all five consolidated
procedures. `draft_only` additionally expects no result if only a Draft exists.

## Coverage and scope

- All nine result procedures and all seven statuses, plus the unfiltered NULL case.
- No key filter, one key, and multiple keys, including both SQL Server query branches.
- Newer Draft/Release/Process must not replace Complete; newer Complete must replace it.
- Forecast labels/status, Abort/Cancel, tenant, employee, division, evaluation-date
  and retro-job filters.

Fixtures isolate database filtering. They deliberately bypass foreign keys on the
six fixture tables inside the disposable SQL Server database. This is not an
application/payrun lifecycle or payroll regulation test. Assertions compare returned
job identities, with deliberately different result values to make the error visible.

No existing database is used. Each invocation creates a random container and internal
Docker network, publishes no ports, mounts no host directories, and removes its
resources on normal completion or a handled exception.

## Verify all installation paths after the fix

Add `--all-installations` to repeat the suite after reapplying the nine individual
procedure sources and, on MySQL, the merged routine bundle. This gives 1002 MySQL
assertions and 668 SQL Server assertions. Source replacement must preserve stored rows.

The default uses only Create-Model, to isolate this bug from a pre-existing stale
MySQL merged bundle on the original revision. Historical migration snapshots are
not exercised. Updating only individual sources does not fix Create-Model: each
installation path must be updated to pass the complete suite.

## Before/after verification

The included workflow runs this same test against two pinned revisions:

- Original: `ecd049ee43a64c130981301192a0f8ab36eed1ed` (334 assertions per provider).
- Corrected reference: `ef22b161d31e6abc7a316cf35717cf9a241e239b` (all installation paths).

For original SQL, the workflow explicitly requires the five central assertions to
return Draft instead of Complete. Its green status therefore means the expected
failure was confirmed. The Python test itself remains red on original SQL.
For corrected SQL, the workflow requires every assertion to pass.
JSON artifacts retain all expected/actual job IDs.

Issue: https://github.com/Payroll-Engine/PayrollEngine.Backend/issues/15
