import argparse
import csv
import logging
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum

import requests
from dotenv import load_dotenv
from msal import ConfidentialClientApplication


load_dotenv()

AUTHORITY_URL = os.getenv("AUTHORITY_URL")
TENANT_ID = os.getenv("AZURE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
API_SCOPE = os.getenv("API_SCOPE")
API_BASE_URL = os.getenv("API_BASE_URL")
GROUP_DOCUMENTS_UPLOAD_URL = f"{API_BASE_URL}/external/group_documents/upload"
PUBLIC_DOCUMENTS_UPLOAD_URL = f"{API_BASE_URL}/external/public_documents/upload"
PUBLIC_DOCUMENTS_LIST_URL = f"{API_BASE_URL}/external/public_documents"
GROUP_DOCUMENTS_LIST_URL = f"{API_BASE_URL}/external/group_documents"
BEARER_TOKEN_TEST_URL = f"{API_BASE_URL}/external/testaccesstoken"
UPLOAD_DIRECTORY = os.getenv("UPLOAD_DIRECTORY")
g_ACCESS_TOKEN = None
MAX_RETRIES = 100
RETRY_DELAY = 60

successFileLogger = None
ignoredFileLogger = None
file_formatter = logging.Formatter('%(message)s')
appLogname = "./logfile.log"
success_fileLogname = "./file_logger_success.log"
ignored_fileLogname = "./file_logger_ignored.log"
appLogger = logging.getLogger(__name__)


class UploadOutcome(Enum):
    QUEUED = "queued"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


def setup_FileLoggers(name, log_file, level=logging.DEBUG):
    handler = logging.FileHandler(log_file)
    handler.setFormatter(file_formatter)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)
    return logger


def configure_logging():
    global successFileLogger, ignoredFileLogger
    logging.basicConfig(
        filename=appLogname,
        filemode='a',
        format='%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.DEBUG,
    )
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(stdout_handler)
    successFileLogger = setup_FileLoggers('success_file_logger', success_fileLogname)
    ignoredFileLogger = setup_FileLoggers('ignored_file_logger', ignored_fileLogname)


def validate_configuration(action):
    settings = {
        "AUTHORITY_URL": AUTHORITY_URL,
        "AZURE_TENANT_ID": TENANT_ID,
        "AZURE_CLIENT_ID": CLIENT_ID,
        "AZURE_CLIENT_SECRET": CLIENT_SECRET,
        "API_SCOPE": API_SCOPE,
        "API_BASE_URL": API_BASE_URL,
    }
    missing = [name for name, value in settings.items() if not value or not value.strip()]
    if missing:
        raise ValueError(f"Missing environment settings: {', '.join(missing)}")
    if action == "upload" and (not UPLOAD_DIRECTORY or not os.path.isdir(UPLOAD_DIRECTORY)):
        raise ValueError(f"Upload directory does not exist: {UPLOAD_DIRECTORY}")


def get_access_token():
    """Acquire an application token using the configured client credentials."""
    app = ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=f"{AUTHORITY_URL}/{TENANT_ID}",
    )
    result = app.acquire_token_for_client(scopes=[API_SCOPE])
    if "access_token" not in result:
        raise RuntimeError(
            f"Token acquisition failed: {result.get('error')}: {result.get('error_description')}"
        )
    return result["access_token"]


def workspace_parameters(user_id, workspace_id, scope):
    if scope not in ("public", "group"):
        raise ValueError(f"Invalid workspace scope: {scope}")
    if not user_id or not user_id.strip() or not workspace_id or not workspace_id.strip():
        raise ValueError("User ID and workspace ID are required.")
    key = "active_workspace_id" if scope == "public" else "active_group_id"
    return {"user_id": user_id.strip(), key: workspace_id.strip()}


def retry_delay(response):
    value = response.headers.get("Retry-After")
    if value is not None:
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is not None:
                    return max(0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
        appLogger.warning("Invalid Retry-After header %r; using %s seconds.", value, RETRY_DELAY)
    return RETRY_DELAY


def api_request(method, url, access_token=None, **kwargs):
    """Retry explicit authentication/throttling rejections, not ambiguous failures."""
    global g_ACCESS_TOKEN
    token = g_ACCESS_TOKEN or access_token
    if not token:
        raise RuntimeError("An access token is required.")
    for attempt in range(MAX_RETRIES):
        for _, stream in kwargs.get("files", {}).values():
            stream.seek(0)
        response = requests.request(
            method, url, headers={"Authorization": f"Bearer {token}"}, timeout=60, **kwargs
        )
        if response.status_code not in (401, 429):
            response.raise_for_status()
            return response
        if attempt + 1 == MAX_RETRIES:
            response.raise_for_status()
        if response.status_code == 401:
            appLogger.warning("Token rejected; refreshing before retry %s.", attempt + 2)
            token = get_access_token()
            if not token:
                raise RuntimeError("Token refresh failed.")
            g_ACCESS_TOKEN = token
        else:
            delay = retry_delay(response)
            appLogger.warning("HTTP 429; waiting %s seconds before retry %s.", delay, attempt + 2)
            time.sleep(delay)
    raise RuntimeError("No API request attempts configured.")


def test_access_token(access_token):
    try:
        response = api_request("POST", BEARER_TOKEN_TEST_URL, access_token)
        if response.status_code != 200:
            raise ValueError(f"Unexpected token test status: {response.status_code}")
        return True
    except (requests.RequestException, ValueError, RuntimeError) as error:
        appLogger.error("Access token validation failed: %s", error)
        return False


def get_workspace_files(user_id, workspace_id, active_workspace_scope, access_token, search=None):
    """Fetch and validate every page; failed listings must never look empty."""
    params = workspace_parameters(user_id, workspace_id, active_workspace_scope)
    params.update(page=1, page_size=25)
    if search is not None:
        params["search"] = search
    list_url = (
        PUBLIC_DOCUMENTS_LIST_URL if active_workspace_scope == "public" else GROUP_DOCUMENTS_LIST_URL
    )
    documents = []
    while True:
        response = api_request("GET", list_url, access_token, params=dict(params))
        if response.status_code != 200:
            raise ValueError(f"Unexpected listing status: {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Document listing is not an object.")
        page = data.get("page")
        page_size = data.get("page_size")
        total = data.get("total_count")
        page_documents = data.get("documents")
        if (
            type(page) is not int or page != params["page"]
            or type(page_size) is not int or page_size <= 0
            or type(total) is not int or total < 0
            or not isinstance(page_documents, list)
            or any(
                not isinstance(doc, dict)
                or not isinstance(doc.get("file_name"), str) or not doc["file_name"]
                for doc in page_documents
            )
        ):
            raise ValueError("Invalid document listing or pagination metadata.")
        if not page_documents and total > len(documents):
            raise ValueError("Document listing ended before the reported total.")
        documents.extend(page_documents)
        if page * page_size >= total:
            return documents
        params["page"] += 1


def completion_percentage(document):
    value = document.get("percentage_complete")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= 100 or not math.isfinite(value):
        return None
    return value


def document_completed(document):
    status = document.get("status")
    return completion_percentage(document) == 100 and isinstance(status, str) and (
        status == "Processing complete"
        or status.startswith("Processing complete - ")
        or status == "Audio transcription complete"
        or status == "Metadata extraction complete"
    )


def get_existing_upload_outcome(file_name, user_id, workspace_id, access_token, active_workspace_scope):
    """Classify exact-name remote matches, blocking unresolved uploads."""
    documents = get_workspace_files(
        user_id, workspace_id, active_workspace_scope, access_token, search=file_name
    )
    matches = [doc for doc in documents if doc.get("file_name") == file_name]
    unresolved = [doc for doc in matches if not document_completed(doc)]
    if unresolved:
        for doc in unresolved:
            appLogger.warning(
                "Blocked %s: existing document %s has status %r, completion %r. "
                "Wait for processing or run --action clean explicitly for stale/failed uploads, then retry.",
                file_name, doc.get("id"), doc.get("status"), doc.get("percentage_complete"),
            )
        return UploadOutcome.BLOCKED
    return UploadOutcome.SKIPPED if matches else None


def upload_document(file_path, user_id, active_workspace_scope, active_workspace_id, classification, access_token=None):
    """Queue a file, or report an exact-name completed/incomplete remote match."""
    file_name = os.path.basename(file_path)
    try:
        data = workspace_parameters(user_id, active_workspace_id, active_workspace_scope)
        data["classification"] = classification.strip()
        existing = get_existing_upload_outcome(
            file_name, user_id, active_workspace_id, access_token, active_workspace_scope
        )
        if existing is not None:
            if existing == UploadOutcome.SKIPPED:
                appLogger.info("Skipping completed exact filename match: %s", file_name)
            if ignoredFileLogger:
                ignoredFileLogger.debug("%s: %s", existing.value, os.path.abspath(file_path))
            return existing
        upload_url = (
            PUBLIC_DOCUMENTS_UPLOAD_URL if active_workspace_scope == "public"
            else GROUP_DOCUMENTS_UPLOAD_URL
        )
        with open(file_path, 'rb') as file:
            response = api_request(
                "POST", upload_url, access_token, files={"file": (file_name, file)}, data=data
            )
        payload = response.json()
        if (
            response.status_code != 200 or not isinstance(payload, dict)
            or payload.get("errors") != [] or not isinstance(payload.get("document_ids"), list)
            or len(payload["document_ids"]) != 1
            or not isinstance(payload["document_ids"][0], str) or not payload["document_ids"][0]
        ):
            raise ValueError(f"Upload was not fully accepted: HTTP {response.status_code}, {payload}")
        appLogger.info("Queued %s as document %s; background processing is not yet complete.",
                       file_name, payload["document_ids"][0])
        if successFileLogger:
            successFileLogger.debug("%s", os.path.abspath(file_path))
        return UploadOutcome.QUEUED
    except (OSError, requests.RequestException, ValueError, RuntimeError) as error:
        appLogger.error("Upload failed for %s: %s. Check remote status before retrying.", file_path, error)
        return UploadOutcome.FAILED


def read_files_in_directory(directory, user_id, active_workspace_scope, active_workspace_id, classification, access_token=None):
    """Upload immediate files only, returning per-outcome counts."""
    if not os.path.isdir(directory):
        raise ValueError(f"Directory does not exist: {directory}")
    workspace_parameters(user_id, active_workspace_id, active_workspace_scope)
    summary = Counter()
    for filename in sorted(os.listdir(directory)):
        file_path = os.path.abspath(os.path.join(directory, filename))
        if not os.path.isfile(file_path):
            appLogger.info("Skipping non-file: %s", file_path)
            continue
        result = upload_document(
            file_path, user_id, active_workspace_scope, active_workspace_id, classification,
            g_ACCESS_TOKEN or access_token,
        )
        summary[result.value] += 1
    return summary


def read_csv_ignore_header(file_path):
    """Validate all mappings before processing any mapped folders."""
    mappings = []
    with open(file_path, newline='', encoding='utf-8-sig') as file:
        reader = csv.reader(file, strict=True)
        header = next(reader, None)
        expected = ["folderName", "userId", "workspaceScope", "workspaceId", "classification"]
        if header is None or [column.strip() for column in header] != expected:
            raise ValueError(f"Map header must be: {','.join(expected)}")
        for line, row in enumerate(reader, 2):
            if not row or all(not value.strip() for value in row):
                continue
            if len(row) != 5:
                raise ValueError(f"Map line {line}: expected five columns, got {len(row)}.")
            folder, user_id, scope, workspace_id, classification = [value.strip() for value in row]
            if not folder:
                raise ValueError(f"Map line {line}: folder is required.")
            workspace_parameters(user_id, workspace_id, scope)
            mappings.append((folder, user_id, scope, workspace_id, classification))
    if not mappings:
        raise ValueError("Map contains no folders.")
    summary = Counter()
    for folder, user_id, scope, workspace_id, classification in mappings:
        try:
            if not UPLOAD_DIRECTORY:
                raise ValueError("UPLOAD_DIRECTORY is required.")
            directory = os.path.join(UPLOAD_DIRECTORY, folder)
            summary.update(read_files_in_directory(
                directory, user_id, scope, workspace_id, classification, g_ACCESS_TOKEN
            ))
        except (OSError, ValueError) as error:
            appLogger.error("Failed mapped folder %s: %s", folder, error)
            summary["failed"] += 1
    return summary


def delete_workspace_file(file_id, user_id, workspace_id, active_workspace_scope, access_token):
    params = workspace_parameters(user_id, workspace_id, active_workspace_scope)
    if not isinstance(file_id, str) or not file_id:
        raise ValueError("Cannot delete a document without an ID.")
    list_url = (
        PUBLIC_DOCUMENTS_LIST_URL if active_workspace_scope == "public" else GROUP_DOCUMENTS_LIST_URL
    )
    response = api_request("DELETE", f"{list_url}/{file_id}", access_token, params=params)
    if response.status_code not in (200, 204):
        raise ValueError(f"Unexpected deletion status for {file_id}: {response.status_code}")
    appLogger.info("Deleted document %s.", file_id)
    return True


def get_uploading_workspace_files(files):
    return [
        file for file in files
        if completion_percentage(file) is not None and completion_percentage(file) < 100
    ]


def delete_failed_workspace_files(files, user_id, workspace_id, active_workspace_scope, access_token):
    """Delete only explicitly failed or more-than-12-hour-old incomplete uploads."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
    deleted = []
    for file in files:
        percent = completion_percentage(file)
        if percent is None:
            appLogger.warning("Preserving document %s: invalid completion metadata.", file.get("id"))
            continue
        if percent == 100:
            continue
        status = file.get("status")
        failed = isinstance(status, str) and status.startswith("Error:")
        if not failed:
            try:
                timestamp = file.get("last_updated")
                if not isinstance(timestamp, str):
                    raise ValueError("missing timestamp")
                last_updated = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if last_updated.tzinfo is None:
                    raise ValueError("timestamp has no timezone")
            except ValueError as error:
                appLogger.warning("Preserving document %s: invalid last_updated (%s).", file.get("id"), error)
                continue
            if last_updated >= cutoff:
                continue
        if not delete_workspace_file(file.get("id"), user_id, workspace_id, active_workspace_scope, access_token):
            raise RuntimeError(f"Deletion failed for document {file.get('id')}.")
        deleted.append(file)
    appLogger.info("Deleted %s stale/failed incomplete documents.", len(deleted))
    return deleted


def delete_duplicate_workspace_files(files, user_id, workspace_id, active_workspace_scope, access_token):
    by_name = defaultdict(list)
    for file in files:
        name = file.get("file_name")
        if isinstance(name, str) and name:
            by_name[name].append(file)
    deleted = []
    for matches in by_name.values():
        keep = next((file for file in matches if document_completed(file)), matches[0])
        for file in matches:
            if file is keep:
                continue
            if not delete_workspace_file(file.get("id"), user_id, workspace_id, active_workspace_scope, access_token):
                raise RuntimeError(f"Deletion failed for document {file.get('id')}.")
            deleted.append(file)
    appLogger.info("Deleted %s duplicate documents.", len(deleted))
    return deleted


def main():
    global g_ACCESS_TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--action", choices=["upload", "delete", "clean"], required=True)
    parser.add_argument("-w", "--workspaceid", help="Workspace ID for clean/delete")
    parser.add_argument("-u", "--userid", help="User ID for clean/delete")
    parser.add_argument("-s", "--scope", choices=["public", "group"])
    parser.add_argument("-dupes", "--removeduplicates", action="store_true",
                        help="Also delete exact-name duplicates during explicit cleanup")
    args = parser.parse_args()
    if args.action in ("delete", "clean") and not all((args.workspaceid, args.userid, args.scope)):
        parser.error("workspaceid, userid, and scope are required for delete and clean actions.")
    if args.removeduplicates and args.action != "clean":
        parser.error("removeduplicates is only supported with --action clean.")
    try:
        configure_logging()
        validate_configuration(args.action)
        g_ACCESS_TOKEN = get_access_token()
        if not g_ACCESS_TOKEN or not test_access_token(g_ACCESS_TOKEN):
            appLogger.error("Token validation failed; aborting.")
            return 1
        if args.action == "upload":
            summary = read_csv_ignore_header("map.csv")
            appLogger.info(
                "Upload summary: queued=%s, skipped=%s, blocked=%s, failed=%s",
                summary["queued"], summary["skipped"], summary["blocked"], summary["failed"],
            )
            return 1 if summary["blocked"] or summary["failed"] else 0
        files = get_workspace_files(args.userid, args.workspaceid, args.scope, g_ACCESS_TOKEN)
        if args.action == "delete":
            appLogger.warning("--action delete is not implemented; no documents were deleted.")
            return 0
        deleted = delete_failed_workspace_files(
            files, args.userid, args.workspaceid, args.scope, g_ACCESS_TOKEN
        )
        if args.removeduplicates:
            deleted_ids = {file["id"] for file in deleted}
            remaining = [file for file in files if file.get("id") not in deleted_ids]
            delete_duplicate_workspace_files(
                remaining, args.userid, args.workspaceid, args.scope, g_ACCESS_TOKEN
            )
        return 0
    except (OSError, csv.Error, requests.RequestException, ValueError, RuntimeError) as error:
        appLogger.error("Bulk operation failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
