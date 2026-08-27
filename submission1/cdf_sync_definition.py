"""Reverse Sync Definition (as code)

This script defines the Lakebase CDF (Change Data Feed) configuration
that streams Postgres changes from northpeak_app schema into Unity Catalog
as SCD Type 2 history tables.

Run via: databricks bundle deploy (or invoke directly)
Co-authored-by: Genie Code <genie-code@databricks.com>
"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.postgres import CdfConfig

PROJECT_ID = "northpeak"
BRANCH_ID = "production"
DATABASE_ID = "databricks-postgres"
CDF_CONFIG_ID = "northpeak_app"

# Destination: external-storage-backed catalog for CDF writes
DEST_CATALOG = "northpeak_cdf_catalog"
DEST_SCHEMA = "cdf_history"

# Source: writable Postgres schema
SOURCE_PG_SCHEMA = "northpeak_app"


def create_cdf_config():
    w = WorkspaceClient()
    parent = f"projects/{PROJECT_ID}/branches/{BRANCH_ID}/databases/{DATABASE_ID}"

    op = w.postgres.create_cdf_config(
        parent=parent,
        cdf_config=CdfConfig(
            catalog=DEST_CATALOG,
            schema=DEST_SCHEMA,
            postgres_schema=SOURCE_PG_SCHEMA,
        ),
        cdf_config_id=CDF_CONFIG_ID,
    )
    result = op.wait()
    print(f"CDF Config created: {result.name}")
    print(f"  Streams {SOURCE_PG_SCHEMA}.* -> {DEST_CATALOG}.{DEST_SCHEMA}.lb_<table>_history")
    return result


def check_status():
    w = WorkspaceClient()
    config_name = f"projects/{PROJECT_ID}/branches/{BRANCH_ID}/databases/{DATABASE_ID}/cdf-configs/{CDF_CONFIG_ID}"
    statuses = list(w.postgres.list_cdf_statuses(parent=config_name))
    for s in statuses:
        print(f"  {s.postgres_table}: {s.state} -> {s.uc_table}")
    return statuses


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        check_status()
    else:
        create_cdf_config()
