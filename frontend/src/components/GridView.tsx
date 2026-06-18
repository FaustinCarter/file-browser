import { useEffect, useState } from "react";
import { api, Annotation, fmtBytes, NodeOut } from "../api";

interface Props {
  datasetId: number;
  userName: string;
  toast: (m: string, e?: boolean) => void;
}

const PAGE_SIZE = 100;

export default function GridView({ datasetId, userName, toast }: Props) {
  const [items, setItems] = useState<NodeOut[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("full_path");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);

  // filters
  const [q, setQ] = useState("");
  const [isDir, setIsDir] = useState<string>("");
  const [keep, setKeep] = useState<string>("");
  const [processed, setProcessed] = useState<string>("");
  const [jira, setJira] = useState("");

  const load = () => {
    setLoading(true);
    api
      .search({
        dataset_id: datasetId,
        q: q || undefined,
        is_dir: isDir === "" ? undefined : isDir === "true",
        keep: keep === "" ? undefined : keep === "true",
        processed: processed === "" ? undefined : processed === "true",
        jira: jira || undefined,
        sort,
        direction: dir,
        page,
        page_size: PAGE_SIZE,
      })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
      })
      .finally(() => setLoading(false));
  };

  useEffect(load, [datasetId, page, sort, dir]);
  // Reset to page 1 when filters change, then load.
  const applyFilters = () => {
    if (page !== 1) setPage(1);
    else load();
  };

  const setSortCol = (c: string) => {
    if (c === sort) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSort(c);
      setDir("asc");
    }
  };

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const toggleSel = (id: number) => {
    const s = new Set(selected);
    s.has(id) ? s.delete(id) : s.add(id);
    setSelected(s);
  };
  const allOnPage = items.length > 0 && items.every((i) => selected.has(i.id));
  const toggleAll = () => {
    const s = new Set(selected);
    if (allOnPage) items.forEach((i) => s.delete(i.id));
    else items.forEach((i) => s.add(i.id));
    setSelected(s);
  };

  async function patch(id: number, values: Partial<Annotation>) {
    if (!("user_name" in values) && userName) values.user_name = userName;
    try {
      const updated = await api.updateAnnotation(id, values);
      setItems((prev) => prev.map((it) => (it.id === id ? updated : it)));
    } catch (e: any) {
      toast(String(e.message || e), true);
    }
  }

  async function bulkApply(values: Partial<Annotation>) {
    if (selected.size === 0) return;
    if (userName) values.user_name = userName;
    try {
      await Promise.all(Array.from(selected).map((id) => api.updateAnnotation(id, values)));
      toast(`Updated ${selected.size} rows`);
      load();
    } catch (e: any) {
      toast(String(e.message || e), true);
    }
  }

  return (
    <div>
      <div className="filterbar">
        <div className="filter-group">
          <label>Path / name contains</label>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFilters()}
            placeholder="e.g. Reports"
          />
        </div>
        <div className="filter-group">
          <label>Kind</label>
          <select value={isDir} onChange={(e) => setIsDir(e.target.value)}>
            <option value="">All</option>
            <option value="false">Files</option>
            <option value="true">Folders</option>
          </select>
        </div>
        <div className="filter-group">
          <label>Keep</label>
          <select value={keep} onChange={(e) => setKeep(e.target.value)}>
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div className="filter-group">
          <label>Processed</label>
          <select value={processed} onChange={(e) => setProcessed(e.target.value)}>
            <option value="">Any</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div className="filter-group">
          <label>JIRA ticket</label>
          <input
            value={jira}
            onChange={(e) => setJira(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFilters()}
            placeholder="MIG-123"
          />
        </div>
        <div className="filter-group">
          <label>&nbsp;</label>
          <button className="primary" onClick={applyFilters}>
            Apply
          </button>
        </div>
        <div className="spacer" />
        <div className="filter-group">
          <label>&nbsp;</label>
          <span className="muted">{total.toLocaleString()} rows</span>
        </div>
      </div>

      {selected.size > 0 && (
        <BulkBar count={selected.size} onApply={bulkApply} onClear={() => setSelected(new Set())} />
      )}

      <div style={{ overflow: "auto" }}>
        <table className="grid">
          <thead>
            <tr>
              <th>
                <input type="checkbox" checked={allOnPage} onChange={toggleAll} />
              </th>
              <th onClick={() => setSortCol("name")}>Name</th>
              <th onClick={() => setSortCol("full_path")}>Full Path</th>
              <th onClick={() => setSortCol("file_type")}>Type</th>
              <th onClick={() => setSortCol("size")}>Size</th>
              <th onClick={() => setSortCol("last_accessed")}>Last Acc.</th>
              <th onClick={() => setSortCol("owner")}>Owner</th>
              <th>Keep</th>
              <th>Proc.</th>
              <th>JIRA</th>
              <th>Target location</th>
              <th>Comment</th>
              <th>User</th>
            </tr>
          </thead>
          <tbody>
            {items.map((n) => (
              <GridRow
                key={n.id}
                n={n}
                selected={selected.has(n.id)}
                onToggle={() => toggleSel(n.id)}
                onPatch={patch}
              />
            ))}
            {items.length === 0 && !loading && (
              <tr>
                <td colSpan={13} className="muted" style={{ padding: 20 }}>
                  No rows match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          ◀ Prev
        </button>
        <span>
          Page {page} / {pages}
        </span>
        <button disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
          Next ▶
        </button>
        {loading && <span className="muted">loading…</span>}
      </div>
    </div>
  );
}

function GridRow({
  n,
  selected,
  onToggle,
  onPatch,
}: {
  n: NodeOut;
  selected: boolean;
  onToggle: () => void;
  onPatch: (id: number, v: Partial<Annotation>) => void;
}) {
  const eff = n.effective!;
  const inh = new Set(n.inherited_fields);
  return (
    <tr className={selected ? "selected" : ""}>
      <td>
        <input type="checkbox" checked={selected} onChange={onToggle} />
      </td>
      <td>
        {n.is_dir ? "📁 " : ""}
        {n.name}
      </td>
      <td title={n.full_path} style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis" }}>
        {n.full_path}
      </td>
      <td>{n.file_type}</td>
      <td>{fmtBytes(n.size_bytes)}</td>
      <td>{n.last_accessed || ""}</td>
      <td>{n.owner}</td>
      <td title={inh.has("keep") ? "inherited" : ""}>
        <input
          type="checkbox"
          checked={!!eff.keep}
          style={inh.has("keep") ? { opacity: 0.5 } : undefined}
          onChange={(e) => onPatch(n.id, { keep: e.target.checked })}
        />
      </td>
      <td title={inh.has("processed") ? "inherited" : ""}>
        <input
          type="checkbox"
          checked={!!eff.processed}
          style={inh.has("processed") ? { opacity: 0.5 } : undefined}
          onChange={(e) => onPatch(n.id, { processed: e.target.checked })}
        />
      </td>
      <CellInput
        value={eff.jira_ticket || ""}
        inherited={inh.has("jira_ticket")}
        onSave={(v) => onPatch(n.id, { jira_ticket: v || null })}
      />
      <CellInput
        value={eff.target_location || ""}
        inherited={inh.has("target_location")}
        onSave={(v) => onPatch(n.id, { target_location: v || null })}
      />
      <CellInput
        value={eff.comment || ""}
        inherited={inh.has("comment")}
        onSave={(v) => onPatch(n.id, { comment: v || null })}
      />
      <CellInput
        value={eff.user_name || ""}
        inherited={inh.has("user_name")}
        onSave={(v) => onPatch(n.id, { user_name: v || null })}
      />
    </tr>
  );
}

function CellInput({
  value,
  inherited,
  onSave,
}: {
  value: string;
  inherited: boolean;
  onSave: (v: string) => void;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <td>
      <input
        className="cell"
        style={inherited ? { color: "var(--warn)", fontStyle: "italic" } : undefined}
        value={v}
        title={inherited ? "inherited — edit to override" : ""}
        onChange={(e) => setV(e.target.value)}
        onBlur={() => v !== value && onSave(v)}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
    </td>
  );
}

function BulkBar({
  count,
  onApply,
  onClear,
}: {
  count: number;
  onApply: (v: Partial<Annotation>) => void;
  onClear: () => void;
}) {
  const [jira, setJira] = useState("");
  const [target, setTarget] = useState("");
  return (
    <div className="bulkbar">
      <span className="chip">{count} selected</span>
      <button onClick={() => onApply({ keep: true })}>Keep ✓</button>
      <button onClick={() => onApply({ keep: false })}>Keep ✗</button>
      <button onClick={() => onApply({ processed: true })}>Processed ✓</button>
      <button onClick={() => onApply({ processed: false })}>Processed ✗</button>
      <input
        placeholder="JIRA ticket"
        value={jira}
        onChange={(e) => setJira(e.target.value)}
        style={{ width: 110 }}
      />
      <button disabled={!jira} onClick={() => onApply({ jira_ticket: jira })}>
        Set JIRA
      </button>
      <input
        placeholder="Target location"
        value={target}
        onChange={(e) => setTarget(e.target.value)}
        style={{ width: 160 }}
      />
      <button disabled={!target} onClick={() => onApply({ target_location: target })}>
        Set target
      </button>
      <div className="spacer" />
      <button onClick={onClear}>Clear selection</button>
    </div>
  );
}
