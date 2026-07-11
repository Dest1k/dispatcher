from app.planning import validate_assignments


def test_valid_disjoint_plan():
    issues = validate_assignments([
        {"provider": "a", "files": ["x.py"]},
        {"provider": "b", "files": ["y.py"]},
    ], {"a", "b"})
    assert issues == []


def test_overlapping_paths_flagged():
    issues = validate_assignments([
        {"provider": "a", "files": ["shared.py"]},
        {"provider": "b", "files": ["shared.py"]},
    ], {"a", "b"})
    assert any("shared.py" in i for i in issues)


def test_unknown_provider_flagged():
    issues = validate_assignments([
        {"provider": "ghost", "files": ["x.py"]},
    ], {"a", "b"})
    assert any("ghost" in i for i in issues)


def test_same_provider_repeat_ok():
    issues = validate_assignments([
        {"provider": "a", "files": ["x.py"]},
        {"provider": "a", "files": ["x.py"]},
    ], {"a"})
    assert issues == []


def test_compare_url_helper():
    from app.orchestrator import _compare_url
    assert _compare_url("owner/repo", "dispatcher/x/integration") == \
        "https://github.com/owner/repo/compare/dispatcher/x/integration?expand=1"
    assert _compare_url("", "b") == ""
