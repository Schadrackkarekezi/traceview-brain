import { useCallback, useEffect, useState } from "react";
import Sidebar from "./components/Sidebar";
import Home from "./components/Home";
import DataCatalog from "./components/DataCatalog";
import Upload from "./components/Upload";
import QueryEditor from "./components/QueryEditor";
import QueryHistory from "./components/QueryHistory";
import ResultsTable from "./components/ResultsTable";
import ChatPanel from "./components/ChatPanel";
import Dashboard from "./components/Dashboard";
import Memory from "./components/Memory";
import GraphViewer from "./components/GraphViewer";
import { MenuIcon } from "./components/Icons";
import { runQuery, getBrainStatus } from "./api";
import { readJSON, readString } from "./storage";

const HISTORY_KEY = "traceview_query_history";
const USER_KEY = "traceview_user";

const PAGE_TITLES = {
  home: "Home",
  ask: "Ask AI",
  memory: "Memory",
  catalog: "Data Catalog",
  upload: "Upload",
  query: "Query Editor",
  graph: "Knowledge Graph",
  logs: "Observability",
};

function loadHistory() {
  return readJSON(localStorage, HISTORY_KEY, []);
}

export default function App() {
  const [sql, setSql] = useState("SELECT * FROM deals LIMIT 10");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [connected, setConnected] = useState(null);
  const [activeTable, setActiveTable] = useState(null);
  const [history, setHistory] = useState(loadHistory);
  const [mode, setMode] = useState("home");
  const [graphHighlight, setGraphHighlight] = useState(null);
  const [pendingQuestion, setPendingQuestion] = useState(null);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  // Who's asking - there's no login, so this is a name the person picks,
  // kept in localStorage (unlike conversations, which are per-tab) because
  // the whole point of long-term memory is that it outlives the session.
  const [userId, setUserId] = useState(() => readString(localStorage, USER_KEY, ""));
  const [brain, setBrain] = useState(null);

  useEffect(() => {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  }, [history]);

  useEffect(() => {
    try {
      localStorage.setItem(USER_KEY, userId);
    } catch {
      /* storage unavailable - the name just won't survive a reload */
    }
  }, [userId]);

  const refreshBrain = useCallback(() => {
    getBrainStatus()
      .then(setBrain)
      .catch(() => setBrain({ enabled: false }));
  }, []);

  useEffect(refreshBrain, [refreshBrain]);

  const runSql = useCallback(async (queryText) => {
    setLoading(true);
    setError(null);
    try {
      const data = await runQuery(queryText);
      setResult(data);
      setHistory((prev) => {
        const entry = { sql: queryText, time: new Date().toLocaleTimeString() };
        return [entry, ...prev.filter((h) => h.sql !== queryText)].slice(0, 20);
      });
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleRun = useCallback(() => runSql(sql), [sql, runSql]);

  // Clicking a table in the catalog previews it immediately - the whole
  // point is "click a table, see its data," not "click a table, then
  // remember to also press Run."
  const handlePickTable = (name) => {
    const query = `SELECT * FROM ${name} LIMIT 50`;
    setActiveTable(name);
    setSql(query);
    setMode("query");
    runSql(query);
  };

  const handleViewOnGraph = (entities) => {
    setGraphHighlight({ entities, onClear: () => setGraphHighlight(null) });
    setMode("graph");
  };

  // Home's entry points funnel through here - either a question (goes to
  // the one Ask AI conversation) or a direct link to another page.
  const handleHomeAsk = (question, targetMode) => {
    if (question) {
      setPendingQuestion(question);
      setMode("ask");
    } else if (targetMode) {
      setMode(targetMode);
    }
  };

  return (
    <div className="app">
      <Sidebar
        mode={mode}
        onModeChange={setMode}
        onPickTable={handlePickTable}
        activeTable={activeTable}
        onSchemaLoaded={setConnected}
        connected={connected}
        brain={brain}
        userId={userId}
        open={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
      />
      <div className="main-column">
        <div className="content-topbar">
          <div className="content-topbar-title">
            <button
              className="mobile-menu-btn"
              onClick={() => setMobileNavOpen(true)}
              aria-label="Open menu"
            >
              <MenuIcon width={20} height={20} />
            </button>
            <h1>{PAGE_TITLES[mode]}</h1>
          </div>
          {mode === "query" && (
            <QueryHistory history={history} onSelect={setSql} onClear={() => setHistory([])} />
          )}
        </div>

        <main>
          {/* All panels stay mounted so switching pages never loses state
              (query history, the active conversation, dashboard scroll) -
              only visibility toggles. */}
          <div style={{ display: mode === "home" ? "block" : "none" }}>
            <Home onAsk={handleHomeAsk} />
          </div>

          <div style={{ display: mode === "catalog" ? "block" : "none" }}>
            <DataCatalog onPreview={handlePickTable} />
          </div>

          <div style={{ display: mode === "upload" ? "block" : "none" }}>
            <Upload />
          </div>

          <div style={{ display: mode === "query" ? "block" : "none" }}>
            <QueryEditor sql={sql} setSql={setSql} onRun={handleRun} loading={loading} />
            {error && <div className="error-banner">{error}</div>}
            <ResultsTable result={result} loading={loading} />
          </div>

          <div style={{ display: mode === "ask" ? "block" : "none" }}>
            <ChatPanel
              userId={userId}
              brain={brain}
              onOpenMemory={() => setMode("memory")}
              onViewOnGraph={handleViewOnGraph}
              pendingQuestion={pendingQuestion}
              onConsumePending={() => setPendingQuestion(null)}
            />
          </div>

          <div style={{ display: mode === "memory" ? "block" : "none" }}>
            <Memory
              active={mode === "memory"}
              userId={userId}
              onUserChange={setUserId}
              brain={brain}
              onRefreshBrain={refreshBrain}
            />
          </div>

          <div style={{ display: mode === "graph" ? "block" : "none" }}>
            <GraphViewer highlight={graphHighlight} />
          </div>

          <div style={{ display: mode === "logs" ? "block" : "none" }}>
            <Dashboard active={mode === "logs"} onViewOnGraph={handleViewOnGraph} />
          </div>
        </main>
      </div>
    </div>
  );
}
