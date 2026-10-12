import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import ResultsTable from "./ResultsTable";
import ReasoningTrace from "./ReasoningTrace";
import { SendIcon, SparkleIcon, ChevronDownIcon, PlusIcon, CloseIcon } from "./Icons";
import { askQuestionStream, clearConversation, sendFeedback } from "../api";
import { readJSON } from "../storage";
import { SUGGESTED_QUESTIONS as SUGGESTIONS } from "../suggestedQuestions";

function newConversationId() {
  return crypto.randomUUID();
}

// No login yet, so "history" here means per-browser-session, not per-user:
// sessionStorage (not localStorage) on purpose - it survives a page
// refresh but clears when the tab closes, which is the honest boundary
// given the backend's own conversation memory is also just in-process and
// gone on restart (documented in conversations.py). Persisting further
// than that would silently promise more durability than actually exists.
const CONV_LIST_KEY = "traceview_conversations";
const CONV_PREFIX = "traceview_conv_";
const ACTIVE_CONV_KEY = "traceview_active_conversation";

function loadConversationList() {
  return readJSON(sessionStorage, CONV_LIST_KEY, []);
}

function saveConversationList(list) {
  sessionStorage.setItem(CONV_LIST_KEY, JSON.stringify(list));
}

function loadConversationHistory(id) {
  return readJSON(sessionStorage, CONV_PREFIX + id, []);
}

function saveConversationHistory(id, history) {
  sessionStorage.setItem(CONV_PREFIX + id, JSON.stringify(history));
}

function titleFromQuestion(q) {
  const trimmed = q.trim();
  return trimmed.length > 40 ? trimmed.slice(0, 40) + "…" : trimmed;
}

const SOURCE_LABELS = {
  account_note: "Account note",
  enablement_content: "Enablement content",
};

function SourceCard({ s }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = s.text.length > 90;

  return (
    <div className={`chat-source-card${expanded ? " expanded" : ""}`}>
      <button
        className="chat-source-card-head"
        onClick={() => setExpanded((e) => !e)}
        disabled={!isLong}
      >
        <span className={`chat-source-type ${s.source_type}`}>{SOURCE_LABELS[s.source_type] || s.source_type}</span>
        <span className="chat-source-snippet">
          {expanded ? s.text : `${s.text.slice(0, 90)}${isLong ? "…" : ""}`}
        </span>
        {isLong && (
          <ChevronDownIcon
            className={`chat-source-chevron${expanded ? " open" : ""}`}
            width={12}
            height={12}
          />
        )}
      </button>
      {expanded && (
        <div className="chat-source-meta">
          {s.title && `${s.title} · `}
          match score {(s.score * 100).toFixed(0)}%
          {s.account_id != null && ` · account #${s.account_id}`}
        </div>
      )}
    </div>
  );
}

function Sources({ sources }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="chat-sources">
      <div className="chat-sources-label">Grounded in {sources.length} retrieved source{sources.length > 1 ? "s" : ""}</div>
      <div className="chat-sources-list">
        {sources.map((s) => (
          <SourceCard s={s} key={s.chunk_id} />
        ))}
      </div>
    </div>
  );
}

// What the brain layer added to this answer beyond the retrieved text:
// what it remembered about the asker, and the entity relationships
// HydraDB's graph connected to the question. Both are evidence of *why*
// the answer came out the way it did, so they sit with the sources.
function BrainContext({ data }) {
  const memories = data.memories || [];
  const paths = data.graph_paths || [];
  if (memories.length === 0 && paths.length === 0 && !data.remembered) return null;

  return (
    <div className="chat-brain">
      {data.remembered && (
        <div className="chat-brain-block">
          <div className="chat-sources-label">Saved to your memory</div>
          <div className="chat-brain-chips">
            <span className="chat-memory-chip saved">{data.remembered}</span>
          </div>
        </div>
      )}
      {memories.length > 0 && (
        <div className="chat-brain-block">
          <div className="chat-sources-label">Remembered about you</div>
          <div className="chat-brain-chips">
            {memories.map((m, i) => (
              <span className="chat-memory-chip" key={m.id || i}>
                {m.text}
              </span>
            ))}
          </div>
        </div>
      )}
      {paths.length > 0 && (
        <div className="chat-brain-block">
          <div className="chat-sources-label">Connected in the knowledge graph</div>
          <ul className="chat-graph-paths">
            {paths.map((p, i) => (
              <li key={i} title={p.context || undefined}>
                {p.path}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// Only shown for answers whose context came from HydraDB - the rating is
// attached to that specific retrieval, so there's nothing to attach it to
// on a pgvector or pure-SQL turn.
function Feedback({ requestId }) {
  const [sent, setSent] = useState(null);
  const [failed, setFailed] = useState(false);

  if (!requestId) return null;

  const rate = async (rating) => {
    setFailed(false);
    setSent(rating);
    try {
      await sendFeedback(requestId, rating);
    } catch {
      setSent(null);
      setFailed(true);
    }
  };

  return (
    <div className="chat-feedback">
      {sent ? (
        <span>Thanks - sent to HydraDB to tune retrieval.</span>
      ) : (
        <>
          <span>{failed ? "Couldn't send that - try again?" : "Was the retrieved context right?"}</span>
          <button onClick={() => rate("positive")} aria-label="Context was right">
            👍
          </button>
          <button onClick={() => rate("negative")} aria-label="Context was wrong">
            👎
          </button>
        </>
      )}
    </div>
  );
}

function AgentAvatar() {
  return (
    <span className="chat-avatar agent">
      <SparkleIcon width={13} height={13} />
    </span>
  );
}

function CostBadge({ data }) {
  if (data.estimated_cost_usd == null) return null;
  return (
    <span className="chat-cost">
      {data.prompt_tokens + data.completion_tokens} tokens · ${data.estimated_cost_usd.toFixed(5)}
    </span>
  );
}

function Exchange({ entry, onViewOnGraph }) {
  const { question, error, data } = entry;
  const hasSql = data?.response_type === "sql" || data?.response_type === "hybrid";
  const hasMessage = data?.response_type === "chat" || data?.response_type === "unstructured" || data?.response_type === "hybrid";
  // Present (even as "") the moment streaming starts, absent for
  // verified-match answers (which skip the explanation step entirely)
  // and explicitly null if the explanation call failed.
  const hasExplanation = data?.response_type === "sql" && data.explanation != null;

  return (
    <div className="chat-exchange">
      <div className="chat-question-row">
        <div className="chat-question-bubble">{question}</div>
      </div>

      {error && (
        <div className="chat-answer-row">
          <AgentAvatar />
          <div className="error-banner">{error}</div>
        </div>
      )}

      {data && (
        <div className="chat-answer-row">
          <AgentAvatar />
          <div className="chat-answer-body">
            <ReasoningTrace trace={data.trace} responseType={data.response_type} onViewOnGraph={onViewOnGraph} />

            {hasSql && (
              <div className="chat-sql">
                <div className="chat-sql-label">
                  <span>
                    {data.verified ? (
                      <span className="verified-badge verified" title="Matched a pre-vetted question - skipped generation entirely">
                        ✓ Verified
                      </span>
                    ) : (
                      <span className="verified-badge generated" title="Freshly generated by the model">
                        ⚡ Generated
                      </span>
                    )}
                    {" "}Generated SQL{data.attempts > 1 ? ` (took ${data.attempts} attempts)` : ""}
                  </span>
                  {!hasMessage && !hasExplanation && <CostBadge data={data} />}
                </div>
                <pre>{data.generated_sql}</pre>
              </div>
            )}

            {hasSql && <ResultsTable result={data} loading={false} />}

            {hasExplanation && (
              <div className="chat-explanation">
                <ReactMarkdown>{data.explanation}</ReactMarkdown>
                {data.estimated_cost_usd == null && <span className="chat-typing-cursor" />}
                {data.estimated_cost_usd != null && <BrainContext data={data} />}
                <CostBadge data={data} />
              </div>
            )}

            {hasMessage && (
              <div className="chat-reply">
                {data.response_type === "hybrid" && <div className="chat-hybrid-label">Answer, combining the query result above with retrieved context</div>}
                <ReactMarkdown>{data.message}</ReactMarkdown>
                {/* "chat" turns never stream, so they arrive complete with a cost and no cursor. */}
                {data.estimated_cost_usd == null && <span className="chat-typing-cursor" />}
                {data.estimated_cost_usd != null && <Sources sources={data.retrieved_sources} />}
                {data.estimated_cost_usd != null && <BrainContext data={data} />}
                {data.estimated_cost_usd != null && <Feedback requestId={data.hydra_request_id} />}
                <CostBadge data={data} />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ChatPanel({ userId, brain, onOpenMemory, onViewOnGraph, pendingQuestion, onConsumePending }) {
  const [question, setQuestion] = useState("");
  // Separate from `question` on purpose - the input box clears immediately
  // on submit (so it's ready for the next message), but the "thinking..."
  // bubble still needs the text that was actually just asked, not whatever
  // `question` holds by the time it renders (which is already "").
  const [pendingText, setPendingText] = useState("");
  const [loading, setLoading] = useState(false);
  const [conversations, setConversations] = useState(loadConversationList);
  const [conversationId, setConversationId] = useState(() => {
    const active = sessionStorage.getItem(ACTIVE_CONV_KEY);
    const list = loadConversationList();
    if (active && list.some((c) => c.id === active)) return active;
    return list[0]?.id || newConversationId();
  });
  const [history, setHistory] = useState(() => loadConversationHistory(conversationId));

  useEffect(() => {
    sessionStorage.setItem(ACTIVE_CONV_KEY, conversationId);
  }, [conversationId]);

  useEffect(() => {
    saveConversationHistory(conversationId, history);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, history]);

  // Follows new content (including streamed deltas, since those also
  // update `history`) the way ChatGPT/Claude do - but only while already
  // near the bottom, so scrolling up mid-stream to reread something isn't
  // fought by an auto-scroll yanking the view back down. The actual
  // scrolling element is <main> (owned by App.jsx, shared across every
  // page) rather than anything inside this component, so this reaches up
  // for it directly instead of threading a ref through props for one
  // cross-cutting behavior.
  const chatPanelRef = useRef(null);
  const scrollElRef = useRef(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    const scrollEl = document.querySelector("main");
    scrollElRef.current = scrollEl;
    if (!scrollEl) return;
    const handleScroll = () => {
      const distance = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight;
      stickToBottomRef.current = distance < 120;
    };
    scrollEl.addEventListener("scroll", handleScroll);
    return () => scrollEl.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    const scrollEl = scrollElRef.current;
    // offsetParent is null when a display:none ancestor hides this panel
    // (switched to another page) - a background stream still updating
    // history shouldn't scroll whatever page is actually on screen.
    const isVisible = chatPanelRef.current?.offsetParent != null;
    if (scrollEl && isVisible && stickToBottomRef.current) {
      scrollEl.scrollTop = scrollEl.scrollHeight;
    }
  }, [history, loading]);

  // `conversationIdOverride`/`startFresh` exist for sendAsNewConversation
  // below: setConversationId/setHistory queue a state update, but this
  // function's own `conversationId`/`history` closure variables won't see
  // it until the next render - calling send() synchronously right after
  // would otherwise still target the conversation being left.
  const send = async (q, { conversationIdOverride, startFresh = false } = {}) => {
    if (!q.trim() || loading) return;
    const activeConversationId = conversationIdOverride ?? conversationId;
    const isFirstMessage = startFresh || history.length === 0;
    setQuestion("");
    setPendingText(q);
    setLoading(true);
    // Submitting is an explicit "show me this" action - follow the new
    // exchange even if a scroll-up earlier had paused auto-follow.
    stickToBottomRef.current = true;

    if (isFirstMessage) {
      setConversations((prev) => {
        const next = [
          { id: activeConversationId, title: titleFromQuestion(q) },
          ...prev.filter((c) => c.id !== activeConversationId),
        ];
        saveConversationList(next);
        return next;
      });
    }

    // "chat" turns have no "start" at all - straight from the loading
    // placeholder to onComplete. "sql" streams into `explanation` (a
    // short note under the table); "unstructured"/"hybrid" stream into
    // `message` (the primary answer) - same delta mechanics, different
    // target field depending on which one this turn actually fills in.
    let entryIndex = null;
    let streamField = "message";
    const upsert = (data) => {
      setHistory((prev) => {
        if (entryIndex === null) {
          entryIndex = prev.length;
          return [...prev, { question: q, data }];
        }
        const next = [...prev];
        next[entryIndex] = { question: q, data };
        return next;
      });
    };
    const appendToStream = (text, replace) => {
      setHistory((prev) => {
        if (entryIndex === null) return prev;
        const next = [...prev];
        const entry = next[entryIndex];
        const current = entry.data[streamField] || "";
        const value = replace ? text : current + text;
        next[entryIndex] = { ...entry, data: { ...entry.data, [streamField]: value } };
        return next;
      });
    };

    try {
      await askQuestionStream(q, activeConversationId, {
        userId,
        onStart: (data) => {
          setLoading(false);
          streamField = data.response_type === "sql" ? "explanation" : "message";
          upsert({ ...data, [streamField]: "" });
        },
        onDelta: (text) => appendToStream(text, false),
        onSynthError: (text) => appendToStream(text, true),
        onComplete: upsert,
      });
    } catch (err) {
      setHistory((prev) => [...prev, { question: q, error: err.message }]);
    } finally {
      setLoading(false);
    }
  };

  const sendAsNewConversation = (q) => {
    const freshId = newConversationId();
    setConversationId(freshId);
    setHistory([]);
    send(q, { conversationIdOverride: freshId, startFresh: true });
  };

  // A question submitted from the Home view arrives here rather than being
  // sent directly from Home - reuses send()'s logic, not a second entry
  // point that duplicates it. Always starts its own new conversation
  // (like ChatGPT/Claude's "new chat"), never silently continues whatever
  // conversation happened to be active when Home was last visited - that
  // was a real bug: asking a fresh question from Home was appending to an
  // unrelated, already-open thread instead of starting its own.
  useEffect(() => {
    if (pendingQuestion) {
      sendAsNewConversation(pendingQuestion);
      onConsumePending?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingQuestion]);

  const handleAsk = () => send(question);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  };

  // Starting a new conversation doesn't clear the one being left - it
  // might still have tabs to switch back to, and a follow-up there should
  // still resolve correctly. clearConversation() is reserved for an
  // actual delete, where "discard the backend's memory of this thread" is
  // really what's meant.
  const handleNewConversation = () => {
    setConversationId(newConversationId());
    setHistory([]);
  };

  const switchConversation = (id) => {
    if (id === conversationId || loading) return;
    setConversationId(id);
    setHistory(loadConversationHistory(id));
  };

  const deleteConversation = (id, e) => {
    e.stopPropagation();
    sessionStorage.removeItem(CONV_PREFIX + id);
    clearConversation(id);

    const next = conversations.filter((c) => c.id !== id);
    saveConversationList(next);
    setConversations(next);

    if (id === conversationId) {
      if (next.length > 0) {
        setConversationId(next[0].id);
        setHistory(loadConversationHistory(next[0].id));
      } else {
        setConversationId(newConversationId());
        setHistory([]);
      }
    }
  };

  return (
    <div className="chat-panel" ref={chatPanelRef}>
      {conversations.length > 0 && (
        <div className="chat-tabs">
          {conversations.map((c) => (
            <button
              key={c.id}
              className={`chat-tab${c.id === conversationId ? " active" : ""}`}
              onClick={() => switchConversation(c.id)}
              title={c.title}
            >
              <span className="chat-tab-title">{c.title}</span>
              <span className="chat-tab-close" onClick={(e) => deleteConversation(c.id, e)} role="button" aria-label="Delete conversation">
                <CloseIcon width={10} height={10} />
              </span>
            </button>
          ))}
          <button className="chat-tab-new" onClick={handleNewConversation} title="New conversation">
            <PlusIcon width={13} height={13} />
          </button>
        </div>
      )}

      <div className="chat-toolbar">
        <span className="chat-toolbar-hint">
          {history.length > 0 ? "Follow-up questions use this conversation's context" : "Ask in plain English"}
        </span>
        {brain?.enabled && (
          <button className="chat-memory-toggle" onClick={onOpenMemory}>
            {userId ? `Memory on · ${userId}` : "Memory off · set who's asking"}
          </button>
        )}
      </div>

      <div className="chat-history">
        {history.length === 0 && !loading && (
          <div className="chat-empty">
            <AgentAvatar />
            <p>Ask a question about your data - or try one of these:</p>
            <div className="suggestion-chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="suggestion-chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {history.map((entry, i) => (
          <Exchange key={i} entry={entry} onViewOnGraph={onViewOnGraph} />
        ))}
        {loading && (
          <div className="chat-exchange">
            <div className="chat-question-row">
              <div className="chat-question-bubble">{pendingText}</div>
            </div>
            <div className="chat-answer-row">
              <AgentAvatar />
              <div className="chat-thinking">
                <span className="spinner" /> thinking…
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="chat-input-row">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={history.length > 0 ? "Ask a follow-up…" : "Ask a question about your data…"}
          rows={1}
        />
        <button className="chat-send-btn" onClick={handleAsk} disabled={loading || !question.trim()}>
          {loading ? <span className="spinner" /> : <SendIcon width={15} height={15} />}
        </button>
      </div>
    </div>
  );
}
