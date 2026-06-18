import { useCallbackRef } from "../hooks";
import { useEffect, useState } from "react";
import { api, FlagState, Filters, fmtBytes, folderFlagState, NodeOut } from "../api";

function flagState(node: NodeOut, field: "no_transfer" | "processed"): FlagState {
  if (node.is_dir) {
    const marked =
      field === "no_transfer" ? node.no_transfer_marked : node.processed_marked;
    return folderFlagState(marked, node.total_files);
  }
  return node.effective?.[field] ? "all" : "none";
}

interface Props {
  datasetId: number;
  filters: Filters;
  selectedId: number | null;
  onSelect: (n: NodeOut) => void;
  refreshKey: number;
}

export default function TreeView({
  datasetId,
  filters,
  selectedId,
  onSelect,
  refreshKey,
}: Props) {
  const [roots, setRoots] = useState<NodeOut[] | null>(null);

  useEffect(() => {
    setRoots(null);
    api
      .children(datasetId, null, filters)
      .then((r) => setRoots(r.children))
      .catch(() => setRoots([]));
  }, [datasetId, filters, refreshKey]);

  if (roots === null) return <div className="empty">Loading…</div>;
  if (roots.length === 0) return <div className="empty">No data in this dataset.</div>;

  return (
    <div className="tree">
      {roots.map((n) => (
        <TreeNode
          key={n.id}
          node={n}
          datasetId={datasetId}
          filters={filters}
          depth={0}
          selectedId={selectedId}
          onSelect={onSelect}
          refreshKey={refreshKey}
          defaultExpanded
        />
      ))}
    </div>
  );
}

function TreeNode({
  node,
  datasetId,
  filters,
  depth,
  selectedId,
  onSelect,
  refreshKey,
  defaultExpanded = false,
}: {
  node: NodeOut;
  datasetId: number;
  filters: Filters;
  depth: number;
  selectedId: number | null;
  onSelect: (n: NodeOut) => void;
  refreshKey: number;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [children, setChildren] = useState<NodeOut[] | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallbackRef(() => {
    if (!node.is_dir) return;
    setLoading(true);
    api
      .children(datasetId, node.id, filters)
      .then((r) => setChildren(r.children))
      .finally(() => setLoading(false));
  });

  // Reload children when expanded or when filters/refresh change (if open).
  useEffect(() => {
    if (expanded && node.is_dir) load();
  }, [expanded, filters, refreshKey]);

  const isSel = selectedId === node.id;
  const eff = node.effective;

  return (
    <div>
      <div
        className={`tree-row${isSel ? " selected" : ""}`}
        style={{ paddingLeft: 8 + depth * 16 }}
        onClick={() => onSelect(node)}
      >
        <span
          className="twisty"
          onClick={(e) => {
            e.stopPropagation();
            if (node.is_dir) setExpanded((x) => !x);
          }}
        >
          {node.is_dir ? (expanded ? "▾" : "▸") : ""}
        </span>
        <span className="icon">{node.is_dir ? "📁" : "📄"}</span>
        <span className="nm">{node.name}</span>
        {(() => {
          const nt = flagState(node, "no_transfer");
          const pr = flagState(node, "processed");
          return (
            <>
              {nt === "all" && <span className="badge keep">NO XFER</span>}
              {nt === "some" && (
                <span className="badge warn" title="Mixed: some files not marked">
                  NO XFER⚠
                </span>
              )}
              {pr === "all" && <span className="badge proc">PROC</span>}
              {pr === "some" && (
                <span className="badge warn" title="Mixed: some files not processed">
                  PROC⚠
                </span>
              )}
            </>
          );
        })()}
        {eff?.jira_ticket && <span className="badge">{eff.jira_ticket}</span>}
        <span className="meta">
          {node.is_dir ? (
            <span title="files under here (respecting filters)">
              {(node.filtered_file_count ?? 0).toLocaleString()} files
            </span>
          ) : (
            <span>{node.file_type}</span>
          )}
          <span>
            {node.is_dir
              ? fmtBytes(node.filtered_total_size)
              : fmtBytes(node.size_bytes)}
          </span>
          <span className="muted">{node.last_accessed || ""}</span>
        </span>
      </div>
      {expanded && (
        <div>
          {loading && (
            <div className="muted" style={{ paddingLeft: 8 + (depth + 1) * 16 }}>
              loading…
            </div>
          )}
          {children?.map((c) => (
            <TreeNode
              key={c.id}
              node={c}
              datasetId={datasetId}
              filters={filters}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              refreshKey={refreshKey}
            />
          ))}
          {children && children.length === 0 && !loading && (
            <div
              className="muted"
              style={{ paddingLeft: 8 + (depth + 1) * 16, fontSize: 11 }}
            >
              (empty)
            </div>
          )}
        </div>
      )}
    </div>
  );
}
