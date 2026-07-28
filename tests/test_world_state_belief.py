from fireclaw_core.mission.world_state_belief import (
    WorldStateBeliefBuilder,
    WorldStateObservation,
)


CAPTURED_AT = "2026-07-28T01:00:10+00:00"


def _observation(
    observation_id: str,
    *,
    value: bool,
    source: str,
    confidence: float,
    observed_at: str = "2026-07-28T01:00:09+00:00",
    expires_at: str | None = None,
) -> WorldStateObservation:
    return WorldStateObservation(
        observation_id=observation_id,
        subject_id="west-stair",
        kind="passage_open",
        value=value,
        source=source,
        observed_at=observed_at,
        evidence_ids=(f"evidence:{observation_id}",),
        confidence=confidence,
        expires_at=expires_at,
    )


def test_credible_cross_source_disagreement_is_conflicted() -> None:
    belief = WorldStateBeliefBuilder().build(
        (
            _observation(
                "robot-a-blocked",
                value=False,
                source="robot-a:lidar",
                confidence=0.9,
            ),
            _observation(
                "robot-b-open",
                value=True,
                source="robot-b:map",
                confidence=0.85,
            ),
        ),
        captured_at=CAPTURED_AT,
    )[0]

    assert belief.status == "conflicted"
    assert belief.value is None
    assert belief.reason_code == "credible_sources_disagree"
    assert len(belief.candidates) == 2
    assert set(belief.supporting_observation_ids) | set(
        belief.conflicting_observation_ids
    ) == {"robot-a-blocked", "robot-b-open"}


def test_independent_agreement_can_cross_confirmation_threshold() -> None:
    belief = WorldStateBeliefBuilder().build(
        (
            _observation(
                "robot-a-open",
                value=True,
                source="robot-a:lidar",
                confidence=0.6,
            ),
            _observation(
                "camera-open",
                value=True,
                source="camera-west",
                confidence=0.6,
            ),
        ),
        captured_at=CAPTURED_AT,
    )[0]

    assert belief.status == "confirmed"
    assert belief.value is True
    assert belief.confidence == 0.84
    assert belief.candidates[0].sources == (
        "camera-west",
        "robot-a:lidar",
    )


def test_low_confidence_observation_remains_uncertain() -> None:
    belief = WorldStateBeliefBuilder().build(
        (
            _observation(
                "thermal-victim",
                value=True,
                source="robot-a:thermal",
                confidence=0.6,
            ),
        ),
        captured_at=CAPTURED_AT,
    )[0]

    assert belief.status == "uncertain"
    assert belief.value is True
    assert belief.reason_code == "confirmation_threshold_not_met"


def test_expired_or_old_observations_do_not_expose_resolved_value() -> None:
    belief = WorldStateBeliefBuilder(default_max_age_seconds=5).build(
        (
            _observation(
                "old-map",
                value=True,
                source="robot-b:map",
                confidence=0.95,
                observed_at="2026-07-28T01:00:00+00:00",
                expires_at="2026-07-28T01:00:05+00:00",
            ),
        ),
        captured_at=CAPTURED_AT,
    )[0]

    assert belief.status == "stale"
    assert belief.value is None
    assert belief.confidence == 0.0
    assert belief.stale_observation_ids == ("old-map",)
    assert belief.candidates == ()


def test_latest_observation_per_source_supersedes_older_report() -> None:
    belief = WorldStateBeliefBuilder().build(
        (
            _observation(
                "robot-a-old-blocked",
                value=False,
                source="robot-a:lidar",
                confidence=0.9,
                observed_at="2026-07-28T01:00:05+00:00",
            ),
            _observation(
                "robot-a-new-open",
                value=True,
                source="robot-a:lidar",
                confidence=0.9,
                observed_at="2026-07-28T01:00:08+00:00",
            ),
            _observation(
                "camera-open",
                value=True,
                source="camera-west",
                confidence=0.8,
            ),
        ),
        captured_at=CAPTURED_AT,
    )[0]

    assert belief.status == "confirmed"
    assert belief.value is True
    assert belief.conflicting_observation_ids == ()
    assert belief.superseded_observation_ids == ("robot-a-old-blocked",)
    assert set(belief.supporting_observation_ids) == {
        "camera-open",
        "robot-a-new-open",
    }


def test_source_reliability_reduces_effective_confidence() -> None:
    belief = WorldStateBeliefBuilder(
        source_reliability={"unverified-camera": 0.5},
    ).build(
        (
            _observation(
                "camera-open",
                value=True,
                source="unverified-camera",
                confidence=0.9,
            ),
        ),
        captured_at=CAPTURED_AT,
    )[0]

    assert belief.status == "uncertain"
    assert belief.confidence == 0.45


def test_stale_latest_report_prevents_reuse_of_older_source_value() -> None:
    belief = WorldStateBeliefBuilder(default_max_age_seconds=30).build(
        (
            _observation(
                "robot-a-old-open",
                value=True,
                source="robot-a:lidar",
                confidence=0.95,
                observed_at="2026-07-28T01:00:00+00:00",
            ),
            _observation(
                "robot-a-new-expired",
                value=False,
                source="robot-a:lidar",
                confidence=0.95,
                observed_at="2026-07-28T01:00:09+00:00",
                expires_at="2026-07-28T01:00:09.500000+00:00",
            ),
        ),
        captured_at=CAPTURED_AT,
    )[0]

    assert belief.status == "stale"
    assert belief.value is None
    assert belief.stale_observation_ids == ("robot-a-new-expired",)
    assert belief.superseded_observation_ids == ("robot-a-old-open",)


def test_stale_other_source_does_not_freshen_confirmed_winner_timestamp() -> None:
    belief = WorldStateBeliefBuilder(default_max_age_seconds=30).build(
        (
            _observation(
                "camera-open",
                value=True,
                source="camera-west",
                confidence=0.9,
                observed_at="2026-07-28T01:00:01+00:00",
            ),
            _observation(
                "robot-expired",
                value=False,
                source="robot-a:lidar",
                confidence=0.95,
                observed_at="2026-07-28T01:00:09+00:00",
                expires_at="2026-07-28T01:00:09.500000+00:00",
            ),
        ),
        captured_at=CAPTURED_AT,
    )[0]

    assert belief.status == "confirmed"
    assert belief.value is True
    assert belief.observed_at == "2026-07-28T01:00:01+00:00"
    assert belief.stale_observation_ids == ("robot-expired",)
