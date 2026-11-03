# Bulk document management

Run these commands from the `application/external_apps/bulkloader` directory,
using a Python environment with this tool's dependencies installed:

```powershell
python -m pip install -r .\requirements.txt
```

## STEP 1: Create a Service Principal for doc management
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
UPLOAD_DIRECTORY=./test-documents (Example only)
```

## STEP 4 (for upload action only): Create a folder repository of files to upload

./test-documents is a sample folder

## STEP 5 (for upload action only): Update the map.csv file and add the following columns (Example only)

```csv
folderName,userId,workspaceScope,workspaceId,classification
folder1,user-id,public,public-workspace-id,
folder2,user-id,group,group-workspace-id,
```

- All five columns are required, including the final comma when classification
  is empty. The header must use the names above.
- Each folder is relative to `UPLOAD_DIRECTORY`; use `.` to upload files directly
  from that directory. An absolute local or UNC folder path is also supported.
- Each row selects a `public` or `group` destination and a user authorized to
  operate on it. Upload destinations come from this file, not the CLI workspace
  flags.
- Only immediate files are uploaded. Subdirectories are ignored; add separate
  rows for additional folders.
- The entire mapping is validated before uploads begin. Missing folders or
  unreadable files are reported as failures, not successful no-ops.

## STEP 6: Run main.py script

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

## Explicit cleanup of stuck uploads

Cleanup is **never run automatically during upload**. Run it explicitly for
one workspace:

```powershell
python .\main.py --action clean --userid user-id --scope public --workspaceid public-workspace-id
python .\main.py --action clean --userid user-id --scope group --workspaceid group-workspace-id
```

Cleanup deletes a document only when its completion is below 100% and either:

1. Its `last_updated` timestamp is **more than 12 hours old**, or
2. Its status starts with `Error:`.

Recent pending documents, documents exactly at the 12-hour boundary, and
documents at 100% completion are preserved. Timezone-aware ISO timestamps
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

The legacy `--action delete` option remains unimplemented: it lists documents
but does not delete them. Use `clean` only for the criteria described above;
it is not a delete-all command.

## Regression tests

From the repository root, using an environment with the bulkloader dependencies:

```powershell
python -B -m unittest discover -s .\application\external_apps\bulkloader\tests -p test_main.py
```

The tests mock authentication, HTTP, and background processing; no live service
credentials or uploads are needed.