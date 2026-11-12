# test_pdf_batch_ingestion.py
"""Offline PDF batching regressions and request-count benchmark.

Version: 0.261.063
Implemented in: 0.261.063

Execute real ingestion functions with Azure I/O replaced, without importing app config.
"""

import ast
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import time
import traceback
from unittest.mock import Mock

import pytest


APP_ROOT = Path(__file__).resolve().parents[1] / "application" / "single_app"


class RateLimited(Exception):
    pass


def load_functions(file_name, names, namespace):
    tree = ast.parse((APP_ROOT / file_name).read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "VISION_ANALYSIS_CONTENT_FIELDS"
            for target in node.targets
        ):
            exec(compile(ast.Module(body=[node], type_ignores=[]), file_name, "exec"), namespace)
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), file_name, "exec"), namespace)
            found.add(node.name)
    assert found == set(names)


def embedding_response(texts):
    return SimpleNamespace(
        data=[
            SimpleNamespace(index=index, embedding=[float(len(text)), 0.5])
            for index, text in enumerate(texts)
        ],
        usage=SimpleNamespace(prompt_tokens=len(texts) * 3 + 1, total_tokens=len(texts) * 3 + 2),
    )


@pytest.fixture
def pipeline():
    metadata = {
        "version": 4,
        "authors": "Test author",
        "title": "Test title",
        "document_classification": "Internal",
        "tags": ["budget"],
        "shared_user_ids": ["other-user"],
        "shared_group_ids": ["other-group"],
    }
    embedding_client = SimpleNamespace(
        embeddings=SimpleNamespace(create=Mock(side_effect=lambda **kw: embedding_response(kw["input"]))),
        close=Mock(),
    )

    def search_client():
        return SimpleNamespace(upload_documents=Mock(side_effect=lambda **kw: [
            SimpleNamespace(key=doc["id"], succeeded=True) for doc in kw["documents"]
        ]))

    namespace = {
        "datetime": datetime,
        "ExitStack": ExitStack,
        "timezone": timezone,
        "time": time,
        "traceback": traceback,
        "os": os,
        "logging": logging,
        "log_event": Mock(),
        "debug_print": Mock(),
        "get_document_metadata": Mock(return_value=metadata),
        "get_embedding_safe_chunk_characters": lambda: 1000,
        "ensure_list": lambda value: value if isinstance(value, list) else [value] if value else [],
        "add_file_task_to_file_processing_log": Mock(),
        "cosmos_public_documents_container": object(),
        "cosmos_group_documents_container": object(),
        "cosmos_user_documents_container": object(),
        "cosmos_data_management_jobs_container": object(),
        "hold_data_management_search_write_slot": Mock(side_effect=lambda _: nullcontext()),
        "CLIENTS": {
            "search_client_public": search_client(),
            "search_client_group": search_client(),
            "search_client_user": search_client(),
        },
        "get_settings": Mock(return_value={
            "azure_openai_embedding_authentication_type": "api_key",
            "embedding_model": {"selected": [{"deploymentName": "test-embedding"}]},
        }),
        "AzureOpenAI": Mock(return_value=embedding_client),
        "RateLimitError": RateLimited,
        "_get_rate_limit_wait_time": lambda error, delay: delay,
    }
    load_functions("functions_content.py", ["generate_embeddings_batch", "iter_embeddings_batches"], namespace)
    load_functions("functions_documents.py", [
        "_search_indexing_results_succeeded", "_execute_document_search_write",
        "_is_usable_vision_analysis", "_format_vision_analysis_block", "_append_vision_block_to_chunk_text",
        "save_chunks", "save_chunks_batch", "process_di_document",
    ], namespace)
    namespace["generate_embedding"] = lambda text: (
        [float(len(text)), 0.5],
        {"total_tokens": 5, "prompt_tokens": 4, "model_deployment_name": "test-embedding"},
    )
    return namespace, metadata, embedding_client


def chunks(count):
    return [
        {"page_text_content": f"Page {index} content", "page_number": index, "file_name": "test.pdf"}
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize("scope,client_key", [
    ({}, "search_client_user"),
    ({"group_id": "group"}, "search_client_group"),
    ({"public_workspace_id": "workspace"}, "search_client_public"),
])
def test_batch_preserves_single_chunk_payload(pipeline, scope, client_key):
    ns, metadata, client = pipeline
    metadata["vision_analysis"] = {"description": "A chart", "model": "vision"}
    chunk = chunks(1)[0] | {"page_number": 7}
    ns["save_chunks"](**chunk, user_id="user", document_id="doc", **scope)
    search = ns["CLIENTS"][client_key].upload_documents
    single = search.call_args.kwargs["documents"][0]
    search.reset_mock()
    ns["save_chunks_batch"]([chunk], "user", "doc", **scope)
    batched = search.call_args.kwargs["documents"][0]
    # Timestamps can cross a wall-clock second between calls.
    assert {k: v for k, v in single.items() if k != "upload_date"} == {
        k: v for k, v in batched.items() if k != "upload_date"
    }
    assert client.embeddings.create.call_args.kwargs["input"] == [batched["chunk_text"]]


def test_large_document_request_counts_and_streaming(pipeline):
    ns, _, client = pipeline
    search = ns["CLIENTS"]["search_client_user"].upload_documents
    progress = []

    def saved(count, last):
        # Acknowledged Search progress precedes the next embedding request.
        assert search.call_count == client.embeddings.create.call_count
        progress.append((count, last["page_number"]))

    usage = ns["save_chunks_batch"](chunks(642), "user", "doc", progress_callback=saved)
    assert client.embeddings.create.call_count == 41
    assert search.call_count == 41
    assert ns["AzureOpenAI"].call_count == 1
    assert ns["get_document_metadata"].call_count == 1
    assert ns["hold_data_management_search_write_slot"].call_count == 41
    assert max(len(call.kwargs["documents"]) for call in search.call_args_list) == 16
    assert [doc["id"] for call in search.call_args_list for doc in call.kwargs["documents"]] == [
        f"doc_{page}" for page in range(1, 643)
    ]
    assert progress[-1] == (642, 642)
    assert len(progress) == 41
    assert usage == {
        "chunks_saved": 642, "prompt_tokens": 642 * 3 + 41,
        "total_tokens": 642 * 3 + 82, "model_deployment_name": "test-embedding",
    }
    client.close.assert_called_once()
    timings = next(
        call.kwargs["extra"] for call in ns["log_event"].call_args_list
        if call.args[0] == "[DOCUMENT_INGESTION] Content batches complete."
    )
    for key in ("embedding_seconds", "search_seconds", "progress_seconds", "elapsed_seconds"):
        assert timings[key] >= 0


def test_embedding_only_clamp_and_empty_filter_preserve_page_ids(pipeline):
    ns, _, client = pipeline
    ns["get_embedding_safe_chunk_characters"] = lambda: 5
    data = [
        chunks(1)[0] | {"page_text_content": " "},
        chunks(2)[1] | {"page_number": 9, "page_text_content": "long source text"},
    ]
    usage = ns["save_chunks_batch"](data, "user", "doc")
    client.embeddings.create.assert_called_once_with(model="test-embedding", input=["long "])
    document = ns["CLIENTS"]["search_client_user"].upload_documents.call_args.kwargs["documents"][0]
    assert document["chunk_text"] == "long source text"
    assert document["id"] == "doc_9"
    assert document["page_number"] == document["chunk_sequence"] == 9
    assert usage["chunks_saved"] == 1


@pytest.mark.parametrize("data", [[], [{"page_text_content": " ", "page_number": 1, "file_name": "test.pdf"}]])
def test_empty_input_never_calls_embedding_or_search(pipeline, data):
    ns, _, client = pipeline
    assert ns["save_chunks_batch"](data, "user", "doc")["chunks_saved"] == 0
    client.embeddings.create.assert_not_called()
    ns["CLIENTS"]["search_client_user"].upload_documents.assert_not_called()


@pytest.mark.parametrize("failure", ["search", "acknowledgement", "short_acknowledgement", "embedding", "progress", "fence"])
def test_failed_second_batch_never_reports_completion(pipeline, failure):
    ns, _, client = pipeline
    search = ns["CLIENTS"]["search_client_user"].upload_documents
    original_search = search.side_effect
    original_embedding = client.embeddings.create.side_effect
    progress = []

    def upload(**kw):
        if search.call_count == 2:
            if failure == "search":
                raise RuntimeError("Search unavailable")
            if failure == "acknowledgement":
                return [SimpleNamespace(succeeded=False) for _ in kw["documents"]]
            if failure == "short_acknowledgement":
                return [SimpleNamespace(succeeded=True)]
        return original_search(**kw)

    def embed(**kw):
        if failure == "embedding" and client.embeddings.create.call_count == 2:
            raise RuntimeError("Embedding unavailable")
        return original_embedding(**kw)

    def saved(count, last):
        if failure == "progress" and count > 16:
            raise RuntimeError("Document deleted or update failed")
        progress.append(count)

    search.side_effect = upload
    client.embeddings.create.side_effect = embed
    if failure == "fence":
        ns["hold_data_management_search_write_slot"].side_effect = [
            nullcontext(), RuntimeError("Search writes frozen")
        ]
    with pytest.raises(RuntimeError):
        ns["save_chunks_batch"](chunks(33), "user", "doc", progress_callback=saved)
    assert progress == [16]
    assert not any(
        call.args[0] == "[DOCUMENT_INGESTION] Content batches complete."
        for call in ns["log_event"].call_args_list
    )
    client.close.assert_called_once()


def test_out_of_order_embeddings_are_mapped_by_index(pipeline):
    ns, _, client = pipeline
    response = embedding_response(["short", "much longer"])
    response.data.reverse()
    client.embeddings.create.side_effect = None
    client.embeddings.create.return_value = response
    results = ns["generate_embeddings_batch"](["short", "much longer"])
    assert [embedding for embedding, _ in results] == [[5.0, 0.5], [11.0, 0.5]]
    assert sum(usage["total_tokens"] for _, usage in results) == 8
    client.close.assert_called_once()


@pytest.mark.parametrize("indices,vectors", [
    ([0], [[1.0]]),
    ([0, 0], [[1.0], [1.0]]),
    ([0, 2], [[1.0], [1.0]]),
    ([0, 1], [[1.0], []]),
])
def test_malformed_embeddings_are_rejected_before_search(pipeline, indices, vectors):
    ns, _, client = pipeline
    response = embedding_response(["first", "second"])
    response.data = [SimpleNamespace(index=index, embedding=vector) for index, vector in zip(indices, vectors)]
    client.embeddings.create.side_effect = None
    client.embeddings.create.return_value = response
    with pytest.raises(ValueError):
        ns["save_chunks_batch"](chunks(2), "user", "doc")
    ns["CLIENTS"]["search_client_user"].upload_documents.assert_not_called()
    client.close.assert_called_once()


def test_rate_limit_retries_same_batch_without_duplicate_search_writes(pipeline):
    ns, _, client = pipeline
    ns["time"] = SimpleNamespace(sleep=Mock(), perf_counter=time.perf_counter)
    client.embeddings.create.side_effect = [
        RateLimited(), embedding_response(["first", "second"]),
    ]
    ns["save_chunks_batch"](chunks(2), "user", "doc")
    ns["time"].sleep.assert_called_once_with(1.0)
    assert client.embeddings.create.call_args_list[0] == client.embeddings.create.call_args_list[1]
    ns["CLIENTS"]["search_client_user"].upload_documents.assert_called_once()
    client.close.assert_called_once()


def test_exhausted_rate_limit_is_not_a_success_shaped_result(pipeline):
    ns, _, client = pipeline
    ns["time"] = SimpleNamespace(sleep=Mock(), perf_counter=time.perf_counter)
    client.embeddings.create.side_effect = RateLimited()
    with pytest.raises(RateLimited):
        ns["save_chunks_batch"](chunks(2), "user", "doc")
    assert client.embeddings.create.call_count == 6
    assert ns["time"].sleep.call_count == 5
    ns["CLIENTS"]["search_client_user"].upload_documents.assert_not_called()
    client.close.assert_called_once()


def test_success_does_not_sleep_artificially(pipeline):
    ns, _, _ = pipeline
    ns["time"] = SimpleNamespace(sleep=Mock(), perf_counter=time.perf_counter)
    ns["save_chunks_batch"](chunks(17), "user", "doc")
    ns["time"].sleep.assert_not_called()


def test_missing_metadata_fails_before_embedding(pipeline):
    ns, _, client = pipeline
    ns["get_document_metadata"].return_value = None
    with pytest.raises(ValueError, match="No metadata"):
        ns["save_chunks_batch"](chunks(1), "user", "doc")
    client.embeddings.create.assert_not_called()


@pytest.mark.parametrize("batch_size", [0, -1])
def test_invalid_embedding_batch_size_is_explicit(pipeline, batch_size):
    ns, _, _ = pipeline
    with pytest.raises(ValueError, match="batch_size"):
        ns["generate_embeddings_batch"](["text"], batch_size=batch_size)
    ns["AzureOpenAI"].assert_not_called()


def test_empty_embedding_input_does_not_construct_client(pipeline):
    ns, _, _ = pipeline
    assert ns["generate_embeddings_batch"]([]) == []
    ns["AzureOpenAI"].assert_not_called()


def test_missing_embedding_deployment_is_explicit(pipeline):
    ns, _, _ = pipeline
    ns["get_settings"].return_value = {}
    with pytest.raises(ValueError, match="No embedding deployment"):
        ns["generate_embeddings_batch"](["text"])
    ns["AzureOpenAI"].assert_not_called()


def test_managed_identity_is_closed_when_client_initialization_fails(pipeline):
    ns, _, _ = pipeline
    ns["get_settings"].return_value["azure_openai_embedding_authentication_type"] = "managed_identity"
    credential = SimpleNamespace(close=Mock())
    ns["DefaultAzureCredential"] = lambda: credential
    ns["get_bearer_token_provider"] = Mock(return_value="provider")
    ns["cognitive_services_scope"] = "test-scope"
    ns["AzureOpenAI"].side_effect = ValueError("Invalid endpoint")
    with pytest.raises(ValueError, match="Invalid endpoint"):
        ns["generate_embeddings_batch"](["text"])
    credential.close.assert_called_once()


@pytest.mark.parametrize("auth", ["api_key", "managed_identity", "apim"])
def test_embedding_client_is_reused_and_closed_for_all_auth_paths(pipeline, auth):
    ns, _, client = pipeline
    settings = ns["get_settings"].return_value
    settings["azure_openai_embedding_authentication_type"] = auth
    settings["enable_embedding_apim"] = auth == "apim"
    settings["azure_apim_embedding_deployment"] = "apim-embedding"
    credential = SimpleNamespace(close=Mock())
    ns["DefaultAzureCredential"] = Mock(return_value=credential)
    ns["get_bearer_token_provider"] = Mock(return_value="provider")
    ns["cognitive_services_scope"] = "test-scope"
    ns["generate_embeddings_batch"](["one", "two", "three"], batch_size=2)
    assert ns["AzureOpenAI"].call_count == 1
    assert client.embeddings.create.call_count == 2
    client.close.assert_called_once()
    if auth == "managed_identity":
        credential.close.assert_called_once()
        assert ns["AzureOpenAI"].call_args.kwargs["azure_ad_token_provider"] == "provider"


@pytest.mark.parametrize("scope", [{}, {"group_id": "group"}, {"public_workspace_id": "workspace"}])
def test_pdf_processor_wires_batches_scopes_and_acknowledged_progress(pipeline, tmp_path, scope):
    ns, metadata, client = pipeline
    pages = [{"page_number": page, "content": f"Page {page}"} for page in range(1, 35)]
    pages[1]["content"] = " "  # The third page must remain page 3, not be renumbered to 2.
    metadata["number_of_pages"] = len(pages)
    ns.update({
        "IMAGE_EXTENSIONS": ["png"],
        "extract_pdf_metadata": lambda _: ("", "", None, None),
        "parse_authors": lambda _: [],
        "get_pdf_page_count": lambda _: len(pages),
        "get_chunk_size_config": lambda _: {"pdf": {"value": 1}},
        "is_enhanced_extraction_enabled": lambda _: False,
        "get_document_intelligence_auto_sample_pages": lambda _: 3,
        "EXTRACTION_ENGINE_DOCUMENT_INTELLIGENCE": "di",
        "EXTRACTION_ENGINE_CONTENT_UNDERSTANDING": "cu",
        "get_effective_document_intelligence_pdf_image_extraction_mode": lambda _: "read",
        "_resolve_extraction_engine_for_mode": lambda *_: ("di", ""),
        "upload_to_blob": Mock(),
        "_extract_pages_with_extraction_engine": lambda *a, **k: (pages, "di", ""),
        "save_chunks": Mock(side_effect=AssertionError("PDF used the single-chunk path")),
    })
    updates = []

    def update(**kw):
        metadata.update(kw)
        updates.append(kw)

    path = tmp_path / "test.pdf"
    path.write_bytes(b"PDF source is mocked")
    result = ns["process_di_document"](
        "doc", "user", str(path), "test.pdf", ".pdf", False, update,
        auto_extract_metadata=False, **scope,
    )
    assert result == (33, 33 * 3 + 3 * 2, "test-embedding")
    progress = [kw for kw in updates if kw.get("status", "").startswith("Saved ")]
    assert [kw["current_file_chunk"] for kw in progress] == [17, 33, 34]
    assert all(kw["number_of_pages"] == 34 for kw in progress)
    assert client.embeddings.create.call_count == 3
    assert ns["get_document_metadata"].call_count == 2
    expected_scope = {"document_id": "doc", "user_id": "user", **scope}
    assert all(call.kwargs == expected_scope for call in ns["get_document_metadata"].call_args_list)
