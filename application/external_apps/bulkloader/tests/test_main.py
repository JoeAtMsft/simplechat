import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
import importlib.util
import logging
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import requests


LOADER_DIRECTORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bulkloader_main", LOADER_DIRECTORY / "main.py")
loader = importlib.util.module_from_spec(SPEC)
with patch("dotenv.load_dotenv"), patch("logging.basicConfig"), patch.object(logging.getLogger(), "addHandler"):
    SPEC.loader.exec_module(loader)


def response(status=200, payload=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result.url = "https://example.invalid"
    result.headers.update(headers or {})
    result.json = Mock(return_value=payload)
    return result


def document(name="report.pdf", percent=100, status="Processing complete", **extra):
    return {
        "id": "document-id",
        "file_name": name,
        "percentage_complete": percent,
        "status": status,
        **extra,
    }


def listing(documents, page=1, total=None):
    return response(payload={
        "documents": documents,
        "page": page,
        "page_size": 25,
        "total_count": len(documents) if total is None else total,
    })


class LoaderTestCase(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(loader, "appLogger"),
            patch.object(loader, "successFileLogger"),
            patch.object(loader, "ignoredFileLogger"),
            patch.object(loader, "g_ACCESS_TOKEN", "old-token"),
            patch.object(loader, "MAX_RETRIES", 3),
            patch.object(loader, "get_access_token", return_value="new-token"),
            patch.object(loader.time, "sleep"),
            patch.object(loader.requests, "request"),
            patch.object(loader.requests, "get"),
            patch.object(loader.requests, "post"),
            patch.object(loader.requests, "delete"),
            patch.multiple(
                loader,
                PUBLIC_DOCUMENTS_LIST_URL="https://example.invalid/external/public_documents",
                GROUP_DOCUMENTS_LIST_URL="https://example.invalid/external/group_documents",
                PUBLIC_DOCUMENTS_UPLOAD_URL="https://example.invalid/external/public_documents/upload",
                GROUP_DOCUMENTS_UPLOAD_URL="https://example.invalid/external/group_documents/upload",
            ),
        ]
        self.mocks = [self.enterContext(patcher) for patcher in patches]
        self.request = loader.requests.request
        self.request.side_effect = AssertionError("Unexpected HTTP request")
        loader.requests.get.side_effect = AssertionError("Use the bounded request helper")
        loader.requests.post.side_effect = AssertionError("Use the bounded request helper")
        loader.requests.delete.side_effect = AssertionError("Use the bounded request helper")
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.file = self.directory / "report.pdf"
        self.file.write_bytes(b"complete document bytes")

    def upload(self, scope="public"):
        return loader.upload_document(str(self.file), "user", scope, "workspace", "", "old-token")

    def assert_outcome(self, result, expected):
        self.assertEqual(result.value, expected)


class UploadTests(LoaderTestCase):
    def test_completed_exact_match_skipped_even_if_local_content_changes(self):
        for content in [b"original", b"changed"]:
            with self.subTest(content=content):
                self.file.write_bytes(content)
                self.request.side_effect = [listing([document()])]
                self.assert_outcome(self.upload(), "skipped")
                self.assertEqual(self.request.call_args.args[0], "GET")

    def test_existing_success_status_variants_are_skipped(self):
        for status in [
            "Processing complete",
            "Processing complete - no content indexed",
            "Audio transcription complete",
            "Metadata extraction complete",
        ]:
            with self.subTest(status=status):
                self.request.side_effect = [listing([document(status=status)])]
                self.assert_outcome(self.upload(), "skipped")

    def test_incomplete_or_unknown_exact_matches_block_upload(self):
        cases = [
            document(percent=0, status="Queued for processing"),
            document(percent=50, status="Processing"),
            document(percent=0, status="Error: failed"),
            document(percent=100, status="Error: failed"),
            document(percent=None),
            document(percent="100"),
            document(percent=True),
            document(percent=101),
            document(percent=float("nan")),
            document(percent=float("inf")),
            document(percent=10 ** 400),
            document(status=""),
            document(status="Processing"),
        ]
        for existing in cases:
            with self.subTest(existing=existing):
                self.request.side_effect = [listing([existing])]
                self.assert_outcome(self.upload(), "blocked")
        self.assertEqual(self.request.call_count, len(cases))
        loader.appLogger.warning.assert_called()

    def test_mixed_completed_and_incomplete_matches_report_blocked(self):
        self.request.side_effect = [listing([document(), document(percent=0)])]
        self.assert_outcome(self.upload(), "blocked")

    def test_substring_title_and_different_case_do_not_skip(self):
        existing = [
            document(name="report.pdf.backup"),
            document(name="other.pdf", title="report.pdf"),
            document(name="REPORT.PDF"),
        ]
        self.request.side_effect = [
            listing(existing),
            response(payload={"document_ids": ["new"], "errors": []}),
        ]
        self.assert_outcome(self.upload(), "queued")

    def test_exact_match_on_later_page(self):
        self.request.side_effect = [
            listing([document(name="unrelated.pdf")], total=26),
            listing([document()], page=2, total=26),
        ]
        self.assert_outcome(self.upload(), "skipped")
        self.assertEqual(self.request.call_args.kwargs["params"]["page"], 2)

    def test_both_scopes_use_correct_url_and_workspace_parameter(self):
        for scope, key, list_url, upload_url in [
            ("public", "active_workspace_id", loader.PUBLIC_DOCUMENTS_LIST_URL, loader.PUBLIC_DOCUMENTS_UPLOAD_URL),
            ("group", "active_group_id", loader.GROUP_DOCUMENTS_LIST_URL, loader.GROUP_DOCUMENTS_UPLOAD_URL),
        ]:
            with self.subTest(scope=scope):
                self.request.reset_mock()
                self.request.side_effect = [
                    listing([]), response(payload={"document_ids": ["new"], "errors": []}),
                ]
                self.assert_outcome(self.upload(scope), "queued")
                get_call, post_call = self.request.call_args_list
                self.assertEqual(get_call.args[:2], ("GET", list_url))
                self.assertEqual(get_call.kwargs["params"][key], "workspace")
                self.assertEqual(post_call.args[:2], ("POST", upload_url))
                self.assertEqual(post_call.kwargs["data"][key], "workspace")
                other_key = "active_group_id" if scope == "public" else "active_workspace_id"
                self.assertNotIn(other_key, post_call.kwargs["data"])

    def test_retries_replay_full_file_and_refresh_headers(self):
        bodies = []
        auth_headers = []
        statuses = iter([429, 401, 200])

        def send(method, url, **kwargs):
            if method == "GET":
                return listing([])
            bodies.append(kwargs["files"]["file"][1].read())
            auth_headers.append(kwargs["headers"]["Authorization"])
            return response(next(statuses), {"document_ids": ["new"], "errors": []}, {"Retry-After": "2"})

        self.request.side_effect = send
        self.assert_outcome(self.upload(), "queued")
        self.assertEqual(bodies, [self.file.read_bytes()] * 3)
        self.assertEqual(auth_headers, ["Bearer old-token", "Bearer old-token", "Bearer new-token"])
        loader.time.sleep.assert_called_once_with(2)
        self.assertEqual(loader.g_ACCESS_TOKEN, "new-token")

    def test_listing_failure_does_not_upload(self):
        for failed in [
            response(500),
            response(payload={"error": "failed"}),
            listing([None]),
            listing([{}]),
            listing([document(name=None)]),
            response(payload=[]),
        ]:
            with self.subTest(failed=failed):
                self.request.reset_mock()
                self.request.side_effect = [failed]
                self.assert_outcome(self.upload(), "failed")
                self.request.assert_called_once()

    def test_transport_failure_is_not_retried(self):
        self.request.side_effect = [listing([]), requests.Timeout("Outcome unknown")]
        self.assert_outcome(self.upload(), "failed")
        self.assertEqual(self.request.call_count, 2)

    def test_server_error_and_partial_response_are_not_success(self):
        for failed in [
            response(500),
            response(207, {"errors": ["not queued"]}),
            response(200, {"errors": ["failed"], "document_ids": []}),
            response(200, {"message": "unexpected shape"}),
        ]:
            with self.subTest(failed=failed):
                self.request.side_effect = [listing([]), failed]
                self.assert_outcome(self.upload(), "failed")

    def test_top_level_files_only_and_missing_folder_fails(self):
        nested = self.directory / "nested"
        nested.mkdir()
        (nested / "child.pdf").write_bytes(b"nested")
        with patch.object(loader, "upload_document", return_value=loader.UploadOutcome.QUEUED) as upload:
            summary = loader.read_files_in_directory(str(self.directory), "u", "public", "w", "")
        self.assertEqual(summary["queued"], 1)
        upload.assert_called_once()
        self.assertEqual(Path(upload.call_args.args[0]), self.file)
        with self.assertRaises((ValueError, OSError)):
            loader.read_files_in_directory(str(nested / "absent"), "u", "public", "w", "")

    def test_empty_folder_is_valid_noop(self):
        self.file.unlink()
        with patch.object(loader, "upload_document") as upload:
            self.assertEqual(loader.read_files_in_directory(
                str(self.directory), "u", "public", "w", ""
            ), Counter())
        upload.assert_not_called()

    def test_request_reads_entire_file_using_real_multipart_encoding(self):
        bodies = []
        statuses = iter([429, 200])

        def send(method, url, **kwargs):
            if method == "GET":
                return listing([])
            prepared = requests.Request(
                method, url, **{key: value for key, value in kwargs.items() if key != "timeout"}
            ).prepare()
            bodies.append(prepared.body)
            return response(next(statuses), {"document_ids": ["new"], "errors": []})

        self.request.side_effect = send
        self.assert_outcome(self.upload(), "queued")
        for body in bodies:
            self.assertIn(self.file.read_bytes(), body)
        self.assertEqual(len(bodies), 2)


class RequestTests(LoaderTestCase):
    def test_listing_401_refresh_uses_new_token_for_subsequent_requests(self):
        self.request.side_effect = [response(401), listing([]), listing([])]
        loader.get_workspace_files("u", "w", "public", "old-token")
        loader.get_workspace_files("u", "w", "public", "old-token")
        headers = [call.kwargs["headers"]["Authorization"] for call in self.request.call_args_list]
        self.assertEqual(headers, ["Bearer old-token", "Bearer new-token", "Bearer new-token"])

    def test_retry_exhaustion_is_failure_without_last_sleep(self):
        self.request.side_effect = [response(429)] * 3
        with self.assertRaises((requests.RequestException, RuntimeError)):
            loader.get_workspace_files("u", "w", "public", "old-token")
        self.assertEqual(self.request.call_count, 3)
        self.assertEqual(loader.time.sleep.call_count, 2)

    def test_refresh_failure_stops_requests(self):
        loader.get_access_token.return_value = None
        self.request.side_effect = [response(401)]
        with self.assertRaises((requests.RequestException, RuntimeError)):
            loader.get_workspace_files("u", "w", "public", "old-token")
        self.request.assert_called_once()

    def test_missing_token_never_calls_api(self):
        with patch.object(loader, "g_ACCESS_TOKEN", None):
            with self.assertRaises(RuntimeError):
                loader.get_workspace_files("u", "w", "public", None)
        self.request.assert_not_called()

    def test_token_acquisition_failure_is_explicit(self):
        with patch.object(loader, "get_access_token", wraps=None) as acquire:
            acquire.side_effect = RuntimeError("Token acquisition failed")
            self.request.side_effect = [response(401)]
            with self.assertRaisesRegex(RuntimeError, "Token acquisition failed"):
                loader.get_workspace_files("u", "w", "public", "old-token")
        self.request.assert_called_once()

    def test_token_test_uses_expected_endpoint_and_rejects_failure(self):
        self.request.side_effect = [response(200)]
        self.assertTrue(loader.test_access_token("old-token"))
        self.assertEqual(self.request.call_args.args[:2], ("POST", loader.BEARER_TOKEN_TEST_URL))
        self.request.side_effect = [response(403)]
        self.assertFalse(loader.test_access_token("old-token"))

    def test_401_retry_exhaustion_is_bounded(self):
        self.request.side_effect = [response(401)] * 3
        with self.assertRaises(requests.HTTPError):
            loader.get_workspace_files("u", "w", "public", "old-token")
        self.assertEqual(self.request.call_count, 3)
        self.assertEqual(loader.get_access_token.call_count, 2)

    def test_invalid_scope_is_rejected_before_request(self):
        with self.assertRaises(ValueError):
            loader.get_workspace_files("u", "w", "unknown", "old-token")
        self.request.assert_not_called()

    def test_retry_after_date_and_invalid_header(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for header, expected in [
            ("Thu, 01 Jan 2026 00:00:07 GMT", 7),
            ("Thu, 01 Jan 2026 00:00:00 GMT", 0),
            ("invalid", loader.RETRY_DELAY),
            ("-1", loader.RETRY_DELAY),
            ("NaN", loader.RETRY_DELAY),
        ]:
            with self.subTest(header=header), patch.object(loader, "datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                self.request.side_effect = [response(429, headers={"Retry-After": header}), listing([])]
                loader.get_workspace_files("u", "w", "public", "old-token")
                loader.time.sleep.assert_called_with(expected)

    def test_invalid_pagination_is_not_treated_as_empty(self):
        payloads = [
            {"documents": [], "total_count": 1, "page": 1, "page_size": 25},
            {"documents": [], "total_count": 0, "page": 1, "page_size": 0},
            {"documents": [], "total_count": "0", "page": 1, "page_size": 25},
            {"documents": [], "total_count": 0, "page": 2, "page_size": 25},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.request.side_effect = [response(payload=payload)]
                with self.assertRaises(ValueError):
                    loader.get_workspace_files("u", "w", "public", "old-token")


class CleanupTests(LoaderTestCase):
    def clean(self, docs):
        return loader.delete_failed_workspace_files(docs, "u", "w", "public", "old-token")

    def test_thresholds_statuses_and_timestamp_formats(self):
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        cutoff = now - timedelta(hours=12)
        cases = [
            ("old", 20, "Queued for processing", (cutoff - timedelta(seconds=1)).isoformat()),
            ("boundary", 20, "Queued for processing", cutoff.isoformat()),
            ("recent", 20, "Queued for processing", (cutoff + timedelta(seconds=1)).isoformat()),
            ("completed", 100, "Processing complete", "2026-01-01T00:00:00Z"),
            ("fractional", 20, "Processing", "2026-01-01T00:00:00.123Z"),
            ("offset", 20, "Processing", "2026-01-01T03:00:00+03:00"),
            ("error", 0, "Error: failed", now.isoformat()),
            ("error-invalid-time", 0, "Error: failed", "invalid"),
            ("invalid-time", 10, "Processing", "invalid"),
            ("naive-time", 10, "Processing", "2026-01-01T00:00:00"),
            ("missing-time", 10, "Processing", None),
            ("missing-progress", None, "Error: failed", None),
            ("string-progress", "20", "Error: failed", None),
            ("bool-progress", False, "Error: failed", None),
            ("negative-progress", -1, "Error: failed", None),
            ("nan-progress", float("nan"), "Error: failed", None),
            ("infinite-progress", float("inf"), "Error: failed", None),
            ("oversized-progress", 10 ** 400, "Error: failed", None),
        ]
        docs = [
            document(name=name, id=name, percent=percent, status=status, last_updated=timestamp)
            for name, percent, status, timestamp in cases
        ]
        with patch.object(loader, "datetime", wraps=datetime) as clock, patch.object(
            loader, "delete_workspace_file", return_value=True
        ) as delete:
            clock.now.return_value = now
            deleted = self.clean(docs)
        expected = {"old", "fractional", "offset", "error", "error-invalid-time"}
        self.assertEqual({doc["id"] for doc in deleted}, expected)
        self.assertEqual({call.args[0] for call in delete.call_args_list}, expected)
        loader.appLogger.warning.assert_called()

    def test_delete_uses_scope_and_accepts_api_success_codes(self):
        for scope, key, url in [
            ("public", "active_workspace_id", loader.PUBLIC_DOCUMENTS_LIST_URL),
            ("group", "active_group_id", loader.GROUP_DOCUMENTS_LIST_URL),
        ]:
            for status in [200, 204]:
                with self.subTest(scope=scope, status=status):
                    self.request.side_effect = [response(status)]
                    self.assertTrue(loader.delete_workspace_file("id", "u", "w", scope, "old-token"))
                    self.assertEqual(self.request.call_args.args[:2], ("DELETE", url + "/id"))
                    self.assertEqual(self.request.call_args.kwargs["params"][key], "w")

    def test_delete_failure_propagates(self):
        self.request.side_effect = [response(500)]
        with self.assertRaises(requests.HTTPError):
            self.clean([document(percent=0, status="Error: failed")])

    def test_false_deletion_result_is_not_counted(self):
        with patch.object(loader, "delete_workspace_file", return_value=False):
            with self.assertRaises(RuntimeError):
                self.clean([document(percent=0, status="Error: failed")])

    def test_duplicate_cleanup_retains_completed_copy(self):
        failed = document(id="failed", percent=0, status="Queued for processing")
        complete = document(id="complete")
        with patch.object(loader, "delete_workspace_file", return_value=True) as delete:
            deleted = loader.delete_duplicate_workspace_files(
                [failed, complete], "u", "w", "public", "old-token"
            )
        self.assertEqual(deleted, [failed])
        self.assertEqual(delete.call_args.args[0], "failed")


class CommandTests(LoaderTestCase):
    def test_csv_accepts_five_columns_and_preserves_summary(self):
        mapping = self.directory / "map.csv"
        mapping.write_text(
            "folderName,userId,workspaceScope,workspaceId,classification\n"
            "., u, group, w,\n", encoding="utf-8"
        )
        with patch.object(loader, "UPLOAD_DIRECTORY", str(self.directory)), patch.object(
            loader, "read_files_in_directory", return_value=Counter(queued=1, blocked=1)
        ) as read:
            summary = loader.read_csv_ignore_header(str(mapping))
        self.assertEqual(summary["queued"], 1)
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(read.call_args.args[1:5], ("u", "group", "w", ""))

    def test_invalid_csv_rows_fail_before_any_upload(self):
        for rows in [
            "folder,user,public,workspace\n",
            "folder,user,wrong,workspace,\n",
            "folder,,public,workspace,\n",
            "",
        ]:
            with self.subTest(rows=rows):
                mapping = self.directory / "invalid.csv"
                mapping.write_text("folderName,userId,workspaceScope,workspaceId,classification\n" + rows)
                with patch.object(loader, "read_files_in_directory") as read:
                    with self.assertRaises(ValueError):
                        loader.read_csv_ignore_header(str(mapping))
                    read.assert_not_called()

    def test_later_invalid_csv_row_does_not_allow_partial_upload(self):
        mapping = self.directory / "invalid.csv"
        mapping.write_text(
            "folderName,userId,workspaceScope,workspaceId,classification\n"
            ".,user,public,workspace,\nfolder,user,wrong,workspace,\n"
        )
        with patch.object(loader, "read_files_in_directory") as read:
            with self.assertRaises(ValueError):
                loader.read_csv_ignore_header(str(mapping))
        read.assert_not_called()

    def test_malformed_csv_is_rejected_before_upload(self):
        mapping = self.directory / "invalid.csv"
        mapping.write_text(
            "folderName,userId,workspaceScope,workspaceId,classification\n"
            ".,user,public,workspace,\"unterminated\n"
        )
        with patch.object(loader, "read_files_in_directory") as read:
            with self.assertRaises(loader.csv.Error):
                loader.read_csv_ignore_header(str(mapping))
        read.assert_not_called()

    def test_missing_folder_counts_failure_and_continues_other_mappings(self):
        mapping = self.directory / "map.csv"
        mapping.write_text(
            "folderName,userId,workspaceScope,workspaceId,classification\n"
            "missing,u,public,w,\n.,u,public,w,\n"
        )
        with patch.object(loader, "UPLOAD_DIRECTORY", str(self.directory)), patch.object(
            loader, "upload_document", return_value=loader.UploadOutcome.QUEUED
        ):
            summary = loader.read_csv_ignore_header(str(mapping))
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["queued"], 2)

    def run_main(self, args):
        env = {
            "AUTHORITY_URL": "https://login.example.invalid",
            "TENANT_ID": "tenant", "CLIENT_ID": "client", "CLIENT_SECRET": "secret",
            "API_SCOPE": "api://example/.default", "API_BASE_URL": "https://example.invalid",
            "UPLOAD_DIRECTORY": str(self.directory),
        }
        with patch.multiple(loader, **env), patch.object(loader, "configure_logging"), patch.object(
            loader, "test_access_token", return_value=True
        ), patch("sys.argv", ["main.py"] + args):
            return loader.main()

    def test_upload_exit_status_and_summary(self):
        for summary, expected in [
            (Counter(queued=1, skipped=1), 0),
            (Counter(blocked=1), 1),
            (Counter(failed=1), 1),
        ]:
            with self.subTest(summary=summary), patch.object(
                loader, "read_csv_ignore_header", return_value=summary
            ), patch.object(loader, "delete_failed_workspace_files") as clean:
                self.assertEqual(self.run_main(["--action", "upload"]), expected)
                clean.assert_not_called()
        loader.appLogger.info.assert_called()

    def test_token_test_failure_aborts_upload(self):
        with patch.object(loader, "configure_logging"), patch.object(
            loader, "validate_configuration"
        ), patch.object(loader, "test_access_token", return_value=False), patch.object(
            loader, "read_csv_ignore_header"
        ) as read, patch("sys.argv", ["main.py", "--action", "upload"]):
            self.assertEqual(loader.main(), 1)
        read.assert_not_called()

    def test_clean_does_not_delete_same_record_twice(self):
        docs = [
            document(id="failed", percent=0, status="Error: failed"),
            document(id="complete"),
        ]
        with patch.object(loader, "get_workspace_files", return_value=docs), patch.object(
            loader, "delete_workspace_file", return_value=True
        ) as delete:
            result = self.run_main([
                "--action", "clean", "-u", "u", "-w", "w", "-s", "public", "--removeduplicates"
            ])
        self.assertEqual(result, 0)
        delete.assert_called_once()
        self.assertEqual(delete.call_args.args[0], "failed")

    def test_clean_failure_produces_nonzero_exit(self):
        with patch.object(loader, "get_workspace_files", return_value=[
            document(percent=0, status="Error: failed")
        ]), patch.object(loader, "delete_workspace_file", side_effect=requests.HTTPError("HTTP 500")):
            self.assertEqual(self.run_main([
                "--action", "clean", "-u", "u", "-w", "w", "-s", "group"
            ]), 1)

    def test_standalone_delete_remains_non_destructive(self):
        with patch.object(loader, "get_workspace_files", return_value=[document()]), patch.object(
            loader, "delete_workspace_file"
        ) as delete:
            self.assertEqual(self.run_main([
                "--action", "delete", "-u", "u", "-w", "w", "-s", "public"
            ]), 0)
        delete.assert_not_called()
        loader.appLogger.warning.assert_called()

    def test_clean_requires_destination_flags(self):
        with patch("sys.argv", ["main.py", "--action", "clean"]), patch("sys.stderr"):
            with self.assertRaises(SystemExit) as error:
                loader.main()
        self.assertEqual(error.exception.code, 2)

    def test_missing_configuration_is_reported(self):
        with patch.object(loader, "CLIENT_ID", None):
            with self.assertRaises(ValueError):
                loader.validate_configuration("upload")


class GroupRouteTests(unittest.TestCase):
    def test_group_upload_schedules_processing_once(self):
        path = LOADER_DIRECTORY.parents[1] / "single_app" / "route_external_group_documents.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        register = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
        upload = next(node for node in register.body if isinstance(node, ast.FunctionDef))
        # Execute the real handler without importing the Azure-backed application.
        upload.decorator_list = []
        handler_tree = ast.Module(body=[upload], type_ignores=[])
        executor = Mock()
        file = Mock(filename="report.pdf")
        named_file = Mock()
        named_file.__enter__ = Mock(return_value=SimpleNamespace(name="temporary.pdf"))
        named_file.__exit__ = Mock(return_value=False)
        namespace = {
            "request": SimpleNamespace(
                form={"user_id": "u", "active_group_id": "g"},
                files=Mock(),
            ),
            "jsonify": lambda value: value,
            "os": os,
            "uuid": uuid,
            "secure_filename": lambda name: name,
            "allowed_file": lambda name: True,
            "tempfile": SimpleNamespace(NamedTemporaryFile=Mock(return_value=named_file)),
            "create_document": Mock(),
            "update_document": Mock(),
            "executor": executor,
            "process_document_upload_background": Mock(),
        }
        namespace["request"].files.__contains__ = Mock(return_value=True)
        namespace["request"].files.getlist.return_value = [file]
        exec(compile(handler_tree, str(path), "exec"), namespace)
        payload, status = namespace[upload.name]()
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["document_ids"]), 1)
        executor.submit.assert_not_called()
        executor.submit_stored.assert_called_once()
        self.assertEqual(executor.submit_stored.call_args.kwargs["group_id"], "g")


if __name__ == "__main__":
    unittest.main()
