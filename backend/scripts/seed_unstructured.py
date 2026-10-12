"""
Seeds the unstructured side of Traceview: account_notes (account-scoped CS/
sales free text) and enablement_content (global battlecards/plays/FAQs),
both chunked into document_chunks - the table Phase 08's pgvector
retrieval actually searches over. Embeddings are left NULL here; that's
Phase 08's job, once the retrieval module exists to generate them with.

account_notes are generated from the real seeded structured data, not
hand-written - a note's numbers (consumption ratio, committed amount,
CSM name) come straight from the same accounts/capacity_contracts/
consumption_usage tables the SQL agent queries, so a hybrid answer that
pulls both the trend (structured) and the narrative (unstructured) will
find them actually agreeing, the same discipline used for seed_db.py's
correlated data. enablement_content is hand-authored, since it's meant to
be general company knowledge, not tied to any one account's numbers.

Deterministic (random.seed(43) - different from seed_db.py's 42, so this
script's own randomized phrasing choices don't shift if seed_db.py's
generation logic ever changes) and truncate-and-reinsert, like seed_db.py.
"""

import random
from datetime import date

from app.chunking import chunk_and_insert
from app.database import get_connection

random.seed(43)

REFERENCE_DATE = date(2026, 2, 1)

TABLES_IN_TRUNCATE_ORDER = ["document_chunks", "account_notes", "enablement_content"]


# ---------------------------------------------------------------------
# account_notes - generated from real structured data
# ---------------------------------------------------------------------

def fetch_account_context(cur) -> list:
    cur.execute(
        """
        SELECT
            cc.account_id, a.name, a.industry, cc.status, cc.committed_amount, cc.contract_id,
            w.name AS workload_name, ae.name AS ae_name, se.name AS se_name, csm.name AS csm_name
        FROM capacity_contracts cc
        JOIN accounts a ON a.account_id = cc.account_id
        JOIN workloads w ON w.workload_id = cc.workload_id
        JOIN account_team ae ON ae.account_id = a.account_id AND ae.role = 'AE'
        JOIN account_team se ON se.account_id = a.account_id AND se.role = 'SE'
        JOIN account_team csm ON csm.account_id = a.account_id AND csm.role = 'CSM'
        ORDER BY cc.account_id
        """
    )
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_recent_consumption_ratio(cur, account_id, workload_name, committed_amount) -> float:
    cur.execute(
        """
        SELECT AVG(credits_consumed)
        FROM (
            SELECT cu.credits_consumed
            FROM consumption_usage cu
            JOIN workloads w ON w.workload_id = cu.workload_id
            WHERE cu.account_id = %s AND w.name = %s
            ORDER BY cu.usage_month DESC
            LIMIT 3
        ) recent
        """,
        (account_id, workload_name),
    )
    row = cur.fetchone()
    avg_recent = float(row[0]) if row and row[0] is not None else 0.0
    monthly_target = float(committed_amount) / 12
    return avg_recent / monthly_target if monthly_target else 0.0


RISK_CAUSES = [
    "the primary champion left the company and a replacement hasn't been named yet",
    "the team's engineering priorities shifted to an internal migration project this quarter",
    "budget got frozen pending a broader vendor consolidation review",
    "the original use case was deprioritized after a reorg on their data platform team",
]
RISK_ACTIONS = [
    "scheduling an exec sponsor call before the next billing cycle",
    "looping in the AE to re-scope the use case with the new stakeholder",
    "proposing a workload health check to re-anchor on original success criteria",
    "escalating internally - this one needs air cover before the renewal conversation",
]
GROWTH_SIGNALS = [
    "usage has been climbing steadily for three straight months",
    "the team added two new pipelines onto the platform last month",
    "they've been asking proactively about additional workload capabilities",
    "the technical champion is actively advocating for the platform internally",
]
STEADY_NOTES = [
    "nothing unusual to flag - usage is tracking close to plan",
    "the relationship is stable, no open asks on our side right now",
    "quiet quarter, which given the account's history is a good sign",
]


def note_for_account(ctx: dict, ratio: float) -> str:
    name = ctx["name"]
    industry = ctx["industry"].replace("_", " ")
    workload = ctx["workload_name"]
    committed = f"${float(ctx['committed_amount']):,.0f}"
    csm = ctx["csm_name"]
    ae = ctx["ae_name"]
    status = ctx["status"]
    pct = f"{ratio * 100:.0f}%"

    if status == "churned":
        cause = random.choice(RISK_CAUSES)
        return (
            f"Post-mortem - {name} ({industry}). Contract on {workload} closed out churned, "
            f"final consumption was tracking at {pct} of the {committed} committed capacity before "
            f"lapsing. Root cause: {cause}. {csm} flagged this in the account plan two quarters "
            f"back but we weren't able to re-engage in time. Lesson for next renewal-risk account "
            f"with a similar profile: escalate the exec sponsor motion earlier, not at the 90-day mark."
        )

    if status == "at_risk":
        cause = random.choice(RISK_CAUSES)
        action = random.choice(RISK_ACTIONS)
        return (
            f"QBR note - {name} ({industry}). Consumption on {workload} is at {pct} of the "
            f"{committed} committed capacity, trending down over the last three months. Cause "
            f"looks like {cause}. {csm} is treating this as renewal-risk in the account plan. "
            f"Action: {action}. {ae} looped in on the exec relationship side."
        )

    if ratio >= 0.9:
        signal = random.choice(GROWTH_SIGNALS)
        return (
            f"QBR note - {name} ({industry}). Strong quarter on {workload} - {pct} of the "
            f"{committed} committed capacity, and {signal}. {csm} sees this as a real workload-"
            f"expansion candidate rather than a renewal concern. Worth {ae} scoping an expansion "
            f"conversation for the next cycle rather than waiting for the renewal date."
        )

    steady = random.choice(STEADY_NOTES)
    return (
        f"QBR note - {name} ({industry}). {workload} contract at {pct} of the {committed} "
        f"committed capacity. {steady}. {csm} has no immediate escalations; next check-in "
        f"scheduled for the standard quarterly cadence."
    )


def seed_account_notes(cur) -> list:
    contexts = fetch_account_context(cur)
    notes = []
    for ctx in contexts:
        ratio = fetch_recent_consumption_ratio(cur, ctx["account_id"], ctx["workload_name"], ctx["committed_amount"])
        content = note_for_account(ctx, ratio)
        author_role = random.choice(["CSM", "AE"])
        note_date = REFERENCE_DATE
        cur.execute(
            """INSERT INTO account_notes (account_id, author_role, note_date, content)
               VALUES (%s, %s, %s, %s) RETURNING note_id""",
            (ctx["account_id"], author_role, note_date, content),
        )
        note_id = cur.fetchone()[0]
        notes.append({"note_id": note_id, "account_id": ctx["account_id"], "content": content})
    return notes


# ---------------------------------------------------------------------
# enablement_content - hand-authored, not account-specific
# ---------------------------------------------------------------------

ENABLEMENT_DOCS = [
    (
        "Competitive Positioning vs. Legacy Data Warehouses",
        "battlecard",
        "When the competitor is an on-prem or legacy cloud data warehouse, the winning angle is "
        "operational burden, not just performance. Legacy warehouses require manual capacity "
        "planning - DBAs sizing clusters ahead of demand, then living with either over-provisioned "
        "idle spend or under-provisioned query queuing. Our consumption model inverts that: compute "
        "scales to the workload automatically, and the customer only pays for what actually ran. "
        "Lead with a concrete question in discovery: \"who currently decides when to resize your "
        "warehouse, and how long does that take?\" The answer is almost always a multi-week change-"
        "management process, which is the wedge. Don't lead with raw benchmark numbers - legacy "
        "warehouse customers have usually been burned by vendor benchmarks before and will discount "
        "them. Anchor instead on total cost of ownership including the DBA hours spent on capacity "
        "management, not license price alone. If the competitor is mid-migration off a legacy "
        "system already, that's a warmer signal than a greenfield deal - they've already accepted "
        "the pain of moving, the only question left is where they land.",
    ),
    (
        "Workload Expansion Play: Data Warehousing to Data Sharing",
        "sales_play",
        "Trigger: the account mentions needing to send data to a partner, customer, or another "
        "internal business unit on a recurring basis - especially if they describe a manual export/"
        "import process (SFTP, flat files, scheduled email reports). This is a pure pain-relief "
        "play, not a new-capability pitch: the account already has the need, they're just solving "
        "it with brittle manual tooling. Quantify the manual process in discovery - how often does "
        "it run, who maintains it, has it ever broken silently. Data Sharing removes the copy "
        "entirely, which is the actual value prop, not \"sharing is easier\" in the abstract. This "
        "play converts fastest with accounts in regulated industries (financial services, "
        "healthcare) where the current manual export process is also an audit liability, not just "
        "an operational one - mention that angle explicitly if the account is in one of those "
        "verticals, it's usually underweighted by the account team going in.",
    ),
    (
        "Sales Play: Reversing Consumption Decline",
        "sales_play",
        "Signal pattern: committed capacity utilization dropping for three or more consecutive "
        "months, especially alongside any account_team change or a champion departure, is the "
        "single most predictive indicator of non-renewal in our historical data - more predictive "
        "than the raw consumption level itself. A account sitting at 60% utilization but stable is "
        "lower risk than one that dropped from 95% to 70% over a quarter, even though the second "
        "number looks higher. Recommended motion: the CSM should initiate an exec alignment call "
        "within 30 days of detecting the pattern, not wait until the standard QBR cadence catches "
        "it, and definitely not wait until the renewal window. Do not lead with a workload-expansion "
        "pitch while an account is showing this pattern - an expansion ask lands worse during a "
        "visible disengagement signal than during stable or growing usage, and can read as tone-deaf "
        "if the account is dealing with an internal reorg or budget pressure.",
    ),
    (
        "Sales Play: Identifying Under-Consumption Risk Early",
        "sales_play",
        "Don't wait for capacity_contracts.status to flip to at_risk before acting - that status is "
        "a lagging label, not an early warning. The leading signal is the ratio of trailing-3-month "
        "average consumption to the straight-line monthly target (committed_amount divided by 12). "
        "An account dropping below roughly 70% of that target for two consecutive months should "
        "trigger a CSM check-in regardless of what the account status field currently says. This is "
        "specifically why CSMs should be looking at the consumption trend directly rather than only "
        "scanning for accounts already marked at-risk in a dashboard - by the time the label "
        "changes, the disengagement is usually already a quarter old, and the intervention options "
        "have narrowed considerably.",
    ),
    (
        "Partner Co-Sell Motion with Cloud Marketplaces",
        "sales_play",
        "When an account already has committed spend with a major cloud provider that they're "
        "trying to draw down (a common enterprise procurement pattern), transacting through that "
        "cloud's marketplace instead of a direct contract can meaningfully accelerate procurement - "
        "it often skips a separate vendor security review entirely, since the marketplace "
        "transaction inherits the cloud provider's existing approval. Ask early in the deal cycle "
        "whether the account has committed cloud spend they're trying to burn down; if yes, loop in "
        "the partner team immediately rather than late in negotiation, since marketplace listings "
        "require lead time to set up correctly for a specific deal. Don't default to marketplace for "
        "every deal, though - for accounts with heavily negotiated custom pricing, a direct contract "
        "usually gives more flexibility than the marketplace's more rigid pricing structure allows.",
    ),
    (
        "Objection Handling: Consumption Pricing Feels Unpredictable",
        "objection_handling",
        "This objection usually means the buyer has been burned by a variable-cost platform before, "
        "not that they've done the math on our specific pricing and found it wanting. Don't respond "
        "with a pricing calculator immediately - first ask what happened at the previous platform "
        "that made costs unpredictable. Almost always the answer is a lack of visibility into what "
        "was driving spend, not the variable pricing model itself. Our answer should lead with "
        "visibility: consumption is tracked per workload, per account, in near-real time, so a cost "
        "spike is traceable to a specific pipeline or query pattern within the same day, not "
        "discovered on next month's invoice. Offer a committed-capacity contract as the actual "
        "answer to the predictability concern - it converts variable draw-down into a fixed, "
        "budgeted number the finance team can plan against, while still only paying for consumption "
        "under the hood. Don't oversell committed capacity as \"fixed pricing\" - it isn't; be "
        "precise that it's a budget ceiling with usage-based draw-down underneath.",
    ),
    (
        "Objection Handling: We Already Have a Data Warehouse",
        "objection_handling",
        "This is rarely a hard no - it's usually a signal that the conversation started too broad. "
        "Narrow immediately to a specific workload or pain point the existing platform handles "
        "poorly, rather than trying to win a head-to-head \"replace everything\" conversation the "
        "buyer isn't ready to have. Good narrowing questions: what's the one report or pipeline that "
        "consistently causes friction (slow, flaky, expensive to run)? Is there a need - sharing "
        "data with an external partner without a manual export - that the current platform can't "
        "do at all, forcing a workaround? Land a single well-scoped workload first rather than "
        "pitching a full migration; migrations are won incrementally in this market, not in one "
        "sales cycle. Avoid "
        "disparaging the incumbent platform directly - the buyer chose it and defending that choice "
        "is a natural reflex; focus entirely on the specific gap, not the platform's general quality.",
    ),
    (
        "Objection Handling: Concerned About Migration Risk and Downtime",
        "objection_handling",
        "Reframe immediately: this almost never needs to be a cutover migration. Most winning deals "
        "start with a net-new workload running in parallel with the existing platform - no data "
        "leaves the incumbent system, nothing is at risk, and the new workload proves itself on its "
        "own merits before any conversation about migrating existing workloads even comes up. This "
        "removes the downtime question entirely for the initial deal. If the account genuinely does "
        "need a full migration eventually (contract expiring, platform being sunset), that's a "
        "separate, later conversation with its own technical validation plan - don't conflate it "
        "with the initial land. Bringing in the SE for a technical deep-dive on the parallel-running "
        "approach, rather than the AE describing it secondhand, meaningfully increases the buyer's "
        "confidence here - this is a credibility objection as much as a technical one.",
    ),
    (
        "FAQ: What Counts as 'Active' Consumption?",
        "faq",
        "An account is considered actively consuming a workload when it has non-zero credits_consumed "
        "in consumption_usage for that workload in the current month. This is distinct from the "
        "capacity_contracts.status field, which reflects a broader relationship-health judgment "
        "(active, at_risk, churned) that a CSM sets based on the consumption trend, not a single "
        "month's activity. An account can be technically \"active\" (some usage this month) while "
        "its contract status is at_risk, if the trend is declining even though it hasn't hit zero. "
        "Always check the trailing 2-3 month trend, not a single month's snapshot, before drawing a "
        "conclusion about account health from consumption data alone.",
    ),
    (
        "FAQ: How Do Capacity Contracts and True-Ups Work?",
        "faq",
        "A capacity contract is a committed dollar amount an account agrees to over a term, drawn "
        "down via actual usage rather than paid as a flat subscription fee. If an account consumes "
        "beyond its committed amount before the term ends, that's handled via a true-up - a new "
        "contract row with contract_type 'true_up', reflecting additional committed capacity added "
        "mid-term rather than waiting for the renewal date. True-ups are generally a healthy signal, "
        "not a billing problem to apologize for - they mean the account is consuming faster than "
        "planned, which is the opposite of the under-consumption pattern that predicts churn. Don't "
        "confuse a true-up with an overage penalty; there isn't one, it's simply additional "
        "committed capacity purchased when the original amount runs out early.",
    ),
    (
        "FAQ: POC Best Practices and Technical Validation Checklist",
        "faq",
        "A POC (tracked as deals.poc_status) should have an explicit, written success criteria "
        "agreed with the account before it starts, not just \"show them the product.\" Best-"
        "performing POCs are scoped to a single, specific use case the account already has pain "
        "with, using their real data where possible rather than a generic demo dataset. The SE owns "
        "the technical validation; the AE owns keeping the business stakeholder engaged in parallel "
        "so the deal doesn't stall purely on technical merits after the POC succeeds. A POC that "
        "drags past four weeks without a clear blocker identified is usually a scoping problem, not "
        "a technical one - go back to the account and re-narrow the success criteria rather than "
        "extending the timeline indefinitely.",
    ),
]


def seed_enablement_content(cur) -> list:
    docs = []
    for title, category, content in ENABLEMENT_DOCS:
        cur.execute(
            """INSERT INTO enablement_content (title, category, content)
               VALUES (%s, %s, %s) RETURNING content_id""",
            (title, category, content),
        )
        content_id = cur.fetchone()[0]
        docs.append({"content_id": content_id, "content": content})
    return docs


# ---------------------------------------------------------------------
# Chunking pass - both sources feed the same document_chunks table,
# via the same chunk_and_insert app/upload.py uses for live uploads.
# ---------------------------------------------------------------------


def main():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(TABLES_IN_TRUNCATE_ORDER)} RESTART IDENTITY CASCADE")

            notes = seed_account_notes(cur)
            docs = seed_enablement_content(cur)

            note_chunks = 0
            for n in notes:
                note_chunks += chunk_and_insert(cur, "account_note", n["note_id"], n["account_id"], n["content"])

            doc_chunks = 0
            for d in docs:
                doc_chunks += chunk_and_insert(cur, "enablement_content", d["content_id"], None, d["content"])

        conn.commit()

    print(
        f"Seeded {len(notes)} account_notes ({note_chunks} chunks), "
        f"{len(docs)} enablement_content docs ({doc_chunks} chunks)."
    )


if __name__ == "__main__":
    main()
