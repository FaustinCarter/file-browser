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

    # Mark the Reports folder as no_transfer=True
    client.patch(f"/api/nodes/{reports['id']}/annotation", json={"no_transfer": True})

    # deck.pptx (a child) should inherit no_transfer=True
    deck_after = client.get(f"/api/nodes/{deck['id']}").json()
    assert deck_after["effective"]["no_transfer"] is True
    assert "no_transfer" in deck_after["inherited_fields"]
    assert deck_after["own"]["no_transfer"] is None

    # Override on the child wins
    client.patch(f"/api/nodes/{deck['id']}/annotation", json={"no_transfer": False})
    deck_override = client.get(f"/api/nodes/{deck['id']}").json()
    assert deck_override["effective"]["no_transfer"] is False
    assert "no_transfer" not in deck_override["inherited_fields"]


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
            "values": {"no_transfer": True},
        },
    )
    assert r.json()["updated"] == 2

    deck = client.get(f"/api/nodes/{_find(client, ds['id'], 'deck.pptx')['id']}").json()
    assert deck["effective"]["no_transfer"] is True  # a PPTX got it

    data = client.get(f"/api/nodes/{_find(client, ds['id'], 'data.xlsx')['id']}").json()
    assert data["effective"]["no_transfer"] is None  # the XLSX did not

    # the folder itself is untouched
    rep = client.get(f"/api/nodes/{reports['id']}").json()
    assert rep["own"]["no_transfer"] is None


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
            "values": {"no_transfer": True},
        },
    )
    assert r.json()["updated"] == 2  # deck + data, not notes

    notes = client.get(f"/api/nodes/{_find(client, ds['id'], 'notes.pptx')['id']}").json()
    assert notes["effective"]["no_transfer"] is None  # old file excluded
    deck = client.get(f"/api/nodes/{_find(client, ds['id'], 'deck.pptx')['id']}").json()
    assert deck["effective"]["no_transfer"] is True


def test_grid_effective_flag_filter_hides_marked(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    # Mark the whole Reports subtree no_transfer via the folder-flag endpoint.
    client.post(
        f"/api/nodes/{reports['id']}/folder-flag",
        json={"field": "no_transfer", "value": True},
    )
    # Hide marked -> only the 2 Images files remain.
    hidden = client.get(
        "/api/nodes/search",
        params={"dataset_id": ds["id"], "is_dir": False, "no_transfer": "no"},
    ).json()
    names = {i["name"] for i in hidden["items"]}
    assert names == {"a.jpg", "b.png"}
    # Show only marked -> the 3 Reports files.
    shown = client.get(
        "/api/nodes/search",
        params={"dataset_id": ds["id"], "is_dir": False, "no_transfer": "yes"},
    ).json()
    assert shown["total"] == 3


def test_folder_flag_whole_subtree_rollup(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    client.post(
        f"/api/nodes/{reports['id']}/folder-flag",
        json={"field": "no_transfer", "value": True},
    )
    rep = client.get(f"/api/nodes/{reports['id']}").json()
    assert rep["total_files"] == 3
    assert rep["no_transfer_marked"] == 3  # fully marked
    assert rep["own"]["no_transfer"] is True

    # Clear it again.
    client.post(
        f"/api/nodes/{reports['id']}/folder-flag",
        json={"field": "no_transfer", "value": None},
    )
    rep2 = client.get(f"/api/nodes/{reports['id']}").json()
    assert rep2["no_transfer_marked"] == 0
    assert rep2["own"]["no_transfer"] is None


def test_folder_flag_scoped_is_indeterminate(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    # Mark only PPTX under Reports -> 2 of 3 files -> indeterminate folder.
    client.post(
        f"/api/nodes/{reports['id']}/folder-flag",
        json={"field": "no_transfer", "value": True, "types": ["PPTX File"]},
    )
    rep = client.get(f"/api/nodes/{reports['id']}").json()
    assert rep["total_files"] == 3
    assert rep["no_transfer_marked"] == 2  # mixed -> UI shows indeterminate/irregular
    assert rep["own"]["no_transfer"] is None  # folder itself stays unset


def test_tree_hide_marked_drops_fully_marked_folder(client, loaded):
    ds = loaded
    root = client.get("/api/tree/children", params={"dataset_id": ds["id"]}).json()["children"][0]
    images = _find(client, ds["id"], "Images")
    client.post(
        f"/api/nodes/{images['id']}/folder-flag",
        json={"field": "no_transfer", "value": True},
    )
    kids = client.get(
        "/api/tree/children",
        params={"dataset_id": ds["id"], "parent_id": root["id"], "no_transfer": "no"},
    ).json()["children"]
    names = {k["name"] for k in kids}
    assert "Images" not in names  # fully marked -> dropped
    assert "Reports" in names


def test_audit_fields_set_on_edit(client, loaded):
    ds = loaded
    deck = _find(client, ds["id"], "deck.pptx")
    # Untouched -> audit is null.
    before = client.get(f"/api/nodes/{deck['id']}").json()
    assert before["updated_at"] is None and before["updated_by"] is None

    # Edit with an actor header -> updated_by/updated_at populated.
    client.patch(
        f"/api/nodes/{deck['id']}/annotation",
        json={"comment": "looked at it"},
        headers={"X-Actor": "carol"},
    )
    after = client.get(f"/api/nodes/{deck['id']}").json()
    assert after["updated_by"] == "carol"
    assert after["updated_at"] is not None


def test_assignee_inheritance_and_filter(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    deck = _find(client, ds["id"], "deck.pptx")
    a_jpg = _find(client, ds["id"], "a.jpg")  # under Images, not Reports

    # Assign the Reports folder to dave -> children inherit.
    client.patch(
        f"/api/nodes/{reports['id']}/annotation",
        json={"assignee": "dave"}, headers={"X-Actor": "dave"},
    )
    deck_v = client.get(f"/api/nodes/{deck['id']}").json()
    assert deck_v["effective"]["assignee"] == "dave"
    assert "assignee" in deck_v["inherited_fields"]

    # Filter the grid by effective assignee=dave -> Reports subtree only.
    g = client.get(
        "/api/nodes/search",
        params={"dataset_id": ds["id"], "is_dir": False, "assignee": "dave"},
    ).json()
    names = {i["name"] for i in g["items"]}
    assert {"deck.pptx", "notes.pptx", "data.xlsx"} <= names
    assert a_jpg["name"] not in names

    # Filter for unassigned -> the Images files (no assignee).
    ug = client.get(
        "/api/nodes/search",
        params={"dataset_id": ds["id"], "is_dir": False, "assignee": "__none__"},
    ).json()
    un = {i["name"] for i in ug["items"]}
    assert {"a.jpg", "b.png"} <= un
    assert "deck.pptx" not in un


def test_jira_filter_value_and_unassigned(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    client.post(
        "/api/nodes/bulk-annotation",
        json={"node_id": reports["id"], "files_only": True, "types": ["PPTX File"],
              "values": {"jira_ticket": "MIG-7"}},
        headers={"X-Actor": "bob"},
    )
    hit = client.get(
        "/api/nodes/search",
        params={"dataset_id": ds["id"], "is_dir": False, "jira": "MIG-7"},
    ).json()
    assert hit["total"] == 2  # the 2 PPTX

    none = client.get(
        "/api/nodes/search",
        params={"dataset_id": ds["id"], "is_dir": False, "jira": "__none__"},
    ).json()
    names = {i["name"] for i in none["items"]}
    assert "deck.pptx" not in names and "a.jpg" in names


def test_distinct_values_endpoint(client, loaded):
    ds = loaded
    reports = _find(client, ds["id"], "Reports")
    client.patch(f"/api/nodes/{reports['id']}/annotation", json={"assignee": "dave"})
    images = _find(client, ds["id"], "Images")
    client.patch(f"/api/nodes/{images['id']}/annotation", json={"assignee": "erin"})
    r = client.get(f"/api/datasets/{ds['id']}/distinct/assignee").json()
    assert r["values"] == ["dave", "erin"]


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
