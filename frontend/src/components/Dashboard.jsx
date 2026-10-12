import { Fragment, useEffect, useState } from "react";
import { getLogs } from "../api";
import ReasoningTrace from "./ReasoningTrace";
import { ChatIcon, CheckCircleIcon, ChevronDownIcon, ClockIcon, CoinIcon, TargetIcon } from "./Icons";

// Matches run_eval.py's BUDGET_MAX_AVG_LATENCY_MS - the same budget the
// eval suite is held to, surfaced here so a slow stretch is visible at a
// glance rather than only failing quietly in CI.
const LATENCY_BUDGET_MS = 5000;

export default function Dashboard({ active, onViewOnGraph }) {
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState(null);
  const [expandedRows, setExpandedRows] = useState({});

  const toggleRow = (i) => setExpandedRows((prev) => ({ ...prev, [i]: !prev[i] }));

  useEffect(() => {
    if (!active) return;
    getLogs(50)
      .then((data) => setEntries(data.entries))
      .catch((err) => setError(err.message));
  }, [active]);

  if (error) return <div className="error-banner">{error}</div>;

  if (entries.length === 0) {
    return (
      <div className="chat-empty">
        <span className="chat-avatar agent">
          <ChatIcon width={16} height={16} />
        </span>
        <p>No /ask calls logged yet - try the Ask AI tab first.</p>
      </div>
    );
  }

  const successCount = entries.filter((e) => e.success).length;
  const successRate = Math.round((successCount / entries.length) * 100);
  const avgLatency = Math.round(
    entries.reduce((sum, e) => sum + e.total_latency_ms, 0) / entries.length
  );
  const maxLatency = Math.max(...entries.map((e) => e.total_latency_ms));
  const totalCost = entries.reduce((sum, e) => sum + (e.estimated_cost_usd || 0), 0);
  const sqlEntries = entries.filter((e) => e.response_type === "sql");
  const verifiedCount = sqlEntries.filter((e) => e.verified).length;
  const verifiedRate = sqlEntries.length > 0 ? Math.round((verifiedCount / sqlEntries.length) * 100) : null;

  const stats = [
    {
      icon: <ChatIcon width={15} height={15} />,
      value: entries.length,
      label: "Calls logged",
      tone: "neutral",
    },
    {
      icon: <CheckCircleIcon width={15} height={15} />,
      value: `${successRate}%`,
      label: "Success rate",
      tone: successRate >= 95 ? "good" : successRate >= 80 ? "warn" : "bad",
    },
    {
      icon: <ClockIcon width={15} height={15} />,
      value: `${avgLatency} ms`,
      label: "Avg latency",
      tone: avgLatency <= LATENCY_BUDGET_MS ? "neutral" : "warn",
    },
    {
      icon: <CoinIcon width={15} height={15} />,
      value: `$${totalCost.toFixed(4)}`,
      label: "Est. total cost",
      tone: "neutral",
    },
  ];

  if (verifiedRate !== null) {
    stats.push({
      icon: <TargetIcon width={15} height={15} />,
      value: `${verifiedRate}%`,
      label: `Verified hit rate (${verifiedCount}/${sqlEntries.length})`,
      tone: "accent",
    });
  }

  return (
    <div className="dashboard">
      <p className="dashboard-intro">
        Every question sent to the agent, logged automatically - latency, cost, and how it was routed.
      </p>

      <div className="dashboard-stats">
        {stats.map((s, i) => (
          <div className="stat-tile" key={i}>
            <span className={`stat-icon tone-${s.tone}`}>{s.icon}</span>
            <div>
              <div className={`stat-value tone-${s.tone}`}>{s.value}</div>
              <div className="stat-label">{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="latency-chart-card">
        <div className="latency-chart-header">
          <span>Latency - last {entries.length} calls, oldest to newest</span>
          <span className="latency-chart-avg">avg {avgLatency} ms</span>
        </div>
        <div className="latency-chart">
          <div
            className="latency-avg-line"
            style={{ bottom: `${Math.min(100, (avgLatency / maxLatency) * 100)}%` }}
          />
          {entries
            .slice()
            .reverse()
            .map((e, i) => (
              <div
                key={i}
                className={`latency-bar ${e.success ? "" : "failed"}`}
                style={{ height: `${Math.max(4, (e.total_latency_ms / maxLatency) * 100)}%` }}
                title={`${e.question} - ${e.total_latency_ms}ms`}
              />
            ))}
        </div>
      </div>

      <div className="table-scroll">
        <table className="log-table">
          <thead>
            <tr>
              <th className="log-expand-col" />
              <th>Time</th>
              <th>Question</th>
              <th>Status</th>
              <th>Source</th>
              <th className="num">Attempts</th>
              <th className="num">Rows</th>
              <th className="num">Latency</th>
              <th className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e, i) => {
              const hasTrace = e.trace && e.trace.length > 0;
              const isOpen = !!expandedRows[i];
              return (
                <Fragment key={i}>
                  <tr
                    className={`${i % 2 === 0 ? "log-row-even" : ""}${hasTrace ? " log-row-clickable" : ""}`}
                    onClick={hasTrace ? () => toggleRow(i) : undefined}
                  >
                    <td className="log-expand-col">
                      {hasTrace && (
                        <ChevronDownIcon
                          className={`log-expand-chevron${isOpen ? " open" : ""}`}
                          width={13}
                          height={13}
                        />
                      )}
                    </td>
                    <td className="cell-muted">{e.timestamp}</td>
                    <td className="log-question" title={e.question}>
                      {e.question}
                    </td>
                    <td>
                      <span className={`status-pill ${e.success ? "ok" : "fail"}`}>
                        <span className="status-pill-dot" />
                        {e.success ? "success" : "failed"}
                      </span>
                    </td>
                    <td>
                      {e.response_type === "sql" ? (
                        <span className={`verified-badge ${e.verified ? "verified" : "generated"}`}>
                          {e.verified ? "✓ verified" : "⚡ generated"}
                        </span>
                      ) : (
                        <span className="cell-muted">-</span>
                      )}
                    </td>
                    <td className="num">{e.attempts ?? "-"}</td>
                    <td className="num">{e.row_count ?? "-"}</td>
                    <td className="num">{e.total_latency_ms} ms</td>
                    <td className="num">
                      {e.estimated_cost_usd != null ? `$${e.estimated_cost_usd.toFixed(5)}` : "-"}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="log-trace-row">
                      <td />
                      <td colSpan={8}>
                        <ReasoningTrace
                          trace={e.trace}
                          responseType={e.response_type}
                          onViewOnGraph={onViewOnGraph}
                          defaultExpanded
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
