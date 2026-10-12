"""
HydraDB integration tests, against a fake client - not the real service,
unlike the rest of this suite. The properties under test are this app's
own: that every search carries the right scope filter, that a chunk
outside that scope is dropped even if the server returned it, that a
HydraDB failure falls back to pgvector instead of failing the turn, and
that memory lands in the asking user's own collection. None of those
depend on HydraDB's ranking, so they don't need an account or a network
connection to verify - and CI has neither.
"""

import json
from types import SimpleNamespace

import pytest

import app.hydra_store as hydra_store
import app.retrieval as retrieval
import app.upload as upload


def _chunk(scope, source_type="account_note", account_id=None, text="note text", score=0.9):
    additional = {"record_id": 1, "doc_title": "A title"}
    if account_id is not None:
        additional["account_id"] = account_id
    return SimpleNamespace(
        chunk_uuid=f"uuid-{scope}",
        id=f"{source_type}_1",
        chunk_content=text,
        source_title="A title.md",
        source_type="file",
        relevancy_score=score,
        metadata={"scope": scope, "doc_kind": source_type},
        additional_metadata=additional,
    )


def _triplet(source, predicate, target):
    return SimpleNamespace(
        source={"name": source}, relation={"canonical_predicate": predicate}, target={"name": target}
    )


class FakeClient:
    def __init__(self, chunks=None, graph_context=None, fail=False):
        self.chunks = chunks or []
        self.graph_context = graph_context
        self.fail = fail
        self.queries = []
        self.ingests = []
        self.context = SimpleNamespace(ingest=self._ingest)

    def query(self, **kwargs):
        self.queries.append(kwargs)
        if self.fail:
            raise RuntimeError("hydra is down")
        return SimpleNamespace(
            data=SimpleNamespace(chunks=self.chunks, graph_context=self.graph_context),
            meta=SimpleNamespace(request_id="req-123"),
        )

    def _ingest(self, **kwargs):
        self.ingests.append(kwargs)
        results = [SimpleNamespace(id=m["id"], filename=None, error=None)
                   for m in json.loads(kwargs.get("document_metadata", "[]"))]
        return SimpleNamespace(data=SimpleNamespace(results=results or [SimpleNamespace(id="mem-1", error=None)]))


@pytest.fixture
def fake_hydra(monkeypatch):
    def install(**kwargs):
        client = FakeClient(**kwargs)
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        monkeypatch.delenv("RETRIEVAL_BACKEND", raising=False)
        monkeypatch.setattr(hydra_store, "_get_client", lambda: client)
        return client

    return install


def test_disabled_without_an_api_key(monkeypatch):
    monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)
    assert hydra_store.is_enabled() is False


def test_retrieval_backend_override_forces_pgvector(monkeypatch):
    monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
    monkeypatch.setenv("RETRIEVAL_BACKEND", "pgvector")
    assert hydra_store.is_enabled() is False


def _scopes(query):
    return query["metadata_filters"]["scope"]["contains_any"]


def test_account_scoped_search_queries_the_account_and_global_scopes_separately(fake_hydra):
    # Two queries on purpose - see search_knowledge: one combined query
    # let generic playbooks crowd the account's own note out of the top.
    client = fake_hydra(chunks=[_chunk("account_7", account_id=7), _chunk("global", "enablement_content")])

    result = hydra_store.search_knowledge("why is consumption declining", account_ids=[7], top_k=4)

    assert sorted(_scopes(q) for q in client.queries) == [["account_7"], ["global"]]
    # Graph paths only come back in thinking mode, and the account query is the one that needs them.
    assert {tuple(_scopes(q)): q["mode"] for q in client.queries} == {("account_7",): "thinking", ("global",): "fast"}
    assert all(q["collection"] == hydra_store.KNOWLEDGE_COLLECTION for q in client.queries)
    assert [s["account_id"] for s in result["sources"]] == [7, None]
    assert result["request_id"] == "req-123"


def test_unscoped_search_filters_to_global_only(fake_hydra):
    client = fake_hydra(chunks=[_chunk("global", "enablement_content")])

    hydra_store.search_knowledge("how do I handle a pricing objection")

    assert [_scopes(q) for q in client.queries] == [["global"]]


def test_chunk_outside_the_requested_scope_is_dropped_even_if_returned(fake_hydra):
    # The server-side filter is the real boundary; this is the second
    # check behind it - another account's note must not get through just
    # because the server sent it.
    fake_hydra(chunks=[_chunk("account_7", account_id=7), _chunk("account_8", account_id=8)])

    result = hydra_store.search_knowledge("why is consumption declining", account_ids=[7])

    assert [s["account_id"] for s in result["sources"]] == [7]


def test_graph_paths_are_flattened_and_deduplicated(fake_hydra):
    path = SimpleNamespace(
        triplets=[_triplet("Kovac Studios", "HAS_CSM", "Nina Okafor")], relevancy_score=0.8
    )
    graph = SimpleNamespace(query_paths=[path], chunk_relations=[path], chunk_id_to_group_ids={})
    fake_hydra(chunks=[_chunk("global", "enablement_content")], graph_context=graph)

    result = hydra_store.search_knowledge("who owns Kovac")

    assert result["graph_paths"] == [{"path": "Kovac Studios --[HAS_CSM]--> Nina Okafor", "score": 0.8, "context": None}]


def test_retrieve_context_uses_hydradb_when_enabled(fake_hydra):
    fake_hydra(chunks=[_chunk("global", "enablement_content")])

    result = retrieval.retrieve_context("how do capacity contracts work")

    assert result["backend"] == "hydradb"
    assert result["hydra_request_id"] == "req-123"
    assert result["fallback_reason"] is None


def test_retrieve_context_falls_back_to_pgvector_when_hydradb_fails(fake_hydra):
    fake_hydra(fail=True)

    result = retrieval.retrieve_context("How do capacity contracts and true-ups work?", top_k=3)

    assert result["backend"] == "pgvector"
    assert result["fallback_reason"] == "RuntimeError"
    assert len(result["sources"]) > 0
    assert result["graph_paths"] == []


def test_retrieve_context_uses_pgvector_when_hydradb_is_not_configured(monkeypatch):
    monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)

    result = retrieval.retrieve_context("How do capacity contracts and true-ups work?", top_k=3)

    assert result["backend"] == "pgvector"
    assert result["fallback_reason"] is None


def test_each_user_gets_their_own_memory_collection():
    assert hydra_store.user_collection("Nina Okafor") == "user_nina_okafor"
    assert hydra_store.user_collection("nina.okafor@acme.com") == "user_nina_okafor_acme_com"
    assert hydra_store.user_collection("Nina Okafor") != hydra_store.user_collection("Owen Osei")
    assert hydra_store.user_collection("") == "user_anonymous"


def test_recall_queries_only_the_asking_users_collection(fake_hydra):
    client = fake_hydra(chunks=[SimpleNamespace(id="m1", chunk_content="Covers healthcare accounts", relevancy_score=0.7)])

    memories = hydra_store.recall_memories("Nina Okafor", "which of my accounts are at risk")

    assert client.queries[0]["collection"] == "user_nina_okafor"
    assert client.queries[0]["type"] == "memory"
    assert memories == [{"id": "m1", "text": "Covers healthcare accounts", "score": 0.7}]


def test_remembering_a_fact_writes_it_verbatim_to_the_users_collection(fake_hydra):
    client = fake_hydra()

    hydra_store.remember_fact("Nina Okafor", "The user is the CSM for Kovac Studios.", origin="conversation")

    ingest = client.ingests[0]
    memory = json.loads(ingest["memories"])[0]
    assert ingest["collection"] == "user_nina_okafor"
    assert ingest["type"] == "memory"
    assert memory["infer"] is False
    assert memory["text"] == "The user is the CSM for Kovac Studios."
    assert memory["additional_metadata"] == {"origin": "conversation", "fact": "The user is the CSM for Kovac Studios."}


def test_the_same_fact_told_twice_is_one_memory(fake_hydra):
    client = fake_hydra()

    hydra_store.remember_fact("Nina Okafor", "The user is a CSM.")
    hydra_store.remember_fact("Nina Okafor", "the user  is a CSM. ")

    first, second = (json.loads(i["memories"])[0]["id"] for i in client.ingests)
    assert first == second


def test_ingested_document_carries_its_scope_and_account(fake_hydra):
    client = fake_hydra()

    ids = hydra_store.ingest_documents([{
        "id": "account_note_42",
        "title": "Account note - Kovac Studios",
        "text": "Usage is climbing.",
        "source_type": "account_note",
        "account_id": 7,
        "extra": {"record_id": 42},
    }])

    metadata = json.loads(client.ingests[0]["document_metadata"])[0]
    assert ids == ["account_note_42"]
    assert metadata["metadata"] == {"scope": "account_7", "doc_kind": "account_note"}
    assert metadata["additional_metadata"]["account_id"] == 7
    assert "graph_payload" not in client.ingests[0]


def test_upload_reports_hydra_indexing_without_failing_when_hydradb_errors(fake_hydra, monkeypatch):
    fake_hydra()
    monkeypatch.setattr(hydra_store, "ingest_documents", lambda docs: (_ for _ in ()).throw(RuntimeError("down")))

    assert upload._index_in_hydra({"id": "x"}) is False


def test_upload_skips_hydra_when_not_configured(monkeypatch):
    monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)

    assert upload._index_in_hydra({"id": "x"}) is None


def test_hybrid_hints_come_from_name_columns_of_the_sql_result():
    from app.agent import _account_hints_from_rows

    columns = ["name", "status", "total_credits"]
    rows = [["Parkview Consumer Brands", "at_risk", 10], ["Kessler Broadcasting Co.", "at_risk", 20]]

    assert _account_hints_from_rows(columns, rows) == ["Parkview Consumer Brands", "Kessler Broadcasting Co."]
    assert _account_hints_from_rows(["status", "count"], [["at_risk", 9]]) == []
