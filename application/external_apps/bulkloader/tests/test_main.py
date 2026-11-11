import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import logging
import os
import socket
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
            patch.object(loader, "g_INTERACTIVE_AUTH", None),
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
        loader.appLogger.isEnabledFor.return_value = False
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

    def test_failed_upload_does_not_log_a_successful_change(self):
        self.request.side_effect = [listing([]), response(500)]
        self.assert_outcome(self.upload(), "failed")
        loader.appLogger.info.assert_not_called()
        loader.appLogger.error.assert_called()

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


class DiagnosticTests(LoaderTestCase):
    def test_runtime_diagnostics_show_environment_without_credentials(self):
        logger = logging.getLogger("bulkloader.diagnostic.tests")
        with patch.object(loader, "appLogger", logger), patch.object(
            loader, "API_BASE_URL", "https://url-user:URL_SECRET@example.invalid/base?key=QUERY_SECRET"
        ), patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy-user:PROXY_SECRET@proxy.invalid"}, clear=True):
            with self.assertLogs(logger, level="DEBUG") as captured:
                loader.log_runtime_diagnostics()
        text = "\n".join(captured.output)
        for expected in [loader.sys.executable, "Python=", "requests=", "urllib3=", "example.invalid", "HTTPS_PROXY"]:
            self.assertIn(expected, text)
        for secret in ["URL_SECRET", "QUERY_SECRET", "PROXY_SECRET", "url-user", "proxy-user"]:
            self.assertNotIn(secret, text)

    def test_transport_diagnostics_capture_real_requests_exception_chain_and_independent_probe(self):
        with requests.Session() as session:
            session.trust_env = False
            with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror(11002, "simulated failure")):
                with self.assertRaises(requests.ConnectionError) as original:
                    session.get("https://example.invalid", timeout=1)
        logger = logging.getLogger("bulkloader.diagnostic.tests")
        probe_result = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.0.2.1", 443))]
        for probe in [probe_result, socket.gaierror(11002, "still failing")]:
            self.request.reset_mock()
            self.request.side_effect = original.exception
            probe_options = {"side_effect": probe} if isinstance(probe, OSError) else {"return_value": probe}
            with self.subTest(probe=probe), patch.object(
                loader, "appLogger", logger
            ), patch.object(socket, "getaddrinfo", **probe_options) as lookup:
                with self.assertLogs(logger, level="DEBUG") as captured:
                    with self.assertRaises(requests.ConnectionError) as raised:
                        loader.api_request(
                            "POST", "https://url-user:URL_SECRET@example.invalid/endpoint?key=QUERY_SECRET",
                            "old-token",
                        )
                self.assertIs(raised.exception, original.exception)
                self.request.assert_called_once()
                lookup.assert_called_once_with("example.invalid", 443, type=socket.SOCK_STREAM)
                text = "\n".join(captured.output)
                for expected in ["ConnectionError", "NameResolutionError", "gaierror", "11002", "Independent resolver probe"]:
                    self.assertIn(expected, text)
                for secret in ["URL_SECRET", "QUERY_SECRET", "url-user", "old-token"]:
                    self.assertNotIn(secret, text)
                if not isinstance(probe, OSError):
                    self.assertIn("192.0.2.1", text)
                    self.assertIn("does not establish why", text)
        loader.time.sleep.assert_not_called()

    def test_standard_mode_does_not_run_extra_resolver_lookups(self):
        self.request.side_effect = requests.ConnectionError("simulated failure")
        with patch.object(socket, "getaddrinfo") as lookup:
            with self.assertRaises(requests.ConnectionError):
                loader.api_request("GET", "https://example.invalid", "old-token")
        lookup.assert_not_called()
        self.request.assert_called_once()

    def test_token_preflight_distinguishes_transport_failure_from_token_rejection(self):
        for error in [requests.ConnectionError("connection failed"), requests.Timeout("timed out")]:
            self.request.side_effect = error
            with self.subTest(error=type(error).__name__):
                self.assertFalse(loader.test_access_token("old-token"))
                message = loader.appLogger.error.call_args.args[0]
                self.assertIn("transport failure, not evidence that the token was rejected", message)
                self.assertIn("--verbose", message)
        loader.get_access_token.assert_not_called()

    def test_successful_request_logs_status_without_token_or_query(self):
        self.request.side_effect = [response(200)]
        logger = logging.getLogger("bulkloader.diagnostic.tests")
        with patch.object(loader, "appLogger", logger), self.assertLogs(logger, level="DEBUG") as captured:
            loader.api_request("GET", "https://example.invalid/endpoint?key=QUERY_SECRET", "old-token")
        text = "\n".join(captured.output)
        self.assertIn("status=200", text)
        self.assertIn("elapsed=", text)
        self.assertNotIn("old-token", text)
        self.assertNotIn("QUERY_SECRET", text)


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

    def test_stale_hours_controls_incomplete_cutoff(self):
        now = datetime(2026, 1, 2, tzinfo=timezone.utc)
        docs = [
            document(name=name, id=name, percent=20, status="Processing",
                     last_updated=(now - timedelta(hours=hours)).isoformat())
            for name, hours in [("two-hours", 2), ("thirty-min", 0.5)]
        ]
        for stale_hours, expected in [(12, set()), (1, {"two-hours"}), (0, {"two-hours", "thirty-min"})]:
            with self.subTest(stale_hours=stale_hours), patch.object(
                loader, "datetime", wraps=datetime
            ) as clock, patch.object(loader, "delete_workspace_file", return_value=True):
                clock.now.return_value = now
                deleted = loader.delete_failed_workspace_files(
                    docs, "u", "w", "public", "old-token", stale_hours=stale_hours
                )
            self.assertEqual({doc["id"] for doc in deleted}, expected)

    def test_stale_hours_cli_validation(self):
        for args in (["-a", "clean", "--stale-hours", "-1"], ["-a", "list", "--stale-hours", "1"]):
            with self.subTest(args=args), patch("sys.argv", [
                "main.py", *args, "-u", "u", "-w", "w", "-s", "public",
            ]), patch("sys.stderr"), self.assertRaises(SystemExit) as error:
                loader.main()
            self.assertEqual(error.exception.code, 2)

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

    def test_cleanup_logs_names_ids_and_reasons_before_deletion(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
        docs = [
            document(name="failed.pdf", id="failed", percent=0, status="Error: failed"),
            document(name="stale.pdf", id="stale", percent=20, status="Processing", last_updated=old),
            document(name="complete.pdf", id="complete"),
        ]
        logger = logging.getLogger("bulkloader.cleanup.tests")
        self.request.side_effect = [response(204), response(200)]
        with patch.object(loader, "appLogger", logger), self.assertLogs(logger, level="INFO") as captured:
            deleted = self.clean(docs)
        text = "\n".join(captured.output)
        self.assertIn("Working on cleanup:", text)
        self.assertIn("Cleaning failed.pdf (document failed): error status.", text)
        self.assertIn("Cleaning stale.pdf (document stale): incomplete and last updated more than 12 hours ago.", text)
        self.assertLess(text.index("Cleaning failed.pdf"), text.index("Deleted document failed."))
        self.assertLess(text.index("Cleaning stale.pdf"), text.index("Deleted document stale."))
        self.assertNotIn("complete.pdf", text)
        self.assertIn("Deleted 2 stale/failed incomplete documents.", text)
        self.assertEqual(len(deleted), 2)

    def test_failed_delete_does_not_log_success(self):
        logger = logging.getLogger("bulkloader.cleanup.tests")
        self.request.side_effect = [response(500)]
        with patch.object(loader, "appLogger", logger), self.assertLogs(logger, level="INFO") as captured:
            with self.assertRaises(requests.HTTPError):
                self.clean([document(name="failed.pdf", percent=0, status="Error: failed")])
        text = "\n".join(captured.output)
        self.assertIn("Cleaning failed.pdf", text)
        self.assertNotIn("Deleted ", text)

    def test_duplicate_cleanup_logs_deleted_and_retained_ids(self):
        logger = logging.getLogger("bulkloader.cleanup.tests")
        self.request.side_effect = [response(204)]
        with patch.object(loader, "appLogger", logger), self.assertLogs(logger, level="INFO") as captured:
            loader.delete_duplicate_workspace_files(
                [document(id="keep"), document(id="remove")], "u", "w", "public", "old-token"
            )
        text = "\n".join(captured.output)
        self.assertIn("Working on duplicate cleanup:", text)
        self.assertIn("Cleaning duplicate report.pdf (document remove); retaining document keep.", text)
        self.assertIn("Deleted document remove.", text)


class LoggingTests(LoaderTestCase):
    def check_logging(self, verbose):
        root = logging.RootLogger(logging.WARNING)
        loggers = {}

        def get_logger(name=None):
            if name is None:
                return root
            if name not in loggers:
                loggers[name] = logging.Logger(name)
                loggers[name].parent = root
            return loggers[name]

        app_logger = get_logger("bulkloader_main")
        try:
            with patch.object(logging, "root", root), patch.object(
                logging, "getLogger", side_effect=get_logger
            ), patch.object(loader, "appLogger", app_logger), patch.multiple(
                loader,
                appLogname=str(self.directory / "application.log"),
                success_fileLogname=str(self.directory / "success.log"),
                ignored_fileLogname=str(self.directory / "ignored.log"),
            ), patch("sys.stdout", new_callable=io.StringIO) as output:
                if verbose:
                    loader.configure_logging(verbose=True)
                else:
                    loader.configure_logging()
                self.request.side_effect = [
                    listing([document()]),
                    listing([]),
                    response(payload={"document_ids": ["new"], "errors": []}),
                    listing([document(percent=0, status="Processing")]),
                    response(204),
                ]
                self.assert_outcome(self.upload(), "skipped")
                self.assert_outcome(self.upload(), "queued")
                self.assert_outcome(self.upload(), "blocked")
                loader.delete_failed_workspace_files(
                    [
                        document(name="failed.pdf", id="failed", percent=0, status="Error: failed"),
                        document(name="healthy.pdf", id="healthy"),
                        document(
                            name="recent.pdf", id="recent", percent=10, status="Processing",
                            last_updated=datetime.now(timezone.utc).isoformat(),
                        ),
                    ],
                    "u", "w", "public", "old-token",
                )
                get_logger("urllib3.connectionpool").debug("HTTP diagnostic marker")
                get_logger("msal").debug("Identity diagnostic marker")
                app_logger.error("Upload error marker")
                app_logger.info("Upload summary: queued=1, skipped=1, blocked=1, failed=0")
                console = output.getvalue()
                application_log = Path(loader.appLogname).read_text(encoding="utf-8")
                for text in [console, application_log]:
                    self.assertIn("Upload summary:", text)
                    self.assertIn("Blocked report.pdf", text)
                    self.assertIn("Upload error marker", text)
                    self.assertIn("Working on cleanup:", text)
                    self.assertIn("Cleaning failed.pdf (document failed): error status.", text)
                    self.assertIn("Deleted document failed.", text)
                    self.assertIn("Queued report.pdf as document new", text)
                    self.assertNotIn("Identity diagnostic marker", text)
                    for message in [
                        "Skipping completed exact filename match:",
                        "Preserving healthy.pdf",
                        "Preserving recent.pdf",
                        "HTTP diagnostic marker",
                    ]:
                        if verbose:
                            self.assertIn(message, text)
                        else:
                            self.assertNotIn(message, text)
                    self.assertNotIn("skipped: ", text)
                    self.assertNotIn("blocked: ", text)
                success = Path(loader.success_fileLogname).read_text(encoding="utf-8")
                ignored = Path(loader.ignored_fileLogname).read_text(encoding="utf-8")
                self.assertEqual(success.splitlines(), [str(self.file)])
                self.assertIn(f"skipped: {self.file}", ignored)
                self.assertIn(f"blocked: {self.file}", ignored)
        finally:
            for logger in [root, *loggers.values()]:
                for handler in logger.handlers:
                    handler.close()

    def test_standard_logging_shows_changes_but_hides_existing_and_preserved_files(self):
        self.check_logging(verbose=False)

    def test_verbose_logging_includes_files_and_http_diagnostics_without_duplicate_audit_logs(self):
        self.check_logging(verbose=True)


class CommandTests(LoaderTestCase):
    def test_all_actions_log_start_authentication_and_api_preflight(self):
        for action in ["upload", "list", "clean", "delete"]:
            loader.appLogger.reset_mock()
            args = ["-a", action]
            if action != "upload":
                args.extend(["-s", "public", "-w", "workspace", "-u", "user"])
            with self.subTest(action=action), patch.object(
                loader, "get_workspace_files", return_value=[]
            ), patch.object(loader, "read_csv_ignore_header", return_value=Counter()), patch(
                "sys.stdout", new_callable=io.StringIO
            ):
                self.assertEqual(self.run_main(args), 0)
                loader.appLogger.info.assert_any_call("Starting %s operation.", action)
                loader.appLogger.info.assert_any_call("Authenticating using %s mode.", "application")
                loader.appLogger.info.assert_any_call("Checking API access before starting document operations.")

    def test_listing_logs_bounded_progress_but_filename_lookups_stay_verbose(self):
        self.request.side_effect = [
            listing(
                [document(id=f"{page}-{item}") for item in range(25 if page < 11 else 1)],
                page=page, total=251,
            )
            for page in range(1, 12)
        ]
        loader.get_workspace_files("u", "w", "public", "old-token")
        loader.appLogger.info.assert_any_call("Working on document listing for %s workspace %s.", "public", "w")
        progress = [
            call.args[1] for call in loader.appLogger.info.call_args_list
            if call.args[0].startswith("Listing progress:")
        ]
        self.assertEqual(progress, [1, 10, 11])
        loader.appLogger.reset_mock()
        self.request.side_effect = [listing([])]
        loader.get_workspace_files("u", "w", "public", "old-token", search="report.pdf")
        loader.appLogger.info.assert_not_called()
        loader.appLogger.debug.assert_any_call(
            "Working on filename lookup: %s in %s workspace %s.", "report.pdf", "public", "w"
        )

    def test_incomplete_only_filters_status_not_percentage(self):
        docs = [
            document(name="complete.pdf"),
            document(name="complete-case.pdf", status="  PROCESSING COMPLETE  "),
            document(name="complete-status.pdf", percent=50),
            document(name="pending.pdf", percent=0, status="Queued for processing"),
            document(name="processing.pdf", percent=59, status="Saving chunks"),
            document(name="failed.pdf", percent=100, status="Error: failed"),
            document(name="unknown.pdf", percent=None, status=None),
            document(name="missing-status.pdf"),
        ]
        del docs[-1]["status"]
        for scope in ["public", "group"]:
            with self.subTest(scope=scope), patch.object(
                loader, "get_workspace_files", return_value=docs
            ), patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
                loader, "upload_document"
            ) as upload, patch.object(loader, "delete_workspace_file") as delete:
                self.assertEqual(self.run_main([
                    "-a", "list", "-u", "u", "-w", "w", "-s", scope, "--incomplete-only",
                ]), 0)
                text = output.getvalue()
                for name in ["complete.pdf", "complete-case.pdf", "complete-status.pdf"]:
                    self.assertNotIn(name, text)
                for name in ["pending.pdf", "processing.pdf", "failed.pdf", "unknown.pdf", "missing-status.pdf"]:
                    self.assertIn(name, text)
                self.assertIn("Total documents: 5 (filtered; workspace total: 8)", text)
                upload.assert_not_called()
                delete.assert_not_called()

    def test_incomplete_only_still_fetches_later_pages(self):
        self.request.side_effect = [
            listing([document(name=f"done-{index}.pdf") for index in range(25)], total=26),
            listing([document(name="pending.pdf", status="Queued for processing", percent=0)],
                    page=2, total=26),
        ]
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.run_main([
                "-a", "list", "-u", "u", "-w", "w", "-s", "public", "--incomplete-only",
            ]), 0)
        text = output.getvalue()
        self.assertIn("pending.pdf", text)
        self.assertNotIn("done-", text)
        self.assertIn("Total documents: 1 (filtered; workspace total: 26)", text)
        self.assertEqual(self.request.call_count, 2)

    def test_incomplete_only_empty_or_all_complete(self):
        for docs in [[], [document()]]:
            with self.subTest(docs=docs), patch.object(
                loader, "get_workspace_files", return_value=docs
            ), patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(self.run_main([
                    "-a", "list", "-u", "u", "-w", "w", "-s", "public", "--incomplete-only",
                ]), 0)
                self.assertIn(
                    f"Total documents: 0 (filtered; workspace total: {len(docs)})",
                    output.getvalue(),
                )
                self.assertNotIn("report.pdf", output.getvalue())

    def test_incomplete_only_rejected_for_other_actions(self):
        for action in ["upload", "clean", "delete"]:
            with self.subTest(action=action), patch("sys.argv", [
                "main.py", "-a", action, "-u", "u", "-w", "w", "-s", "public", "--incomplete-only",
            ]), patch("sys.stderr"), self.assertRaises(SystemExit) as error:
                loader.main()
            self.assertEqual(error.exception.code, 2)
        self.request.assert_not_called()

    def test_list_reads_all_pages_for_both_scopes_without_modifications(self):
        for scope, key, url in [
            ("public", "active_workspace_id", loader.PUBLIC_DOCUMENTS_LIST_URL),
            ("group", "active_group_id", loader.GROUP_DOCUMENTS_LIST_URL),
        ]:
            with self.subTest(scope=scope):
                docs = [document(name=f"report-{index}.pdf", id=f"id-{index}") for index in range(25)]
                self.request.reset_mock()
                self.request.side_effect = [
                    listing(docs, total=26),
                    listing([document(name="pending.pdf", percent=25, status="Processing",
                                      last_updated="2026-11-11T12:00:00Z")], page=2, total=26),
                ]
                with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
                    loader, "upload_document"
                ) as upload, patch.object(loader, "delete_workspace_file") as delete:
                    result = self.run_main([
                        "--action", "list", "-u", "u", "-w", "w", "-s", scope,
                    ])
                self.assertEqual(result, 0)
                text = output.getvalue()
                self.assertIn("Filename\tDocument ID\tStatus\tCompletion\tLast updated", text)
                self.assertIn("report-24.pdf\tid-24\tProcessing complete\t100%\tUnknown", text)
                self.assertIn("pending.pdf\tdocument-id\tProcessing\t25%\t2026-11-11T12:00:00Z", text)
                self.assertIn("Total documents: 26", text)
                self.assertEqual(self.request.call_count, 2)
                for call in self.request.call_args_list:
                    self.assertEqual(call.args[:2], ("GET", url))
                    self.assertEqual(call.kwargs["params"][key], "w")
                    self.assertEqual(call.kwargs["params"]["user_id"], "u")
                upload.assert_not_called()
                delete.assert_not_called()

    def test_list_empty_workspace_and_unknown_metadata(self):
        for docs in [[], [{"file_name": "legacy.pdf"}]]:
            with self.subTest(docs=docs), patch.object(
                loader, "get_workspace_files", return_value=docs
            ), patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(self.run_main([
                    "-a", "list", "-u", "u", "-w", "w", "-s", "public",
                ]), 0)
                self.assertIn(f"Total documents: {len(docs)}", output.getvalue())
                if docs:
                    self.assertIn("legacy.pdf\tUnknown\tUnknown\tUnknown\tUnknown", output.getvalue())

    def test_list_failure_is_not_printed_as_empty_success(self):
        self.request.side_effect = [response(500)]
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.run_main([
                "-a", "list", "-u", "u", "-w", "w", "-s", "public",
            ]), 1)
        self.assertNotIn("Total documents:", output.getvalue())
        loader.appLogger.error.assert_called()

    def test_list_requires_workspace_arguments_and_disallows_duplicate_removal(self):
        for args in [
            ["-a", "list"],
            ["-a", "list", "-w", "w"],
            ["-a", "list", "-s", "public"],
            ["-a", "list", "-w", "w", "-s", "public", "--removeduplicates"],
        ]:
            with self.subTest(args=args), patch("sys.argv", ["main.py"] + args), patch("sys.stderr"):
                with self.assertRaises(SystemExit) as error:
                    loader.main()
                self.assertEqual(error.exception.code, 2)

    def test_list_preserves_optional_user_id_for_compatibility(self):
        self.request.side_effect = [listing([])]
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(self.run_main([
                "-a", "list", "-u", "u", "-w", "w", "-s", "public",
            ]), 0)
        self.assertEqual(self.request.call_args.kwargs["params"]["user_id"], "u")

    def test_write_operations_still_require_user_id(self):
        for action in ["clean", "delete"]:
            with self.subTest(action=action), patch("sys.argv", [
                "main.py", "-a", action, "-w", "w", "-s", "public",
            ]), patch("sys.stderr"):
                with self.assertRaises(SystemExit) as error:
                    loader.main()
                self.assertEqual(error.exception.code, 2)
        with self.assertRaisesRegex(ValueError, "User ID"):
            loader.delete_workspace_file("id", None, "w", "public", "old-token")
        self.assert_outcome(
            loader.upload_document(str(self.file), None, "public", "w", "", "old-token"),
            "failed",
        )
        self.request.assert_not_called()

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

    def run_main(self, args, **settings):
        env = {
            "AUTHORITY_URL": "https://login.example.invalid",
            "TENANT_ID": "11111111-1111-1111-1111-111111111111", "CLIENT_ID": "client", "CLIENT_SECRET": "secret",
            "API_SCOPE": "api://example/.default", "API_BASE_URL": "https://example.invalid",
            "UPLOAD_DIRECTORY": str(self.directory),
        }
        env.update(settings)
        with patch.multiple(loader, **env), patch.object(loader, "configure_logging"), patch.object(
            loader, "test_access_token", return_value=True
        ), patch("sys.argv", ["main.py"] + args):
            return loader.main()

    def test_direct_upload_uses_requested_folder_and_destination_without_csv(self):
        folder = self.directory / "folder with spaces"
        folder.mkdir()
        for scope in ["public", "group"]:
            for classification in [None, "Internal"]:
                with self.subTest(scope=scope, classification=classification), patch.object(
                    loader, "read_files_in_directory", return_value=Counter(queued=1)
                ) as read, patch.object(loader, "read_csv_ignore_header") as mapping, patch.object(
                    loader, "delete_failed_workspace_files"
                ) as clean:
                    args = [
                        "-a", "upload", "--folder", str(folder), "-w", "workspace",
                        "-s", scope, "-u", "user",
                    ]
                    if classification is not None:
                        args.extend(["--classification", classification])
                    self.assertEqual(self.run_main(args, UPLOAD_DIRECTORY=None), 0)
                    read.assert_called_once_with(
                        str(folder), "user", scope, "workspace", classification or "", "new-token"
                    )
                    mapping.assert_not_called()
                    clean.assert_not_called()

    def test_direct_upload_preserves_outcome_exit_codes(self):
        for summary, expected in [
            (Counter(queued=1, skipped=1), 0),
            (Counter(blocked=1), 1),
            (Counter(failed=1), 1),
            (Counter(), 0),
        ]:
            with self.subTest(summary=summary), patch.object(
                loader, "read_files_in_directory", return_value=summary
            ):
                self.assertEqual(self.run_main([
                    "-a", "upload", "--folder", str(self.directory),
                    "-w", "workspace", "-s", "public", "-u", "user",
                ], UPLOAD_DIRECTORY="missing-base-directory"), expected)

    def test_direct_upload_uses_relative_path_and_skips_subdirectories(self):
        nested = self.directory / "nested"
        nested.mkdir()
        (nested / "ignored.pdf").write_bytes(b"nested file")
        folder = os.path.relpath(self.directory)
        with patch.object(loader, "upload_document", return_value=loader.UploadOutcome.QUEUED) as upload:
            self.assertEqual(self.run_main([
                "-a", "upload", "--folder", folder, "-w", "workspace",
                "-s", "public", "-u", "user",
            ], UPLOAD_DIRECTORY=None), 0)
        upload.assert_called_once_with(str(self.file), "user", "public", "workspace", "", "new-token")
        self.request.assert_not_called()

    def test_direct_upload_requires_destination_and_application_user_before_authentication(self):
        for flags in [
            [],
            ["-w", "workspace"],
            ["-w", "workspace", "-s", "public"],
            ["-w", "workspace", "-s", "public", "-u", " "],
            ["-w", " ", "-s", "public", "-u", "user"],
        ]:
            with self.subTest(flags=flags), patch("sys.stderr"), self.assertRaises(SystemExit) as error:
                self.run_main(["-a", "upload", "--folder", str(self.directory)] + flags)
            self.assertEqual(error.exception.code, 2)
        loader.get_access_token.assert_not_called()
        self.request.assert_not_called()

    def test_direct_upload_rejects_missing_folder_before_authentication(self):
        self.assertEqual(self.run_main([
            "-a", "upload", "--folder", str(self.directory / "missing"),
            "-w", "workspace", "-s", "public", "-u", "user",
        ]), 1)
        loader.get_access_token.assert_not_called()
        loader.appLogger.error.assert_called()
        self.request.assert_not_called()

    def test_upload_options_are_not_silently_ignored(self):
        for args in [
            ["-a", "upload", "-w", "workspace"],
            ["-a", "upload", "-s", "public"],
            ["-a", "upload", "-u", "user"],
            ["-a", "upload", "--classification", "Internal"],
            ["-a", "upload", "--folder", "", "-w", "workspace", "-s", "public", "-u", "user"],
            ["-a", "list", "--folder", str(self.directory), "-w", "workspace", "-s", "public"],
            ["-a", "clean", "--classification", "Internal", "-w", "workspace", "-s", "public", "-u", "user"],
        ]:
            with self.subTest(args=args), patch("sys.stderr"), self.assertRaises(SystemExit) as error:
                self.run_main(args)
            self.assertEqual(error.exception.code, 2)
        loader.get_access_token.assert_not_called()
        self.request.assert_not_called()

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

    def test_verbose_flag_selects_logging_mode(self):
        for flags, verbose in [([], False), (["--verbose"], True), (["-v"], True)]:
            with self.subTest(flags=flags), patch.object(
                loader, "configure_logging"
            ) as configure, patch.object(loader, "validate_configuration"), patch.object(
                loader, "test_access_token", return_value=True
            ), patch.object(loader, "read_csv_ignore_header", return_value=Counter()), patch(
                "sys.argv", ["main.py", "--action", "upload"] + flags
            ):
                self.assertEqual(loader.main(), 0)
                configure.assert_called_once_with(verbose)

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


class InteractiveAuthenticationTests(unittest.TestCase):
    TENANT = "11111111-1111-1111-1111-111111111111"
    USER = "22222222-2222-2222-2222-222222222222"
    CLIENT = "33333333-3333-3333-3333-333333333333"
    SCOPE = "api://simplechat/access_as_user"

    def setUp(self):
        self.enterContext(patch.multiple(
            loader,
            TENANT_ID=self.TENANT,
            CLIENT_ID=None,
            CLIENT_SECRET=None,
            API_SCOPE=None,
            INTERACTIVE_CLIENT_ID=self.CLIENT,
            INTERACTIVE_API_SCOPE=self.SCOPE,
            AUTHORITY_URL="https://login.microsoftonline.us",
            API_BASE_URL="https://example.invalid",
            g_INTERACTIVE_AUTH=None,
        ))
        self.log = self.enterContext(patch.object(loader, "appLogger"))
        self.public = self.enterContext(patch.object(loader, "PublicClientApplication"))
        self.confidential = self.enterContext(patch.object(loader, "ConfidentialClientApplication"))
        self.client = self.public.return_value
        self.result = {
            "access_token": "delegated-token",
            "id_token_claims": {"oid": self.USER, "tid": self.TENANT},
        }
        self.account = {"local_account_id": self.USER, "realm": self.TENANT}
        self.client.get_accounts.return_value = [self.account]
        self.client.acquire_token_interactive.return_value = self.result
        self.client.acquire_token_silent.return_value = {"access_token": "refreshed-token"}
        self.client.initiate_device_flow.return_value = {
            "user_code": "TEST-CODE", "message": "Use TEST-CODE at the test sign-in page",
        }
        self.client.acquire_token_by_device_flow.return_value = self.result

    def test_browser_login_discovers_oid_and_uses_public_client_without_secret(self):
        auth = loader.InteractiveAuthentication("interactive")
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(auth.acquire_token(), "delegated-token")
        self.assertEqual(auth.user_id, self.USER)
        self.public.assert_called_once_with(
            client_id=self.CLIENT, authority=f"https://login.microsoftonline.us/{self.TENANT}"
        )
        self.client.acquire_token_interactive.assert_called_once_with(
            scopes=[self.SCOPE], prompt="select_account", timeout=180
        )
        self.client.initiate_device_flow.assert_not_called()
        self.confidential.assert_not_called()

    def test_device_code_mode_does_not_open_browser_or_log_code(self):
        auth = loader.InteractiveAuthentication("device-code")
        with patch("sys.stdout", new_callable=io.StringIO) as terminal:
            self.assertEqual(auth.acquire_token(), "delegated-token")
        self.assertIn("TEST-CODE", terminal.getvalue())
        self.assertNotIn("TEST-CODE", str(self.log.mock_calls))
        self.client.acquire_token_interactive.assert_not_called()
        self.client.initiate_device_flow.assert_called_once_with(scopes=[self.SCOPE])

    def test_browser_startup_and_timeout_fall_back_explicitly(self):
        for error in [OSError("cannot bind callback"), RuntimeError("browser timed out"),
                      loader.webbrowser.Error("no browser")]:
            with self.subTest(error=error):
                self.client.reset_mock()
                self.client.acquire_token_interactive.side_effect = error
                auth = loader.InteractiveAuthentication("interactive")
                with patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(auth.acquire_token(), "delegated-token")
                self.client.initiate_device_flow.assert_called_once()
                self.log.warning.assert_called()

    def test_denial_and_bad_state_do_not_trigger_fallback(self):
        for result in [{"error": "access_denied", "error_description": "User cancelled"}, None]:
            with self.subTest(result=result):
                self.client.acquire_token_interactive.return_value = result
                auth = loader.InteractiveAuthentication("interactive")
                with patch("sys.stdout", new_callable=io.StringIO), self.assertRaises(RuntimeError):
                    auth.acquire_token()
                self.client.initiate_device_flow.assert_not_called()
        self.client.acquire_token_interactive.side_effect = ValueError("state mismatch")
        with patch("sys.stdout", new_callable=io.StringIO), self.assertRaises(ValueError):
            loader.InteractiveAuthentication("interactive").acquire_token()
        self.client.initiate_device_flow.assert_not_called()

    def test_invalid_claims_and_wrong_tenant_fail(self):
        for claims in [
            None, {}, {"oid": "not-an-id", "tid": self.TENANT},
            {"oid": self.USER, "tid": self.CLIENT},
            {"oid": self.USER},
        ]:
            with self.subTest(claims=claims):
                self.client.acquire_token_interactive.return_value = {
                    "access_token": "token", "id_token_claims": claims,
                }
                with patch("sys.stdout", new_callable=io.StringIO), self.assertRaises(ValueError):
                    loader.InteractiveAuthentication("interactive").acquire_token()

    def test_missing_cached_account_fails(self):
        self.client.get_accounts.return_value = []
        with patch("sys.stdout", new_callable=io.StringIO), self.assertRaises(RuntimeError):
            loader.InteractiveAuthentication("interactive").acquire_token()

    def test_get_access_token_refreshes_same_account_without_application_credentials(self):
        auth = loader.InteractiveAuthentication("interactive")
        with patch.object(loader, "g_INTERACTIVE_AUTH", auth), patch(
            "sys.stdout", new_callable=io.StringIO
        ):
            self.assertEqual(loader.get_access_token(), "delegated-token")
            self.assertEqual(loader.get_access_token(), "refreshed-token")
        self.client.acquire_token_silent.assert_called_once_with(
            [self.SCOPE], account=self.account, force_refresh=True
        )
        self.assertEqual(auth.user_id, self.USER)
        self.client.acquire_token_interactive.assert_called_once()
        self.confidential.assert_not_called()

    def test_refresh_failure_or_identity_change_aborts_without_prompt(self):
        for result in [
            None, {"error": "interaction_required"},
            {"access_token": "token", "id_token_claims": {"oid": self.CLIENT, "tid": self.TENANT}},
            {"access_token": "token", "id_token_claims": {"oid": self.USER, "tid": self.CLIENT}},
        ]:
            with self.subTest(result=result):
                auth = loader.InteractiveAuthentication("device-code")
                with patch("sys.stdout", new_callable=io.StringIO):
                    auth.acquire_token()
                self.client.acquire_token_silent.return_value = result
                with self.assertRaises((RuntimeError, ValueError)):
                    auth.acquire_token()
                self.assertEqual(auth.user_id, self.USER)
        self.client.acquire_token_interactive.assert_not_called()

    def test_invalid_or_expired_device_flow_reports_error(self):
        self.client.initiate_device_flow.return_value = {"error": "invalid_client"}
        with self.assertRaisesRegex(RuntimeError, "invalid_client"):
            loader.InteractiveAuthentication("device-code").acquire_token()
        self.client.acquire_token_by_device_flow.assert_not_called()
        self.client.initiate_device_flow.return_value = {"user_code": "CODE", "message": "Instructions"}
        self.client.acquire_token_by_device_flow.return_value = {"error": "expired_token"}
        with patch("sys.stdout", new_callable=io.StringIO), self.assertRaisesRegex(RuntimeError, "expired_token"):
            loader.InteractiveAuthentication("device-code").acquire_token()

    def test_interactive_configuration_needs_no_client_secret_or_application_scope(self):
        loader.validate_configuration("list", "interactive")
        with self.assertRaises(ValueError):
            loader.validate_configuration("list", "application")

    def test_interactive_scope_must_target_explicit_custom_api_scope(self):
        for scope in [None, "User.Read", "https://graph.microsoft.com/User.Read",
                      "api://simplechat/.default", "api://simplechat/access other", "api://simplechat/"]:
            with self.subTest(scope=scope), patch.object(loader, "INTERACTIVE_API_SCOPE", scope):
                with self.assertRaises(ValueError):
                    loader.validate_configuration("list", "interactive")

    def test_public_client_id_can_use_existing_client_id(self):
        with patch.object(loader, "INTERACTIVE_CLIENT_ID", None), patch.object(loader, "CLIENT_ID", self.CLIENT):
            loader.validate_configuration("list", "interactive")
            loader.InteractiveAuthentication("interactive")
        self.assertEqual(self.public.call_args.kwargs["client_id"], self.CLIENT)

    def test_application_mode_still_uses_confidential_client(self):
        self.confidential.return_value.acquire_token_for_client.return_value = {"access_token": "app-token"}
        with patch.multiple(loader, CLIENT_ID=self.CLIENT, CLIENT_SECRET="test-secret", API_SCOPE="api://simplechat/.default"):
            self.assertEqual(loader.get_access_token(), "app-token")
        self.confidential.assert_called_once()
        self.public.assert_not_called()

    def test_interactive_command_calls_api_with_delegated_token_and_refreshes(self):
        with patch.object(loader, "configure_logging"), patch.object(
            loader.requests, "request", side_effect=[response(200), response(401), listing([])]
        ) as request, patch.object(loader, "g_ACCESS_TOKEN", None), patch("sys.argv", [
            "main.py", "--auth", "interactive", "--action", "list", "-s", "public", "-w", "workspace",
        ]), patch("sys.stdout", new_callable=io.StringIO) as terminal:
            self.assertEqual(loader.main(), 0)
        self.assertIn("Total documents: 0", terminal.getvalue())
        self.assertEqual([call.kwargs["headers"]["Authorization"] for call in request.call_args_list], [
            "Bearer delegated-token", "Bearer delegated-token", "Bearer refreshed-token",
        ])
        self.assertEqual(request.call_args.kwargs["params"]["user_id"], self.USER)
        self.client.acquire_token_silent.assert_called_once()
        self.confidential.assert_not_called()

    def test_interactive_api_denial_stops_without_downgrading_to_application_auth(self):
        with patch.object(loader, "configure_logging"), patch.object(
            loader.requests, "request", return_value=response(403)
        ) as request, patch("sys.argv", [
            "main.py", "--auth", "interactive", "--action", "list", "-s", "public", "-w", "workspace",
        ]), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(loader.main(), 1)
        request.assert_called_once()
        self.confidential.assert_not_called()
        self.log.error.assert_called()


class InteractiveCommandTests(LoaderTestCase):
    run_main = CommandTests.run_main
    USER = InteractiveAuthenticationTests.USER

    def setUp(self):
        super().setUp()
        self.auth_class = self.enterContext(patch.object(loader, "InteractiveAuthentication"))
        self.auth_class.return_value.user_id = self.USER
        self.enterContext(patch.multiple(
            loader, INTERACTIVE_CLIENT_ID="public-client",
            INTERACTIVE_API_SCOPE="api://simplechat/access_as_user",
        ))

    def test_list_uses_discovered_identity_and_never_uses_client_credentials(self):
        self.request.side_effect = [listing([])]
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(self.run_main([
                "-a", "list", "--auth", "interactive", "-s", "public", "-w", "workspace",
            ]), 0)
        self.auth_class.assert_called_once_with("interactive")
        self.assertEqual(self.request.call_args.kwargs["params"]["user_id"], self.USER)

    def test_clean_passes_signed_in_identity_without_explicit_userid(self):
        with patch.object(loader, "get_workspace_files", return_value=[]), patch.object(
            loader, "delete_failed_workspace_files", return_value=[]
        ) as clean:
            self.assertEqual(self.run_main([
                "-a", "clean", "--auth", "device-code", "-s", "group", "-w", "workspace",
            ]), 0)
        self.auth_class.assert_called_once_with("device-code")
        self.assertEqual(clean.call_args.args[1], self.USER)

    def test_upload_uses_signed_in_identity_for_every_mapping(self):
        mapping = self.directory / "map.csv"
        mapping.write_text(
            "folderName,userId,workspaceScope,workspaceId,classification\n"
            ".,old-user,public,w,\n.,,group,w,\n"
        )
        with patch.object(loader, "UPLOAD_DIRECTORY", str(self.directory)), patch.object(
            loader, "read_files_in_directory", return_value=Counter(queued=1)
        ) as read:
            summary = loader.read_csv_ignore_header(str(mapping), override_user_id=self.USER)
        self.assertEqual(summary["queued"], 2)
        self.assertEqual([call.args[1] for call in read.call_args_list], [self.USER, self.USER])

    def test_upload_command_passes_identity_override(self):
        with patch.object(loader, "read_csv_ignore_header", return_value=Counter()) as read:
            self.assertEqual(self.run_main(["-a", "upload", "--auth", "interactive"]), 0)
        read.assert_called_once_with("map.csv", override_user_id=self.USER)

    def test_direct_upload_uses_signed_in_identity_without_csv_or_upload_directory(self):
        for mode in ["interactive", "device-code"]:
            with self.subTest(mode=mode), patch.object(
                loader, "read_files_in_directory", return_value=Counter(queued=1)
            ) as read, patch.object(loader, "read_csv_ignore_header") as mapping:
                self.assertEqual(self.run_main([
                    "-a", "upload", "--auth", mode, "--folder", str(self.directory),
                    "-w", "workspace", "-s", "group",
                ], UPLOAD_DIRECTORY=None, CLIENT_SECRET=None), 0)
                read.assert_called_once_with(
                    str(self.directory), self.USER, "group", "workspace", "", "new-token"
                )
                mapping.assert_not_called()

    def test_explicit_userid_is_rejected_in_interactive_mode(self):
        with patch("sys.argv", [
            "main.py", "-a", "clean", "--auth", "interactive", "-u", "different-user", "-s", "group", "-w", "w",
        ]), patch("sys.stderr"), self.assertRaises(SystemExit) as error:
            loader.main()
        self.assertEqual(error.exception.code, 2)
        self.auth_class.assert_not_called()

    def test_cancelled_sign_in_stops_before_api_requests(self):
        loader.get_access_token.side_effect = KeyboardInterrupt
        self.assertEqual(self.run_main([
            "-a", "list", "--auth", "interactive", "-s", "public", "-w", "w",
        ]), 130)
        self.request.assert_not_called()


class GroupRouteTests(unittest.TestCase):
    def test_group_upload_schedules_processing_once(self):
        path = LOADER_DIRECTORY.parents[1] / "single_app" / "route_external_group_documents.py"
        if not path.exists():
            self.skipTest("route_external_group_documents.py is not part of this build")
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
