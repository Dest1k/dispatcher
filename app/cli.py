"""`dispatcher` console entry point.

Headless commands next to the GUI:

  dispatcher                  запустить GUI (как раньше)
  dispatcher doctor           обнаружение официальных CLI: установка, вход,
                              модели, готовность (--probe: живой сквозной тест)
  dispatcher council "вопрос" совет ИИ: solo | pair | council | full_council
  dispatcher route "задача"   объяснимая маршрутизация ролей
  dispatcher capabilities     реестр способностей активных моделей
  dispatcher reputation       накопленная репутация агентов
  dispatcher memory …         память проекта (граф решений/уроков)

Все пользовательские тексты — русские; вывод с --json — машиночитаемый.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _p(text: str = "") -> None:
    print(text)


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def _provider_for_flavor(cfg, flavor: str) -> dict:
    for p in cfg.ordered_providers():
        if p.get("cli_flavor") == flavor:
            return p
    return {}


def cmd_doctor(args) -> int:
    from . import cliagents
    from .config import Config
    cfg = Config.load()
    statuses = cliagents.detect_all()
    payload = {"providers": [], "ready": 0}
    for st in statuses:
        prov = _provider_for_flavor(cfg, st.flavor)
        entry = st.to_dict()
        entry["configured_model"] = prov.get("model", st.default_model)
        entry["configured_effort"] = prov.get("effort", st.default_effort)
        payload["providers"].append(entry)
        if st.ready:
            payload["ready"] += 1

    if args.probe:
        if not args.json:
            _p("Живой тест (--probe): каждый готовый CLI получает короткий "
               "запрос — это расходует квоту подписки…")
        payload["probe"] = _probe(statuses, cfg)

    if args.json:
        _p(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ready"] else 1

    _p("Диагностика официальных CLI-агентов")
    _p("=" * 46)
    for entry in payload["providers"]:
        _p(f"\n{entry['title']}")
        inst = f"да ({entry['binary']})" if entry["installed"] else "нет"
        _p(f"  установлен:       {inst}")
        if entry["version"]:
            _p(f"  версия:           {entry['version']}")
        auth = {True: f"да — {entry['auth_source']}",
                False: f"нет — {entry['auth_source']}",
                None: f"неизвестно — {entry['auth_source']}"}[entry["authenticated"]]
        _p(f"  аутентификация:   {auth}")
        _p(f"  модель:           {entry['configured_model']} "
           f"(effort: {entry['configured_effort']})")
        models = ", ".join(entry["models"][:8]) or "—"
        _p(f"  доступные модели: {models}")
        _p(f"                    источник: {entry['models_source']}")
        _p(f"  статус:           {'ГОТОВ' if entry['ready'] else 'НЕ ГОТОВ'}"
           + (f" — {entry['detail']}" if entry.get("detail") else ""))
        probe = payload.get("probe", {}).get(entry["flavor"])
        if probe:
            if probe.get("ok"):
                _p(f"  живой тест:       ок за {probe['seconds']:.1f} с — "
                   f"«{probe['reply'][:60]}»")
            else:
                _p(f"  живой тест:       ошибка — {probe['error'][:200]}")
    _p(f"\nГотово к работе: {payload['ready']} из {len(payload['providers'])}")
    if not payload["ready"]:
        _p("Ни один официальный CLI не готов: установи и выполни вход "
           "(claude / codex login / grok login), затем повтори.")
    return 0 if payload["ready"] else 1


def _probe(statuses, cfg) -> dict:
    """Real end-to-end invocation of each ready CLI (uses subscription quota)."""
    from .providers import make_adapter
    from .providers.base import Message
    out: dict[str, dict] = {}
    for st in statuses:
        if not st.ready:
            continue
        prov = dict(_provider_for_flavor(cfg, st.flavor)) or {
            "kind": "cli", "cli_flavor": st.flavor,
            "model": st.default_model, "effort": st.default_effort}
        prov["cli_timeout"] = 300
        started = time.monotonic()
        try:
            res = make_adapter(prov).complete(
                "", [Message("user", text="Ответь ровно одним словом: pong")], [])
            out[st.flavor] = {"ok": True,
                              "seconds": time.monotonic() - started,
                              "reply": res.text.strip()[:200]}
        except Exception as exc:
            out[st.flavor] = {"ok": False,
                              "seconds": time.monotonic() - started,
                              "error": str(exc)}
    return out


# --------------------------------------------------------------------------
# council / route / capabilities / reputation
# --------------------------------------------------------------------------

def _load_active_providers(cfg, only: str = "") -> list[dict]:
    providers = [dict(p) for p in cfg.active_providers()]
    if only:
        wanted = {s.strip() for s in only.split(",") if s.strip()}
        providers = [p for p in providers if p["id"] in wanted]
    return providers


def _project_key(cfg, project: str) -> tuple[str, str]:
    """(project_id, workdir) from --project (config id / name / path / cwd)."""
    if project:
        for p in cfg.projects:
            if project in (p.get("id"), p.get("name")):
                return p["id"], p.get("local_path", "") or str(Path.cwd())
        path = Path(project).resolve()
        for p in cfg.projects:
            if p.get("local_path") and Path(p["local_path"]).resolve() == path:
                return p["id"], str(path)
        return f"external:{path}", str(path)
    cwd = Path.cwd().resolve()
    for p in cfg.projects:
        if p.get("local_path") and Path(p["local_path"]).resolve() == cwd:
            return p["id"], str(cwd)
    return f"external:{cwd}", str(cwd)


def cmd_council(args) -> int:
    from .config import Config
    from .council import Council
    from .ledger import ContextLedger
    from .memory_graph import MemoryGraph
    from .reputation import ReputationStore
    cfg = Config.load()
    providers = _load_active_providers(cfg, args.providers)
    if not providers:
        _p("Нет активных провайдеров. Проверь `dispatcher doctor` или добавь "
           "API-ключи в настройках GUI.")
        return 1
    project_id, workdir = _project_key(cfg, args.project)
    lead_id = cfg.orchestration.get("lead_provider", "")
    lead = next((p for p in providers if p["id"] == lead_id), providers[0])
    if args.timeout:
        for p in providers:
            p["cli_timeout"] = args.timeout

    memory = MemoryGraph()
    ledger = ContextLedger()
    ledger.record("repo", workdir)
    ledger.record("permission", "council: только чтение, инструменты записи отключены")
    memory_ctx = memory.context_pack(project_id, args.question)

    cancel = threading.Event()
    labels = {p["id"]: p.get("short", p["id"]) for p in providers}

    def on_event(stage: str, pid: str, payload: str) -> None:
        name = labels.get(pid, pid)
        if stage == "ask":
            _p(f"… {name} думает (роль: {payload})")
        elif stage == "answer":
            ledger.record("provider_call", f"{name}: ответ получен", agent=pid)
            _p(f"✓ {name} ответил")

    council = Council(providers, lead=lead, reputation=ReputationStore(),
                      on_event=on_event, cancel=cancel, workdir=workdir,
                      context=memory_ctx)
    _p(f"Совет ИИ · режим {args.mode} · участники: "
       + ", ".join(labels.values()))
    try:
        result = council.run(args.question, mode=args.mode)
    except KeyboardInterrupt:
        cancel.set()
        _p("Остановлено пользователем.")
        return 130

    md = result.to_markdown() + "\n\n" + ledger.summary_md()
    if args.json:
        _p(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        _p("\n" + md)

    # Память: фиксируем совет как «подход» с итогом.
    try:
        node = memory.add_node(project_id, "approach",
                               f"Совет ({args.mode}): {args.question[:120]}",
                               body=(result.synthesis or
                                     (result.opinions[0].text if result.opinions
                                      else ""))[:4000])
        _p(f"\nСохранено в память проекта: узел {node}")
    except Exception:
        pass
    out_path = _save_report(md, "council")
    if out_path:
        _p(f"Отчёт: {out_path}")
    return 0


def _save_report(markdown: str, prefix: str) -> str:
    try:
        from .config import CONFIG_DIR
        reports = CONFIG_DIR / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = reports / f"{prefix}-{stamp}.md"
        path.write_text(markdown, encoding="utf-8")
        return str(path)
    except OSError:
        return ""


def cmd_route(args) -> int:
    from .config import Config
    from .reputation import ReputationStore
    from .routing import route
    cfg = Config.load()
    providers = _load_active_providers(cfg, args.providers)
    if not providers:
        _p("Нет активных провайдеров.")
        return 1
    decision = route(args.task, providers, reputation=ReputationStore())
    if args.json:
        _p(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
    else:
        _p(decision.describe())
    return 0


def cmd_capabilities(args) -> int:
    from . import capabilities
    from .config import Config
    cfg = Config.load()
    providers = cfg.active_providers() or cfg.ordered_providers()
    if args.json:
        payload = []
        for p in providers:
            prof = capabilities.profile_for(p.get("model", ""))
            payload.append({
                "id": p["id"], "label": p.get("label"), "model": p.get("model"),
                "scores": (prof.scores if prof else {}),
                "source": (prof.source if prof else "нет данных"),
                "verified_at": (prof.verified_at if prof else "")})
        _p(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    _p("Реестр способностей моделей")
    _p("=" * 46)
    for p in providers:
        _p(f"\n{p.get('label', p['id'])} · модель {p.get('model', '—')}")
        _p(f"  {capabilities.describe(p.get('model', ''))}")
    return 0


def cmd_reputation(args) -> int:
    from .config import Config
    from .reputation import ReputationStore
    cfg = Config.load()
    store = ReputationStore()
    snap = store.snapshot()
    if args.json:
        _p(json.dumps(snap, ensure_ascii=False, indent=2))
        return 0
    _p("Репутация агентов (по наблюдаемым результатам)")
    _p("=" * 46)
    if not snap:
        _p("Истории пока нет — репутация появится после первых прогонов.")
        return 0
    labels = {p["id"]: p.get("label", p["id"]) for p in cfg.ordered_providers()}
    for pid in snap:
        _p(f"\n{labels.get(pid, pid)}")
        _p(f"  {store.explain(pid)}")
    return 0


# --------------------------------------------------------------------------
# memory
# --------------------------------------------------------------------------

def cmd_memory(args) -> int:
    from .config import Config
    from .memory_graph import NODE_KINDS, MemoryGraph
    cfg = Config.load()
    graph = MemoryGraph()
    project_id, _ = _project_key(cfg, args.project)

    if args.memory_cmd == "list":
        nodes = graph.recent(project_id, limit=args.limit)
        if not nodes:
            _p(f"Память проекта «{project_id}» пуста.")
            return 0
        for n in nodes:
            stamp = datetime.fromtimestamp(n.created_at).strftime("%Y-%m-%d %H:%M")
            _p(f"{n.id}  [{n.kind}/{n.status}] {stamp}  {n.title}")
        return 0
    if args.memory_cmd == "show":
        node = graph.get_node(args.node_id)
        if node is None:
            _p(f"Узел {args.node_id} не найден.")
            return 1
        _p(f"[{node.kind}/{node.status}] {node.title}\n")
        if node.body:
            _p(node.body)
        rels = graph.neighbors(node.id)
        if rels:
            _p("\nСвязи:")
            for edge, other in rels:
                _p(f"  {edge.rel} → {other.id} «{other.title[:60]}»"
                   + (f" (причина: {edge.reason})" if edge.reason else ""))
        return 0
    if args.memory_cmd == "add":
        if args.kind not in NODE_KINDS:
            _p(f"Тип должен быть одним из: {', '.join(NODE_KINDS)}")
            return 2
        node_id = graph.add_node(project_id, args.kind, args.title,
                                 body=args.body or "")
        _p(f"Добавлен узел {node_id} в память проекта «{project_id}».")
        return 0
    if args.memory_cmd == "pack":
        pack = graph.context_pack(project_id, query=args.query or "")
        _p(pack or "Память проекта пуста — дайджест не сформирован.")
        return 0
    _p("Неизвестная команда памяти.")
    return 2


# --------------------------------------------------------------------------
# gui + parser
# --------------------------------------------------------------------------

def cmd_gui(_args) -> int:
    try:
        from run import main as gui_main
    except ImportError:
        # installed package: run.py may not be importable; use the app path
        try:
            from PySide6.QtWidgets import QApplication
            from .ui.main_window import MainWindow
            from .ui.style import APP_QSS
        except ImportError as exc:
            _p(f"GUI недоступен (PySide6 не установлен?): {exc}")
            return 1
        qt_app = QApplication(sys.argv[:1])
        qt_app.setApplicationName("Multi-AI Control Center")
        qt_app.setStyleSheet(APP_QSS)
        window = MainWindow()
        window.show()
        return qt_app.exec()
    return gui_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dispatcher",
        description="Dispatcher — центр управления ИИ-агентами "
                    "(GUI по умолчанию, headless-команды ниже)")
    sub = parser.add_subparsers(dest="command")

    d = sub.add_parser("doctor", help="диагностика официальных CLI-агентов")
    d.add_argument("--json", action="store_true")
    d.add_argument("--probe", action="store_true",
                   help="живой сквозной тест каждого готового CLI "
                        "(расходует квоту подписки)")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("council", help="совет ИИ по вопросу")
    c.add_argument("question")
    c.add_argument("--mode", default="council",
                   choices=["solo", "pair", "council", "full_council"])
    c.add_argument("--providers", default="",
                   help="ограничить состав: id через запятую")
    c.add_argument("--project", default="",
                   help="проект (id/имя из конфига или путь; по умолчанию cwd)")
    c.add_argument("--timeout", type=int, default=0,
                   help="таймаут одного вызова CLI, секунд")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_council)

    r = sub.add_parser("route", help="объяснимая маршрутизация задачи по ролям")
    r.add_argument("task")
    r.add_argument("--providers", default="")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_route)

    cap = sub.add_parser("capabilities", help="реестр способностей моделей")
    cap.add_argument("--json", action="store_true")
    cap.set_defaults(func=cmd_capabilities)

    rep = sub.add_parser("reputation", help="репутация агентов")
    rep.add_argument("--json", action="store_true")
    rep.set_defaults(func=cmd_reputation)

    m = sub.add_parser("memory", help="память проекта (граф)")
    msub = m.add_subparsers(dest="memory_cmd", required=True)
    ml = msub.add_parser("list")
    ml.add_argument("--project", default="")
    ml.add_argument("--limit", type=int, default=20)
    ms = msub.add_parser("show")
    ms.add_argument("node_id")
    ms.add_argument("--project", default="")
    ma = msub.add_parser("add")
    ma.add_argument("kind")
    ma.add_argument("title")
    ma.add_argument("--body", default="")
    ma.add_argument("--project", default="")
    mp = msub.add_parser("pack")
    mp.add_argument("--query", default="")
    mp.add_argument("--project", default="")
    m.set_defaults(func=cmd_memory)

    g = sub.add_parser("gui", help="запустить графический интерфейс")
    g.set_defaults(func=cmd_gui)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        return cmd_gui(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
