import { useEffect, useState } from "react";
import { runQuery } from "../api";
import { SUGGESTED_QUESTIONS } from "../suggestedQuestions";
import {
  SendIcon,
  ArrowRightIcon,
  DatabaseIcon,
  CheckCircleIcon,
  TargetIcon,
  CoinIcon,
  TerminalIcon,
  GraphIcon,
} from "./Icons";

// The proactive half of the app - surfaced the moment Home loads, not
// only when someone thinks to ask. Ranks by how far under target an
// account is trending, and deliberately includes accounts still labeled
// "active": capacity_contracts.status is a lagging, manually-set label
// (see the "under-consumption" glossary definition), so scanning only
// for accounts already marked at_risk misses exactly the accounts this
// is supposed to catch early. account_notes.content is fetched inline
// (a plain correlated subquery, not a semantic search) since we already
// know the account - there's nothing to search for, just the latest
// note to show as the "why."
const ATTENTION_QUERY = `
SELECT a.account_id, a.name, a.industry, w.name AS workload, cc.status,
       ROUND(AVG(cu.credits_consumed) / (cc.committed_amount / 12) * 100) AS pct_of_target,
       (SELECT content FROM account_notes an
        WHERE an.account_id = a.account_id
        ORDER BY note_date DESC LIMIT 1) AS latest_note
FROM capacity_contracts cc
JOIN accounts a ON a.account_id = cc.account_id
JOIN workloads w ON w.workload_id = cc.workload_id
JOIN consumption_usage cu ON cu.account_id = cc.account_id AND cu.workload_id = cc.workload_id
WHERE cc.status IN ('active', 'at_risk')
  AND cu.usage_month >= (SELECT MAX(usage_month) FROM consumption_usage) - INTERVAL '2 months'
GROUP BY a.account_id, a.name, a.industry, w.name, cc.status, cc.committed_amount
HAVING AVG(cu.credits_consumed) < (cc.committed_amount / 12) * 0.7
ORDER BY pct_of_target ASC
LIMIT 6
`.trim();

const SUGGESTIONS = SUGGESTED_QUESTIONS.slice(0, 3);

function greeting() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

// A snapshot query per tile - each one is a plain read-only SELECT run
// through the same safety-guarded /query endpoint the SQL editor uses, not
// a separate stats API. If a tile is wrong, it's wrong for the same reason
// a query editor result would be wrong - one code path, not two.
const SNAPSHOT_QUERIES = [
  { label: "Accounts", sql: "SELECT COUNT(*) FROM accounts", icon: DatabaseIcon, tone: "neutral" },
  {
    label: "Active contracts",
    sql: "SELECT COUNT(*) FROM capacity_contracts WHERE status = 'active'",
    icon: CheckCircleIcon,
    tone: "good",
  },
  {
    label: "At-risk contracts",
    sql: "SELECT COUNT(*) FROM capacity_contracts WHERE status = 'at_risk'",
    icon: TargetIcon,
    tone: "warn",
  },
  {
    label: "Committed capacity",
    sql: "SELECT ROUND(SUM(committed_amount)) FROM capacity_contracts WHERE status = 'active'",
    format: (v) => `$${(Number(v) / 1_000_000).toFixed(1)}M`,
    icon: CoinIcon,
    tone: "neutral",
  },
];

const LINKS = [
  {
    label: "Query Editor",
    description: "Write raw SQL directly, same safety guard as the agent",
    icon: TerminalIcon,
    target: "query",
  },
  {
    label: "Knowledge Graph",
    description: "Explore the schema's real relationships, interactively",
    icon: GraphIcon,
    target: "graph",
  },
];

export default function Home({ onAsk }) {
  const [question, setQuestion] = useState("");
  const [snapshot, setSnapshot] = useState(null);
  const [snapshotError, setSnapshotError] = useState(null);
  const [attention, setAttention] = useState(null);
  const [attentionError, setAttentionError] = useState(null);

  useEffect(() => {
    Promise.all(SNAPSHOT_QUERIES.map((q) => runQuery(q.sql)))
      .then((results) => {
        setSnapshot(
          results.map((r, i) => {
            const raw = r.rows[0][0];
            const q = SNAPSHOT_QUERIES[i];
            return q.format ? q.format(raw) : raw;
          })
        );
      })
      .catch((err) => setSnapshotError(err.message));

    runQuery(ATTENTION_QUERY)
      .then((result) => {
        const idx = Object.fromEntries(result.columns.map((c, i) => [c, i]));
        setAttention(
          result.rows.map((row) => ({
            accountId: row[idx.account_id],
            name: row[idx.name],
            industry: row[idx.industry],
            workload: row[idx.workload],
            status: row[idx.status],
            pct: row[idx.pct_of_target],
            note: row[idx.latest_note],
          }))
        );
      })
      .catch((err) => setAttentionError(err.message));
  }, []);

  const submit = () => {
    if (!question.trim()) return;
    onAsk(question);
    setQuestion("");
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="home-view">
      <div className="home-hero">
        <h1>{greeting()}. I'm Traceview, your GTM AI assistant.</h1>
        <p>
          Ask about any account, deal, or usage trend, and I'll pull the real numbers and the
          notes behind them to give you a data-backed answer.
        </p>

        <div className="home-ask-box">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="e.g. Which accounts are under-consuming ahead of renewal?"
            rows={2}
          />
          <button className="home-ask-submit" onClick={submit} disabled={!question.trim()}>
            <SendIcon />
          </button>
        </div>

        <div className="home-suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="suggestion-chip" onClick={() => onAsk(s)}>
              {s}
            </button>
          ))}
        </div>
      </div>

      <div className="home-snapshot">
        <div className="home-snapshot-label">GTM snapshot</div>
        {snapshotError && <div className="error-banner">{snapshotError}</div>}
        <div className="home-snapshot-tiles">
          {SNAPSHOT_QUERIES.map((q, i) => (
            <div className="home-snapshot-tile" key={q.label}>
              <span className={`stat-icon tone-${q.tone}`}>
                <q.icon width={15} height={15} />
              </span>
              <div>
                <div className={`home-snapshot-value tone-${q.tone}`}>
                  {snapshot ? snapshot[i] : "…"}
                </div>
                <div className="home-snapshot-tile-label">{q.label}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="home-attention">
        <div className="home-snapshot-label">Needs attention</div>
        {attentionError && <div className="error-banner">{attentionError}</div>}
        {attention && attention.length === 0 && (
          <p className="home-attention-empty">Nothing trending under target right now.</p>
        )}
        {attention === null && !attentionError && (
          <p className="home-attention-empty">Scanning consumption trends…</p>
        )}
        {attention && attention.length > 0 && (
          <div className="home-attention-list">
            {attention.map((a) => (
              <button
                key={`${a.accountId}-${a.workload}`}
                className="home-attention-card"
                onClick={() => onAsk(`Why is ${a.name}'s consumption declining?`)}
              >
                <span className={`stat-icon tone-${a.status === "at_risk" ? "bad" : "warn"}`}>
                  <TargetIcon width={15} height={15} />
                </span>
                <div className="home-attention-body">
                  <div className="home-attention-head">
                    <strong>{a.name}</strong>
                    <span className={`status-pill ${a.status === "at_risk" ? "fail" : "ok"}`}>
                      <span className="status-pill-dot" />
                      {a.status === "at_risk" ? "at risk" : "trending down"}
                    </span>
                    <span className="home-attention-pct">{a.pct}% of target</span>
                  </div>
                  <div className="home-attention-meta">
                    {a.industry.replace("_", " ")} · {a.workload}
                  </div>
                  {a.note && <div className="home-attention-note">{a.note}</div>}
                </div>
                <ArrowRightIcon className="home-link-arrow" />
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="home-links">
        {LINKS.map((link) => (
          <button className="home-link-card" key={link.label} onClick={() => onAsk(null, link.target)}>
            <span className="stat-icon tone-accent">
              <link.icon width={16} height={16} />
            </span>
            <div>
              <strong>{link.label}</strong>
              <span>{link.description}</span>
            </div>
            <ArrowRightIcon className="home-link-arrow" />
          </button>
        ))}
      </div>
    </div>
  );
}
