"""Tests for job state machine and valid/invalid transitions."""

import pytest

from harness.app.core.state import (
    LEGAL_TRANSITIONS,
    InvalidStateTransitionError,
    JobStatus,
    transition,
)


def test_valid_transitions():
    assert transition(JobStatus.QUEUED, JobStatus.RUNNING) == JobStatus.RUNNING
    assert transition(JobStatus.RUNNING, JobStatus.COMPLETED) == JobStatus.COMPLETED
    assert transition(JobStatus.RUNNING, JobStatus.AWAITING_APPROVAL) == JobStatus.AWAITING_APPROVAL
    assert transition(JobStatus.AWAITING_APPROVAL, JobStatus.APPROVED) == JobStatus.APPROVED
    assert transition(JobStatus.APPROVED, JobStatus.EXECUTING) == JobStatus.EXECUTING
    assert transition(JobStatus.EXECUTING, JobStatus.COMPLETED) == JobStatus.COMPLETED
    assert transition(JobStatus.RUNNING, JobStatus.TIMED_OUT) == JobStatus.TIMED_OUT
    assert transition(JobStatus.RUNNING, JobStatus.FAILED) == JobStatus.FAILED
    assert transition(JobStatus.AWAITING_APPROVAL, JobStatus.REJECTED) == JobStatus.REJECTED


def test_invalid_transitions():
    with pytest.raises(InvalidStateTransitionError):
        transition(JobStatus.QUEUED, JobStatus.COMPLETED)

    with pytest.raises(InvalidStateTransitionError):
        transition(JobStatus.COMPLETED, JobStatus.RUNNING)

    with pytest.raises(InvalidStateTransitionError):
        transition(JobStatus.FAILED, JobStatus.RUNNING)

    with pytest.raises(InvalidStateTransitionError):
        transition(JobStatus.REJECTED, JobStatus.APPROVED)

    with pytest.raises(InvalidStateTransitionError):
        transition(JobStatus.AWAITING_APPROVAL, JobStatus.COMPLETED)


def test_terminal_states_have_no_transitions():
    for terminal in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TIMED_OUT, JobStatus.REJECTED]:
        assert len(LEGAL_TRANSITIONS[terminal]) == 0
