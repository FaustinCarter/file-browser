import { useEffect, useMemo, useState } from "react";
import {
  api,
  Annotation,
  Filters,
  FlagField,
  fmtBytes,
  folderFlagState,
  NodeOut,
} from "../api";

const FLAGS: { field: FlagField; label: string }[] = [
  { field: "no_transfer", label: "No Transfer?" },
  { field: "processed", label: "Processed?" },
];

interface Props {
  nodeId: number;
  filters: Filters;
  onMutated: () => void;
  toast: (msg: string, err?: boolean) => void;
}

const EDIT_FIELDS: { key: keyof Annotation; label: string }[] = [
  { key: "assignee", label: "Assignee" },
  { key: "target_location", label: "Target location" },
  { key: "jira_ticket", label: "JIRA ticket" },
  { key: "comment", label: "Comment" },
];

export default function DetailPanel({
  nodeId,
  filters,
  onMutated,
  toast,
}: Props) {
  const [node, setNode] = useState<NodeOut | null>(null);
  const [counts, setCounts] = useState<{ file_count: number; folder_count: number } | null>(null);
  const [matchCount, setMatchCount] = useState<number | null>(null);

  const filterActive = !!(
    (filters.types && filters.types.length) ||
    filters.accessed_after ||
    filters.accessed_before
  );

  const reload = () => {
    api.node(nodeId).then(setNode);
  };

  useEffect(() => {
    setNode(null);
    setCounts(null);
    api.node(nodeId).then(setNode);
  }, [nodeId]);

  useEffect(() => {
    if (node?.is_dir) api.counts(nodeId).then(setCounts);
  }, [node?.is_dir, nodeId]);

  // When a folder is selected with an active filter, count how many files the
  // scoped edits would touch.
  useEffect(() => {
    if (node?.is_dir && filterActive) {
      api.stats(nodeId, filters).then((s) => setMatchCount(s.file_count));
    } else {
      setMatchCount(null);
    }
  }, [node?.is_dir, nodeId, filterActive, filters]);

  if (!node) return <div className="detail muted">Loading…</div>;

  const eff = node.effective!;
  const inh = new Set(node.inherited_fields);

  async function save(values: Partial<Annotation>) {
    try {
      const updated = await api.updateAnnotation(nodeId, values);
      setNode(updated);
      onMutated();
      toast("Saved");
    } catch (e: any) {
      toast(String(e.message || e), true);
    }
  }

  // Folder edit while a filter is active: stamp only the matching files in the
  // subtree (recursively), leaving other types and the folder itself untouched.
  async function applyScoped(values: Partial<Annotation>) {
    try {
      const r = await api.bulkAnnotation({
        node_id: nodeId,
        files_only: true,
        include_self: false,
        types: filters.types,
        accessed_after: filters.accessed_after,
        accessed_before: filters.accessed_before,
        values,
      });
      toast(`Updated ${r.updated.toLocaleString()} matching file(s)`);
      onMutated();
      reload();
    } catch (e: any) {
      toast(String(e.message || e), true);
    }
  }

  // Set/clear a rollup boolean on a folder. When a filter is active the change
  // is scoped to the matching files (folder stays indeterminate); otherwise the
  // whole subtree is affected.
  async function setFolderFlag(field: FlagField, value: boolean | null) {
    try {
      const updated = await api.folderFlag(nodeId, {
        field,
        value,
        types: filterActive ? filters.types : undefined,
        accessed_after: filterActive ? filters.accessed_after : undefined,
        accessed_before: filterActive ? filters.accessed_before : undefined,
      });
      setNode(updated);
      onMutated();
      toast(value === null ? "Cleared" : "Saved");
    } catch (e: any) {
      toast(String(e.message || e), true);
    }
  }

  return (
    <div className="detail">
      <h2>
        {node.is_dir ? "📁 " : "📄 "}
        {node.name}
      </h2>
      <div className="path">{node.full_path}</div>

      <div className="section">
        <h3>Read-only (from CSV)</h3>
        <div className="kv">
          <div className="k">Type</div>
          <div className="v">{node.file_type || "—"}</div>
          <div className="k">Size</div>
          <div className="v">
            {node.size_raw || "—"}{" "}
            <span className="muted">({fmtBytes(node.size_bytes)})</span>
          </div>
          <div className="k">Allocated</div>
          <div className="v">{node.allocated_raw || "—"}</div>
          <div className="k">% of parent</div>
          <div className="v">{node.pct_parent_raw || "—"}</div>
          <div className="k">Files / Folders</div>
          <div className="v">
            {node.files_count ?? "—"} / {node.folders_count ?? "—"}
          </div>
          <div className="k">Last modified</div>
          <div className="v">{node.last_modified || "—"}</div>
          <div className="k">Last accessed</div>
          <div className="v">{node.last_accessed || "—"}</div>
          <div className="k">Owner</div>
          <div className="v">{node.owner || "—"}</div>
          <div className="k">Dir level</div>
          <div className="v">{node.dir_level ?? "—"}</div>
          {node.is_dir && counts && (
            <>
              <div className="k">Nested (total)</div>
              <div className="v">
                {counts.file_count.toLocaleString()} files,{" "}
                {counts.folder_count.toLocaleString()} folders
              </div>
            </>
          )}
        </div>
      </div>

      <div className="section">
        <h3>
          Editable
          {node.is_dir &&
            (filterActive
              ? " — filter active: matching files only"
              : "")}
        </h3>

        {/* Boolean flags: folders show a tri-state rollup of their files. */}
        <div style={{ marginBottom: 10 }}>
          {FLAGS.map((f) =>
            node.is_dir ? (
              <FolderFlag
                key={f.field}
                label={f.label}
                marked={
                  f.field === "no_transfer"
                    ? node.no_transfer_marked
                    : node.processed_marked
                }
                total={node.total_files}
                scoped={filterActive}
                onSet={(v) => setFolderFlag(f.field, v)}
              />
            ) : (
              <FileFlag
                key={f.field}
                label={f.label}
                checked={!!eff[f.field]}
                inherited={inh.has(f.field)}
                onSet={(v) => save({ [f.field]: v } as Partial<Annotation>)}
                onClear={() => save({ [f.field]: null } as Partial<Annotation>)}
              />
            ),
          )}
        </div>

        {node.is_dir && filterActive ? (
          <ScopedFolderEdit matchCount={matchCount} onApply={applyScoped} />
        ) : (
          <>
            {EDIT_FIELDS.map((f) => (
              <EditableField
                key={f.key}
                label={f.label}
                value={(eff[f.key] as string) || ""}
                inherited={inh.has(f.key)}
                onSave={(v) => save({ [f.key]: v || null } as Partial<Annotation>)}
              />
            ))}
          </>
        )}
        <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>
          {node.updated_at
            ? `Last updated by ${node.updated_by || "—"} · ${new Date(
                node.updated_at,
              ).toLocaleString()}`
            : "Never edited"}
        </div>
      </div>

      {node.is_dir && !filterActive && (
        <BulkStamp
          node={node}
          filters={filters}
          onDone={(n) => {
            toast(`Stamped ${n} nodes`);
            onMutated();
            reload();
          }}
          toast={toast}
        />
      )}

      {node.is_dir && (
        <TypeBreakdown nodeId={nodeId} filters={filters} />
      )}
    </div>
  );
}

function EditableField({
  label,
  value,
  inherited,
  onSave,
}: {
  label: string;
  value: string;
  inherited: boolean;
  onSave: (v: string) => void;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const dirty = v !== value;
  return (
    <div className="form-row">
      <label>
        {label} {inherited && <span className="inherited">(inherited)</span>}
      </label>
      <div style={{ display: "flex", gap: 6 }}>
        <input
          style={{ flex: 1 }}
          value={v}
          onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSave(v);
          }}
        />
        {dirty && (
          <button className="primary" onClick={() => onSave(v)}>
            Save
          </button>
        )}
      </div>
    </div>
  );
}

function FolderFlag({
  label,
  marked,
  total,
  scoped,
  onSet,
}: {
  label: string;
  marked: number | null;
  total: number | null;
  scoped: boolean;
  onSet: (v: boolean | null) => void;
}) {
  const state = folderFlagState(marked, total);
  const m = marked ?? 0;
  const t = total ?? 0;
  return (
    <div className="form-row">
      <label>
        <input
          type="checkbox"
          ref={(el) => {
            if (el) el.indeterminate = state === "some";
          }}
          checked={state === "all"}
          disabled={t === 0}
          onChange={(e) => onSet(e.target.checked ? true : null)}
        />{" "}
        {label}{" "}
        <span className="muted" style={{ fontSize: 11 }}>
          ({m.toLocaleString()}/{t.toLocaleString()} files
          {scoped ? ", matching only" : ""})
        </span>
        {state === "some" && (
          <span
            className="inherited"
            title="Irregular: this folder is partially marked — some files are not."
          >
            {" "}
            ⚠ mixed
          </span>
        )}
      </label>
    </div>
  );
}

function FileFlag({
  label,
  checked,
  inherited,
  onSet,
  onClear,
}: {
  label: string;
  checked: boolean;
  inherited: boolean;
  onSet: (v: boolean) => void;
  onClear: () => void;
}) {
  return (
    <div className="form-row">
      <label>
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onSet(e.target.checked)}
        />{" "}
        {label}{" "}
        {inherited && (
          <span className="inherited">
            (inherited){" "}
            <button
              style={{ padding: "0 6px", fontSize: 11 }}
              onClick={onClear}
              title="Clear this node's own value (revert to inheriting)"
            >
              clear
            </button>
          </span>
        )}
      </label>
    </div>
  );
}

function ScopedFolderEdit({
  matchCount,
  onApply,
}: {
  matchCount: number | null;
  onApply: (v: Partial<Annotation>) => void;
}) {
  const n = matchCount ?? 0;
  return (
    <div>
      <div className="muted" style={{ marginBottom: 10, fontSize: 11 }}>
        A filter is active, so the fields below apply <b>recursively to the{" "}
        {n.toLocaleString()} matching file(s)</b> in this subtree only. Other file
        types (and the folder itself) are left unchanged.
      </div>
      <ScopedText label="Target location" n={n} onApply={(v) => onApply({ target_location: v || null })} />
      <ScopedText label="JIRA ticket" n={n} onApply={(v) => onApply({ jira_ticket: v || null })} />
      <ScopedText label="Comment" n={n} onApply={(v) => onApply({ comment: v || null })} />
    </div>
  );
}

function ScopedText({
  label,
  n,
  onApply,
}: {
  label: string;
  n: number;
  onApply: (v: string) => void;
}) {
  const [v, setV] = useState("");
  return (
    <div className="form-row">
      <label>{label}</label>
      <div style={{ display: "flex", gap: 6 }}>
        <input style={{ flex: 1 }} value={v} onChange={(e) => setV(e.target.value)} />
        <button className="primary" disabled={!n} onClick={() => onApply(v)}>
          Apply to {n.toLocaleString()}
        </button>
      </div>
    </div>
  );
}

function BulkStamp({
  node,
  filters,
  onDone,
  toast,
}: {
  node: NodeOut;
  filters: Filters;
  onDone: (n: number) => void;
  toast: (m: string, e?: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [assignee, setAssignee] = useState("");
  const [jira, setJira] = useState("");
  const [target, setTarget] = useState("");
  const [comment, setComment] = useState("");
  const [filesOnly, setFilesOnly] = useState(true);
  const [useFilters, setUseFilters] = useState(true);
  const [busy, setBusy] = useState(false);

  async function apply() {
    const values: Partial<Annotation> = {};
    if (assignee) values.assignee = assignee;
    if (jira) values.jira_ticket = jira;
    if (target) values.target_location = target;
    if (comment) values.comment = comment;
    if (Object.keys(values).length === 0) {
      toast("Enter at least one value to stamp", true);
      return;
    }
    setBusy(true);
    try {
      const r = await api.bulkAnnotation({
        node_id: node.id,
        files_only: filesOnly,
        include_self: false,
        types: useFilters ? filters.types : undefined,
        accessed_after: useFilters ? filters.accessed_after : undefined,
        accessed_before: useFilters ? filters.accessed_before : undefined,
        values,
      });
      onDone(r.updated);
      setAssignee("");
      setJira("");
      setTarget("");
      setComment("");
    } catch (e: any) {
      toast(String(e.message || e), true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="section">
      <h3
        style={{ cursor: "pointer" }}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "▾" : "▸"} Bulk stamp descendants
      </h3>
      {open && (
        <div>
          <div className="muted" style={{ marginBottom: 8, fontSize: 11 }}>
            Writes concrete values onto every matching file under this folder
            (e.g. assign one JIRA ticket or person to hundreds of files at once).
          </div>
          <div className="form-row">
            <label>Assignee</label>
            <input value={assignee} onChange={(e) => setAssignee(e.target.value)} />
          </div>
          <div className="form-row">
            <label>JIRA ticket</label>
            <input value={jira} onChange={(e) => setJira(e.target.value)} />
          </div>
          <div className="form-row">
            <label>Target location</label>
            <input value={target} onChange={(e) => setTarget(e.target.value)} />
          </div>
          <div className="form-row">
            <label>Comment</label>
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </div>
          <div className="checks">
            <label>
              <input
                type="checkbox"
                checked={filesOnly}
                onChange={(e) => setFilesOnly(e.target.checked)}
              />
              Files only
            </label>
            <label>
              <input
                type="checkbox"
                checked={useFilters}
                onChange={(e) => setUseFilters(e.target.checked)}
              />
              Respect active filters
            </label>
          </div>
          <button className="primary" disabled={busy} onClick={apply}>
            {busy ? "Applying…" : "Apply to descendants"}
          </button>
        </div>
      )}
    </div>
  );
}

function TypeBreakdown({ nodeId, filters }: { nodeId: number; filters: Filters }) {
  const [rows, setRows] = useState<
    { file_type: string | null; count: number; total_size: number }[]
  >([]);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"count" | "file_type" | "total_size">("count");
  const [dir, setDir] = useState<1 | -1>(-1);

  useEffect(() => {
    api.typeBreakdown(nodeId, filters).then(setRows);
  }, [nodeId, filters]);

  const view = useMemo(() => {
    let r = rows;
    if (search)
      r = r.filter((x) =>
        (x.file_type || "").toLowerCase().includes(search.toLowerCase()),
      );
    return [...r].sort((a, b) => {
      const av = a[sort] ?? "";
      const bv = b[sort] ?? "";
      if (av < bv) return -dir;
      if (av > bv) return dir;
      return 0;
    });
  }, [rows, search, sort, dir]);

  const setS = (s: typeof sort) => {
    if (s === sort) setDir((d) => (d === 1 ? -1 : 1));
    else {
      setSort(s);
      setDir(-1);
    }
  };

  return (
    <div className="section">
      <h3>File types here ({rows.length}) — respects filters</h3>
      <input
        placeholder="Filter types…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{ width: "100%", marginBottom: 6 }}
      />
      <div className="breakdown">
        <table>
          <thead>
            <tr>
              <th onClick={() => setS("file_type")}>Type</th>
              <th className="num" onClick={() => setS("count")}>
                Count
              </th>
              <th className="num" onClick={() => setS("total_size")}>
                Size
              </th>
            </tr>
          </thead>
          <tbody>
            {view.map((r) => (
              <tr key={r.file_type}>
                <td>{r.file_type}</td>
                <td className="num">{r.count.toLocaleString()}</td>
                <td className="num">{fmtBytes(r.total_size)}</td>
              </tr>
            ))}
            {view.length === 0 && (
              <tr>
                <td colSpan={3} className="muted">
                  No files match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
