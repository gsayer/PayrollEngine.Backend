#!/usr/bin/env python3
"""Exercise the actual result procedures in a disposable Docker database.

Requires Python 3 and Docker, no Python packages or existing database. No ports
are published; each container has its own internal Docker network.
Synthetic persistence fixtures deliberately bypass foreign keys
on SQL Server; this is not a payrun lifecycle or application integration test.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
TABLES = ["WageTypeResult", "WageTypeCustomResult", "CollectorResult",
          "CollectorCustomResult", "PayrunResult"]
PROCEDURES = {"GetConsolidated" + t + "s": t for t in TABLES}
PROCEDURES.update({"Get" + t + "s": t for t in TABLES[:-1]})
STATUSES = ["Draft", "Release", "Process", "Complete", "Forecast", "Abort", "Cancel"]


def quote(value):
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


class Database:
    def __init__(self, provider, image):
        self.provider = provider
        self.mysql = provider == "mysql"
        self.image = image
        self.name = "pe-result-status-test-" + secrets.token_hex(6)
        self.columns = {}
        self.env = dict(os.environ)

    def execute(self, sql, database="PayrollEngine"):
        if self.mysql:
            client = ["mysql", "--protocol=TCP", "-h", "127.0.0.1", "-u", "root",
                      "--batch", "--raw", "--skip-column-names"]
            if database:
                client.append(database)
        else:
            client = ["/opt/mssql-tools18/bin/sqlcmd", "-S", "tcp:127.0.0.1,1433", "-U", "sa",
                      "-C", "-b", "-r", "1", "-h", "-1", "-s", "|", "-W", "-w", "65535",
                      "-l", "5", "-d", database or "master"]
            sql = "SET NOCOUNT ON;\nGO\n" + sql
        password_env = "MYSQL_PWD" if self.mysql else "SQLCMDPASSWORD"
        result = subprocess.run(["docker", "exec", "-i", "--env", password_env, self.name, *client],
                                input=sql, text=True, capture_output=True, timeout=120, env=self.env)
        if result.returncode:
            raise RuntimeError(result.stderr + result.stdout)
        separator = "\t" if self.mysql else "|"
        return [[part.strip() for part in line.split(separator)]
                for line in result.stdout.splitlines() if line.strip()]

    def __enter__(self):
        password = "TestOnly_aA1!" + secrets.token_hex(12)
        self.env.update(MYSQL_ROOT_PASSWORD=password, MYSQL_PWD=password,
                        MSSQL_SA_PASSWORD=password, SQLCMDPASSWORD=password)
        options = (["--env", "MYSQL_ROOT_PASSWORD"] if self.mysql else
                   ["--env", "ACCEPT_EULA=Y", "--env", "MSSQL_PID=Developer",
                    "--env", "MSSQL_SA_PASSWORD",
                    "--env", "MSSQL_MEMORY_LIMIT_MB=2048"])
        # MySQL can use tmpfs; SQL Server uses its image's normal data filesystem.
        mounts = ["--tmpfs", "/var/lib/mysql:rw,size=1g", "--tmpfs", "/tmp:rw,size=256m"] if self.mysql else []
        subprocess.run(["docker", "network", "create", "--internal", self.name],
                       check=True, capture_output=True)
        try:
            subprocess.run(["docker", "run", "-d", "--name", self.name, "--network", self.name,
                            *mounts, *options, self.image], env=self.env, check=True, capture_output=True)
            for attempt in range(30):
                try:
                    self.execute("SELECT 1;", database=None)
                    break
                except RuntimeError:
                    state = subprocess.check_output(
                        ["docker", "inspect", "--format", "{{.State.Status}}", self.name], text=True).strip()
                    if state == "exited" or attempt == 29:
                        raise
                    time.sleep(2)
            self.execute((ROOT / "Database" / ("Create-Model.mysql.sql" if self.mysql
                         else "Create-Model.sql")).read_text(encoding="utf-8-sig"), database=None)
            for table in TABLES + ["PayrunJob"]:
                self.columns[table] = self.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
                    f"WHERE TABLE_NAME = '{table}' AND " +
                    ("TABLE_SCHEMA = 'PayrollEngine'" if self.mysql else "TABLE_SCHEMA = 'dbo'") +
                    " ORDER BY ORDINAL_POSITION;")
                if not self.mysql:
                    self.execute(f"ALTER TABLE [{table}] NOCHECK CONSTRAINT ALL;")
            return self
        except BaseException:
            logs = subprocess.run(["docker", "logs", "--tail", "60", self.name],
                                  capture_output=True, text=True)
            print(logs.stdout + logs.stderr, flush=True)
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            subprocess.run(["docker", "rm", "-f", "-v", self.name], check=True, capture_output=True)
        finally:
            subprocess.run(["docker", "network", "rm", self.name], check=True, capture_output=True)

    def identifier(self, name):
        return f"`{name}`" if self.mysql else f"[{name}]"

    def insert(self, table, overrides):
        values = {}
        for name, typ, nullable in self.columns[table]:
            if name in overrides:
                values[name] = overrides[name]
            elif nullable == "NO":
                values[name] = ("2026-01-01 00:00:00" if typ.startswith("datetime") else
                                1 if typ in ("int", "bigint", "tinyint", "bit", "decimal") else "synthetic")
        sql = (f"INSERT INTO {self.identifier(table)} "
               f"({','.join(self.identifier(k) for k in values)}) "
               f"VALUES ({','.join(quote(v) for v in values.values())});")
        if not self.mysql:
            sql = f"SET IDENTITY_INSERT [{table}] ON;\n{sql}\nSET IDENTITY_INSERT [{table}] OFF;"
        return sql

    def fixture(self, rows):
        sql = [f"DELETE FROM {self.identifier(t)};" for t in TABLES + ["PayrunJob"]]
        for row in rows:
            created = row.get("created", "2026-01-15 00:00:00")
            common = dict(Id=row["id"], TenantId=1, Created=created, Updated=created,
                          Forecast=row.get("forecast"), ParentJobId=row.get("parent"))
            sql.append(self.insert("PayrunJob", dict(common, JobStatus=row["status"],
                       Name=f"synthetic-{row['id']}", JobEnd=created)))
            for table in TABLES:
                sql.append(self.insert(table, dict(common, PayrunJobId=row["id"], EmployeeId=1,
                    DivisionId=1, Start=row.get("start", "2026-01-01 00:00:00"),
                    End="2026-01-31 23:59:59", StartHash=row.get("start_hash", 1), WageTypeNumber=1000,
                    CollectorName="synthetic", CollectorNameHash=1, Name="synthetic",
                    Value=row.get("value", 1000))))
        self.execute("\n".join(sql))

    def source(self, procedure):
        return ROOT / "Persistence" / ("Persistence.MySql" if self.mysql else "Persistence.SqlServer") / \
            "StoredProcedures" / (procedure + (".mysql.sql" if self.mysql else ".sql"))

    def call(self, procedure, status, key_count=0, **overrides):
        source = self.source(procedure).read_text(encoding="utf-8-sig")
        names = re.findall(r"^\s+IN p_(\w+)", source, re.M) if self.mysql else \
            re.findall(r"^\s+@(\w+) AS ", source, re.M)
        # Consolidated runtime queries always supply the completed period hashes.
        # Unlike MySQL, SQL Server does not treat an absent period list as all periods.
        values = dict(tenantId=1, employeeId=1, jobStatus=status, noRetro=0,
                      periodStartHashes="[1]")
        if key_count:
            values.update(wageTypeNumbers=json.dumps([1000, 2000][:key_count]),
                          collectorNameHashes=json.dumps([1, 2][:key_count]),
                          names=json.dumps(["synthetic", "other"][:key_count]))
        values.update(overrides)
        args = ", ".join(quote(values.get(n)) if self.mysql else f"@{n}={quote(values.get(n))}" for n in names)
        sql = f"CALL {procedure}({args});" if self.mysql else f"EXEC dbo.[{procedure}] {args};"
        fields = [col[0] for col in self.columns[PROCEDURES[procedure]]]
        return [dict(zip(fields, row, strict=True)) for row in self.execute(sql)]

    def install_sources(self):
        for procedure in PROCEDURES:
            self.execute(self.source(procedure).read_text(encoding="utf-8-sig"))


def run_suite(db, stage, observations):
    print(f"{db.provider}: testing {stage}", flush=True)
    def check(name, procedure, expected, status=3, **kwargs):
        rows = db.call(procedure, status, **kwargs)
        actual = sorted(int(r["PayrunJobId"]) for r in rows)
        observations.append(dict(stage=stage, case=name, procedure=procedure,
            expected=expected, actual=actual, passed=actual == expected))

    # Different periods keep consolidation from hiding statuses. Status and forecast
    # are tested separately; these rows do not model public lifecycle transitions.
    db.fixture([dict(id=s+1, status=s, start=f"2026-01-{s+1:02} 00:00:00", start_hash=s+1)
                for s in range(len(STATUSES))])
    for proc in PROCEDURES:
        for status in list(range(7)) + [None]:
            for key_count in range(3):  # unfiltered, single-key fast path, multi-key branch
                check(f"status={status},keys={key_count}", proc,
                      list(range(1, 8)) if status is None else [status+1],
                      status=status, key_count=key_count, periodStartHashes="[1,2,3,4,5,6,7]")

    completed = dict(id=20, status=3, value=1000)
    correction = dict(id=21, status=0, value=9999, created="2026-01-16 00:00:00")
    for label, rows, expected in [
        ("complete_only", [completed], [20]),
        ("draft_only", [correction], []),
        ("newer_draft_must_not_replace_complete", [completed, correction], [20]),
        ("newer_release_must_not_replace_complete", [completed, dict(correction, status=1)], [20]),
        ("newer_process_must_not_replace_complete", [completed, dict(correction, status=2)], [20]),
        ("newer_complete_replaces_complete", [completed, dict(correction, status=3)], [21]),
        ("named_forecast_excluded", [completed, dict(correction, status=3, forecast="budget")], [20]),
        ("newer_abort_excluded", [completed, dict(correction, status=5)], [20]),
        ("newer_cancel_excluded", [completed, dict(correction, status=6)], [20]),
    ]:
        db.fixture(rows)
        for proc in PROCEDURES:
            if proc.startswith("GetConsolidated"):
                check(label, proc, expected, key_count=1)

    db.fixture([completed, correction])
    for proc in PROCEDURES:
        check("other_tenant", proc, [], tenantId=2)
        check("other_employee", proc, [], employeeId=2)
        check("other_division", proc, [], divisionId=2)
        check("evaluation_date_before_all_results", proc, [], evaluationDate="2026-01-01 00:00:00")
        # A null status deliberately permits the correction; preserve this contract.
        check("no_status_filter", proc, [21] if proc.startswith("GetConsolidated") else [20, 21], status=None)

    db.fixture([dict(completed, status=4, forecast="budget"), correction])
    for proc in PROCEDURES:
        check("forecast_status_excludes_draft", proc, [20], status=4, forecast="budget")
        check("forecast_label_excluded_without_forecast", proc, [], status=4)

    db.fixture([completed, dict(correction, status=3, parent=99)])
    for proc in PROCEDURES:
        if proc.startswith("GetConsolidated"):
            check("no_retro", proc, [20], noRetro=1)
            check("exclude_parent_job", proc, [20], excludeParentJobId=99)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["mysql", "sqlserver"])
    parser.add_argument("--image", help="Override the provider's Docker image (tag or digest)")
    parser.add_argument("--output", type=Path, help="Write all assertion results as JSON")
    args = parser.parse_args()
    image = args.image or ("mysql:8.4" if args.provider == "mysql" else "mcr.microsoft.com/mssql/server:2022-latest")
    observations = []
    completed = False
    try:
        with Database(args.provider, image) as db:
            run_suite(db, "create-model", observations)
            # Reapply only the nine source procedures. Existing result rows survive.
            before = db.call("GetConsolidatedWageTypeResults", 3)
            db.install_sources()
            assert db.call("GetConsolidatedWageTypeResults", 3) == before
            run_suite(db, "reapplied-source-procedures", observations)
            if db.mysql:
                db.execute((ROOT / "Database/MySql-Routines.merged.sql").read_text(encoding="utf-8-sig"))
                run_suite(db, "merged-routines", observations)
        completed = True
    finally:
        failed = [r for r in observations if not r["passed"]]
        report = dict(provider=args.provider, image=image, completed=completed,
                      assertions=len(observations), failures=len(failed), results=observations)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{args.provider}: {len(observations)} assertions, {len(failed)} failures", flush=True)
    for failure in failed[:10]:
        print(json.dumps(failure))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
