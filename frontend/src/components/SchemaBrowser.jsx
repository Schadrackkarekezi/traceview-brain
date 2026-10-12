import { useEffect, useState } from "react";
import { getSchema } from "../api";
import { ArrowRightIcon } from "./Icons";

export default function SchemaBrowser({ onPickTable, activeTable, onSchemaLoaded, onOpenCatalog }) {
  const [tables, setTables] = useState([]);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    getSchema()
      .then((data) => {
        setTables(data.tables);
        onSchemaLoaded?.(true);
      })
      .catch((err) => {
        setError(err.message);
        onSchemaLoaded?.(false);
      });
  }, []);

  if (error) return <div className="schema-browser error">{error}</div>;

  const filtered = tables.filter((t) =>
    t.name.toLowerCase().includes(filter.toLowerCase())
  );

  const groups = [
    { label: "Dimensions", kind: "dimension" },
    { label: "Facts", kind: "fact" },
  ];
  const grouped = groups
    .map((g) => ({ ...g, tables: filtered.filter((t) => t.kind === g.kind) }))
    .filter((g) => g.tables.length > 0);
  const ungrouped = filtered.filter((t) => t.kind !== "dimension" && t.kind !== "fact");

  // Collapsed to name + column count - a full column listing per table
  // reads fine as a handful of tables, but at a dozen+ (once unstructured
  // content tables are in the mix) it turns the sidebar into an
  // unreadable wall of text. Full detail lives in the Data Catalog page
  // instead, which actually has room for it.
  const renderTable = (table) => (
    <button
      key={table.name}
      className={`schema-row${activeTable === table.name ? " active" : ""}`}
      onClick={() => onPickTable(table.name)}
      title="Click to preview this table's data"
    >
      <span className={`schema-row-dot ${table.kind || ""}`} />
      <span className="schema-row-name">{table.name}</span>
      <span className="schema-row-count">{table.columns.length}</span>
    </button>
  );

  return (
    <div className="schema-browser">
      <div className="schema-browser-header">
        <h2>Records</h2>
        {onOpenCatalog && (
          <button className="schema-browser-viewall" onClick={onOpenCatalog}>
            View all <ArrowRightIcon width={12} height={12} />
          </button>
        )}
      </div>
      <input
        className="schema-search"
        placeholder="Filter tables…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {grouped.map((g) => (
        <div className="schema-group" key={g.kind}>
          <div className="schema-group-label">{g.label}</div>
          {g.tables.map(renderTable)}
        </div>
      ))}
      {ungrouped.length > 0 && (
        <div className="schema-group">
          {grouped.length > 0 && <div className="schema-group-label">Other</div>}
          {ungrouped.map(renderTable)}
        </div>
      )}
      {tables.length > 0 && filtered.length === 0 && (
        <div className="schema-empty">No matching tables</div>
      )}
    </div>
  );
}
