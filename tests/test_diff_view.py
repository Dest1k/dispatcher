"""Colored per-file diff viewer: parsing + rendering + selector navigation."""
from app.ui.diff_view import DiffView, _render_html, split_diff

_DIFF = """diff --git a/app/main.py b/app/main.py
index 111..222 100644
--- a/app/main.py
+++ b/app/main.py
@@ -1,3 +1,3 @@
 import os
-print("old")
+print("new")
 done
diff --git a/README.md b/README.md
new file mode 100644
index 000..333
--- /dev/null
+++ b/README.md
@@ -0,0 +1 @@
+# Title
"""


def test_split_diff_by_file():
    sections = split_diff(_DIFF)
    paths = [p for p, _ in sections]
    assert paths == ["app/main.py", "README.md"]
    assert 'print("new")' in dict(sections)["app/main.py"]
    assert "# Title" in dict(sections)["README.md"]


def test_split_diff_new_file_path_from_plus_header():
    # README's `diff --git` line is present, path taken from a/..b/ form
    sections = split_diff(_DIFF)
    assert dict(sections).get("README.md")


def test_split_empty():
    assert split_diff("") == []
    assert split_diff("   \n  ") == []


def test_render_html_colors_lines():
    html = _render_html(_DIFF)
    # added line green, removed line red, hunk cyan, file header muted
    assert "#56d364" in html                # added
    assert "#f85149" in html                # removed
    assert "#79c0ff" in html                # hunk header
    assert "print(&quot;new&quot;)" in html  # content HTML-escaped
    assert "<script" not in html


def test_render_html_empty():
    assert "изменений нет" in _render_html("")


def test_diff_view_selector_and_stats(qapp):
    dv = DiffView()
    dv.set_diff(_DIFF)
    # "Все файлы (2)" + 2 files
    assert dv.selector.count() == 3
    assert dv.changed_files() == ["app/main.py", "README.md"]
    # all-files view: 2 additions (new print + title), 1 removal
    assert "+2" in dv.stat.text() and "−1" in dv.stat.text()
    # select just main.py → its own +1/−1
    dv.selector.setCurrentIndex(1)
    assert "+1" in dv.stat.text() and "−1" in dv.stat.text()
    assert 'print(&quot;new&quot;)' in dv.browser.toHtml()


def test_diff_view_empty(qapp):
    dv = DiffView()
    dv.set_diff("")
    assert dv.selector.count() == 1          # just "Все файлы (0)"
    assert dv.changed_files() == []
    assert dv.stat.text() == ""


def test_approval_dialog_uses_diff_view(qapp):
    from app.ui.approval_dialog import ApprovalDialog
    payload = {"status": "pass", "changed_files": ["app/main.py", "README.md"],
               "can_autopublish": True, "verification": {"checks": []}}
    dlg = ApprovalDialog(payload, _DIFF)
    assert dlg.diff_view.changed_files() == ["app/main.py", "README.md"]
    dlg.close()
