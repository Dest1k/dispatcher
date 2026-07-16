"""Project memory graph, context-integrity ledger, deterministic risk scoring."""
import pytest

from app.ledger import ContextLedger
from app.memory_graph import MemoryGraph
from app.risk import assess_change


# ---- memory graph -------------------------------------------------------------

@pytest.fixture
def graph(tmp_path):
    g = MemoryGraph(tmp_path / "memory.db")
    yield g
    g.close()


def test_nodes_edges_and_neighbors(graph):
    bug = graph.add_node("p1", "bug", "Гонка при записи конфига",
                         body="Два потока пишут config.json одновременно")
    fix = graph.add_node("p1", "solution", "Атомарная запись через tmp+replace")
    graph.add_edge(bug, fix, "solved_by", reason="replace атомарен на одном томе")
    rels = graph.neighbors(bug)
    assert len(rels) == 1
    edge, other = rels[0]
    assert edge.rel == "solved_by" and "атомарен" in edge.reason
    assert other.id == fix


def test_validation_rejects_garbage(graph):
    with pytest.raises(ValueError):
        graph.add_node("p1", "vibe", "не тип")
    with pytest.raises(ValueError):
        graph.add_node("p1", "bug", "x", status="великолепно")
    node = graph.add_node("p1", "task", "ок")
    with pytest.raises(ValueError):
        graph.add_edge(node, "missing-node", "relates_to")
    with pytest.raises(ValueError):
        graph.add_edge(node, node, "дружит_с")


def test_search_recent_counts_scoped_by_project(graph):
    graph.add_node("p1", "decision", "Использовать SQLite для прогонов")
    graph.add_node("p2", "decision", "Использовать Postgres")
    found = graph.search("p1", "SQLite")
    assert len(found) == 1 and found[0].project_id == "p1"
    assert graph.search("p1", "Postgres") == []
    assert graph.counts("p1") == {"decision": 1}
    assert [n.kind for n in graph.recent("p2")] == ["decision"]


def test_context_pack_digest_with_reasons(graph):
    bug = graph.add_node("p1", "bug", "Патчи ломались на CRLF")
    fix = graph.add_node("p1", "lesson", "Всегда писать патчи с newline='\\n'")
    graph.add_edge(fix, bug, "learned_from", reason="Windows переводил LF в CRLF")
    pack = graph.context_pack("p1", query="патчи CRLF")
    assert "Память проекта" in pack
    assert "CRLF" in pack
    assert "причина" in pack
    assert graph.context_pack("empty-project") == ""


def test_status_transitions(graph):
    node = graph.add_node("p1", "approach", "Подход А")
    graph.set_status(node, "superseded")
    assert graph.get_node(node).status == "superseded"


# ---- ledger ----------------------------------------------------------------------

def test_ledger_records_and_claims():
    ledger = ContextLedger()
    assert not ledger.can_claim("inspected_repository")
    ledger.record("repo", "D:/proj")
    ledger.record_tool("a1", "read_file", {"path": "app/main.py"})
    ledger.record_tool("a1", "write_file", {"path": "app/new.py"})
    ledger.record_tool("a1", "run_command", {"command": "pytest -q"})
    ledger.record("test", "pytest: pass")
    assert ledger.can_claim("inspected_repository")
    assert ledger.can_claim("modified_files")
    assert ledger.can_claim("ran_tests")
    assert not ledger.can_claim("called_providers")
    assert ledger.count("file_read") == 1 and ledger.count("command") == 1


def test_ledger_attest_blocks_unbacked_claims():
    ledger = ContextLedger()
    honest = ledger.attest("ran_tests", "все тесты прошли")
    assert "нет свидетельств" in honest
    ledger.record("test", "pytest: pass")
    assert ledger.attest("ran_tests", "все тесты прошли") == "все тесты прошли"


def test_ledger_sink_and_summary():
    persisted = []
    ledger = ContextLedger(sink=lambda k, p: persisted.append((k, p)))
    ledger.record("commit", "abc123")
    ledger.record("file_write", "x.py", agent="codex_cli")
    assert ("commit", "abc123") in persisted
    assert ("file_write", "codex_cli: x.py") in persisted
    md = ledger.summary_md()
    assert "abc123" in md and "записей файлов: 1" in md
    empty = ContextLedger().summary_md()
    assert "свидетельств не зафиксировано" in empty


def test_ledger_rejects_unknown_kind():
    with pytest.raises(ValueError):
        ContextLedger().record("vibes", "x")


# ---- risk -----------------------------------------------------------------------

def _diff(added=5, deleted=2):
    lines = ["diff --git a/x.py b/x.py", "--- a/x.py", "+++ b/x.py"]
    lines += [f"+line {i}" for i in range(added)]
    lines += [f"-old {i}" for i in range(deleted)]
    return "\n".join(lines)


def test_small_docs_change_is_low_risk():
    a = assess_change(_diff(3, 1), ["README.md"])
    assert a.level == "low"
    assert a.factors == []


def test_auth_paths_raise_risk():
    a = assess_change(_diff(30, 5), ["app/auth/login.py"])
    names = [f.name for f in a.factors]
    assert any("auth" in n or "чувствительные" in n for n in names)
    assert a.level in ("medium", "high")


def test_huge_diff_and_ci_files_are_high():
    big = "\n".join(["+x"] * 1600)
    a = assess_change(big, [".github/workflows/ci.yml"] +
                      [f"m{i}.py" for i in range(12)])
    assert a.level == "high"
    assert a.score >= 0.6


def test_deletion_dominant_flagged():
    diff = "\n".join(["-gone"] * 200 + ["+kept"] * 3)
    a = assess_change(diff, ["core.py"])
    assert any("удаления" in f.name for f in a.factors)


def test_code_without_tests_flagged_and_describe_russian():
    a = assess_change(_diff(50, 0), ["app/feature.py"])
    assert any("без тестов" in f.name for f in a.factors)
    text = a.describe()
    assert "Риск изменений" in text
    b = assess_change(_diff(50, 0), ["app/feature.py", "tests/test_feature.py"])
    assert not any("без тестов" in f.name for f in b.factors)
