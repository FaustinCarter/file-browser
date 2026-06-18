"""End-to-end API tests covering import, tree, aggregates, and inheritance."""


def _find(client, dataset_id, name):
    """Helper: locate a node by name via search."""
    r = client.get("/api/nodes/search", params={"dataset_id": dataset_id, "q": name})
    items = r.json()["items"]
    return next(i for i in items if i["name"] == name)


def test_import_builds_tree(client, loaded):
    ds = loaded
    assert ds["row_count"] == 8
    # roots
    r = client.get("/api/tree/children", params={"dataset_id": ds["id"]})
    roots = r.json()["children"]
    assert len(roots) == 1
    assert roots[0]["name"] == "Root"
    assert roots[0]["is_dir"] is True


def test_size_parsing_and_readonly_columns(client, loaded):
    ds = loaded
    deck = _find(client, ds["id"], "deck.pptx")
    assert deck["size_bytes"] == 500 * 1024**2
    assert deck["size_raw"] == "500 MB"
    assert deck["file_type"] == "PPTX File"
    assert deck["is_dir"] is False


def test_folder_filtered_count_respects_type(client, loaded):
    ds = loaded
    root = client.get("/api/tree/children", params={"dataset_id": ds["id"]}).json()["children"][0]
    # Unfiltered: 5 files under Root
    assert root["filtered_file_count"] == 5
    # Filter to PPTX only -> 2 files under Root
    r = client.get(
        "/api/tree/children",
        params={"dataset_id": ds["id"], "types": ["PPTX File"]},
    )
    root_f = r.json()["children"][0]
    assert root_f["filtered_file_count"] == 2


def test_folder_count_respects_last_accessed(client, loaded):
    ds = loaded
    # notes.pptx accessed 01/15/2020; others 2024. Filter accessed_after 2023.
    r = client.get(
        "/api/tree/children",
        params={"dataset_id": ds["id"], "accessed_after": "2023-01-01"},
    )
    root = r.json()["children"][0]
    assert root["filtered_file_count"] == 4  # excludes notes.pptx


def test_counts_endpoint(client, loaded):
    ds = loaded
    root = client.get("/api/tree/children", params={"dataset_id": ds["id"]}).json()["children"][0]
    c = client.get(f"/api/nodes/{root['id']}/counts").json()
    assert c["file_count"] == 5
    assert c["folder_count"] == 2


def test_type_breakdown(client, loaded):
    ds = loaded
    root = client.get("/api/tree/children", params={"dataset_id": ds["id"]}).json()["children"][0]
    rows = client.get(f"/api/nodes/{root['id']}/type-breakdown").json()
    by_type = {r["file_type"]: r["count"] for r in rows}
    assert by_type["PPTX File"] == 2
    assert by_type["XLSX File"] == 1
    assert by_type["JPG File"] == 1


def test_folder_type_counts_bulk(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    images = _find(client, ds["id"], "Images")
    r = client.post(
        "/api/nodes/type-counts",
        json={"node_ids": [reports["id"], images["id"]], "types": ["PPTX File"]},
    )
    res = {x["name"]: x["file_count"] for x in r.json()["results"]}
    assert res["Reports"] == 2
    assert res["Images"] == 0


def test_annotation_inheritance(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    deck = _find(client, ds["id"], "deck.pptx")

    # Mark the Reports folder as keep=True
    client.patch(f"/api/nodes/{reports['id']}/annotation", json={"keep": True})

    # deck.pptx (a child) should inherit keep=True
    deck_after = client.get(f"/api/nodes/{deck['id']}").json()
    assert deck_after["effective"]["keep"] is True
    assert "keep" in deck_after["inherited_fields"]
    assert deck_after["own"]["keep"] is None

    # Override on the child wins
    client.patch(f"/api/nodes/{deck['id']}/annotation", json={"keep": False})
    deck_override = client.get(f"/api/nodes/{deck['id']}").json()
    assert deck_override["effective"]["keep"] is False
    assert "keep" not in deck_override["inherited_fields"]


def test_bulk_annotation_stamps_descendants(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    r = client.post(
        "/api/nodes/bulk-annotation",
        json={
            "node_id": reports["id"],
            "files_only": True,
            "types": ["PPTX File"],
            "values": {"jira_ticket": "MIG-100"},
        },
    )
    assert r.json()["updated"] == 2
    # Filter the grid by jira ticket
    g = client.get(
        "/api/nodes/search",
        params={"dataset_id": ds["id"], "jira": "MIG-100"},
    ).json()
    assert g["total"] == 2


def test_filtered_folder_flag_only_matching_type(client, loaded):
    """Checking Keep on a folder with a type filter active must touch only the
    matching files; other types in the subtree stay unchanged."""
    ds = loaded
    reports = _find(client, ds["id"], "Reports")  # 2 pptx + 1 xlsx
    r = client.post(
        "/api/nodes/bulk-annotation",
        json={
            "node_id": reports["id"],
            "files_only": True,
            "include_self": False,
            "types": ["PPTX File"],
            "values": {"keep": True},
        },
    )
    assert r.json()["updated"] == 2

    deck = client.get(f"/api/nodes/{_find(client, ds['id'], 'deck.pptx')['id']}").json()
    assert deck["effective"]["keep"] is True  # a PPTX got it

    data = client.get(f"/api/nodes/{_find(client, ds['id'], 'data.xlsx')['id']}").json()
    assert data["effective"]["keep"] is None  # the XLSX did not

    # the folder itself is untouched
    rep = client.get(f"/api/nodes/{reports['id']}").json()
    assert rep["own"]["keep"] is None


def test_filtered_folder_flag_respects_last_accessed(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    # deck.pptx & data.xlsx accessed 2024; notes.pptx accessed 01/15/2020.
    r = client.post(
        "/api/nodes/bulk-annotation",
        json={
            "node_id": reports["id"],
            "files_only": True,
            "include_self": False,
            "accessed_after": "2023-01-01",
            "values": {"keep": True},
        },
    )
    assert r.json()["updated"] == 2  # deck + data, not notes

    notes = client.get(f"/api/nodes/{_find(client, ds['id'], 'notes.pptx')['id']}").json()
    assert notes["effective"]["keep"] is None  # old file excluded
    deck = client.get(f"/api/nodes/{_find(client, ds['id'], 'deck.pptx')['id']}").json()
    assert deck["effective"]["keep"] is True


def test_search_filters_and_pagination(client, loaded):
    ds = loaded
    r = client.get(
        "/api/nodes/search",
        params={"dataset_id": ds["id"], "is_dir": False, "page_size": 2},
    ).json()
    assert r["total"] == 5
    assert len(r["items"]) == 2


def test_delete_dataset(client, loaded):
    ds = loaded
    client.delete(f"/api/datasets/{ds['id']}")
    r = client.get("/api/datasets").json()
    assert all(d["id"] != ds["id"] for d in r)


def test_multiple_datasets_isolated(client, loaded):
    ds1 = loaded
    # upload a second, smaller dataset
    csv2 = (
        "Name,Full Path,Size,Allocated,Files,Folders,% of Parent (Allocated),"
        "Last Modified,Last Accessed,Owner,Type,Dir Level (Relative)\n"
        "Other,D:\\Other\\,10 MB,10 MB,1,0,100 %,01/01/2023,01/01/2023,zoe,File Folder,0\n"
        "x.txt,D:\\Other\\x.txt,10 MB,10 MB,1,0,100 %,01/01/2023,01/01/2023,zoe,Text File,1\n"
    )
    ds2 = client.post(
        "/api/datasets", files={"file": ("o.csv", csv2, "text/csv")}
    ).json()
    r1 = client.get("/api/tree/children", params={"dataset_id": ds1["id"]}).json()
    r2 = client.get("/api/tree/children", params={"dataset_id": ds2["id"]}).json()
    assert r1["children"][0]["name"] == "Root"
    assert r2["children"][0]["name"] == "Other"
