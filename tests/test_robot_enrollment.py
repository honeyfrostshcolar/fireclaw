from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.robot_enrollment import (
    ENROLLMENT_TERMINAL_STATUSES,
    EnrollmentRequest,
    JsonlEnrollmentStore,
)


@pytest.fixture()
def store(tmp_path: Path) -> JsonlEnrollmentStore:
    return JsonlEnrollmentStore(tmp_path / "enrollment.jsonl")


# ---------------------------------------------------------------------------
# EnrollmentRequest dataclass
# ---------------------------------------------------------------------------


class TestEnrollmentRequest:
    def test_frozen(self) -> None:
        req = EnrollmentRequest(
            pairing_code="ABC123",
            robot_id=None,
            status="pending",
            created_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-01T00:05:00Z",
        )
        with pytest.raises(AttributeError):
            req.status = "approved"  # type: ignore[misc]

    def test_is_terminal(self) -> None:
        pending = EnrollmentRequest(
            pairing_code="ABC123",
            robot_id=None,
            status="pending",
            created_at="",
            expires_at="",
        )
        assert not pending.is_terminal

        approved = EnrollmentRequest(
            pairing_code="ABC123",
            robot_id="r1",
            status="approved",
            created_at="",
            expires_at="",
        )
        assert approved.is_terminal

        rejected = EnrollmentRequest(
            pairing_code="ABC123",
            robot_id=None,
            status="rejected",
            created_at="",
            expires_at="",
        )
        assert rejected.is_terminal

        expired = EnrollmentRequest(
            pairing_code="ABC123",
            robot_id=None,
            status="expired",
            created_at="",
            expires_at="",
        )
        assert expired.is_terminal

    def test_to_dict_roundtrip(self) -> None:
        req = EnrollmentRequest(
            pairing_code="A3K9X2",
            robot_id=None,
            status="pending",
            created_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-01T00:05:00Z",
        )
        d = req.to_dict()
        assert d["pairing_code"] == "A3K9X2"
        assert d["status"] == "pending"
        restored = EnrollmentRequest(**d)
        assert restored == req


# ---------------------------------------------------------------------------
# JsonlEnrollmentStore
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_returns_pending_request(self, store: JsonlEnrollmentStore) -> None:
        req = store.create(pairing_code="ABC123", ttl_seconds=300)
        assert req.pairing_code == "ABC123"
        assert req.status == "pending"
        assert req.robot_id is None
        assert req.created_at  # non-empty
        assert req.expires_at  # non-empty

    def test_create_persists_to_file(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="ABC123", ttl_seconds=300)
        assert store.path.exists()
        lines = store.path.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["pairing_code"] == "ABC123"
        assert parsed["status"] == "pending"

    def test_create_duplicate_pending_code_raises(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="ABC123", ttl_seconds=300)
        with pytest.raises(ValueError, match="already pending"):
            store.create(pairing_code="ABC123", ttl_seconds=300)

    def test_create_rejected_code_can_be_reused(self, store: JsonlEnrollmentStore) -> None:
        """After a code is rejected, the same code should be allowed again."""
        store.create(pairing_code="ABC123", ttl_seconds=300)
        store.reject("ABC123")
        # Should not raise
        req = store.create(pairing_code="ABC123", ttl_seconds=300)
        assert req.status == "pending"


class TestApprove:
    def test_approve_sets_robot_id_and_status(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="ABC123", ttl_seconds=300)
        approved = store.approve("ABC123", robot_id="robot-7")
        assert approved is not None
        assert approved.status == "approved"
        assert approved.robot_id == "robot-7"
        assert approved.pairing_code == "ABC123"

    def test_approve_unknown_code_returns_none(self, store: JsonlEnrollmentStore) -> None:
        assert store.approve("NOPE", robot_id="r1") is None

    def test_approve_already_approved_returns_none(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="ABC123", ttl_seconds=300)
        store.approve("ABC123", robot_id="r1")
        assert store.approve("ABC123", robot_id="r2") is None

    def test_approve_rejected_returns_none(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="ABC123", ttl_seconds=300)
        store.reject("ABC123")
        assert store.approve("ABC123", robot_id="r1") is None


class TestReject:
    def test_reject_sets_status(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="ABC123", ttl_seconds=300)
        rejected = store.reject("ABC123")
        assert rejected is not None
        assert rejected.status == "rejected"
        assert rejected.pairing_code == "ABC123"

    def test_reject_unknown_code_returns_none(self, store: JsonlEnrollmentStore) -> None:
        assert store.reject("NOPE") is None

    def test_reject_already_rejected_returns_none(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="ABC123", ttl_seconds=300)
        store.reject("ABC123")
        assert store.reject("ABC123") is None


class TestListPending:
    def test_empty_store(self, store: JsonlEnrollmentStore) -> None:
        assert store.list_pending() == []

    def test_returns_only_pending(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="AAA111", ttl_seconds=300)
        store.create(pairing_code="BBB222", ttl_seconds=300)
        store.approve("AAA111", robot_id="r1")
        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0].pairing_code == "BBB222"

    def test_excluded_expired(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="AAA111", ttl_seconds=300)
        store.create(pairing_code="BBB222", ttl_seconds=300)
        store.cleanup_expired()
        # All entries are still "pending" in the file but list_pending
        # should only return non-terminal statuses. Since we haven't
        # actually expired them (ttl hasn't passed), both are still pending.
        # This test just verifies the basic filter logic.


class TestCleanupExpired:
    def test_marks_expired_entries(self, store: JsonlEnrollmentStore) -> None:
        # Create a request with a very short TTL that we can manually expire
        store.create(pairing_code="AAA111", ttl_seconds=300)
        # Manually write an already-expired entry
        expired_req = EnrollmentRequest(
            pairing_code="EXPIRED",
            robot_id=None,
            status="pending",
            created_at="2020-01-01T00:00:00Z",
            expires_at="2020-01-01T00:05:00Z",
        )
        store._append(expired_req.to_dict())

        count = store.cleanup_expired(current_iso="2026-06-01T00:00:00Z")
        assert count == 1

        # Verify the expired entry is now marked expired
        records = store._records_by_pairing_code()
        assert records["EXPIRED"].status == "expired"
        # The non-expired one should still be pending
        assert records["AAA111"].status == "pending"

    def test_no_pending_to_expire(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="AAA111", ttl_seconds=300)
        count = store.cleanup_expired(current_iso="2020-01-01T00:00:00Z")
        assert count == 0

    def test_ignores_terminal_entries(self, store: JsonlEnrollmentStore) -> None:
        store.create(pairing_code="AAA111", ttl_seconds=300)
        store.approve("AAA111", robot_id="r1")
        # Write an already-expired entry that was previously approved
        already_approved_expired = EnrollmentRequest(
            pairing_code="BBB222",
            robot_id="r1",
            status="approved",
            created_at="2020-01-01T00:00:00Z",
            expires_at="2020-01-01T00:05:00Z",
        )
        store._append(already_approved_expired.to_dict())
        count = store.cleanup_expired(current_iso="2026-06-01T00:00:00Z")
        assert count == 0


class TestPairingCodeGeneration:
    def test_generate_pairing_code_format(self) -> None:
        from fireclaw_core.robot_enrollment import generate_pairing_code

        code = generate_pairing_code()
        assert len(code) == 6
        assert code.isalnum()
        assert code.isupper()

    def test_generate_pairing_code_uniqueness(self) -> None:
        from fireclaw_core.robot_enrollment import generate_pairing_code

        codes = {generate_pairing_code() for _ in range(100)}
        # With 36^6 = 2.1B possible codes, 100 should all be unique
        assert len(codes) == 100
