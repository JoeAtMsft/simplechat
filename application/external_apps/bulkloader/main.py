import argparse
import csv
import logging
import math
import os
import socket
import sys
import time
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from uuid import UUID
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from msal import ConfidentialClientApplication, PublicClientApplication


load_dotenv()

AUTHORITY_URL = os.getenv("AUTHORITY_URL")
TENANT_ID = os.getenv("AZURE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
API_SCOPE = os.getenv("API_SCOPE")
INTERACTIVE_CLIENT_ID = os.getenv("INTERACTIVE_CLIENT_ID")
INTERACTIVE_API_SCOPE = os.getenv("INTERACTIVE_API_SCOPE")
API_BASE_URL = os.getenv("API_BASE_URL")
GROUP_DOCUMENTS_UPLOAD_URL = f"{API_BASE_URL}/external/group_documents/upload"
PUBLIC_DOCUMENTS_UPLOAD_URL = f"{API_BASE_URL}/external/public_documents/upload"
PUBLIC_DOCUMENTS_LIST_URL = f"{API_BASE_URL}/external/public_documents"
GROUP_DOCUMENTS_LIST_URL = f"{API_BASE_URL}/external/group_documents"
BEARER_TOKEN_TEST_URL = f"{API_BASE_URL}/external/testaccesstoken"
UPLOAD_DIRECTORY = os.getenv("UPLOAD_DIRECTORY")
g_ACCESS_TOKEN = None
g_INTERACTIVE_AUTH = None
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


class InteractiveAuthentication:
    """Keep delegated tokens and the selected tenant identity in memory for one run."""

    def __init__(self, mode):
        self.mode = mode
        self.scopes = [INTERACTIVE_API_SCOPE]
        self.client = PublicClientApplication(
            client_id=INTERACTIVE_CLIENT_ID or CLIENT_ID,
            authority=f"{AUTHORITY_URL.rstrip('/')}/{TENANT_ID}",
        )
        self.user_id = None
        self.account = None

    def acquire_token(self):
        if self.account is not None:
            result = self.client.acquire_token_silent(
                self.scopes, account=self.account, force_refresh=True
            )
            if not result:
                raise RuntimeError("Interactive token refresh failed; rerun the command to sign in again.")
        elif self.mode == "device-code":
            result = self.acquire_device_token()
        else:
            print("Opening your browser to sign in. Use --auth device-code if a browser is unavailable.")
            try:
                result = self.client.acquire_token_interactive(
                    scopes=self.scopes, prompt="select_account", timeout=180
                )
            except (OSError, RuntimeError, webbrowser.Error) as error:
                appLogger.warning("Browser sign-in unavailable or timed out (%s); using device code.", error)
                result = self.acquire_device_token()
        if not isinstance(result, dict) or not result.get("access_token"):
            error = result.get("error", "no_token") if isinstance(result, dict) else "no_result"
            description = result.get("error_description", "") if isinstance(result, dict) else ""
            raise RuntimeError(f"Interactive authentication failed: {error}: {description}")
        claims = result.get("id_token_claims")
        if self.user_id is None or claims is not None:
            if not isinstance(claims, dict):
                raise ValueError("Sign-in did not return user identity claims.")
            oid, tenant = claims.get("oid"), claims.get("tid")
            try:
                user_id = str(UUID(oid))
                tenant_id = str(UUID(tenant))
                expected_tenant = str(UUID(TENANT_ID))
            except (ValueError, TypeError, AttributeError) as error:
                raise ValueError("Sign-in requires valid Entra oid/tid claims and a tenant GUID.") from error
            if tenant_id != expected_tenant:
                raise ValueError("Signed-in user belongs to a different tenant.")
            if self.user_id is not None and user_id != self.user_id:
                raise ValueError("Token refresh changed the signed-in user; aborting.")
            self.user_id = user_id
        if self.account is None:
            self.account = next((
                account for account in self.client.get_accounts()
                if account.get("local_account_id", "").lower() == self.user_id
                and account.get("realm", "").lower() == str(UUID(TENANT_ID))
            ), None)
            if self.account is None:
                raise RuntimeError("The signed-in account was not found in the MSAL cache.")
            appLogger.info("Signed in; using Entra user ID %s for this run.", self.user_id)
        return result["access_token"]

    def acquire_device_token(self):
        flow = self.client.initiate_device_flow(scopes=self.scopes)
        if not isinstance(flow, dict) or not flow.get("user_code") or not flow.get("message"):
            error = flow.get("error", "invalid_device_flow") if isinstance(flow, dict) else "no_result"
            raise RuntimeError(f"Could not start device-code sign-in: {error}")
        # Device codes are shown only in the terminal, never written to log files.
        print(flow["message"], flush=True)
        return self.client.acquire_token_by_device_flow(flow)


def setup_FileLoggers(name, log_file, level=logging.DEBUG):
    handler = logging.FileHandler(log_file)
    handler.setFormatter(file_formatter)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(handler)
    return logger


def configure_logging(verbose=False):
    global successFileLogger, ignoredFileLogger
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        filename=appLogname,
        filemode='a',
        format='%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=level,
    )
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logging.getLogger().addHandler(stdout_handler)
    logging.getLogger("msal").setLevel(logging.WARNING)
    successFileLogger = setup_FileLoggers('success_file_logger', success_fileLogname)
    ignoredFileLogger = setup_FileLoggers('ignored_file_logger', ignored_fileLogname)


def log_runtime_diagnostics():
    if not appLogger.isEnabledFor(logging.DEBUG):
        return
    proxy_names = [
        name for name in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        ) if os.environ.get(name)
    ]
    appLogger.debug(
        "Runtime diagnostic: executable=%s; Python=%s; requests=%s; urllib3=%s; "
        "API hostname=%r; resolver=%s.%s; proxy environment variable names=%s",
        sys.executable, sys.version.split()[0], requests.__version__,
        requests.packages.urllib3.__version__, urlsplit(API_BASE_URL).hostname,
        socket.getaddrinfo.__module__, socket.getaddrinfo.__name__, proxy_names,
    )


def log_transport_diagnostics(method, url, error):
    if not appLogger.isEnabledFor(logging.DEBUG):
        return
    causes = []
    seen = set()
    cause = error
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        causes.append(f"{type(cause).__module__}.{type(cause).__name__}(errno={getattr(cause, 'errno', None)})")
        cause = cause.__cause__ or cause.__context__
    appLogger.debug("Transport diagnostic: method=%s; exception chain=%s", method, " -> ".join(causes))
    try:
        endpoint = urlsplit(url)
        hostname = endpoint.hostname
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    except ValueError:
        appLogger.debug("Transport diagnostic: endpoint could not be parsed; no resolver probe performed.")
        return
    if not hostname:
        appLogger.debug("Transport diagnostic: endpoint has no hostname; no resolver probe performed.")
        return
    try:
        addresses = sorted({
            address[4][0] for address in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        })
    except OSError as probe_error:
        appLogger.debug(
            "Independent resolver probe: hostname=%r; port=%s; result=%s; errno=%s",
            hostname, port, type(probe_error).__name__, probe_error.errno,
        )
    else:
        appLogger.debug(
            "Independent resolver probe: hostname=%r; port=%s; addresses=%s. "
            "This probe does not establish why the earlier request failed.",
            hostname, port, addresses,
        )


def validate_configuration(action, auth_mode="application", upload_directory=None):
    settings = {
        "AUTHORITY_URL": AUTHORITY_URL,
        "AZURE_TENANT_ID": TENANT_ID,
        "API_BASE_URL": API_BASE_URL,
    }
    if auth_mode == "application":
        settings.update(AZURE_CLIENT_ID=CLIENT_ID, AZURE_CLIENT_SECRET=CLIENT_SECRET, API_SCOPE=API_SCOPE)
    else:
        settings.update(
            INTERACTIVE_CLIENT_ID=INTERACTIVE_CLIENT_ID or CLIENT_ID,
            INTERACTIVE_API_SCOPE=INTERACTIVE_API_SCOPE,
        )
    missing = [name for name, value in settings.items() if not value or not value.strip()]
    if missing:
        raise ValueError(f"Missing environment settings: {', '.join(missing)}")
    if auth_mode != "application":
        UUID(TENANT_ID)
        if (
            not INTERACTIVE_API_SCOPE.startswith("api://")
            or INTERACTIVE_API_SCOPE.endswith("/.default")
            or len(INTERACTIVE_API_SCOPE.split()) != 1
            or not INTERACTIVE_API_SCOPE.rsplit("/", 1)[-1]
        ):
            raise ValueError("INTERACTIVE_API_SCOPE must be one explicit SimpleChat delegated api:// scope.")
    if action == "upload":
        directory = UPLOAD_DIRECTORY if upload_directory is None else upload_directory
        if not directory or not os.path.isdir(directory):
            raise ValueError(f"Upload directory does not exist: {directory}")


def get_access_token():
    """Acquire or refresh a token using the selected authentication mode."""
    if g_INTERACTIVE_AUTH is not None:
        return g_INTERACTIVE_AUTH.acquire_token()
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


def workspace_parameters(user_id, workspace_id, scope, require_user_id=True):
    if scope not in ("public", "group"):
        raise ValueError(f"Invalid workspace scope: {scope}")
    if not workspace_id or not workspace_id.strip():
        raise ValueError("Workspace ID is required.")
    if require_user_id and (not user_id or not user_id.strip()):
        raise ValueError("User ID is required.")
    key = "active_workspace_id" if scope == "public" else "active_group_id"
    params = {key: workspace_id.strip()}
    if user_id and user_id.strip():
        params["user_id"] = user_id.strip()
    return params


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
        started = time.monotonic()
        appLogger.debug(
            "API request: method=%s; hostname=%r; attempt=%s/%s",
            method, urlsplit(url).hostname, attempt + 1, MAX_RETRIES,
        )
        try:
            response = requests.request(
                method, url, headers={"Authorization": f"Bearer {token}"}, timeout=60, **kwargs
            )
        except requests.RequestException as error:
            appLogger.debug(
                "API request raised %s after %.3f seconds; no retry will be attempted for this transport failure.",
                type(error).__name__, time.monotonic() - started,
            )
            log_transport_diagnostics(method, url, error)
            raise
        appLogger.debug(
            "API response: method=%s; status=%s; elapsed=%.3f seconds",
            method, response.status_code, time.monotonic() - started,
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
    except (requests.ConnectionError, requests.Timeout) as error:
        appLogger.error(
            "Could not reach the API token-test endpoint: %s. "
            "This is a transport failure, not evidence that the token was rejected. "
            "Use --verbose for runtime and transport diagnostics.",
            error,
        )
        return False
    except (requests.RequestException, ValueError, RuntimeError) as error:
        appLogger.error("Access token validation failed: %s", error)
        return False


def get_workspace_files(user_id, workspace_id, active_workspace_scope, access_token, search=None):
    """Fetch and validate every page; failed listings must never look empty."""
    params = workspace_parameters(
        user_id, workspace_id, active_workspace_scope, require_user_id=True
    )
    params.update(page=1, page_size=25)
    if search is not None:
        params["search"] = search
        appLogger.debug("Working on filename lookup: %s in %s workspace %s.", search, active_workspace_scope, workspace_id)
    else:
        appLogger.info("Working on document listing for %s workspace %s.", active_workspace_scope, workspace_id)
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
        if search is None and (page == 1 or page % 10 == 0 or page * page_size >= total):
            appLogger.info("Listing progress: page %s; loaded %s/%s documents.", page, len(documents), total)
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
    appLogger.debug("Working on file upload: %s.", file_path)
    file_name = os.path.basename(file_path)
    try:
        data = workspace_parameters(user_id, active_workspace_id, active_workspace_scope)
        data["classification"] = classification.strip()
        existing = get_existing_upload_outcome(
            file_name, user_id, active_workspace_id, access_token, active_workspace_scope
        )
        if existing is not None:
            if existing == UploadOutcome.SKIPPED:
                appLogger.debug("Skipping completed exact filename match: %s", file_name)
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
    appLogger.info(
        "Working on folder upload: %s to %s workspace %s.",
        directory, active_workspace_scope, active_workspace_id,
    )
    if not os.path.isdir(directory):
        raise ValueError(f"Directory does not exist: {directory}")
    workspace_parameters(user_id, active_workspace_id, active_workspace_scope)
    summary = Counter()
    for filename in sorted(os.listdir(directory)):
        file_path = os.path.abspath(os.path.join(directory, filename))
        if not os.path.isfile(file_path):
            appLogger.debug("Skipping non-file: %s", file_path)
            continue
        result = upload_document(
            file_path, user_id, active_workspace_scope, active_workspace_id, classification,
            g_ACCESS_TOKEN or access_token,
        )
        summary[result.value] += 1
    return summary


def read_csv_ignore_header(file_path, override_user_id=None):
    """Validate all mappings before processing any mapped folders."""
    appLogger.info("Working on CSV upload mappings from %s.", file_path)
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
            if override_user_id is not None:
                user_id = override_user_id
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
    appLogger.debug("Working on deletion of document %s from %s workspace %s.", file_id, active_workspace_scope, workspace_id)
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


DEFAULT_STALE_HOURS = 12


def delete_failed_workspace_files(
    files, user_id, workspace_id, active_workspace_scope, access_token, stale_hours=DEFAULT_STALE_HOURS
):
    """Delete explicitly failed uploads and incomplete uploads older than stale_hours."""
    appLogger.info(
        "Working on cleanup: scanning %s documents in %s workspace %s for errors or incomplete uploads older than %g hours.",
        len(files), active_workspace_scope, workspace_id, stale_hours,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
    deleted = []
    for file in files:
        percent = completion_percentage(file)
        if percent is None:
            appLogger.warning("Preserving document %s: invalid completion metadata.", file.get("id"))
            continue
        if percent == 100:
            appLogger.debug(
                "Preserving %s (document %s): completion is 100%%.",
                file.get("file_name"), file.get("id"),
            )
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
                appLogger.debug(
                    "Preserving %s (document %s): recent incomplete upload.",
                    file.get("file_name"), file.get("id"),
                )
                continue
        appLogger.info(
            "Cleaning %s (document %s): %s.",
            file.get("file_name"), file.get("id"),
            "error status" if failed else f"incomplete and last updated more than {stale_hours:g} hours ago",
        )
        if not delete_workspace_file(file.get("id"), user_id, workspace_id, active_workspace_scope, access_token):
            raise RuntimeError(f"Deletion failed for document {file.get('id')}.")
        deleted.append(file)
    appLogger.info("Deleted %s stale/failed incomplete documents.", len(deleted))
    return deleted


def delete_duplicate_workspace_files(files, user_id, workspace_id, active_workspace_scope, access_token):
    appLogger.info(
        "Working on duplicate cleanup: scanning %s documents in %s workspace %s.",
        len(files), active_workspace_scope, workspace_id,
    )
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
                appLogger.debug(
                    "Preserving %s (document %s) during duplicate cleanup.",
                    file.get("file_name"), file.get("id"),
                )
                continue
            appLogger.info(
                "Cleaning duplicate %s (document %s); retaining document %s.",
                file.get("file_name"), file.get("id"), keep.get("id"),
            )
            if not delete_workspace_file(file.get("id"), user_id, workspace_id, active_workspace_scope, access_token):
                raise RuntimeError(f"Deletion failed for document {file.get('id')}.")
            deleted.append(file)
    appLogger.info("Deleted %s duplicate documents.", len(deleted))
    return deleted


def main():
    global g_ACCESS_TOKEN, g_INTERACTIVE_AUTH
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--action", choices=["upload", "list", "delete", "clean"], required=True)
    parser.add_argument("-w", "--workspaceid", help="Workspace ID for direct-folder upload, list, clean, or delete")
    parser.add_argument("-u", "--userid", help="User ID for application mode; discovered automatically in interactive mode")
    parser.add_argument("--auth", choices=["application", "interactive", "device-code"], default="application",
                        help="Application credentials (default), browser sign-in with device-code fallback, or device code")
    parser.add_argument("-s", "--scope", choices=["public", "group"])
    parser.add_argument("--folder", help="Upload immediate files from this local folder instead of map.csv")
    parser.add_argument("--classification", help="Optional classification for --folder uploads (default: empty)")
    parser.add_argument("-dupes", "--removeduplicates", action="store_true",
                        help="Also delete exact-name duplicates during explicit cleanup")
    parser.add_argument("--incomplete-only", action="store_true",
                        help="With list, hide files whose status is Processing complete (case-insensitive)")
    parser.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS,
                        help="With clean, delete incomplete uploads last updated more than this many hours ago "
                             f"(default: {DEFAULT_STALE_HOURS}; 0 deletes every incomplete upload, including active ones)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show per-file activity and HTTP diagnostics (default: summaries, warnings, and errors)")
    args = parser.parse_args()
    direct_upload = args.action == "upload" and args.folder is not None
    if args.folder is not None and args.action != "upload":
        parser.error("folder is only supported with --action upload.")
    if args.classification is not None and not direct_upload:
        parser.error("classification requires --action upload and --folder.")
    if direct_upload:
        if not args.folder.strip():
            parser.error("folder must not be empty.")
        if not args.workspaceid or not args.workspaceid.strip() or not args.scope:
            parser.error("workspaceid and scope are required for --folder uploads.")
        if args.auth == "application" and (not args.userid or not args.userid.strip()):
            parser.error("userid is required for --folder uploads with application authentication.")
    elif args.action == "upload" and any(
        value is not None for value in (args.workspaceid, args.scope, args.userid)
    ):
        parser.error("Upload destination flags require --folder; otherwise destinations come from map.csv.")
    if args.action in ("list", "delete", "clean") and not all((args.workspaceid, args.scope)):
        parser.error("workspaceid and scope are required for list, delete, and clean actions.")
    if args.auth == "application" and args.action in ("list", "delete", "clean") and (not args.userid or not args.userid.strip()):
        parser.error("userid is required for list, delete, and clean actions.")
    if args.auth != "application" and args.userid is not None:
        parser.error("Do not supply userid with interactive authentication; the signed-in user is used.")
    if args.removeduplicates and args.action != "clean":
        parser.error("removeduplicates is only supported with --action clean.")
    if args.incomplete_only and args.action != "list":
        parser.error("incomplete-only is only supported with --action list.")
    if args.stale_hours != DEFAULT_STALE_HOURS and args.action != "clean":
        parser.error("stale-hours is only supported with --action clean.")
    if not math.isfinite(args.stale_hours) or args.stale_hours < 0:
        parser.error("stale-hours must be a non-negative number.")
    try:
        configure_logging(args.verbose)
        appLogger.info("Starting %s operation.", args.action)
        validate_configuration(args.action, args.auth, args.folder)
        log_runtime_diagnostics()
        appLogger.info("Authenticating using %s mode.", args.auth)
        g_INTERACTIVE_AUTH = None if args.auth == "application" else InteractiveAuthentication(args.auth)
        g_ACCESS_TOKEN = get_access_token()
        if g_INTERACTIVE_AUTH is not None:
            args.userid = g_INTERACTIVE_AUTH.user_id
            if not args.userid:
                raise RuntimeError("Interactive sign-in did not resolve a user ID.")
        appLogger.info("Checking API access before starting document operations.")
        if not g_ACCESS_TOKEN or not test_access_token(g_ACCESS_TOKEN):
            appLogger.error("API preflight failed; aborting before document operations.")
            return 1
        if args.action == "upload":
            if direct_upload:
                summary = read_files_in_directory(
                    args.folder, args.userid, args.scope, args.workspaceid,
                    args.classification or "", g_ACCESS_TOKEN,
                )
            elif g_INTERACTIVE_AUTH is None:
                summary = read_csv_ignore_header("map.csv")
            else:
                appLogger.info("Using the signed-in user for every upload mapping, overriding CSV userId values.")
                summary = read_csv_ignore_header("map.csv", override_user_id=args.userid)
            appLogger.info(
                "Upload summary: queued=%s, skipped=%s, blocked=%s, failed=%s",
                summary["queued"], summary["skipped"], summary["blocked"], summary["failed"],
            )
            return 1 if summary["blocked"] or summary["failed"] else 0
        files = get_workspace_files(args.userid, args.workspaceid, args.scope, g_ACCESS_TOKEN)
        if args.action == "list":
            workspace_total = len(files)
            if args.incomplete_only:
                files = [
                    file for file in files
                    if not (
                        isinstance(file.get("status"), str)
                        and file["status"].strip().casefold() == "processing complete"
                    )
                ]
            writer = csv.writer(sys.stdout, delimiter="\t")
            writer.writerow(["Filename", "Document ID", "Status", "Completion", "Last updated"])
            for file in files:
                percent = completion_percentage(file)
                writer.writerow([
                    file["file_name"], file.get("id") or "Unknown",
                    file.get("status") or "Unknown",
                    f"{percent:g}%" if percent is not None else "Unknown",
                    file.get("last_updated") or "Unknown",
                ])
            if args.incomplete_only:
                print(f"Total documents: {len(files)} (filtered; workspace total: {workspace_total})")
            else:
                print(f"Total documents: {len(files)}")
            return 0
        if args.action == "delete":
            appLogger.warning("--action delete is not implemented; no documents were deleted.")
            return 0
        deleted = delete_failed_workspace_files(
            files, args.userid, args.workspaceid, args.scope, g_ACCESS_TOKEN,
            stale_hours=args.stale_hours,
        )
        if args.removeduplicates:
            deleted_ids = {file["id"] for file in deleted}
            remaining = [file for file in files if file.get("id") not in deleted_ids]
            delete_duplicate_workspace_files(
                remaining, args.userid, args.workspaceid, args.scope, g_ACCESS_TOKEN
            )
        return 0
    except KeyboardInterrupt:
        appLogger.warning("Sign-in or operation cancelled; no further requests will be made.")
        return 130
    except (OSError, csv.Error, requests.RequestException, ValueError, RuntimeError) as error:
        appLogger.error("Bulk operation failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
