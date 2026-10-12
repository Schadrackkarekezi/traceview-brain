import { useState } from "react";
import { ChevronDownIcon } from "./Icons";

const STEP_META = {
  verified_match: { icon: "✓", label: "Verified match" },
  retrieve: { icon: "📚", label: "Retrieved business context" },
  graph_lookup: { icon: "🕸️", label: "Graph lookup" },
  memory_recall: { icon: "🧠", label: "Recalled long-term memory" },
  respond: { icon: "⚙️", label: "Generate response" },
  unstructured_retrieve: { icon: "📄", label: "Retrieved account notes / enablement content" },
  synthesize: { icon: "✍️", label: "Synthesize answer" },
  explain_sql: { icon: "💡", label: "Explain result" },
};

function describeStep(step) {
  if (step.step === "verified_match") {
    return `matched "${step.matched_question}" (${(step.similarity * 100).toFixed(1)}% similarity) - skipped generation entirely`;
  }

  if (step.step === "retrieve") {
    const terms = step.retrieved_terms || [];
    const history = step.history_turns_used || 0;
    const parts = [
      terms.length > 0 ? `matched: ${terms.join(", ")}` : "no matching business terms",
    ];
    if (history > 0) parts.push(`using ${history} prior turn${history > 1 ? "s" : ""} of context`);
    return parts.join(" · ");
  }

  if (step.step === "graph_lookup") {
    const entities = step.relevant_entities || [];
    const paths = step.join_paths || [];
    if (entities.length === 0) return "no relevant entities detected";
    if (paths.length === 0) return `entities: ${entities.join(", ")} (single table, no join needed)`;
    return `entities: ${entities.join(", ")} · ${paths.length} join path${paths.length > 1 ? "s" : ""} found`;
  }

  if (step.step === "memory_recall") {
    const memories = step.memories || [];
    if (step.error) return `recall failed (${step.error}) - answered without memory`;
    if (memories.length === 0) return `nothing remembered about ${step.user} that's relevant here`;
    return `${memories.length} memor${memories.length > 1 ? "ies" : "y"} about ${step.user}: ${memories
      .map((m) => `"${m.text}"`)
      .join(" · ")}`;
  }

  if (step.step === "respond") {
    if (step.status === "chat") return "replied conversationally, no query needed";
    if (step.status === "error") return `attempt ${step.attempt} failed: ${step.error} - retrying`;
    return `attempt ${step.attempt} · ${step.row_count} row${step.row_count === 1 ? "" : "s"} returned`;
  }

  if (step.step === "unstructured_retrieve") {
    const sources = step.sources || [];
    const ids = step.resolved_account_ids || [];
    const scope = ids.length === 0 ? "company-wide" : ids.length === 1 ? `account #${ids[0]}` : `accounts #${ids.join(", #")}`;
    // Which store really answered - "pgvector (HydraDB unavailable)" is
    // the fallback path, worth seeing rather than hiding.
    const backend =
      step.backend === "hydradb" ? "HydraDB" : step.fallback_reason ? "pgvector (HydraDB unavailable)" : "pgvector";
    const paths = step.graph_paths || [];
    const graph = paths.length > 0 ? ` · ${paths.length} graph path${paths.length > 1 ? "s" : ""}` : "";
    if (sources.length === 0) return `no matching context found in ${backend} (${scope})`;
    return `${sources.length} source${sources.length > 1 ? "s" : ""} from ${backend} (${scope}) - top score ${(sources[0].score * 100).toFixed(0)}%${graph}`;
  }

  if (step.step === "synthesize") {
    if (step.status === "error") return "writing the summary failed - falling back to raw results";
    return "wrote the final answer from the retrieved sources";
  }

  if (step.step === "explain_sql") {
    if (step.status === "error") return "explanation failed - the result above is unaffected";
    return "wrote a short note on what the result shows";
  }

  return "";
}

function stepStatus(step) {
  if (step.step === "respond" && step.status === "error") return "warn";
  if (step.step === "synthesize" && step.status === "error") return "warn";
  if (step.step === "explain_sql" && step.status === "error") return "warn";
  if (step.step === "memory_recall" && step.error) return "warn";
  if (step.step === "unstructured_retrieve" && step.fallback_reason) return "warn";
  return "ok";
}

const MODE_META = {
  sql: { icon: "🔧", label: "SQL" },
  unstructured: { icon: "📄", label: "Unstructured" },
  hybrid: { icon: "🔀", label: "Hybrid" },
  chat: { icon: "💬", label: "Chat" },
};

function summarize(trace) {
  const totalMs = trace.reduce((sum, s) => sum + (s.latency_ms ?? s.generate_latency_ms ?? 0), 0);
  const hasWarning = trace.some((s) => stepStatus(s) === "warn");
  const verified = trace.some((s) => s.step === "verified_match");
  return { totalMs: Math.round(totalMs), hasWarning, verified, count: trace.length };
}

// The mode a plain step count doesn't tell you - "3 steps" looks the same
// whether the agent decided this was a SQL lookup or a hybrid answer, and
// that routing decision is exactly the thing worth seeing at a glance
// rather than only after expanding.
export default function ReasoningTrace({ trace, responseType, onViewOnGraph, defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!trace || trace.length === 0) return null;

  const { totalMs, hasWarning, verified, count } = summarize(trace);
  const mode = MODE_META[responseType];

  return (
    <div className={`reasoning-trace-wrap${hasWarning ? " has-warning" : ""}`}>
      <button className="reasoning-trace-toggle" onClick={() => setExpanded((e) => !e)} aria-expanded={expanded}>
        <ChevronDownIcon className={`reasoning-trace-chevron${expanded ? " open" : ""}`} width={13} height={13} />
        {mode && (
          <span className={`reasoning-trace-mode mode-${responseType}`}>
            {mode.icon} {mode.label}
          </span>
        )}
        <span>
          {verified ? "verified match" : `${count} step${count > 1 ? "s" : ""}`} · {totalMs}ms
          {hasWarning ? " · see steps" : ""}
        </span>
      </button>

      {expanded && (
        <ol className="reasoning-trace">
          {trace.map((step, i) => {
            const meta = STEP_META[step.step] || { icon: "•", label: step.step };
            const status = stepStatus(step);
            const canViewOnGraph =
              step.step === "graph_lookup" && (step.relevant_entities || []).length > 0 && onViewOnGraph;
            return (
              <li key={i} className={`trace-step trace-${status}`}>
                <span className="trace-icon">{meta.icon}</span>
                <div className="trace-body">
                  <div className="trace-label">
                    {meta.label}
                    <span className="trace-latency">{step.latency_ms ?? step.generate_latency_ms} ms</span>
                  </div>
                  <div className="trace-detail">{describeStep(step)}</div>
                  {canViewOnGraph && (
                    <button
                      className="trace-view-on-graph"
                      onClick={() => onViewOnGraph(step.relevant_entities)}
                    >
                      View on graph ↗
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
