"""Migration runner rules. No DB, no driver."""
import hashlib
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
migrate = pytest.importorskip("migrate")


def test_files_are_numbered_in_order_with_no_gaps_or_duplicates():
    names = [f.name for f in migrate.files()]
    numbers = [int(re.match(r"^(\d{3})_", n).group(1)) for n in names]
    assert numbers == sorted(numbers), "migrations must sort in apply order"
    assert numbers == list(range(1, len(numbers) + 1)), f"gap or duplicate in {names}"


def test_plan_applies_everything_on_an_empty_database():
    steps = migrate.plan({})
    assert [a for a, _, _ in steps] == ["apply"] * len(migrate.files())


def test_plan_skips_what_is_already_recorded():
    done = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in migrate.files()}
    assert {a for a, _, _ in migrate.plan(done)} == {"skip"}


def test_plan_refuses_a_file_that_changed_after_it_was_applied():
    done = {f.name: "0" * 64 for f in migrate.files()}
    with pytest.raises(SystemExit, match="changed after it was applied"):
        migrate.plan(done)


def test_every_migration_is_idempotent_by_construction():
    """Each file must be safe to re-run: no bare CREATE TABLE or CREATE INDEX."""
    offenders = []
    for f in migrate.files():
        sql = f.read_text(encoding="utf-8")
        for stmt in re.findall(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX)(?! IF NOT EXISTS)", sql):
            offenders.append(f"{f.name}: {stmt}")
    assert not offenders, offenders
