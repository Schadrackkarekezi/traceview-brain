"""
Loads the unstructured side of Traceview into HydraDB - the same
account_notes and enablement_content rows seed_unstructured.py writes to
Postgres, read back from Postgres here so both stores are seeded from one
source of truth and can't drift apart on what a note says.

Each account note also carries a declared graph (HydraDB's bring-your-
own-graph payload) built from the structured rows Postgres already has:
which AE/SE/CSM is on the account, which workloads it's contracted on,
what state each contract is in. HydraDB extracts its own graph from the
note text regardless - this adds the facts the text only implies, so a
question can walk from "the champion left" (a note) to "Nina Okafor is
the CSM" (a row) inside one graph.

Safe to re-run: every source is upserted by a stable id
(account_note_<note_id>, enablement_content_<content_id>).

--prune does the reverse cleanup instead of seeding: removes any source
in HydraDB that no longer has a row in Postgres (a note deleted there, or
one written by a test run). HydraDB won't delete a source whose graph is
still being built, so a prune right after an ingest may need re-running.

Usage: python -m scripts.seed_hydra [--no-graph] [--no-wait] [--prune]   (run from backend/)
"""

import argparse
import re
import sys

from app import hydra_store
from app.database import get_connection


def _key(prefix: str, name: str) -> str:
    return f"{prefix}_{re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')}"


def fetch_account_facts(cur) -> dict:
    """{account_id: {"name", "segment", "industry", "region", "team": [...], "contracts": [...]}}"""
    cur.execute("SELECT account_id, name, segment, industry, region FROM accounts")
    facts = {
        account_id: {"name": name, "segment": segment, "industry": industry, "region": region, "team": [], "contracts": []}
        for account_id, name, segment, industry, region in cur.fetchall()
    }

    cur.execute("SELECT account_id, name, role FROM account_team ORDER BY account_id, role")
    for account_id, name, role in cur.fetchall():
        facts[account_id]["team"].append({"name": name, "role": role})

    cur.execute(
        """
        SELECT cc.account_id, w.name, cc.status, cc.committed_amount, cc.contract_type
        FROM capacity_contracts cc
        JOIN workloads w ON w.workload_id = cc.workload_id
        ORDER BY cc.account_id
        """
    )
    for account_id, workload, status, committed, contract_type in cur.fetchall():
        facts[account_id]["contracts"].append({
            "workload": workload,
            "status": status,
            "committed": float(committed),
            "contract_type": contract_type,
        })

    return facts


ROLE_LABELS = {"AE": "account executive", "SE": "sales engineer", "CSM": "customer success manager"}


def graph_for_account(account: dict) -> dict:
    name = account["name"]
    industry = account["industry"].replace("_", " ")
    account_key = _key("account", name)

    entities = {
        account_key: {"name": name, "type": "ACCOUNT", "namespace": "accounts"},
        _key("industry", industry): {"name": industry, "type": "INDUSTRY", "namespace": "industries"},
        _key("segment", account["segment"]): {"name": account["segment"], "type": "SEGMENT", "namespace": "segments"},
    }
    relations = [
        {
            "source": account_key,
            "target": _key("industry", industry),
            "predicate": "IN_INDUSTRY",
            "context": f"{name} is a {industry} company in the {account['region']} region.",
        },
        {
            "source": account_key,
            "target": _key("segment", account["segment"]),
            "predicate": "IN_SEGMENT",
            "context": f"{name} is a {account['segment']} segment account.",
        },
    ]

    for member in account["team"]:
        person_key = _key("person", member["name"])
        entities[person_key] = {"name": member["name"], "type": "PERSON", "namespace": "account_team"}
        relations.append({
            "source": account_key,
            "target": person_key,
            "predicate": f"HAS_{member['role']}",
            "context": f"{member['name']} is the {ROLE_LABELS[member['role']]} ({member['role']}) on {name}.",
        })

    for contract in account["contracts"]:
        workload_key = _key("workload", contract["workload"])
        status_key = _key("status", contract["status"])
        entities[workload_key] = {"name": contract["workload"], "type": "WORKLOAD", "namespace": "workloads"}
        entities[status_key] = {"name": contract["status"], "type": "CONTRACT_STATUS", "namespace": "contract_status"}
        relations.append({
            "source": account_key,
            "target": workload_key,
            "predicate": "CONTRACTED_ON",
            "context": (
                f"{name} has a {contract['contract_type']} capacity contract on {contract['workload']} "
                f"with ${contract['committed']:,.0f} committed, currently {contract['status']}."
            ),
        })
        relations.append({
            "source": account_key,
            "target": status_key,
            "predicate": "HAS_CONTRACT_STATUS",
            "context": f"{name}'s {contract['workload']} contract is {contract['status']}.",
        })

    return {"entities": entities, "relations": relations}


def build_docs(cur, with_graph: bool) -> list:
    facts = fetch_account_facts(cur)
    docs = []

    cur.execute("SELECT note_id, account_id, author_role, note_date, content FROM account_notes ORDER BY note_id")
    for note_id, account_id, author_role, note_date, content in cur.fetchall():
        account = facts[account_id]
        docs.append({
            "id": hydra_store.knowledge_id("account_note", note_id),
            "title": f"Account note - {account['name']} ({note_date.isoformat()}, {author_role})",
            "text": content,
            "source_type": "account_note",
            "account_id": account_id,
            "extra": {
                "record_id": note_id,
                "account_name": account["name"],
                "author_role": author_role,
                "note_date": note_date.isoformat(),
            },
            "graph": graph_for_account(account) if with_graph else None,
        })

    cur.execute("SELECT content_id, title, category, content FROM enablement_content ORDER BY content_id")
    for content_id, title, category, content in cur.fetchall():
        docs.append({
            "id": hydra_store.knowledge_id("enablement_content", content_id),
            "title": title,
            "text": f"# {title}\n\n{content}",
            "source_type": "enablement_content",
            "account_id": None,
            "extra": {"record_id": content_id, "category": category},
        })

    return docs


def prune() -> int:
    """Deletes HydraDB sources with no matching Postgres row. Returns how many are still left."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT note_id FROM account_notes")
            valid = {hydra_store.knowledge_id("account_note", row[0]) for row in cur.fetchall()}
            cur.execute("SELECT content_id FROM enablement_content")
            valid |= {hydra_store.knowledge_id("enablement_content", row[0]) for row in cur.fetchall()}

    stray = [source_id for source_id in hydra_store.list_knowledge_ids() if source_id not in valid]
    if not stray:
        print("Nothing to prune - every HydraDB source has a row in Postgres.")
        return 0

    outcome = hydra_store.delete_knowledge(stray)
    refused = {source_id: reason for source_id, reason in outcome.items() if reason}
    print(f"Found {len(stray)} stray source(s); deleted {len(stray) - len(refused)}.")
    for source_id, reason in sorted(refused.items()):
        print(f"  not deleted: {source_id} - {reason}")
    return len(refused)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-graph", action="store_true", help="skip the declared account graph")
    parser.add_argument("--no-wait", action="store_true", help="don't block until indexing finishes")
    parser.add_argument("--prune", action="store_true", help="remove HydraDB sources that have no row in Postgres")
    args = parser.parse_args()

    if not hydra_store.is_enabled():
        sys.exit("HYDRA_DB_API_KEY isn't set in backend/.env - nothing to seed.")

    if args.prune:
        sys.exit(1 if prune() else 0)

    print(f"Ensuring HydraDB database {hydra_store.DATABASE!r} is ready...")
    hydra_store.ensure_database()

    with get_connection() as conn:
        with conn.cursor() as cur:
            docs = build_docs(cur, with_graph=not args.no_graph)

    notes = sum(1 for d in docs if d["source_type"] == "account_note")
    print(f"Ingesting {notes} account notes and {len(docs) - notes} enablement docs into {hydra_store.KNOWLEDGE_COLLECTION!r}...")

    try:
        ids = hydra_store.ingest_documents(docs)
    except Exception as e:
        if args.no_graph:
            raise
        # The notes themselves matter more than the declared graph on top
        # of them - HydraDB still extracts its own graph from the text.
        print(f"Ingest with the declared graph failed ({e}); retrying without it.")
        for doc in docs:
            doc["graph"] = None
        ids = hydra_store.ingest_documents(docs)

    print(f"Accepted {len(ids)} sources.")
    if args.no_wait:
        return

    def progress(states):
        counts = {}
        for state in states.values():
            counts[state] = counts.get(state, 0) + 1
        print("  " + ", ".join(f"{count} {state}" for state, count in sorted(counts.items())))

    print("Waiting for indexing (searchable once past 'processing')...")
    hydra_store.wait_for_indexing(ids, on_progress=progress)
    print("Done - HydraDB is ready to answer.")


if __name__ == "__main__":
    main()
