# Bulk document management

Run these commands from the `application/external_apps/bulkloader` directory,
using a Python environment with this tool's dependencies installed:

```powershell
python -m pip install -r .\requirements.txt
```

## STEP 1: Choose authentication

The default `--auth application` mode preserves unattended application credentials.
For browser/device-code login and automatic user ID discovery, use the
[interactive setup below](#interactive-user-login) instead; no client secret is
required in that mode.

### Application-only credentials
- You will need a Service Principal (app registration) and password for bulk document authentication.
- Use the Client ID and Password in the .env (described below).
- The bulk management Service Principal will need the ExternalAPI app role (which is a app role on the app service app registration)

## STEP 2: Disable the App Service Authentication
- Temporarily disable the App Service Authentication during the bulk document operations.

## STEP 3: .env file

Create a .env file to put environment variables in.

### .env file format

```markup
AUTHORITY_URL=<https://login.microsoftonline.us>
AZURE_TENANT_ID=[YOUR TENANT ID]
AZURE_CLIENT_ID=[YOUR CLIENT ID]
AZURE_CLIENT_SECRET=[YOUR SECRET]
API_SCOPE=api://37d7a13d-a5b5-48a6-972f-428cbf316bd9/.default (Example only)
API_BASE_URL=<https://web-8000.azurewebsites.us> (Example only)
UPLOAD_DIRECTORY=./test-documents (Example only; required only for CSV uploads)
```

## STEP 4 (for upload action only): Choose a local folder

./test-documents is a sample folder

### Upload directly without a CSV

Specify the local folder, workspace scope, workspace ID, and user ID:

```powershell
python .\main.py --auth application --action upload `
  --folder "C:\data\TimelyAccess" `
  --scope public `
  --workspaceid public-workspace-id `
  --userid your-entra-user-object-id
```

This mode does not read `map.csv` or require `UPLOAD_DIRECTORY`. Relative folder
paths are resolved from the current working directory, not `UPLOAD_DIRECTORY`.
Only immediate files are uploaded; subdirectories are ignored. Filename skipping,
incomplete-file blocking, retries, and upload summaries work the same as CSV mode.
It does not delete existing documents.

Use `--scope group` for a group workspace. Optionally add
`--classification "Internal"`; the default classification is empty. With
interactive authentication, omit `--userid` to use the signed-in user:

```powershell
python .\main.py --auth interactive --action upload `
  --folder "C:\data\TimelyAccess" --scope public --workspaceid public-workspace-id
```

The existing interactive Entra setup is still required; direct-folder mode does
not change authentication requirements.

## STEP 5 (optional, for multi-workspace CSV uploads): Update map.csv

To upload multiple mapped folders in one run, omit `--folder` and use the following
columns (example only):

```csv
folderName,userId,workspaceScope,workspaceId,classification
folder1,user-id,public,public-workspace-id,
folder2,user-id,group,group-workspace-id,
```

- All five columns are required, including the final comma when classification
  is empty. The header must use the names above. In interactive mode, `userId`
  can be blank and every row uses the signed-in user, overriding any supplied
  CSV user ID.
- Each folder is relative to `UPLOAD_DIRECTORY`; use `.` to upload files directly
  from that directory. An absolute local or UNC folder path is also supported.
- Each row selects a `public` or `group` destination and a user authorized to
  operate on it. In CSV mode, upload destinations come from this file.
  CLI workspace, scope, user, and classification flags require `--folder` and
  cannot override CSV destinations.
- Only immediate files are uploaded. Subdirectories are ignored; add separate
  rows for additional folders.
- The entire mapping is validated before uploads begin. Missing folders or
  unreadable files are reported as failures, not successful no-ops.

## STEP 6: Run CSV uploads (if using map.csv)

```powershell
python .\main.py --action upload
python .\main.py --help
```

### Filename-based skipping and upload results

The loader checks all matching result pages in the selected workspace, then
compares **exact, case-sensitive filenames**. A substring or document title
match does not count.

- A completed exact-name document is **skipped**. There is no content hash or
  modification-time check: changing a local file without renaming it still
  causes it to be skipped if a completed remote copy exists.
- An incomplete, failed, or unknown-completion exact-name document is
  **blocked**, logged with its document ID/status, and not uploaded again.
  Wait for active processing to finish, or explicitly clean stale/failed
  records and rerun upload. If both completed and incomplete exact-name
  records exist, the incomplete records are still reported as blocked.
- New uploads are reported as **queued**, not successfully processed.
  HTTP acceptance does not guarantee background processing has finished.
  The local success log records accepted uploads for reference only; it is
  not used to decide whether to skip a file.
- HTTP 401 refreshes authorization and HTTP 429 retries after `Retry-After`
  when valid, otherwise after the existing 60-second delay. Each attempt sends
  the full file again, up to the configured attempt limit.
- Other HTTP/transport failures are reported. An ambiguous timeout is not
  automatically retried because the server may already have queued the file.
  Check its remote status before retrying.

The final upload summary reports `queued`, `skipped`, `blocked`, and `failed`.
Exit status is `0` when all files were queued or skipped (including an empty
folder), and `1` for failures or blocked files. Invalid CLI arguments use
argparse's exit status `2`.

### Standard and verbose logging

Standard logging is the default: operation/stage introductions, summaries,
accepted uploads, successful deletions, authentication notices, warnings, and
errors are shown. Accepted uploads identify the filename and new document ID,
and are reported as queued, not processing complete. Full-workspace listings
report progress after the first page, every 10 pages, and the final page.
Cleanup shows each selected filename, document ID, and reason before deletion,
then confirms each successful deletion. Explicit duplicate cleanup also identifies
the retained document. The final cleanup summary reports successful deletions.

No-change messages (completed files being skipped, documents preserved by cleanup,
skipped subdirectories, and per-file filename lookups) and HTTP debug output are
hidden unless verbose logging is enabled.
Operation and major-work-stage introductions are visible in standard mode;
per-file helper introductions remain verbose-only to avoid flooding normal uploads.

Add `--verbose` (or `-v`) to any command for per-file activity and HTTP diagnostics:

```powershell
python .\main.py --action upload --verbose
```

The same flag works with direct-folder uploads. `logfile.log` uses the selected
verbosity. Detailed accepted-upload and ignored-file records are still appended
to `file_logger_success.log` and `file_logger_ignored.log` in both modes, without
echoing those records to the console. MSAL debug logging remains disabled in both
modes.

Blocked uploads, failed uploads, and retry warnings remain visible in standard
mode. Logging verbosity does not hide the requested document table from
`--action list`.

### Investigating connection failures

Run the same command with `--verbose` and capture the runtime and transport
diagnostic lines from `logfile.log`. These report:

- The Python executable, Python/Requests/urllib3 versions, configured API hostname,
  resolver implementation, and names of any proxy environment variables present.
- API request method, hostname, attempt, elapsed time, and HTTP response status
  when a response was received.
- On a transport exception, the exception chain and error numbers, followed by
  an independent resolver lookup in the failing process. A successful follow-up
  lookup does not prove why the preceding HTTP request failed.

These added diagnostics do not log bearer tokens, headers, request bodies, URL
credentials/query strings, or proxy values. Existing per-file and HTTP debug
messages can still contain filenames and workspace identifiers; review logs
before sharing them. Standard mode does not perform the extra resolver lookup.

A token-test connection failure now reports an **API transport/preflight failure**,
not a rejected token. No uploads or deletions are attempted if preflight fails.
Transport failures are not automatically retried, since an interrupted upload
may already have been accepted by the server.

## Interactive user login

Interactive authentication uses **your delegated API token**, not an application
token plus a separately discovered user ID. MSAL supplies your Entra object ID
(`oid`) and tenant ID from the sign-in result; no Microsoft Graph lookup or UPN
mapping is required.

### One-time Entra setup

1. On the **SimpleChat API app registration**, expose a delegated scope such as
   `api://<simplechat-api-client-id>/access_as_user`. This scope is for SimpleChat,
   not Microsoft Graph.
2. Register a **public desktop/CLI client**, preferably separate from the API and
   daemon registrations. Add the **Mobile and desktop applications** redirect URI
   `http://localhost` for browser login. Enable **Allow public client flows** for
   device-code login.
3. Give this public client the SimpleChat **delegated** permission from step 1,
   and obtain the required user/admin consent according to your tenant policy.
4. On the API registration, ensure the app role's **value** is exactly
   `ExternalApi`, with users/groups as allowed members. Assign your user or an
   appropriate group that role through the API's **Enterprise application >
   Users and groups**. A similarly named role with a different value, or an
   application-only role assignment to a service principal, is not sufficient.
5. Keep the API's access-token format compatible with this branch's existing
   validator: it expects a **v1 access token**, audience
   `api://<simplechat-api-client-id>`, and issuer
   `https://sts.windows.net/<tenant-id>/`. Do not change the API to issue v2
   access tokens for this feature without also updating its server-side token
   validation. The MSAL sign-in endpoint version and API access-token version
   are separate settings.

This feature does not create registrations, change tenant permissions, bypass
the `ExternalApi` check, or alter the server's audience/issuer validation.

Configure `.env` for interactive use:

```dotenv
AUTHORITY_URL=https://login.microsoftonline.com
AZURE_TENANT_ID=<tenant-GUID>
INTERACTIVE_CLIENT_ID=<public-client-application-GUID>
INTERACTIVE_API_SCOPE=api://<simplechat-api-client-id>/access_as_user
API_BASE_URL=https://<your-simplechat-host>
UPLOAD_DIRECTORY=./test-documents
```

For Azure Government, use `https://login.microsoftonline.us` and the appropriate
API hostname. `INTERACTIVE_CLIENT_ID` falls back to `AZURE_CLIENT_ID` if omitted,
but that registration must support public-client login. `API_SCOPE` and
`AZURE_CLIENT_SECRET` are not required or used in interactive mode.
`INTERACTIVE_API_SCOPE` must be one explicit `api://` delegated scope, not
`/.default` or a Graph scope.

### Run as the signed-in user

```powershell
python .\main.py --auth interactive --action list --scope public --workspaceid public-workspace-id
python .\main.py --auth interactive --action upload
python .\main.py --auth interactive --action clean --scope group --workspaceid group-workspace-id
```

`--auth interactive` opens an account-selection browser window. If the browser
cannot start, the local callback cannot bind, or browser interaction times out,
the command reports it and falls back to device-code login. A denied sign-in or
invalid authentication response fails rather than falling back.

To select device-code login directly:

```powershell
python .\main.py --auth device-code --action list --scope group --workspaceid group-workspace-id
```

Follow the verification URL/code shown in the terminal. Device codes are not
written to the tool's log files. Tenant policies may disallow device-code login.
Press Ctrl+C to cancel sign-in or stop further work; cancellation returns exit
status `130` and does not undo requests already accepted by the server.

Do not pass `--userid` in either interactive mode. The signed-in tenant object ID
is used for all operations and every upload mapping, even if a different ID is
present in the CSV. This does not grant workspace permissions beyond the API's
existing authorization rules.

Tokens are kept only in the process's in-memory MSAL cache, not persisted to disk.
On HTTP 401 the same selected account is refreshed silently. If sign-in is needed
again or refresh changes identity/tenant, the command fails instead of changing
users midway through a batch; rerun to sign in again.

Omit `--auth` or use `--auth application` to retain the original unattended
workflow, including explicit mapping user IDs and `--userid` for direct-folder
uploads and cleanup.

## List workspace contents

List remote documents before uploading or cleaning:

```powershell
python .\main.py --action list --scope public --workspaceid public-workspace-id
python .\main.py --action list --scope group --workspaceid group-workspace-id
```

This read-only action fetches all result pages and prints tab-separated columns
for filename, document ID, status, completion percentage, and last update, followed
by the total document count. Missing metadata is shown as `Unknown`. It does not
upload or delete documents and does not require `map.csv` or `UPLOAD_DIRECTORY`.
An empty workspace reports zero documents; a failed listing returns exit status
`1` rather than reporting an empty workspace.

To focus on pending, processing, failed, or unknown-status files, add
`--incomplete-only`:

```powershell
python .\main.py --auth application --action list --scope public --workspaceid public-workspace-id --incomplete-only
```

The filter hides exact `Processing complete` status matches, ignoring case and
surrounding whitespace. It filters by status, not completion percentage; missing
or other statuses remain visible. All pages are fetched before filtering. The
summary shows the filtered count and the full workspace total. Without this flag,
listing is unchanged. The flag is supported only with `--action list` and never
changes or deletes documents.

`--userid` is required for listing in application mode, unless `BULKLOADER_USER_ID`
is set in `.env`; the command-line value overrides it. The server checks that
user's role in the public workspace (any member can list; Owner, Admin, or
DocumentManager is needed to upload or delete).
Authentication uses the selected mode and still requires the `ExternalApi` role.
In interactive mode the signed-in user is used automatically.

In application mode uploads still require `userId` in the mapping, and cleanup
requires `--userid`. Shared document-update code requires the ID, public document
metadata stores it, and public blob-path construction uses it during cleanup.
Interactive modes supply this ID automatically. Group document ownership checks
use the group ID.

## Explicit cleanup of stuck uploads

Cleanup is **never run automatically during upload**. Run it explicitly for
one workspace:

```powershell
python .\main.py --action clean --userid user-id --scope public --workspaceid public-workspace-id
python .\main.py --action clean --userid user-id --scope group --workspaceid group-workspace-id
```

Cleanup deletes a document only when its completion is below 100% and either:

1. Its `last_updated` timestamp is **more than 12 hours old** (configurable
   with `--stale-hours`), or
2. Its status starts with `Error:` (deleted regardless of age).

Recent pending documents, documents exactly at the cutoff boundary, and
documents at 100% completion are preserved.

Use `--stale-hours` (clean only) to change the incomplete-upload cutoff:

```powershell
python .\main.py --action clean --userid user-id --scope public --workspaceid public-workspace-id --stale-hours 1
python .\main.py --action clean --userid user-id --scope public --workspaceid public-workspace-id --stale-hours 0
```

`--stale-hours 0` deletes **every** incomplete upload regardless of age,
including documents that are still actively processing. Run it only when no
uploads are in flight. Timezone-aware ISO timestamps
support UTC `Z`, fractional seconds, and explicit offsets. Missing, invalid,
or timezone-less timestamps are reported and preserved unless an explicit
error status independently qualifies the incomplete document. Unknown or
invalid completion percentages are reported and preserved.

Deletion errors stop cleanup and produce exit status `1`; successful deletions
are logged. Cleanup operates on remote documents and associated chunks, so
verify the user, scope, and workspace ID before running it. It does not cancel
an active background worker; a very old but still-running job may meet the
stale-record criterion.

Optional exact-name duplicate removal:

```powershell
python .\main.py --action clean --userid user-id --scope public --workspaceid public-workspace-id --removeduplicates
```

This additionally deletes duplicate records **regardless of age**, retaining
one completed copy when available, otherwise the first copy listed. It operates
only on records remaining after stale/failed cleanup, so records are not deleted
twice.

## Delete every document in a workspace

`--action delete` removes **all** documents from one workspace. It is
destructive and permanent: the documents, their chunks, search-index rows, and
any stored source files are deleted. Local files are not touched.

First preview what would be deleted (nothing is changed):

```powershell
python .\main.py --action delete --all --dry-run --scope public --workspaceid public-workspace-id
```

Then run it. You are asked to type the workspace ID to confirm; add `--yes` to
skip that prompt for unattended runs:

```powershell
python .\main.py --action delete --all --scope public --workspaceid public-workspace-id
```

Behavior:

- `--all` is required. Without it, `--action delete` is rejected.
- `--userid` is required in application mode, with the same `BULKLOADER_USER_ID`
  fallback as the other actions. The caller needs Owner, Admin, or
  DocumentManager in the workspace.
- A server error on one document (for example a stale "ghost" entry whose
  record no longer exists) is logged with the server's message and skipped;
  the run continues and the exit status is `1`.
- A connection failure stops the run immediately. Re-run it to delete whatever
  remains; documents already deleted are not listed again.
- Progress is logged every 100 documents, with a final summary.
- Run `--action list` afterwards to confirm the workspace is empty.

Use `clean` instead when you only want to remove failed or stuck uploads; it
never removes completed documents.

## Regression tests

From the repository root, using an environment with the bulkloader dependencies:

```powershell
python -B -m unittest discover -s .\application\external_apps\bulkloader\tests -p test_main.py
```

The tests mock authentication, HTTP, and background processing; no live service
credentials or uploads are needed.