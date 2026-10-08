"""AST scans that keep the human-only safety paths human-only.

Same approach as tlsehd195/NEW-'s `test_live_boundary.py` /
`test_evolution_boundary.py`: rather than trusting a comment, scan every
file under `src/` and `scripts/` and fail if an automated code path
could release the kill switch, construct an approval, or promote a
candidate into APPROVED/DEPLOYED.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"


def _files(root: Path):
    return sorted(p for p in root.rglob("*.py"))


def _called_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


# Where each human-only callable may be *called*. Definitions are not calls.
HUMAN_ONLY_CALLS = {
    "release_kill_switch": set(),
    "approve_for_live": set(),
    # Constructed only from a human's typed input, or re-validated from
    # the file that script wrote.
    "LiveActivationApproval": {
        "src/cointrader/live/approval.py",  # payload_to_approval
        "scripts/grant_live_approval.py",
    },
    "payload_to_approval": set(),
    "execute_transfer": {"scripts/run_fund_transfer.py"},
    "FundTransferApproval": {
        "src/cointrader/funding/approval.py",
        "scripts/run_fund_transfer.py",
    },
}


def test_human_only_callables_are_not_called_from_automated_code():
    offenders = []
    for path in _files(SRC) + _files(REPO / "scripts"):
        called = _called_names(ast.parse(path.read_text(encoding="utf-8")))
        for name, allowed in HUMAN_ONLY_CALLS.items():
            if name in called and _rel(path) not in allowed:
                offenders.append(f"{_rel(path)} calls {name}")
    assert offenders == []


def test_automatic_promotion_never_names_a_human_only_status():
    """`next_automatic_status` must not mention APPROVED or DEPLOYED."""
    tree = ast.parse((SRC / "cointrader/evolution/status.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "next_automatic_status")
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert not attrs & {"APPROVED", "DEPLOYED"}


def test_no_code_outside_evolution_constructs_human_only_status_transition():
    offenders = []
    for path in _files(SRC):
        if _rel(path) == "src/cointrader/evolution/status.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"APPROVED", "DEPLOYED"}:
                offenders.append(f"{_rel(path)}:{node.lineno}")
    assert offenders == []


def test_kill_switch_engaged_false_is_only_built_in_release():
    """The only `KillSwitchEvent(False, ...)` is inside release_kill_switch."""
    tree = ast.parse((SRC / "cointrader/live/kill_switch.py").read_text(encoding="utf-8"))
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            if getattr(call.func, "id", None) == "KillSwitchEvent" and call.args:
                first = call.args[0]
                if isinstance(first, ast.Constant) and first.value is False:
                    assert fn.name == "release_kill_switch"


def test_src_is_standard_library_only():
    import sys
    allowed = set(sys.stdlib_module_names) | {"cointrader"}
    offenders = []
    for path in _files(SRC):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [node.module or ""]
            else:
                continue
            for m in mods:
                if m.split(".")[0] not in allowed:
                    offenders.append(f"{_rel(path)} imports {m}")
    assert offenders == []


# Order-changing exchange calls: only the gate-checking LiveBroker may make them.
ORDER_SENDING_CALLS = {
    "_new_order": {"src/cointrader/execution/live_broker.py"},
    "_cancel_order": {"src/cointrader/execution/live_broker.py"},
    # Nothing automated builds a LiveBroker; wiring live mode is a human decision (ADR-0015).
    "LiveBroker": set(),
}


def test_order_sending_is_only_reachable_through_the_live_broker():
    offenders = []
    for path in _files(SRC) + _files(REPO / "scripts"):
        called = _called_names(ast.parse(path.read_text(encoding="utf-8")))
        for name, allowed in ORDER_SENDING_CALLS.items():
            if name in called and _rel(path) not in allowed:
                offenders.append(f"{_rel(path)} calls {name}")
    assert offenders == []


def test_live_broker_checks_the_safety_gate_before_every_send():
    tree = ast.parse((SRC / "cointrader/execution/live_broker.py").read_text(encoding="utf-8"))
    submit = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "submit")
    calls = [getattr(c.func, "attr", getattr(c.func, "id", None))
             for c in sorted((n for n in ast.walk(submit) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))]
    assert "preflight" in calls and "_new_order" in calls
    assert calls.index("preflight") < calls.index("_new_order")
    preflight = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "preflight")
    assert "evaluate_safety_gate" in _called_names(preflight)


def test_accumulated_data_never_changes_live_strategies():
    """Journal, dataset and maintenance code may read strategies but never
    write configs or the strategy registry (ADR-0015)."""
    offenders = []
    for path in (_files(SRC / "cointrader/journal") + _files(SRC / "cointrader/learning")
                 + [SRC / "cointrader/research/dataset.py"]):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and n.body
                      and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if id(node) in docstrings:
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                names = [a.name for a in node.names]
                if "strategies.registry" in mod or any("strategies.registry" in n for n in names):
                    offenders.append(f"{_rel(path)} imports the strategy registry")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "configs/" in node.value:
                offenders.append(f"{_rel(path)}:{node.lineno} names a configs/ path")
    assert offenders == []


def test_learning_cycle_cannot_reach_trading():
    """ADR-0044: a retrained (challenger) model is evaluated in shadow only. The learning package may
    not import the trader, execution, live or registry code, nor touch a `strategies` attribute, so it
    has no way to swap what the trader runs; promotion stays on the validation path (CLAUDE.md rule 1)."""
    banned = ("cointrader.paper", "cointrader.execution", "cointrader.live", "cointrader.strategies.registry",
              "cointrader.evolution", "cointrader.research.lifecycle")
    offenders = []
    for path in _files(SRC / "cointrader/learning"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(banned):
                offenders.append(f"{_rel(path)} imports {node.module}")
            if isinstance(node, ast.Import) and any(a.name.startswith(banned) for a in node.names):
                offenders.append(f"{_rel(path)} imports {[a.name for a in node.names]}")
            if isinstance(node, ast.Attribute) and node.attr == "strategies":
                offenders.append(f"{_rel(path)}:{node.lineno} touches .strategies")
    assert offenders == []
