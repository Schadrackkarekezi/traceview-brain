"""
Seeds the Traceview Postgres database with synthetic, GTM-authentic data -
consumption-based, not seat-based: workload adoption, capacity contracts,
credit-consumption trends, modeled after how any usage-based data
platform business works generically, not any one real company.
Deterministic (random.seed(42)), so re-running produces the same
dataset every time.

The schema itself lives in schema.sql, applied once by docker-compose when
the db container is first created. This script only owns data: it
truncates every table and re-inserts, so it's safe to re-run any time
without recreating the container.

Consumption trends are deliberately correlated to contract status here -
a "declining" trend account is far more likely to end up "at_risk" than a
"growing" one - rather than assigning both independently at random. Real
questions like "which accounts are under-consuming ahead of renewal" only
mean something if that correlation actually exists in the data, not just
in the prompt asking about it. This is a first pass at that correlation;
deepening it (tying trends to account-note narrative, sharper thresholds)
is its own later phase, not attempted here.
"""

import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.database import get_connection

random.seed(42)

REFERENCE_DATE = date(2026, 2, 1)  # "today" for this static synthetic dataset

WORKLOADS = [
    "Data Warehousing",
    "Storage",
    "Data Sharing",
]

INDUSTRIES = ["financial_services", "retail", "healthcare", "media", "public_sector", "technology", "other"]
SEGMENTS = ["enterprise", "strategic", "commercial"]
REGIONS = ["NA-WEST", "NA-EAST", "NA-CENTRAL", "EMEA", "APAC"]

INDUSTRY_SUFFIXES = {
    "financial_services": ["Financial Group", "Capital Partners", "Trust & Co."],
    "retail": ["Retail Co.", "Retail Group", "Consumer Brands"],
    "healthcare": ["Health Systems", "Medical Group", "Care Partners"],
    "media": ["Media Group", "Broadcasting Co.", "Studios"],
    "public_sector": ["County Services", "Public Works", "Municipal Systems"],
    "technology": ["Technologies", "Software Inc.", "Systems Inc."],
    "other": ["Industries", "Holdings", "Enterprises"],
}

ACCOUNT_NAME_STEMS = [
    "Meridian", "Vertex", "Sterling", "Ashford", "Marchetti", "Falcon", "Beacon", "Cobalt", "Union",
    "Whitfield", "Delgado", "Nimbus", "Anchor", "Coastline", "Rourke", "Vantage", "Granite", "Sinclair",
    "Harbor", "Comfort", "Parkview", "Nightingale", "Fairmont", "Dellwood", "Lakeside", "Redwood",
    "Codegate", "Osei", "Lindqvist", "Bishop", "Harlow", "Kessler", "Bramwell", "Castellan", "Donati",
    "Ferris", "Grantham", "Holloway", "Ibarra", "Jansen", "Kovac", "Lassiter", "Montrose", "Novak",
    "Osgood", "Prentice", "Quill", "Reeve", "Sawyer", "Thornbury", "Ulric", "Voss", "Wexford",
    "Yates", "Zeller",
]

FIRST_NAMES = [
    "Dana", "Marcus", "Priya", "Jordan", "Elena", "Sam", "Nina", "Chen", "Omar", "Grace", "Leo", "Maya",
    "Trevor", "Ivy", "Caleb", "Rosa", "Felix", "Aisha", "Miles", "Talia", "Derek", "Yuki", "Noah",
    "Sofia", "Ravi", "Hana", "Owen", "Zara", "Diego", "Lena",
]
LAST_NAMES = [
    "Reyes", "Chen", "Nair", "Patel", "Kowalski", "Nguyen", "Brooks", "Osei", "Levin", "Foster",
    "Okafor", "Martinez", "Whitfield", "Sato", "Bianchi", "Hassan", "Turner", "Delgado", "Kim", "Ahmed",
]

PARTNER_NAMES = [
    ("Meristem Consulting", "SI"),
    ("Bluecrest Advisory", "SI"),
    ("Fenwick Digital", "SI"),
    ("Skyline Cloud Marketplace", "cloud_marketplace"),
    ("Harborlight Cloud Marketplace", "cloud_marketplace"),
    ("Northline Cloud Marketplace", "cloud_marketplace"),
    ("Streamline Analytics", "ISV"),
    ("Orbital Data Partners", "ISV"),
]

CAMPAIGNS = [
    "Annual Customer Conference Follow-up",
    "Platform Innovators Webinar",
    "Data Platform Roadshow",
    "Data Sharing Launch Campaign",
    "Partner Co-Sell: Cloud Marketplace",
    "Analyst Report: State of Consumption-Based Platforms",
    "Customer Advisory Board Invite",
    "Regional Field Event",
]

TABLES_IN_TRUNCATE_ORDER = [
    "marketing_touches", "activities", "consumption_usage", "capacity_contracts",
    "deals", "partners", "account_team", "accounts", "workloads",
]


def _money(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _random_date(start: date, end: date) -> date:
    span = (end - start).days
    if span <= 0:
        return start
    return start + timedelta(days=random.randint(0, span))


def seed_workloads(cur) -> dict:
    ids = {}
    for name in WORKLOADS:
        cur.execute("INSERT INTO workloads (name) VALUES (%s) RETURNING workload_id", (name,))
        ids[name] = cur.fetchone()[0]
    return ids


def seed_accounts(cur, n=50) -> list:
    stems = random.sample(ACCOUNT_NAME_STEMS, n)
    accounts = []
    for stem in stems:
        industry = random.choice(INDUSTRIES)
        segment = random.choices(SEGMENTS, weights=[0.35, 0.30, 0.35])[0]
        region = random.choice(REGIONS)
        name = f"{stem} {random.choice(INDUSTRY_SUFFIXES[industry])}"
        created_at = _random_date(date(2021, 1, 1), date(2024, 6, 1))
        cur.execute(
            """INSERT INTO accounts (name, segment, industry, region, created_at)
               VALUES (%s, %s, %s, %s, %s) RETURNING account_id""",
            (name, segment, industry, region, created_at),
        )
        account_id = cur.fetchone()[0]
        accounts.append({"account_id": account_id, "segment": segment, "industry": industry, "created_at": created_at})
    return accounts


def seed_account_team(cur, accounts) -> dict:
    """{account_id: {"AE": team_member_id, "SE": ..., "CSM": ...}}"""
    team_by_account = {}
    for acct in accounts:
        team_by_account[acct["account_id"]] = {}
        for role in ("AE", "SE", "CSM"):
            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            cur.execute(
                "INSERT INTO account_team (account_id, name, role) VALUES (%s, %s, %s) RETURNING team_member_id",
                (acct["account_id"], name, role),
            )
            team_by_account[acct["account_id"]][role] = cur.fetchone()[0]
    return team_by_account


def seed_partners(cur) -> list:
    ids = []
    for name, ptype in PARTNER_NAMES:
        cur.execute(
            "INSERT INTO partners (name, partner_type) VALUES (%s, %s) RETURNING partner_id", (name, ptype)
        )
        ids.append(cur.fetchone()[0])
    return ids


DEAL_VALUE_RANGES = {
    "enterprise": (300_000, 2_000_000),
    "strategic": (500_000, 3_000_000),
    "commercial": (50_000, 400_000),
}


def seed_deals(cur, accounts, workload_ids, team_by_account, partner_ids) -> list:
    deals = []
    workload_names = list(workload_ids.keys())

    for acct in accounts:
        n_deals = random.choice([1, 1, 2, 2, 2, 3])
        for _ in range(n_deals):
            workload_name = random.choice(workload_names)
            created_date = _random_date(date(2023, 6, 1), date(2026, 1, 1))

            stage = random.choices(
                ["closed_won", "closed_lost", "discovery", "technical_validation", "business_case", "negotiation"],
                weights=[0.50, 0.20, 0.08, 0.08, 0.07, 0.07],
            )[0]

            if stage == "closed_won":
                poc_status = random.choices(["passed", "in_progress"], weights=[0.9, 0.1])[0]
            elif stage == "closed_lost":
                poc_status = random.choices(["failed", "not_started", "in_progress"], weights=[0.6, 0.2, 0.2])[0]
            elif stage in ("discovery",):
                poc_status = random.choices(["not_started", "in_progress"], weights=[0.7, 0.3])[0]
            else:
                poc_status = random.choices(["in_progress", "passed"], weights=[0.6, 0.4])[0]

            close_date = None
            if stage in ("closed_won", "closed_lost"):
                close_date = created_date + timedelta(days=random.randint(45, 240))
                if close_date > REFERENCE_DATE:
                    close_date = REFERENCE_DATE

            lo, hi = DEAL_VALUE_RANGES[acct["segment"]]
            deal_value = _money(random.uniform(lo, hi))

            partner_id = random.choice(partner_ids) if random.random() < 0.3 else None
            owner_id = team_by_account[acct["account_id"]]["AE"]

            cur.execute(
                """INSERT INTO deals
                   (account_id, workload_id, owner_team_member_id, partner_id, stage, poc_status,
                    deal_value, created_date, close_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING deal_id""",
                (
                    acct["account_id"], workload_ids[workload_name], owner_id, partner_id, stage, poc_status,
                    deal_value, created_date, close_date,
                ),
            )
            deal_id = cur.fetchone()[0]
            deals.append(
                {
                    "deal_id": deal_id, "account_id": acct["account_id"], "workload_id": workload_ids[workload_name],
                    "workload_name": workload_name, "stage": stage, "poc_status": poc_status,
                    "deal_value": deal_value, "created_date": created_date, "close_date": close_date,
                }
            )
    return deals


def seed_capacity_contracts_and_usage(cur, deals) -> None:
    for deal in deals:
        if deal["stage"] != "closed_won":
            continue

        committed = _money(deal["deal_value"] * Decimal(str(random.uniform(0.9, 1.1))))
        contract_type = random.choices(["new", "expansion", "renewal", "true_up"], weights=[0.5, 0.3, 0.15, 0.05])[0]
        term_start = deal["close_date"]
        term_years = random.choice([1, 1, 2])
        term_end = date(term_start.year + term_years, term_start.month, min(term_start.day, 28))

        trend = random.choices(["growing", "stable", "declining"], weights=[0.35, 0.35, 0.30])[0]

        monthly_target = committed / Decimal(12)
        months_elapsed = min(12, max(1, (min(REFERENCE_DATE, term_end).year - term_start.year) * 12
                                      + (min(REFERENCE_DATE, term_end).month - term_start.month) + 1))

        usage_rows = []
        level = float(monthly_target) * (0.6 if trend == "growing" else 0.9 if trend == "declining" else 0.9)
        for m in range(months_elapsed):
            usage_month = date(
                term_start.year + (term_start.month - 1 + m) // 12,
                (term_start.month - 1 + m) % 12 + 1,
                1,
            )
            if trend == "growing":
                level *= random.uniform(1.03, 1.08)
            elif trend == "declining":
                level *= random.uniform(0.88, 0.97)
            else:
                level *= random.uniform(0.97, 1.03)
            level = max(level, float(monthly_target) * 0.05)

            credits = _money(level)
            warehouses = max(2, min(30, round(level / float(monthly_target) * random.uniform(10, 16))))
            usage_rows.append((deal["account_id"], deal["workload_id"], usage_month, credits, warehouses))

        cur.executemany(
            """INSERT INTO consumption_usage (account_id, workload_id, usage_month, credits_consumed, active_warehouses)
               VALUES (%s, %s, %s, %s, %s)""",
            usage_rows,
        )

        last_ratio = (usage_rows[-1][3] / monthly_target) if usage_rows else Decimal(1)
        if trend == "declining" and last_ratio < Decimal("0.5"):
            status = random.choices(["at_risk", "churned"], weights=[0.7, 0.3])[0]
        elif trend == "declining":
            status = random.choices(["active", "at_risk"], weights=[0.5, 0.5])[0]
        else:
            status = random.choices(["active", "at_risk"], weights=[0.95, 0.05])[0]

        cur.execute(
            """INSERT INTO capacity_contracts
               (account_id, workload_id, deal_id, committed_amount, contract_type, status, term_start, term_end)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                deal["account_id"], deal["workload_id"], deal["deal_id"], committed, contract_type, status,
                term_start, term_end,
            ),
        )


ACTIVITY_SEQUENCE_BY_STAGE = {
    "discovery": [("discovery_call", "AE")],
    "technical_validation": [("discovery_call", "AE"), ("poc_kickoff", "SE"), ("poc_technical_review", "SE")],
    "business_case": [
        ("discovery_call", "AE"), ("poc_kickoff", "SE"), ("poc_technical_review", "SE"), ("exec_review", "AE"),
    ],
    "negotiation": [
        ("discovery_call", "AE"), ("poc_kickoff", "SE"), ("poc_technical_review", "SE"), ("exec_review", "AE"),
    ],
    "closed_won": [
        ("discovery_call", "AE"), ("poc_kickoff", "SE"), ("poc_technical_review", "SE"), ("exec_review", "AE"),
        ("qbr", "CSM"),
    ],
    "closed_lost": [("discovery_call", "AE"), ("poc_kickoff", "SE"), ("poc_technical_review", "SE")],
}


def seed_activities(cur, deals, team_by_account) -> None:
    rows = []
    for deal in deals:
        sequence = ACTIVITY_SEQUENCE_BY_STAGE[deal["stage"]]
        team = team_by_account[deal["account_id"]]
        anchor = deal["created_date"]
        for i, (activity_type, role) in enumerate(sequence):
            activity_date = anchor + timedelta(days=random.randint(i * 10, i * 10 + 15))
            if activity_date > REFERENCE_DATE:
                break
            team_member_id = team.get(role, team["AE"])
            rows.append((deal["deal_id"], team_member_id, activity_type, activity_date))

        if deal["stage"] == "closed_won" and deal["close_date"]:
            for months_after in (3, 6, 9):
                renewal_date = deal["close_date"] + timedelta(days=months_after * 30)
                if renewal_date > REFERENCE_DATE:
                    break
                rows.append((deal["deal_id"], team["CSM"], "renewal_call", renewal_date))

    cur.executemany(
        "INSERT INTO activities (deal_id, team_member_id, activity_type, activity_date) VALUES (%s, %s, %s, %s)",
        rows,
    )


def seed_marketing_touches(cur, accounts) -> None:
    rows = []
    for acct in accounts:
        n_touches = random.randint(2, 6)
        for _ in range(n_touches):
            campaign = random.choice(CAMPAIGNS)
            channel = random.choices(
                ["webinar", "paid_ad", "summit", "field_event", "partner_cosell", "email"],
                weights=[0.25, 0.15, 0.15, 0.15, 0.1, 0.2],
            )[0]
            engagement = random.choices(["sent", "opened", "clicked", "attended"], weights=[0.4, 0.3, 0.2, 0.1])[0]
            touch_date = _random_date(date(2024, 1, 1), REFERENCE_DATE)
            rows.append((acct["account_id"], campaign, channel, touch_date, engagement))

    cur.executemany(
        """INSERT INTO marketing_touches (account_id, campaign_name, channel, touch_date, engagement_type)
           VALUES (%s, %s, %s, %s, %s)""",
        rows,
    )


def main():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(TABLES_IN_TRUNCATE_ORDER)} RESTART IDENTITY CASCADE")

            workload_ids = seed_workloads(cur)
            accounts = seed_accounts(cur)
            team_by_account = seed_account_team(cur, accounts)
            partner_ids = seed_partners(cur)
            deals = seed_deals(cur, accounts, workload_ids, team_by_account, partner_ids)
            seed_capacity_contracts_and_usage(cur, deals)
            seed_activities(cur, deals, team_by_account)
            seed_marketing_touches(cur, accounts)

        conn.commit()

    print(
        f"Seeded {len(accounts)} accounts, {len(deals)} deals, "
        f"{sum(1 for d in deals if d['stage'] == 'closed_won')} capacity contracts."
    )


if __name__ == "__main__":
    main()
