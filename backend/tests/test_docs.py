from pathlib import Path

from sqlagent.semantic.docs import BEGIN, END, render_reference, write_reference


def test_reference_covers_every_table_and_column(catalog):
    text = render_reference(catalog)
    for t in catalog.tables:
        assert f"### `{t.name}`" in text
        for c in t.columns:
            assert f"| `{c.name}` |" in text


def test_write_preserves_hand_written_prose(catalog, tmp_path: Path):
    doc = tmp_path / "dd.md"
    doc.write_text(f"# Intro\nkeep me\n{BEGIN}\nstale\n{END}\n## Footer\n")
    write_reference(catalog, doc)
    out = doc.read_text()
    assert out.startswith("# Intro\nkeep me\n") and out.endswith("## Footer\n")
    assert "stale" not in out and "### `store_sales`" in out
