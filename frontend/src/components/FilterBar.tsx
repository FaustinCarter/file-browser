import { useEffect, useRef, useState } from "react";
import { api, Filters, UNASSIGNED } from "../api";

interface Props {
  datasetId: number;
  filters: Filters;
  onChange: (f: Filters) => void;
}

export default function FilterBar({ datasetId, filters, onChange }: Props) {
  const [types, setTypes] = useState<{ file_type: string; count: number }[]>([]);
  const [jiraVals, setJiraVals] = useState<string[]>([]);
  const [assigneeVals, setAssigneeVals] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.fileTypes(datasetId).then(setTypes).catch(() => setTypes([]));
    api.distinctValues(datasetId, "jira_ticket").then((r) => setJiraVals(r.values)).catch(() => {});
    api.distinctValues(datasetId, "assignee").then((r) => setAssigneeVals(r.values)).catch(() => {});
  }, [datasetId, filters]);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const selected = new Set(filters.types || []);
  const toggle = (t: string) => {
    const next = new Set(selected);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    onChange({ ...filters, types: next.size ? Array.from(next) : undefined });
  };

  const filtered = types.filter((t) =>
    t.file_type?.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="filterbar">
      <div className="filter-group typefilter" ref={ref}>
        <label>File type filter ({types.length} types)</label>
        <button onClick={() => setOpen((o) => !o)}>
          {selected.size ? `${selected.size} type(s) selected` : "All file types"} ▾
        </button>
        {open && (
          <div className="typefilter-dropdown">
            <input
              autoFocus
              placeholder="Search types…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: "100%", marginBottom: 6 }}
            />
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              <button onClick={() => onChange({ ...filters, types: undefined })}>
                Clear
              </button>
              <span className="muted" style={{ alignSelf: "center" }}>
                {selected.size} selected
              </span>
            </div>
            {filtered.map((t) => (
              <div
                key={t.file_type}
                className="typefilter-row"
                onClick={() => toggle(t.file_type)}
              >
                <input type="checkbox" readOnly checked={selected.has(t.file_type)} />
                <span>{t.file_type}</span>
                <span className="cnt">{t.count.toLocaleString()}</span>
              </div>
            ))}
            {filtered.length === 0 && <div className="muted">No matches</div>}
          </div>
        )}
      </div>

      <div className="filter-group">
        <label>Last accessed after</label>
        <input
          type="date"
          value={filters.accessed_after || ""}
          onChange={(e) =>
            onChange({ ...filters, accessed_after: e.target.value || undefined })
          }
        />
      </div>
      <div className="filter-group">
        <label>Last accessed before</label>
        <input
          type="date"
          value={filters.accessed_before || ""}
          onChange={(e) =>
            onChange({ ...filters, accessed_before: e.target.value || undefined })
          }
        />
      </div>

      <div className="filter-group">
        <label>No Transfer</label>
        <select
          value={filters.no_transfer || ""}
          onChange={(e) =>
            onChange({
              ...filters,
              no_transfer: (e.target.value || undefined) as "yes" | "no" | undefined,
            })
          }
        >
          <option value="">Any</option>
          <option value="no">Hide marked</option>
          <option value="yes">Only marked</option>
        </select>
      </div>
      <div className="filter-group">
        <label>Processed</label>
        <select
          value={filters.processed || ""}
          onChange={(e) =>
            onChange({
              ...filters,
              processed: (e.target.value || undefined) as "yes" | "no" | undefined,
            })
          }
        >
          <option value="">Any</option>
          <option value="no">Hide processed</option>
          <option value="yes">Only processed</option>
        </select>
      </div>

      <div className="filter-group">
        <label>Assignee</label>
        <select
          value={filters.assignee || ""}
          onChange={(e) =>
            onChange({ ...filters, assignee: e.target.value || undefined })
          }
        >
          <option value="">Any</option>
          <option value={UNASSIGNED}>Unassigned</option>
          {assigneeVals.map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
      </div>
      <div className="filter-group">
        <label>JIRA</label>
        <select
          value={filters.jira || ""}
          onChange={(e) =>
            onChange({ ...filters, jira: e.target.value || undefined })
          }
        >
          <option value="">Any</option>
          <option value={UNASSIGNED}>No ticket</option>
          {jiraVals.map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
      </div>

      {(selected.size > 0 ||
        filters.accessed_after ||
        filters.accessed_before ||
        filters.no_transfer ||
        filters.processed ||
        filters.jira ||
        filters.assignee) && (
        <div className="filter-group">
          <label>&nbsp;</label>
          <button
            onClick={() => onChange({})}
            title="Clear all filters"
          >
            Reset filters
          </button>
        </div>
      )}
    </div>
  );
}
