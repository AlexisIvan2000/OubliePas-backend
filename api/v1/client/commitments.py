from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query, Request

from api.dependencies import CommitmentServiceDep, CurrentUserDep
from core.rate_limit import limiter
from models.schemas.commitment_schema import (
    CommitmentCreate,
    CommitmentResponse,
    CommitmentStatus,
    CommitmentType,
    CommitmentUpdate,
    DashboardSummary,
    DeletedCommitments,
    OccurrenceResponse,
    OccurrenceStatus,
    OccurrenceUpdate,
    RestoredCount,
    RestoreRequest,
)
from services.commitments.commitment_service import month_bounds
from services.commitments.occurrence_generator import today_utc

router = APIRouter(prefix="/commitments", tags=["commitments"])


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(user: CurrentUserDep, service: CommitmentServiceDep):
    return await service.summary(str(user.id), user.currency)


@router.get("/occurrences", response_model=list[OccurrenceResponse])
async def list_occurrences(
    user: CurrentUserDep,
    service: CommitmentServiceDep,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    status: OccurrenceStatus | None = Query(default=None),
):
    default_start, default_end = month_bounds(today_utc())
    return await service.list_occurrences(
        str(user.id),
        start=start or default_start,
        end=end or default_end,
        status=status,
    )


@router.get("/occurrences/late", response_model=list[OccurrenceResponse])
async def list_late_occurrences(user: CurrentUserDep, service: CommitmentServiceDep):
    return await service.list_late(str(user.id))


@router.patch("/occurrences/{occurrence_id}", response_model=OccurrenceResponse)
@limiter.limit("60/minute")
async def update_occurrence(
    request: Request,
    occurrence_id: UUID,
    payload: OccurrenceUpdate,
    user: CurrentUserDep,
    service: CommitmentServiceDep,
):
    return await service.update_occurrence(str(user.id), occurrence_id, payload)


@router.get("", response_model=list[CommitmentResponse])
async def list_commitments(
    user: CurrentUserDep,
    service: CommitmentServiceDep,
    type: CommitmentType | None = Query(default=None),
    status: CommitmentStatus | None = Query(default=None),
):
    return await service.list_commitments(str(user.id), commitment_type=type, status=status)


@router.delete("", response_model=DeletedCommitments)
@limiter.limit("6/minute")
async def delete_all_commitments(
    request: Request,
    user: CurrentUserDep,
    service: CommitmentServiceDep,
    type: CommitmentType | None = Query(default=None),
    status: CommitmentStatus | None = Query(default=None),
):
    return await service.delete_all(str(user.id), commitment_type=type, status=status)


@router.post("/restore", response_model=RestoredCount)
@limiter.limit("30/minute")
async def restore_commitments(
    request: Request,
    payload: RestoreRequest,
    user: CurrentUserDep,
    service: CommitmentServiceDep,
):
    return await service.restore(str(user.id), list(payload.ids))


@router.post("", response_model=CommitmentResponse, status_code=201)
@limiter.limit("60/minute")
async def create_commitment(
    request: Request,
    payload: CommitmentCreate,
    user: CurrentUserDep,
    service: CommitmentServiceDep,
):
    return await service.create(
        str(user.id), payload, default_reminder_days=user.default_reminder_days
    )


@router.get("/{commitment_id}", response_model=CommitmentResponse)
async def get_commitment(
    commitment_id: UUID, user: CurrentUserDep, service: CommitmentServiceDep
):
    return await service.get(str(user.id), commitment_id)


@router.patch("/{commitment_id}", response_model=CommitmentResponse)
@limiter.limit("60/minute")
async def update_commitment(
    request: Request,
    commitment_id: UUID,
    payload: CommitmentUpdate,
    user: CurrentUserDep,
    service: CommitmentServiceDep,
):
    return await service.update(str(user.id), commitment_id, payload)


@router.delete("/{commitment_id}", response_model=DeletedCommitments)
@limiter.limit("30/minute")
async def delete_commitment(
    request: Request,
    commitment_id: UUID,
    user: CurrentUserDep,
    service: CommitmentServiceDep,
):
    return await service.delete(str(user.id), commitment_id)
