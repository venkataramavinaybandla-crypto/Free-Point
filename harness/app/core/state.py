"""Job state machine with strict legal transition enforcement."""

from enum import Enum
from typing import Dict, Set


class InvalidStateTransitionError(Exception):
    """Raised when an illegal job state transition is attempted."""


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    REJECTED = "REJECTED"


LEGAL_TRANSITIONS: Dict[JobStatus, Set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.FAILED},
    JobStatus.RUNNING: {
        JobStatus.AWAITING_APPROVAL,
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.TIMED_OUT,
    },
    JobStatus.AWAITING_APPROVAL: {
        JobStatus.APPROVED,
        JobStatus.REJECTED,
        JobStatus.FAILED,
        JobStatus.TIMED_OUT,
    },
    JobStatus.APPROVED: {JobStatus.EXECUTING, JobStatus.FAILED},
    JobStatus.EXECUTING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.TIMED_OUT,
    },
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.TIMED_OUT: set(),
    JobStatus.REJECTED: set(),
}


def transition(current_status: JobStatus, target_status: JobStatus) -> JobStatus:
    """Validate and execute a job state transition.

    Raises:
        InvalidStateTransitionError: If target_status is not permitted from current_status.
    """
    if target_status not in LEGAL_TRANSITIONS.get(current_status, set()):
        raise InvalidStateTransitionError(
            f"Illegal state transition from {current_status.value} to {target_status.value}"
        )
    return target_status
