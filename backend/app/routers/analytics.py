"""Router for analytics endpoints.

Each endpoint performs SQL aggregation queries on the interaction data
populated by the ETL pipeline. All endpoints require a `lab` query
parameter to filter results by lab (e.g., "lab-01").
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models.item import ItemRecord
from app.models.learner import Learner
from app.models.interaction import InteractionLog

router = APIRouter()


class ScoreBucket(SQLModel):
    """Score bucket with count."""

    bucket: str
    count: int


class PassRateItem(SQLModel):
    """Pass rate item."""

    task: str
    avg_score: float
    attempts: int


class TimelineItem(SQLModel):
    """Timeline item."""

    date: str
    submissions: int


class GroupItem(SQLModel):
    """Group item."""

    group: str
    avg_score: float
    students: int


def _get_lab_title_from_param(lab: str) -> str:
    """Convert lab parameter to title format.

    e.g., "lab-04" → "Lab 04"
    """
    parts = lab.split("-")
    if len(parts) == 2:
        return f"{parts[0].capitalize()} {parts[1]}"
    return lab


async def _get_lab_and_task_ids(session: AsyncSession, lab: str) -> list[int]:
    """Get task IDs for a given lab parameter.

    Returns list of task item IDs (children of the lab).
    """
    lab_title = _get_lab_title_from_param(lab)

    # Find the lab item
    result = await session.exec(
        select(ItemRecord).where(ItemRecord.title.contains(lab_title))
    )
    lab_item = result.scalars().first()

    if not lab_item:
        return []

    # Find all task items that are children of this lab
    tasks = await session.exec(
        select(ItemRecord.id).where(ItemRecord.parent_id == lab_item.id)
    )
    return list(tasks.scalars().all())


@router.get("/scores")
async def get_scores(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Score distribution histogram for a given lab.

    - Find the lab item by matching title (e.g. "lab-04" → title contains "Lab 04")
    - Find all tasks that belong to this lab (parent_id = lab.id)
    - Query interactions for these items that have a score
    - Group scores into buckets: "0-25", "26-50", "51-75", "76-100"
      using CASE WHEN expressions
    - Return a JSON array:
      [{"bucket": "0-25", "count": 12}, {"bucket": "26-50", "count": 8}, ...]
    - Always return all four buckets, even if count is 0
    """
    task_ids = await _get_lab_and_task_ids(session, lab)

    if not task_ids:
        return [
            ScoreBucket(bucket="0-25", count=0),
            ScoreBucket(bucket="26-50", count=0),
            ScoreBucket(bucket="51-75", count=0),
            ScoreBucket(bucket="76-100", count=0),
        ]

    # Build the score bucket CASE expression
    score_bucket = case(
        (InteractionLog.score <= 25, "0-25"),
        (InteractionLog.score <= 50, "26-50"),
        (InteractionLog.score <= 75, "51-75"),
        (InteractionLog.score <= 100, "76-100"),
        else_="0-25",  # fallback, should not happen for valid scores
    )

    # Query interactions with scores for these tasks
    stmt = (
        select(score_bucket.label("bucket"), func.count().label("count"))
        .select_from(InteractionLog)
        .where(
            InteractionLog.item_id.in_(task_ids),
            InteractionLog.score.isnot(None),
        )
        .group_by(score_bucket)
    )

    result = await session.exec(stmt)
    rows = result.all()

    # Build result with all buckets
    bucket_counts = {row.bucket: row.count for row in rows}

    return [
        ScoreBucket(bucket="0-25", count=bucket_counts.get("0-25", 0)),
        ScoreBucket(bucket="26-50", count=bucket_counts.get("26-50", 0)),
        ScoreBucket(bucket="51-75", count=bucket_counts.get("51-75", 0)),
        ScoreBucket(bucket="76-100", count=bucket_counts.get("76-100", 0)),
    ]


@router.get("/pass-rates")
async def get_pass_rates(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Per-task pass rates for a given lab.

    - Find the lab item and its child task items
    - For each task, compute:
      - avg_score: average of interaction scores (round to 1 decimal)
      - attempts: total number of interactions
    - Return a JSON array:
      [{"task": "Repository Setup", "avg_score": 92.3, "attempts": 150}, ...]
    - Order by task title
    """
    task_ids = await _get_lab_and_task_ids(session, lab)

    if not task_ids:
        return []

    # Query per-task statistics
    stmt = (
        select(
            ItemRecord.title.label("task"),
            func.round(func.avg(InteractionLog.score) * 10) / 10.0,
            func.count().label("attempts"),
        )
        .select_from(ItemRecord)
        .join(InteractionLog, InteractionLog.item_id == ItemRecord.id)
        .where(ItemRecord.id.in_(task_ids))
        .group_by(ItemRecord.id, ItemRecord.title)
        .order_by(ItemRecord.title)
    )

    result = await session.exec(stmt)
    rows = result.all()

    return [
        PassRateItem(task=row.task, avg_score=row[1], attempts=row.attempts)
        for row in rows
    ]


@router.get("/timeline")
async def get_timeline(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Submissions per day for a given lab.

    - Find the lab item and its child task items
    - Group interactions by date (use func.date(created_at))
    - Count the number of submissions per day
    - Return a JSON array:
      [{"date": "2026-02-28", "submissions": 45}, ...]
    - Order by date ascending
    """
    task_ids = await _get_lab_and_task_ids(session, lab)

    if not task_ids:
        return []

    # Query submissions per day
    stmt = (
        select(
            func.date(InteractionLog.created_at).label("date"),
            func.count().label("submissions"),
        )
        .select_from(InteractionLog)
        .where(InteractionLog.item_id.in_(task_ids))
        .group_by(func.date(InteractionLog.created_at))
        .order_by(func.date(InteractionLog.created_at))
    )

    result = await session.exec(stmt)
    rows = result.all()

    return [
        TimelineItem(date=str(row.date), submissions=row.submissions) for row in rows
    ]


@router.get("/groups")
async def get_groups(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Per-group performance for a given lab.

    - Find the lab item and its child task items
    - Join interactions with learners to get student_group
    - For each group, compute:
      - avg_score: average score (round to 1 decimal)
      - students: count of distinct learners
    - Return a JSON array:
      [{"group": "B23-CS-01", "avg_score": 78.5, "students": 25}, ...]
    - Order by group name
    """
    task_ids = await _get_lab_and_task_ids(session, lab)

    if not task_ids:
        return []

    # Query per-group statistics
    stmt = (
        select(
            Learner.student_group.label("group"),
            func.round(func.avg(InteractionLog.score) * 10) / 10.0,
            func.count(func.distinct(Learner.id)).label("students"),
        )
        .select_from(InteractionLog)
        .join(Learner, Learner.id == InteractionLog.learner_id)
        .where(InteractionLog.item_id.in_(task_ids))
        .group_by(Learner.student_group)
        .order_by(Learner.student_group)
    )

    result = await session.exec(stmt)
    rows = result.all()

    return [
        GroupItem(group=row.group, avg_score=row[1], students=row.students)
        for row in rows
    ]
