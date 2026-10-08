"""Every text read/write names its encoding.

Without `encoding=`, Python uses the OS locale: cp949 on Korean Windows, so files holding Korean
text (written as UTF-8) fail to read there while Linux CI stays green."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _missing_encoding(path: Path) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or any(k.arg == "encoding" for k in node.keywords):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            out.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            if not (isinstance(mode, ast.Constant) and "b" in str(mode.value)):
                out.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return out


def test_text_io_always_names_encoding():
    files = [p for d in ("src", "scripts", "tests") for p in sorted((ROOT / d).rglob("*.py"))]
    bad = [hit for p in files for hit in _missing_encoding(p)]
    assert not bad, "add encoding=\"utf-8\": " + ", ".join(bad)
