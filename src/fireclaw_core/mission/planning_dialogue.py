"""Gateway-owned state for bounded Mission Agent clarification dialogues."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import threading
from typing import Any, Callable
from uuid import uuid4


DEFAULT_PLANNING_DIALOGUE_TTL_SECONDS = 900.0
DEFAULT_MAX_CLARIFICATION_ROUNDS = 3
MAX_PLANNING_DIALOGUE_RECORDS = 1024
MAX_OPERATOR_COMMAND_CHARS = 8_000
MAX_CLARIFICATION_MESSAGE_CHARS = 2_000
MAX_CLARIFICATION_ANSWER_CHARS = 4_000


class PlanningDialogueError(RuntimeError):
    """A fail-closed planning-dialogue error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PlanningClarificationTurn:
    round_index: int
    question: str
    answer: str
    reason_code: str
    answered_at: str

    def to_model_dict(self) -> dict[str, Any]:
        return {
            "round": self.round_index,
            "question": self.question,
            "answer": self.answer,
            "reason_code": self.reason_code,
            "answer_source": "authenticated_operator",
            "answered_at": self.answered_at,
        }


@dataclass(frozen=True)
class PlanningDialogue:
    session_id: str
    mission_id: str
    operator_id: str
    original_command: str
    target_robot: str | None
    created_at: str
    expires_at: str
    expires_at_epoch: float
    max_rounds: int
    turns: tuple[PlanningClarificationTurn, ...] = ()
    pending_question: str | None = None
    pending_reason_code: str | None = None

    @property
    def next_round(self) -> int:
        return len(self.turns) + 1

    def canonical_operator_command(self) -> str:
        """Return only operator-authored text for the plan's sealed command.

        Mission Agent questions are deliberately excluded. Otherwise a sample
        coordinate contained in a question could be mistaken for an
        operator-authorized physical target.
        """

        if not self.turns:
            return self.original_command
        lines = [
            self.original_command,
            "",
            "操作员经 Mission Gateway 身份绑定后的补充：",
        ]
        lines.extend(
            f"{turn.round_index}. {turn.answer}"
            for turn in self.turns
        )
        return "\n".join(lines)

    def clarification_context(self) -> list[dict[str, Any]]:
        return [turn.to_model_dict() for turn in self.turns]

    def clarification_response(self) -> dict[str, Any]:
        if self.pending_question is None:
            raise PlanningDialogueError(
                "planning_question_missing",
                "The planning session has no pending clarification question.",
            )
        return {
            "schema_version": 1,
            "status": "clarification_required",
            "message": self.pending_question,
            "question": self.pending_question,
            "reason_code": (
                self.pending_reason_code or "operator_input_required"
            ),
            "preview_created": False,
            "planning_session_id": self.session_id,
            "clarification_round": self.next_round,
            "max_clarification_rounds": self.max_rounds,
            "expires_at": self.expires_at,
            "target_robot": self.target_robot,
        }


class PlanningDialogueStore:
    """Thread-safe, process-local owner of pending clarification state.

    A restart intentionally drops pending questions instead of attempting to
    reconstruct operator intent. Starting a new command supersedes any older
    pending dialogue owned by the same authenticated operator.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_PLANNING_DIALOGUE_TTL_SECONDS,
        max_rounds: int = DEFAULT_MAX_CLARIFICATION_ROUNDS,
        max_records: int = MAX_PLANNING_DIALOGUE_RECORDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 30.0 <= float(ttl_seconds) <= 3600.0:
            raise ValueError(
                "Planning dialogue TTL must be between 30 and 3600 seconds."
            )
        if not 1 <= int(max_rounds) <= 10:
            raise ValueError(
                "Planning clarification rounds must be between 1 and 10."
            )
        if max_records < 1:
            raise ValueError("Planning dialogue max_records must be positive.")
        self.ttl_seconds = float(ttl_seconds)
        self.max_rounds = int(max_rounds)
        self.max_records = int(max_records)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._records: dict[str, PlanningDialogue] = {}
        self._active_by_operator: dict[str, str] = {}

    def begin(
        self,
        *,
        operator_id: str,
        command: str,
        target_robot: str | None,
    ) -> PlanningDialogue:
        normalized_operator = _bounded_nonempty(
            operator_id,
            field="operator_id",
            limit=256,
        )
        normalized_command = _bounded_nonempty(
            command,
            field="command",
            limit=MAX_OPERATOR_COMMAND_CHARS,
        )
        normalized_robot = (
            _bounded_nonempty(target_robot, field="target_robot", limit=256)
            if target_robot is not None
            else None
        )
        with self._lock:
            now = _aware_utc(self._clock())
            self._prune_expired_locked(now.timestamp())
            prior_session = self._active_by_operator.get(normalized_operator)
            if prior_session is not None:
                self._remove_locked(prior_session)
            if len(self._records) >= self.max_records:
                raise PlanningDialogueError(
                    "planning_session_capacity_exhausted",
                    "Mission planning dialogue capacity is exhausted.",
                )
            session_id = f"planning-{uuid4().hex}"
            expires_at = now + timedelta(seconds=self.ttl_seconds)
            record = PlanningDialogue(
                session_id=session_id,
                mission_id=f"preview-{uuid4().hex}",
                operator_id=normalized_operator,
                original_command=normalized_command,
                target_robot=normalized_robot,
                created_at=now.isoformat(),
                expires_at=expires_at.isoformat(),
                expires_at_epoch=expires_at.timestamp(),
                max_rounds=self.max_rounds,
            )
            self._records[session_id] = record
            self._active_by_operator[normalized_operator] = session_id
            return record

    def get(
        self,
        session_id: str,
        *,
        operator_id: str,
    ) -> PlanningDialogue:
        with self._lock:
            return self._require_locked(session_id, operator_id=operator_id)

    def record_question(
        self,
        session_id: str,
        *,
        operator_id: str,
        question: str,
        reason_code: str | None,
    ) -> PlanningDialogue:
        normalized_question = _bounded_nonempty(
            question,
            field="question",
            limit=MAX_CLARIFICATION_MESSAGE_CHARS,
        )
        normalized_reason = (
            _bounded_nonempty(reason_code, field="reason_code", limit=256)
            if reason_code is not None
            else "operator_input_required"
        )
        with self._lock:
            record = self._require_locked(
                session_id,
                operator_id=operator_id,
            )
            if record.pending_question is not None:
                raise PlanningDialogueError(
                    "planning_question_already_pending",
                    "The planning session already has a pending question.",
                )
            if len(record.turns) >= record.max_rounds:
                raise PlanningDialogueError(
                    "clarification_round_limit",
                    "Mission Agent still lacks safe planning information after "
                    f"{record.max_rounds} clarification rounds.",
                )
            updated = replace(
                record,
                pending_question=normalized_question,
                pending_reason_code=normalized_reason,
            )
            self._records[session_id] = updated
            return updated

    def answer(
        self,
        session_id: str,
        *,
        operator_id: str,
        answer: str,
    ) -> PlanningDialogue:
        normalized_answer = _bounded_nonempty(
            answer,
            field="answer",
            limit=MAX_CLARIFICATION_ANSWER_CHARS,
        )
        with self._lock:
            record = self._require_locked(
                session_id,
                operator_id=operator_id,
            )
            if record.pending_question is None:
                raise PlanningDialogueError(
                    "planning_question_missing",
                    "The planning session has no pending question to answer.",
                )
            turn = PlanningClarificationTurn(
                round_index=record.next_round,
                question=record.pending_question,
                answer=normalized_answer,
                reason_code=(
                    record.pending_reason_code or "operator_input_required"
                ),
                answered_at=_aware_utc(self._clock()).isoformat(),
            )
            updated = replace(
                record,
                turns=(*record.turns, turn),
                pending_question=None,
                pending_reason_code=None,
            )
            self._records[session_id] = updated
            return updated

    def finish(self, session_id: str, *, operator_id: str) -> None:
        with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return
            if record.operator_id != operator_id:
                raise PlanningDialogueError(
                    "planning_session_operator_mismatch",
                    "Planning session belongs to a different operator.",
                )
            self._remove_locked(session_id)

    def _require_locked(
        self,
        session_id: str,
        *,
        operator_id: str,
    ) -> PlanningDialogue:
        normalized_session = _bounded_nonempty(
            session_id,
            field="planning_session_id",
            limit=128,
        )
        normalized_operator = _bounded_nonempty(
            operator_id,
            field="operator_id",
            limit=256,
        )
        record = self._records.get(normalized_session)
        if record is None:
            raise PlanningDialogueError(
                "planning_session_not_found",
                "Planning session was not found or was superseded.",
            )
        if record.operator_id != normalized_operator:
            raise PlanningDialogueError(
                "planning_session_operator_mismatch",
                "Planning session belongs to a different operator.",
            )
        if _aware_utc(self._clock()).timestamp() >= record.expires_at_epoch:
            self._remove_locked(normalized_session)
            raise PlanningDialogueError(
                "planning_session_expired",
                "Planning clarification expired; start a new command.",
            )
        return record

    def _prune_expired_locked(self, now_epoch: float) -> None:
        expired = [
            session_id
            for session_id, record in self._records.items()
            if now_epoch >= record.expires_at_epoch
        ]
        for session_id in expired:
            self._remove_locked(session_id)

    def _remove_locked(self, session_id: str) -> None:
        record = self._records.pop(session_id, None)
        if (
            record is not None
            and self._active_by_operator.get(record.operator_id) == session_id
        ):
            self._active_by_operator.pop(record.operator_id, None)


def _bounded_nonempty(value: str, *, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningDialogueError(
            "planning_dialogue_payload_invalid",
            f"Planning dialogue field '{field}' must be a non-empty string.",
        )
    normalized = value.strip()
    if len(normalized) > limit:
        raise PlanningDialogueError(
            "planning_dialogue_payload_invalid",
            f"Planning dialogue field '{field}' exceeds {limit} characters.",
        )
    return normalized


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
