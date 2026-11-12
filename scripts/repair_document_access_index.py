#!/usr/bin/env python3
"""Find and remove stale document_access_index rows for one public workspace.

Stale rows point at source documents that no longer exist, so lists show
"ghost" documents that cannot be opened or deleted. This happens when documents
were deleted by a build that did not update the access index.

Dry run by default. Nothing is deleted without --apply and a typed confirmation.
Rows are backed up to a JSON file before any deletion.

Requires: pip install azure-cosmos azure-identity, an `az login` identity with
the "Cosmos DB Built-in Data Contributor" role on the account, and the
account's endpoint.

Example:
    python scripts/repair_document_access_index.py `
        --endpoint https://vahack25-prd-cosmos.documents.azure.com:443/ `
        --workspaceid d9e0d0c1-c44e-4e0d-8374-3c309e8e6c61
"""

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential

INDEX_CONTAINER = "document_access_index"
SOURCE_CONTAINER = "public_documents"
INDEX_TYPE = "document_access_index"
SOURCE_SCOPE = "public"


def query_index_source_ids(index, scope_key):
    rows = index.query_items(
        query=(
            "SELECT c.source_document_id FROM c "
            "WHERE c.type = @type AND c.source_scope = @scope AND c.scope_key = @key"
        ),
        parameters=[
            {"name": "@type", "value": INDEX_TYPE},
            {"name": "@scope", "value": SOURCE_SCOPE},
            {"name": "@key", "value": scope_key},
        ],
        partition_key=scope_key,
    )
    return {row["source_document_id"] for row in rows if row.get("source_document_id")}


def query_source_ids(source, workspace_id):
    rows = source.query_items(
        query="SELECT c.id FROM c WHERE c.public_workspace_id = @ws",
        parameters=[{"name": "@ws", "value": workspace_id}],
        enable_cross_partition_query=True,
    )
    return {row["id"] for row in rows}


def source_document_exists(source, document_id):
    try:
        source.read_item(item=document_id, partition_key=document_id)
        return True
    except CosmosResourceNotFoundError:
        return False


def query_all_index_rows(index, document_id):
    return list(index.query_items(
        query=(
            "SELECT * FROM c WHERE c.type = @type AND c.source_scope = @scope "
            "AND c.source_document_id = @id"
        ),
        parameters=[
            {"name": "@type", "value": INDEX_TYPE},
            {"name": "@scope", "value": SOURCE_SCOPE},
            {"name": "@id", "value": document_id},
        ],
        enable_cross_partition_query=True,
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", required=True, help="Cosmos account endpoint URL")
    parser.add_argument("--database", default="SimpleChat")
    parser.add_argument("--workspaceid", required=True, help="Public workspace ID")
    parser.add_argument("--apply", action="store_true", help="Delete stale rows (default is a dry run)")
    parser.add_argument("--backup", help="Backup file path (default: a timestamped file in the temp folder)")
    args = parser.parse_args()

    workspace_id = args.workspaceid.strip()
    scope_key = f"{SOURCE_SCOPE}:{workspace_id}"

    client = CosmosClient(args.endpoint, credential=DefaultAzureCredential())
    database = client.get_database_client(args.database)
    index = database.get_container_client(INDEX_CONTAINER)
    source = database.get_container_client(SOURCE_CONTAINER)

    index_ids = query_index_source_ids(index, scope_key)
    source_ids = query_source_ids(source, workspace_id)
    print(f"Index entries for workspace: {len(index_ids)} documents")
    print(f"Real documents in workspace: {len(source_ids)}")

    candidates = sorted(index_ids - source_ids)
    ghosts = [doc_id for doc_id in candidates if not source_document_exists(source, doc_id)]
    print(f"Stale (ghost) documents: {len(ghosts)}")
    if len(candidates) != len(ghosts):
        print(f"Skipped {len(candidates) - len(ghosts)} candidates that exist on a direct read.")
    if not ghosts:
        return 0

    rows = []
    for document_id in ghosts:
        rows.extend(query_all_index_rows(index, document_id))
    print(f"Index rows to delete: {len(rows)}")
    for document_id in ghosts[:10]:
        print(f"  {document_id}")
    if len(ghosts) > 10:
        print(f"  ... and {len(ghosts) - 10} more")

    if not args.apply:
        print("Dry run only. Re-run with --apply to delete these rows.")
        return 0

    backup_path = Path(args.backup) if args.backup else Path(tempfile.gettempdir()) / (
        f"document_access_index_backup_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    backup_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Backed up {len(rows)} rows to {backup_path}")

    if input(f"Type DELETE to remove {len(rows)} index rows: ").strip() != "DELETE":
        print("Cancelled; nothing deleted.")
        return 1

    deleted = 0
    already_gone = 0
    for row in rows:
        try:
            index.delete_item(item=row["id"], partition_key=row["scope_key"])
            deleted += 1
        except CosmosResourceNotFoundError:
            already_gone += 1
        except CosmosHttpResponseError as error:
            print(f"Stopped after deleting {deleted} of {len(rows)} rows: HTTP {error.status_code}.")
            if error.status_code == 403:
                print("Your identity cannot delete from this container. Assign the 'Cosmos DB Built-in Data "
                      "Contributor' role, wait a few minutes, and re-run; the script recomputes what is left.")
            return 1
    print(f"Deleted {deleted} index rows ({already_gone} were already gone).")
    print("The app may cache list results for up to 15 minutes; restart the app to refresh immediately.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
