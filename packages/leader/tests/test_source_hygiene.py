from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]
BOM = chr(0xFEFF)


def test_no_python_source_contains_an_invisible_byte_order_mark():
    # Source must spell it as a visible escape sequence, so a reader can see it is there.
    offenders = [
        str(path.relative_to(PACKAGES))
        for path in sorted(PACKAGES.rglob("*.py"))
        if BOM in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
