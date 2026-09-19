"""watchlist.yaml loader rules. No DB, no network."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
yaml = pytest.importorskip("yaml")
seed = pytest.importorskip("seed_watchlist")

ROOT = pathlib.Path(__file__).resolve().parents[2]


def write(tmp_path, text):
    p = tmp_path / "watchlist.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_benchmarks_are_required(tmp_path):
    with pytest.raises(SystemExit, match="benchmark"):
        seed.load_file(write(tmp_path, "tickers:\n  - {ticker: AAPL}\n"))


def test_holding_tag_is_rejected(tmp_path):
    with pytest.raises(SystemExit, match="holding"):
        seed.load_file(write(tmp_path, "benchmarks: [SPY]\ntickers:\n  - {ticker: AAPL, tags: [holding]}\n"))


def test_duplicate_ticker_is_rejected(tmp_path):
    doc = "benchmarks: [SPY]\ntickers:\n  - {ticker: AAPL}\n  - {ticker: aapl}\n"
    with pytest.raises(SystemExit, match="twice"):
        seed.load_file(write(tmp_path, doc))


def test_benchmark_row_is_added_and_tagged(tmp_path):
    rows = seed.load_file(write(tmp_path, "benchmarks: [SPY, QQQ]\ntickers:\n  - {ticker: AAPL, tags: [tech]}\n"))
    by = {r["ticker"]: r for r in rows}
    assert by["SPY"]["tags"] == ["benchmark"] and by["QQQ"]["tags"] == ["benchmark"]
    assert by["AAPL"]["tags"] == ["tech"]


def test_ticker_also_listed_as_benchmark_keeps_both_tags(tmp_path):
    rows = seed.load_file(write(tmp_path, "benchmarks: [SPY]\ntickers:\n  - {ticker: SPY, tags: [etf]}\n"))
    assert sorted(rows[0]["tags"]) == ["benchmark", "etf"]


def test_plain_string_entries_and_optional_fields(tmp_path):
    doc = "benchmarks: [SPY]\ntickers:\n  - MSFT\n  - {ticker: NEE, target_entry: 60, notes: hold}\n"
    by = {r["ticker"]: r for r in seed.load_file(write(tmp_path, doc))}
    assert by["MSFT"]["tags"] == [] and by["MSFT"]["target_entry"] is None
    assert by["NEE"]["target_entry"] == 60.0 and by["NEE"]["notes"] == "hold"


def test_shipped_example_file_parses():
    rows = seed.load_file(ROOT / "watchlist.example.yaml")
    assert len(rows) > 10
    assert any("benchmark" in r["tags"] for r in rows)
    assert all("holding" not in r["tags"] for r in rows)
