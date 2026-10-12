"""
A lightweight knowledge graph over the schema, used to give the agent
explicit join-path hints instead of making it infer join paths from a flat
column list alone.

This is deliberately NOT a separate graph database - the graph is built
in-memory (networkx) from two things that are already sources of truth:
the schema's actual foreign keys (database.fetch_foreign_keys - so the
graph can't drift out of sync with the real schema) and ontology.json,
which adds the semantic layer FKs don't carry on their own: what an entity
*means* (aliases for matching natural-language questions to tables) and
what a relationship *means* (a human label for each FK edge, not just
"column X references column Y").

The concrete payoff: text-to-SQL agents most often go wrong on multi-table
joins, because a flat schema listing doesn't tell the model *how* two
tables connect when there's more than one hop between them. Given a
question, find_relevant_entities() matches mentioned concepts to tables,
and find_join_paths() computes the actual path between them via graph
traversal - e.g. "employees -> activities -> deals -> products" - and
that path gets handed to the model as a hint, not discovered by guessing.
"""

import json

import networkx as nx

from app.database import get_connection, fetch_foreign_keys

ONTOLOGY_PATH = "data/ontology.json"

_graph = None


def _load_ontology():
    with open(ONTOLOGY_PATH) as f:
        return json.load(f)


def build_graph():
    """
    Only tables explicitly declared in ontology.json ever become graph
    nodes - deliberately, not via networkx's implicit node creation.

    networkx.add_edge() silently creates a node for any table it hasn't
    seen yet, with no attributes. Confirmed as a real bug, not a
    hypothetical: account_notes and document_chunks both have a foreign
    key to accounts, so unconditionally adding every FK edge from the live
    database made them show up as "dimension" tables (the default when a
    node has no kind) - meaning the SQL-generation prompt saw their real
    columns and the agent wrote a raw SQL query dumping note content
    straight into a result cell, completely bypassing retrieve_unstructured
    and synthesis. Restricting edges to declared entities on both ends is
    what keeps infrastructure tables (unstructured content, embeddings,
    verified queries, glossary) out of the graph - and therefore out of
    the SQL schema - unless they're deliberately added here as a real
    business entity, not swept in by accident.
    """
    global _graph

    ontology = _load_ontology()
    graph = nx.DiGraph()

    declared_tables = set()
    for entity in ontology["entity_types"]:
        declared_tables.add(entity["table"])
        graph.add_node(
            entity["table"],
            kind=entity.get("kind", "dimension"),
            description=entity["description"],
            aliases=entity["aliases"],
        )

    with get_connection() as conn:
        edges = fetch_foreign_keys(conn)

    for edge in edges:
        if edge["from_table"] not in declared_tables or edge["to_table"] not in declared_tables:
            continue
        via = f"{edge['from_table']}.{edge['from_column']}"
        label = ontology["relationship_labels"].get(via, "references")
        graph.add_edge(edge["from_table"], edge["to_table"], label=label, via=via)

    _graph = graph
    return graph


def get_graph():
    if _graph is None:
        build_graph()
    return _graph


def tables_by_kind() -> dict:
    """
    {"fact": [...], "dimension": [...]} - the classic dimensional-modeling
    split (facts = transactional events, dimensions = reference data).
    Presenting the schema this way, instead of as one flat table list,
    gives the model the same structural cue a well-designed semantic
    layer gives an analyst: which tables to expect grouping/aggregation
    against (facts) versus which ones are just descriptive lookups
    (dimensions).
    """
    graph = get_graph()
    result = {"fact": [], "dimension": []}
    for table, data in graph.nodes(data=True):
        result[data.get("kind", "dimension")].append(table)
    return result


def find_relevant_entities(question: str) -> list:
    graph = get_graph()
    q = question.lower()

    matched = []
    for table, data in graph.nodes(data=True):
        if any(alias in q for alias in data.get("aliases", [])):
            matched.append(table)
    return matched


def _describe_path(graph, node_path):
    segments = []
    for u, v in zip(node_path, node_path[1:]):
        if graph.has_edge(u, v):
            edge = graph[u][v]
            segments.append(f"{u} --[{edge['label']}, via {edge['via']}]--> {v}")
        else:
            edge = graph[v][u]
            segments.append(f"{u} <--[{edge['label']}, via {edge['via']}]-- {v}")
    return "  ".join(segments)


def find_join_paths(entities: list) -> list:
    if len(entities) < 2:
        return []

    graph = get_graph()
    undirected = graph.to_undirected()

    paths = []
    seen_pairs = set()
    for i, a in enumerate(entities):
        for b in entities[i + 1:]:
            pair = tuple(sorted((a, b)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            try:
                node_path = nx.shortest_path(undirected, a, b)
            except nx.NetworkXNoPath:
                continue

            if len(node_path) > 1:
                paths.append(_describe_path(graph, node_path))

    return paths
