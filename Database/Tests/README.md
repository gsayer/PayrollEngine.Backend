# Result job-status regression tests

Run from any directory with Python 3.10+ and Docker:

```sh
python3 Database/Tests/result_job_status.py --provider mysql
python3 Database/Tests/result_job_status.py --provider sqlserver
```

SQL Server requires a host supported by Microsoft's Linux container image (the
GitHub Actions job uses native x86-64). `--image` accepts a tag or digest override;
`--output path.json` saves every assertion. The exit code is nonzero on failure.

Each invocation creates its own randomly named container and internal Docker
network, with no published ports or host volumes. It imports the real `Create-Model` script,
inserts synthetic result fixtures, executes the actual stored procedures, and
removes the container, anonymous volumes and network on completion or an exception.
No existing database or application instance is used.

The same assertions run against the initial schema, after reapplying the nine
individual procedure sources, and (MySQL only) after applying the merged routine
bundle. Procedure replacement must preserve the existing result rows.

Coverage:

- All seven `PayrunJobStatus` values and the unfiltered `NULL` case.
- All nine result procedures, with no key filter, one key, and multiple keys;
  this exercises both SQL Server non-consolidated query branches.
- Open Draft/Release/Process corrections cannot replace the last Complete row.
- A newer Complete correction does replace the earlier Complete row.
- Forecast labels, Forecast status, Abort/Cancel, evaluation date, tenant,
  employee, division, and retro-job filters remain effective.

The fixtures intentionally isolate database filtering from the payrun lifecycle.
SQL Server foreign-key checks on the six fixture tables are disabled only inside
the disposable database; unrelated tenants, regulations and employee objects are
not populated. This suite does not start the Backend or validate a payroll rule.

For #15, the tests must fail on the original bitwise filter and pass with exact
status comparison. Historical migration snapshots remain unchanged.
