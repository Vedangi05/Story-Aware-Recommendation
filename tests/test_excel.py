import openpyxl

from conftest import MASTER, sha256
from services.excel_reader import MasterDataset, normalize_pair_id


def test_all_pairs_load_and_blank_rows_skipped():
    ds = MasterDataset(MASTER)
    assert len(ds) == 100
    assert ds.pair_ids == [str(i) for i in range(1, 101)]
    # The workbook's row 2 is blank – make sure it was really there and skipped.
    ws = openpyxl.load_workbook(MASTER, read_only=True)["Article-pairs"]
    row2 = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    assert all(v is None for v in row2)


def test_pair_content_is_complete_and_untruncated():
    ds = MasterDataset(MASTER)
    wb = openpyxl.load_workbook(MASTER, read_only=True)
    raw = [r for r in wb["Article-pairs"].iter_rows(min_row=2, values_only=True) if r[0] is not None]
    for r in raw:
        p = ds.get(r[0])
        assert p["a_headline"] == (r[1] or "").strip()
        assert p["a_paragraph"] == (r[3] or "").strip()
        assert p["link_text"] == (r[4] or "").strip()
        assert p["b_paragraph"] == (r[7] or "").strip()  # missing values -> ""


def test_label_definitions_from_label_info_sheet():
    ds = MasterDataset(MASTER)
    names = ds.label_names
    assert names == ["Same Story", "Related", "Unrelated"]
    assert ds.labels[0]["description"].startswith("B covers an earlier stage")


def test_normalize_pair_id():
    assert normalize_pair_id(3) == "3"
    assert normalize_pair_id(3.0) == "3"
    assert normalize_pair_id(" 3 ") == "3"
    assert normalize_pair_id(None) is None
    assert normalize_pair_id("") is None


def test_reading_does_not_modify_master():
    before = sha256(MASTER)
    MasterDataset(MASTER).pairs
    assert sha256(MASTER) == before
