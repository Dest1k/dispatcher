"""Task DAG: validation (deps, cycles, intra-layer zone overlap) + layering."""
from app.dag import TaskNode, topological_layers, validate_dag


def _nodes():
    return [
        {"id": "model", "provider": "a", "title": "Model", "files": ["app/model.py"]},
        {"id": "migration", "provider": "b", "title": "Migration",
         "files": ["app/migrations/"], "depends_on": ["model"]},
        {"id": "api", "provider": "c", "title": "API",
         "files": ["app/api.py"], "depends_on": ["model"]},
        {"id": "docs", "provider": "a", "title": "Docs", "files": ["README.md"],
         "depends_on": ["api", "migration"]},
    ]


PIDS = {"a", "b", "c"}


def test_valid_dag_layers():
    assert validate_dag(_nodes(), PIDS) == []
    layers = topological_layers(_nodes())
    got = [sorted(t.id for t in layer) for layer in layers]
    assert got == [["model"], ["api", "migration"], ["docs"]]


def test_unknown_dependency_rejected():
    nodes = _nodes()
    nodes[1]["depends_on"] = ["ghost"]
    issues = validate_dag(nodes, PIDS)
    assert any("неизвестной «ghost»" in i for i in issues)


def test_unknown_provider_rejected():
    nodes = _nodes()
    nodes[0]["provider"] = "nobody"
    assert any("неизвестный исполнитель" in i for i in validate_dag(nodes, PIDS))


def test_self_dependency_rejected():
    nodes = [{"id": "x", "provider": "a", "depends_on": ["x"]}]
    assert any("сама от себя" in i for i in validate_dag(nodes, PIDS))


def test_cycle_rejected():
    nodes = [
        {"id": "x", "provider": "a", "depends_on": ["y"]},
        {"id": "y", "provider": "b", "depends_on": ["x"]},
    ]
    issues = validate_dag(nodes, PIDS)
    assert any("цикл" in i for i in issues)


def test_cycle_raises_in_layering():
    import pytest
    nodes = [TaskNode("x", "a", "X", "", depends_on=["y"]),
             TaskNode("y", "b", "Y", "", depends_on=["x"])]
    with pytest.raises(ValueError, match="цикл"):
        topological_layers(nodes)


def test_concurrent_zone_overlap_rejected():
    # two independent tasks (same layer) touching overlapping zones
    nodes = [
        {"id": "one", "provider": "a", "files": ["src"]},
        {"id": "two", "provider": "b", "files": ["src/main.py"]},
    ]
    issues = validate_dag(nodes, PIDS)
    assert any("пересекаются по файлам" in i for i in issues)


def test_sequential_zone_reuse_allowed():
    # same file across layers (sequential) is fine — they don't run together
    nodes = [
        {"id": "one", "provider": "a", "files": ["src/main.py"]},
        {"id": "two", "provider": "b", "files": ["src/main.py"],
         "depends_on": ["one"]},
    ]
    assert validate_dag(nodes, PIDS) == []
    layers = topological_layers(nodes)
    assert [len(x) for x in layers] == [1, 1]


def test_duplicate_id_rejected():
    nodes = [{"id": "x", "provider": "a"}, {"id": "x", "provider": "b"}]
    assert any("повторяющийся идентификатор" in i for i in validate_dag(nodes, PIDS))


def test_describe_is_readable():
    from app.dag import describe
    text = describe(topological_layers(_nodes()))
    assert "Слой 1" in text and "Слой 3" in text and "Model" in text
