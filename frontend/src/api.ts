// Typed API client for the FastAPI backend.

export interface Dataset {
  id: number;
  name: string;
  filename: string;
  row_count: number;
  created_at?: string;
}

export interface Annotation {
  processed: boolean | null;
  no_transfer: boolean | null;
  target_location: string | null;
  jira_ticket: string | null;
  comment: string | null;
  assignee: string | null;
}

export type FlagField = "no_transfer" | "processed";

// Filter sentinel: records with no effective value for a field.
export const UNASSIGNED = "__none__";

// Display name of the current user, sent as X-Actor on mutations for audit.
let _actor = "";
export function setActor(name: string) {
  _actor = name;
}
function jsonHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", "X-Actor": _actor };
}

export interface NodeOut {
  id: number;
  dataset_id: number;
  parent_id: number | null;
  depth: number;
  name: string;
  full_path: string;
  is_dir: boolean;
  size_raw: string | null;
  size_bytes: number | null;
  allocated_raw: string | null;
  allocated_bytes: number | null;
  files_count: number | null;
  folders_count: number | null;
  pct_parent_raw: string | null;
  pct_parent: number | null;
  last_modified: string | null;
  last_accessed: string | null;
  owner: string | null;
  file_type: string | null;
  dir_level: number | null;
  has_children: boolean;
  effective: Annotation | null;
  own: Annotation | null;
  inherited_fields: string[];
  filtered_file_count: number | null;
  filtered_total_size: number | null;
  total_files: number | null;
  no_transfer_marked: number | null;
  processed_marked: number | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface Filters {
  types?: string[];
  accessed_after?: string;
  accessed_before?: string;
  // tri-state flag filters: undefined (any) | "yes" (only marked) | "no" (hide marked)
  no_transfer?: "yes" | "no";
  processed?: "yes" | "no";
  // value filters: undefined (any) | a value | UNASSIGNED ("__none__")
  jira?: string;
  assignee?: string;
}

// Tri-state rollup of a folder's descendant files for a boolean flag.
export type FlagState = "empty" | "none" | "some" | "all";

export function folderFlagState(
  marked: number | null | undefined,
  total: number | null | undefined,
): FlagState {
  const m = marked ?? 0;
  const t = total ?? 0;
  if (t === 0) return "empty";
  if (m === 0) return "none";
  if (m >= t) return "all";
  return "some";
}

function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => u.append(k, String(x)));
    else u.append(k, String(v));
  }
  const s = u.toString();
  return s ? `?${s}` : "";
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json() as Promise<T>;
}

export const api = {
  listDatasets: () => fetch("/api/datasets").then((r) => j<Dataset[]>(r)),

  uploadDataset: (file: File, name: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (name) fd.append("name", name);
    return fetch("/api/datasets", { method: "POST", body: fd }).then((r) =>
      j<Dataset>(r),
    );
  },

  deleteDataset: (id: number) =>
    fetch(`/api/datasets/${id}`, { method: "DELETE" }).then((r) => j(r)),

  fileTypes: (datasetId: number) =>
    fetch(`/api/datasets/${datasetId}/file-types`).then((r) =>
      j<{ file_type: string; count: number }[]>(r),
    ),

  children: (datasetId: number, parentId: number | null, filters: Filters) =>
    fetch(
      `/api/tree/children${qs({
        dataset_id: datasetId,
        parent_id: parentId ?? undefined,
        types: filters.types,
        accessed_after: filters.accessed_after,
        accessed_before: filters.accessed_before,
        no_transfer: filters.no_transfer,
        processed: filters.processed,
        jira: filters.jira,
        assignee: filters.assignee,
      })}`,
    ).then((r) => j<{ parent_id: number | null; children: NodeOut[] }>(r)),

  distinctValues: (datasetId: number, field: "jira_ticket" | "assignee") =>
    fetch(`/api/datasets/${datasetId}/distinct/${field}`).then((r) =>
      j<{ values: string[] }>(r),
    ),

  folderFlag: (
    nodeId: number,
    payload: {
      field: FlagField;
      value: boolean | null;
      types?: string[];
      accessed_after?: string;
      accessed_before?: string;
    },
  ) =>
    fetch(`/api/nodes/${nodeId}/folder-flag`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    }).then((r) => j<NodeOut>(r)),

  node: (id: number) => fetch(`/api/nodes/${id}`).then((r) => j<NodeOut>(r)),

  counts: (id: number) =>
    fetch(`/api/nodes/${id}/counts`).then((r) =>
      j<{ file_count: number; folder_count: number }>(r),
    ),

  stats: (id: number, filters: Filters) =>
    fetch(
      `/api/nodes/${id}/stats${qs({
        types: filters.types,
        accessed_after: filters.accessed_after,
        accessed_before: filters.accessed_before,
      })}`,
    ).then((r) => j<{ file_count: number; total_size: number }>(r)),

  typeBreakdown: (id: number, filters: Filters, search?: string) =>
    fetch(
      `/api/nodes/${id}/type-breakdown${qs({
        types: filters.types,
        accessed_after: filters.accessed_after,
        accessed_before: filters.accessed_before,
        search,
      })}`,
    ).then((r) =>
      j<{ file_type: string | null; count: number; total_size: number }[]>(r),
    ),

  typeCounts: (nodeIds: number[], filters: Filters) =>
    fetch("/api/nodes/type-counts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        node_ids: nodeIds,
        types: filters.types,
        accessed_after: filters.accessed_after,
        accessed_before: filters.accessed_before,
      }),
    }).then((r) => j<{ results: any[] }>(r)),

  search: (params: Record<string, unknown>) =>
    fetch(`/api/nodes/search${qs(params)}`).then((r) =>
      j<{ total: number; page: number; page_size: number; items: NodeOut[] }>(r),
    ),

  updateAnnotation: (id: number, values: Partial<Annotation>) =>
    fetch(`/api/nodes/${id}/annotation`, {
      method: "PATCH",
      headers: jsonHeaders(),
      body: JSON.stringify(values),
    }).then((r) => j<NodeOut>(r)),

  bulkAnnotation: (payload: {
    node_id: number;
    include_self?: boolean;
    files_only?: boolean;
    types?: string[];
    accessed_after?: string;
    accessed_before?: string;
    values: Partial<Annotation>;
  }) =>
    fetch("/api/nodes/bulk-annotation", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    }).then((r) => j<{ updated: number }>(r)),
};

export function fmtBytes(b: number | null | undefined): string {
  if (b === null || b === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let v = b;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}
