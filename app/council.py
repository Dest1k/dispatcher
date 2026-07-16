"""AI Council: structured multi-agent reasoning over any adapter.

Modes:
  solo          one agent answers.
  pair          proposal + independent critique.
  council       every participant answers independently (in parallel),
                the lead synthesizes agreements / disagreements / risks.
  full_council  pipeline: architect proposal → red-team attack →
                implementation feasibility → lead synthesis.

Pure Python (no Qt): usable from the `dispatcher` CLI, tests, and the
orchestrator alike. Role casting comes from the explainable routing engine;
every stage is recorded in `decision_path` so the result shows *who said what
and why they were chosen*. A failed participant is reported honestly instead
of silently dropped.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .capabilities import ROLE_TITLES_RU
from .providers import make_adapter
from .providers.base import Message
from .routing import route

MODES = ("solo", "pair", "council", "full_council")

_PROMPTS = {
    "architect": (
        "Ты — архитектор совета ИИ-инженеров. Предложи решение: подходы, "
        "архитектура, компромиссы, план внедрения. Отвечай по-русски, "
        "структурированно и по делу."),
    "red_team": (
        "Ты — red team совета ИИ-инженеров. Атакуй предложение: найди "
        "слабые места, риски, скрытые допущения, сценарии отказа и что "
        "именно сломается. Никакой вежливой воды — только конкретика. "
        "Отвечай по-русски."),
    "developer": (
        "Ты — инженер-реализатор совета ИИ-инженеров. Оцени осуществимость: "
        "сложность, порядок шагов, что потребуется протестировать, узкие "
        "места, что нужно уточнить у автора. Отвечай по-русски."),
    "reviewer": (
        "Ты — независимый ревьюер совета ИИ-инженеров. Проверь предложение "
        "на ошибки, риски и упущения; дай вердикт и список замечаний. "
        "Отвечай по-русски."),
    "researcher": (
        "Ты — исследователь совета ИИ-инженеров. Дай независимый анализ "
        "вопроса: варианты, сравнение, аргументы за/против. Отвечай по-русски."),
    "synthesis": (
        "Ты — ведущий совета ИИ-инженеров. Синтезируй финальный ответ по "
        "материалам совета: рекомендация, обоснование, где участники "
        "согласны, где расходятся, какие риски остаются открытыми, "
        "следующие шаги. Честно отмечай неопределённость. Отвечай по-русски."),
}


@dataclass
class CouncilOpinion:
    provider_id: str
    label: str
    role: str
    text: str = ""
    error: str = ""
    usage_in: int = 0
    usage_out: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {"provider": self.provider_id, "label": self.label,
                "role": self.role, "text": self.text, "error": self.error,
                "usage_in": self.usage_in, "usage_out": self.usage_out,
                "seconds": round(self.seconds, 1)}


@dataclass
class CouncilResult:
    mode: str
    question: str
    opinions: list[CouncilOpinion] = field(default_factory=list)
    synthesis: str = ""
    synthesis_by: str = ""
    synth_usage_in: int = 0
    synth_usage_out: int = 0
    decision_path: list[str] = field(default_factory=list)
    routing_notes: str = ""
    cancelled: bool = False

    def usage_total(self) -> tuple[int, int]:
        return (sum(o.usage_in for o in self.opinions) + self.synth_usage_in,
                sum(o.usage_out for o in self.opinions) + self.synth_usage_out)

    def to_dict(self) -> dict:
        tin, tout = self.usage_total()
        return {"mode": self.mode, "question": self.question,
                "opinions": [o.to_dict() for o in self.opinions],
                "synthesis": self.synthesis, "synthesis_by": self.synthesis_by,
                "decision_path": self.decision_path,
                "routing_notes": self.routing_notes,
                "cancelled": self.cancelled,
                "usage": {"input_tokens": tin, "output_tokens": tout}}

    def to_markdown(self) -> str:
        lines = [f"# Совет ИИ · режим {self.mode}", "",
                 f"**Вопрос:** {self.question}", ""]
        if self.routing_notes:
            lines += [self.routing_notes, ""]
        for o in self.opinions:
            role_ru = ROLE_TITLES_RU.get(o.role, o.role)
            head = f"## {o.label} — {role_ru}"
            if o.error:
                lines += [head, "", f"⚠ не ответил: {o.error}", ""]
            else:
                lines += [head, "", o.text.strip(), ""]
        if self.synthesis:
            lines += [f"## Синтез ({self.synthesis_by})", "",
                      self.synthesis.strip(), ""]
        if self.decision_path:
            lines += ["## Ход совета", ""]
            lines += [f"{i + 1}. {step}" for i, step in enumerate(self.decision_path)]
        if self.cancelled:
            lines += ["", "> ⚠ Совет остановлен до завершения."]
        return "\n".join(lines)


def _noop_event(stage: str, provider_id: str, payload: str) -> None:
    pass


class Council:
    """Runs council modes over config provider dicts."""

    def __init__(self, providers: list[dict], lead: dict | None = None,
                 adapter_factory=make_adapter, reputation=None,
                 on_event=None, cancel: threading.Event | None = None,
                 workdir: str = "", context: str = ""):
        if not providers:
            raise ValueError("нет активных провайдеров для совета")
        self.providers = providers
        self.lead = lead or providers[0]
        self.adapter_factory = adapter_factory
        self.reputation = reputation
        self.on_event = on_event or _noop_event
        self.cancel = cancel or threading.Event()
        self.workdir = workdir
        self.context = context

    # ---- plumbing -----------------------------------------------------
    def _adapter(self, provider: dict):
        cfg = dict(provider)
        if self.workdir:
            cfg["workdir"] = self.workdir
        adapter = self.adapter_factory(cfg)
        if hasattr(adapter, "cancel"):
            adapter.cancel = self.cancel
        return adapter

    def _ask(self, provider: dict, role: str, prompt: str) -> CouncilOpinion:
        op = CouncilOpinion(provider_id=provider["id"],
                            label=provider.get("label", provider["id"]),
                            role=role)
        if self.cancel.is_set():
            op.error = "отменено"
            return op
        system = _PROMPTS.get(role, _PROMPTS["researcher"])
        body = prompt
        if self.context:
            body += f"\n\n[Контекст проекта]\n{self.context}"
        started = time.monotonic()
        self.on_event("ask", provider["id"], role)
        try:
            res = self._adapter(provider).complete(
                system, [Message("user", text=body)], [])
            op.text = res.text
            op.usage_in = res.usage.input_tokens
            op.usage_out = res.usage.output_tokens
        except Exception as exc:
            op.error = str(exc)[:500]
        op.seconds = time.monotonic() - started
        self.on_event("answer", provider["id"],
                      op.error or op.text[:400])
        return op

    def _pick(self, decision, role: str, fallback: dict) -> dict:
        a = decision.by_role(role)
        if a is None:
            return fallback
        return next((p for p in self.providers if p["id"] == a.provider_id),
                    fallback)

    # ---- modes ---------------------------------------------------------
    def run(self, question: str, mode: str = "council") -> CouncilResult:
        if mode not in MODES:
            raise ValueError(f"неизвестный режим совета: {mode}")
        result = CouncilResult(mode=mode, question=question)
        roles = {"solo": ("researcher",),
                 "pair": ("architect", "reviewer"),
                 "council": (),
                 "full_council": ("architect", "red_team", "developer")}[mode]
        decision = route(question, self.providers,
                         roles=roles or ("researcher",),
                         reputation=self.reputation)
        result.routing_notes = decision.describe()

        if mode == "solo":
            provider = self._pick(decision, "researcher", self.lead)
            result.decision_path.append(
                f"solo: отвечает {provider.get('label')}")
            result.opinions.append(self._ask(provider, "researcher", question))
        elif mode == "pair":
            proposer = self._pick(decision, "architect", self.lead)
            critic = self._pick(decision, "reviewer",
                                self._other_than(proposer))
            result.decision_path.append(
                f"предложение: {proposer.get('label')}")
            proposal = self._ask(proposer, "architect", question)
            result.opinions.append(proposal)
            if not self.cancel.is_set():
                result.decision_path.append(
                    f"критика: {critic.get('label')}")
                critique_prompt = (f"Вопрос совета:\n{question}\n\n"
                                   f"Предложение участника "
                                   f"{proposal.label}:\n{proposal.text or proposal.error}")
                result.opinions.append(self._ask(critic, "reviewer",
                                                 critique_prompt))
        elif mode == "council":
            result.decision_path.append(
                "независимые мнения: " +
                ", ".join(p.get("label", p["id"]) for p in self.providers))
            result.opinions = self._parallel_opinions(question)
            self._synthesize(result, question)
        else:  # full_council
            self._full_pipeline(result, decision, question)

        result.cancelled = self.cancel.is_set()
        return result

    def _other_than(self, provider: dict) -> dict:
        return next((p for p in self.providers if p["id"] != provider["id"]),
                    provider)

    def _parallel_opinions(self, question: str) -> list[CouncilOpinion]:
        opinions: list[CouncilOpinion | None] = [None] * len(self.providers)

        def work(i: int, p: dict) -> None:
            role = p.get("default_role", "researcher")
            if role not in _PROMPTS:
                role = "researcher"
            opinions[i] = self._ask(p, role, question)

        threads = [threading.Thread(target=work, args=(i, p), daemon=True)
                   for i, p in enumerate(self.providers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return [o for o in opinions if o is not None]

    def _synthesize(self, result: CouncilResult, question: str) -> None:
        if self.cancel.is_set():
            return
        usable = [o for o in result.opinions if o.text]
        if not usable:
            result.synthesis = "(ни один участник не ответил — синтез невозможен)"
            result.synthesis_by = "—"
            return
        if len(usable) == 1:
            result.decision_path.append(
                "синтез пропущен: содержательное мнение только одно")
            return
        material = "\n\n".join(
            f"### {o.label} ({ROLE_TITLES_RU.get(o.role, o.role)})\n{o.text}"
            for o in usable)
        failed = [o for o in result.opinions if o.error]
        if failed:
            material += "\n\n(Не ответили: " + ", ".join(
                f"{o.label}: {o.error[:120]}" for o in failed) + ")"
        result.decision_path.append(
            f"синтез: {self.lead.get('label')}")
        synth = self._ask(self.lead, "synthesis",
                          f"Вопрос совета:\n{question}\n\nМатериалы совета:\n"
                          f"{material[:24000]}")
        result.synthesis = synth.text or f"(синтез не удался: {synth.error})"
        result.synthesis_by = synth.label
        result.synth_usage_in = synth.usage_in
        result.synth_usage_out = synth.usage_out

    def _full_pipeline(self, result: CouncilResult, decision,
                       question: str) -> None:
        architect = self._pick(decision, "architect", self.lead)
        red = self._pick(decision, "red_team", self._other_than(architect))
        dev = self._pick(decision, "developer", self._other_than(red))

        result.decision_path.append(f"этап 1 · архитектор: {architect.get('label')}")
        proposal = self._ask(architect, "architect", question)
        result.opinions.append(proposal)
        if self.cancel.is_set():
            return

        result.decision_path.append(f"этап 2 · red team: {red.get('label')}")
        attack_prompt = (f"Вопрос совета:\n{question}\n\n"
                         f"Предложение архитектора ({proposal.label}):\n"
                         f"{proposal.text or proposal.error}")
        attack = self._ask(red, "red_team", attack_prompt)
        result.opinions.append(attack)
        if self.cancel.is_set():
            return

        result.decision_path.append(f"этап 3 · осуществимость: {dev.get('label')}")
        feas_prompt = (f"{attack_prompt}\n\nКритика red team ({attack.label}):\n"
                       f"{attack.text or attack.error}")
        feasibility = self._ask(dev, "developer", feas_prompt)
        result.opinions.append(feasibility)
        if self.cancel.is_set():
            return

        self._synthesize(result, question)


def run_council(question: str, providers: list[dict], mode: str = "council",
                lead: dict | None = None, **kwargs) -> CouncilResult:
    return Council(providers, lead=lead, **kwargs).run(question, mode=mode)
