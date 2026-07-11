"""Publish or discard a run that was parked at the approval gate when the app
was last closed. The integration worktree/branch still exist on disk, so we can
finish safely without re-running any agents."""
from __future__ import annotations

from .domain import RunState
from .persistence import RunRecord, RunStore
from .workspace import RunWorkspaces


def _title(record: RunRecord) -> str:
    first = (record.report or "").strip().splitlines()
    return (first[0].lstrip("# ").strip() if first else record.task)[:100] or "Работа консилиума"


def publish_resumed(store: RunStore, record: RunRecord, project: dict,
                    push: bool) -> dict:
    """Commit (and optionally push) the persisted integration branch."""
    result = {"status": "done", "branch": record.integration_branch,
              "commit": None, "pushed": False, "message": ""}
    verification = record.verification or {}
    blocked = verification.get("status") in ("fail", "unknown", "cancelled")
    try:
        rw = RunWorkspaces.reopen(project["local_path"], record.id, record.run_dir,
                                  record.base_commit, record.integration_branch)
        store.set_state(record.id, RunState.PUBLISHING)
        commit = rw.commit_integration(_title(record))
        result["commit"] = commit
        if commit is None:
            result["message"] = "Изменений для коммита нет."
            rw.cleanup()
            store.finish(record.id, RunState.COMPLETED, result["message"])
            return result
        if push and blocked:
            result["message"] = (f"Коммит {commit} создан в ветке "
                                 f"{record.integration_branch}. Пуш отменён: "
                                 "верификация не пройдена.")
        elif push:
            rw.push_integration(project.get("github_url", ""),
                                project.get("github_token", ""))
            result["pushed"] = True
            result["message"] = (f"Коммит {commit} запушен в ветку "
                                 f"{record.integration_branch}.")
        else:
            result["message"] = f"Коммит {commit} в локальной ветке {record.integration_branch}."
        rw.cleanup(keep=[record.integration_branch])
        store.finish(record.id, RunState.COMPLETED, result["message"])
    except Exception as exc:
        result["status"] = "partial"
        result["message"] = f"git-операция не удалась: {exc}"
        store.finish(record.id, RunState.PARTIAL, result["message"])
    return result


def discard_resumed(store: RunStore, record: RunRecord, project: dict) -> dict:
    try:
        rw = RunWorkspaces.reopen(project["local_path"], record.id, record.run_dir,
                                  record.base_commit, record.integration_branch)
        rw.cleanup()
    except Exception:
        pass
    store.finish(record.id, RunState.CANCELLED, "Отклонено при восстановлении")
    return {"status": "cancelled", "message": "Восстановленный прогон отклонён."}
