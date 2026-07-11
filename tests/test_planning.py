from app.planning import validate_assignments, zones_overlap


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


def test_directory_prefix_overlap_flagged():
    # 'src' covers 'src/main.py' under PathPolicy semantics -> collision risk
    issues = validate_assignments([
        {"provider": "a", "files": ["src"]},
        {"provider": "b", "files": ["src/main.py"]},
    ], {"a", "b"})
    assert any("пересек" in i for i in issues)


def test_sibling_directories_do_not_overlap():
    issues = validate_assignments([
        {"provider": "a", "files": ["src"]},
        {"provider": "b", "files": ["src2"]},
    ], {"a", "b"})
    assert issues == []


def test_glob_overlap_flagged():
    issues = validate_assignments([
        {"provider": "a", "files": ["app/*.py"]},
        {"provider": "b", "files": ["app/util.py"]},
    ], {"a", "b"})
    assert any("пересек" in i for i in issues)


def test_empty_zone_overlaps_everything():
    # an empty/whole-root zone plus a specific zone is not safely disjoint
    issues = validate_assignments([
        {"provider": "a", "files": [""]},
        {"provider": "b", "files": ["src/main.py"]},
    ], {"a", "b"})
    assert issues


def test_zones_overlap_unit():
    assert zones_overlap("src", "src/main.py")
    assert zones_overlap("src/", "src")
    assert zones_overlap("a/b/*", "a/b/c.py")
    assert not zones_overlap("src", "src2")
    assert not zones_overlap("app/a.py", "app/b.py")
    assert not zones_overlap("src/x", "lib/x")


def test_compare_url_helper():
    from app.orchestrator import _compare_url
    assert _compare_url("owner/repo", "dispatcher/x/integration") == \
        "https://github.com/owner/repo/compare/dispatcher/x/integration?expand=1"
    assert _compare_url("", "b") == ""
