import SchemaBrowser from "./SchemaBrowser";
import ThemeToggle from "./ThemeToggle";
import { HomeIcon, ChatIcon, TerminalIcon, GraphIcon, ChartIcon, DatabaseIcon, UploadIcon, BrainIcon } from "./Icons";

const NAV_ITEMS = [
  { id: "home", label: "Home", Icon: HomeIcon },
  { id: "ask", label: "Ask AI", Icon: ChatIcon },
  { id: "memory", label: "Memory", Icon: BrainIcon },
  { id: "catalog", label: "Data Catalog", Icon: DatabaseIcon },
  { id: "upload", label: "Upload", Icon: UploadIcon },
  { id: "query", label: "Query Editor", Icon: TerminalIcon },
  { id: "graph", label: "Graph", Icon: GraphIcon },
  { id: "logs", label: "Observability", Icon: ChartIcon },
];

// `open`/`onClose` only matter below the mobile breakpoint (see index.css) -
// the sidebar is always visible on desktop regardless of their value, this
// just controls the slide-in drawer state on narrow viewports. Picking a
// nav item closes the drawer, same as any mobile off-canvas menu - without
// it, every navigation would need a second tap to dismiss the overlay.
export default function Sidebar({ mode, onModeChange, onPickTable, activeTable, onSchemaLoaded, connected, brain, userId, open, onClose }) {
  const pick = (id) => {
    onModeChange(id);
    onClose?.();
  };

  return (
    <>
      {open && <div className="sidebar-overlay" onClick={onClose} />}
      <aside className={`sidebar${open ? " open" : ""}`}>
        <div className="sidebar-brand">
          <span className="logo-mark">T</span>
          <div className="sidebar-brand-text">
            <span className="brand-name">Traceview</span>
            <span className="brand-sub">GTM Intelligence</span>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV_ITEMS.map(({ id, label, Icon }) => {
            const isActive = mode === id;
            return (
              <button
                key={id}
                className={`sidebar-nav-item${isActive ? " active" : ""}`}
                onClick={() => pick(id)}
              >
                <Icon className="sidebar-nav-icon" />
                {label}
              </button>
            );
          })}
        </nav>

        <div className="sidebar-records">
          <SchemaBrowser
            onPickTable={(name) => {
              onPickTable(name);
              onClose?.();
            }}
            activeTable={activeTable}
            onSchemaLoaded={onSchemaLoaded}
            onOpenCatalog={() => pick("catalog")}
          />
        </div>

        {brain?.enabled && (
          <button className="sidebar-brain" onClick={() => pick("memory")} title="Open memory">
            <span className="sidebar-brain-label">Brain · HydraDB</span>
            <span className="sidebar-brain-user">{userId ? `Asking as ${userId}` : "Set who's asking to turn on memory"}</span>
          </button>
        )}

        <div className="sidebar-footer">
          <div className="sidebar-footer-status">
            <span
              className={`status-dot ${connected === null ? "pending" : connected ? "online" : "offline"}`}
            />
            {connected === null ? "Connecting…" : connected ? "Connected" : "Disconnected"}
          </div>
          <ThemeToggle />
        </div>
      </aside>
    </>
  );
}
