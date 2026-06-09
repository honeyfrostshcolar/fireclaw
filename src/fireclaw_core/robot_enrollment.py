from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import string
from typing import Any


ENROLLMENT_TERMINAL_STATUSES = {"approved", "rejected", "expired"}

_PAIRING_CODE_CHARS = string.ascii_uppercase + string.digits
_PAIRING_CODE_LENGTH = 6


def generate_pairing_code() -> str:
    """Return a random 6-char uppercase alphanumeric pairing code."""
    return "".join(random.choices(_PAIRING_CODE_CHARS, k=_PAIRING_CODE_LENGTH))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_at_iso(ttl_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


@dataclass(frozen=True)
class EnrollmentRequest:
    pairing_code: str
    robot_id: str | None
    status: str
    created_at: str
    expires_at: str

    @property
    def is_terminal(self) -> bool:
        return self.status in ENROLLMENT_TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JsonlEnrollmentStore:
    """Append-only JSONL store for robot enrollment / pairing requests."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- public API -----------------------------------------------------------

    def create(
        self,
        *,
        pairing_code: str | None = None,
        ttl_seconds: int = 300,
    ) -> EnrollmentRequest:
        """Create a new pending enrollment request.

        If *pairing_code* is ``None`` a random code is generated.
        Raises ``ValueError`` if the supplied code is already pending.
        """
        code = pairing_code or generate_pairing_code()
        existing = self._records_by_pairing_code().get(code)
        if existing is not None and existing.status == "pending":
            raise ValueError(f"Pairing code {code!r} is already pending.")

        req = EnrollmentRequest(
            pairing_code=code,
            robot_id=None,
            status="pending",
            created_at=_utcnow_iso(),
            expires_at=_expires_at_iso(ttl_seconds),
        )
        self._append(req.to_dict())
        return req

    def approve(self, pairing_code: str, *, robot_id: str) -> EnrollmentRequest | None:
        """Mark a pending request as approved and bind it to *robot_id*."""
        current = self._records_by_pairing_code().get(pairing_code)
        if current is None or current.is_terminal:
            return None
        updated = EnrollmentRequest(
            pairing_code=current.pairing_code,
            robot_id=robot_id,
            status="approved",
            created_at=current.created_at,
            expires_at=current.expires_at,
        )
        self._append(updated.to_dict())
        return updated

    def reject(self, pairing_code: str) -> EnrollmentRequest | None:
        """Mark a pending request as rejected."""
        current = self._records_by_pairing_code().get(pairing_code)
        if current is None or current.is_terminal:
            return None
        updated = EnrollmentRequest(
            pairing_code=current.pairing_code,
            robot_id=current.robot_id,
            status="rejected",
            created_at=current.created_at,
            expires_at=current.expires_at,
        )
        self._append(updated.to_dict())
        return updated

    def list_pending(self) -> list[EnrollmentRequest]:
        """Return all requests whose latest status is ``pending``."""
        return [
            req
            for req in self._records_by_pairing_code().values()
            if req.status == "pending"
        ]

    def cleanup_expired(self, *, current_iso: str | None = None) -> int:
        """Mark pending requests whose *expires_at* is in the past.

        Returns the number of requests that were expired.
        """
        now = current_iso or _utcnow_iso()
        records = self._records_by_pairing_code()
        count = 0
        for req in records.values():
            if req.status != "pending":
                continue
            if req.expires_at <= now:
                expired = EnrollmentRequest(
                    pairing_code=req.pairing_code,
                    robot_id=req.robot_id,
                    status="expired",
                    created_at=req.created_at,
                    expires_at=req.expires_at,
                )
                self._append(expired.to_dict())
                count += 1
        return count

    # -- internal helpers -----------------------------------------------------

    def _records_by_pairing_code(self) -> dict[str, EnrollmentRequest]:
        """Return the latest record for each pairing code."""
        records: dict[str, EnrollmentRequest] = {}
        for entry in self._read_entries():
            code = entry.get("pairing_code")
            if not isinstance(code, str):
                continue
            records[code] = EnrollmentRequest(
                pairing_code=code,
                robot_id=_string_or_none(entry.get("robot_id")),
                status=str(entry.get("status") or "unknown"),
                created_at=str(entry.get("created_at") or ""),
                expires_at=str(entry.get("expires_at") or ""),
            )
        return records

    def _read_entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    entries.append(value)
        return entries

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
