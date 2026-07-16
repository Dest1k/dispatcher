"""Offscreen construction + behavior of the project-memory browser dialog."""


def _graph(tmp_path):
    from app.memory_graph import MemoryGraph
    g = MemoryGraph(tmp_path / "mem.db")
    bug = g.add_node("p1", "bug", "Гонка при записи конфига",
                     body="Два потока пишут config.json")
    fix = g.add_node("p1", "solution", "Атомарная запись tmp+replace")
    g.add_edge(bug, fix, "solved_by", reason="replace атомарен на одном томе")
    g.add_node("p1", "lesson", "Писать патчи с newline='\\n'")
    g.add_node("p2", "decision", "Только для другого проекта")
    return g, bug


def test_memory_dialog_lists_and_filters(qapp, tmp_path):
    from app.ui.memory_dialog import MemoryDialog
    graph, _ = _graph(tmp_path)
    dlg = MemoryDialog({"id": "p1", "name": "Demo"}, graph=graph)
    # p1 has 3 nodes; p2's node must not leak in
    assert dlg.table.rowCount() == 3
    assert "Всего узлов: 3" in dlg.summary.text()

    # filter by kind
    idx = dlg.kind.findData("bug")
    dlg.kind.setCurrentIndex(idx)
    assert dlg.table.rowCount() == 1

    # search
    dlg.kind.setCurrentIndex(0)
    dlg.search.setText("патчи")
    assert dlg.table.rowCount() == 1
    dlg.close()


def test_memory_dialog_shows_node_and_links(qapp, tmp_path):
    from app.ui.memory_dialog import MemoryDialog
    graph, bug = _graph(tmp_path)
    dlg = MemoryDialog({"id": "p1", "name": "Demo"}, graph=graph)
    # select the bug row (order is recent-first; find it)
    target = next(i for i, n in enumerate(dlg._nodes) if n.id == bug)
    dlg.table.selectRow(target)
    html = dlg.detail.toHtml()
    assert "Гонка при записи" in html
    assert "решено через" in html            # reasoned link label
    assert "replace атомарен" in html        # the edge reason
    dlg.close()


def test_memory_dialog_empty_project(qapp, tmp_path):
    from app.memory_graph import MemoryGraph
    from app.ui.memory_dialog import MemoryDialog
    g = MemoryGraph(tmp_path / "mem.db")
    dlg = MemoryDialog({"id": "empty", "name": "Empty"}, graph=g)
    assert dlg.table.rowCount() == 0
    assert "пуста" in dlg.detail.toPlainText()
    dlg.close()
    g.close()
