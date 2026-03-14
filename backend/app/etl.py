"""ETL pipeline: fetch data from the autochecker API and load it into the database.

The autochecker dashboard API provides two endpoints:
- GET /api/items — lab/task catalog
- GET /api/logs  — anonymized check results (supports ?since= and ?limit= params)

Both require HTTP Basic Auth (email + password from settings).
"""

from datetime import datetime

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession

from app.settings import settings


# ---------------------------------------------------------------------------
# Extract — fetch data from the autochecker API
# ---------------------------------------------------------------------------


async def fetch_items() -> list[dict]:
    """Fetch the lab/task catalog from the autochecker API.

    - Use httpx.AsyncClient to GET {settings.autochecker_api_url}/api/items
    - Pass HTTP Basic Auth using settings.autochecker_email and
      settings.autochecker_password
    - The response is a JSON array of objects with keys:
      lab (str), task (str | null), title (str), type ("lab" | "task")
    - Return the parsed list of dicts
    - Raise an exception if the response status is not 200
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.autochecker_api_url}/api/items",
            auth=(settings.autochecker_email, settings.autochecker_password),
        )
        response.raise_for_status()
        return response.json()


async def fetch_logs(since: datetime | None = None) -> list[dict]:
    """Fetch check results from the autochecker API.

    - Use httpx.AsyncClient to GET {settings.autochecker_api_url}/api/logs
    - Pass HTTP Basic Auth using settings.autochecker_email and
      settings.autochecker_password
    - Query parameters:
      - limit=500 (fetch in batches)
      - since={iso timestamp} if provided (for incremental sync)
    - The response JSON has shape:
      {"logs": [...], "count": int, "has_more": bool}
    - Handle pagination: keep fetching while has_more is True
      - Use the submitted_at of the last log as the new "since" value
    - Return the combined list of all log dicts from all pages
    """
    all_logs: list[dict] = []
    current_since = since

    async with httpx.AsyncClient() as client:
        while True:
            params = {"limit": 500}
            if current_since:
                params["since"] = current_since.isoformat()

            response = await client.get(
                f"{settings.autochecker_api_url}/api/logs",
                params=params,
                auth=(settings.autochecker_email, settings.autochecker_password),
            )
            response.raise_for_status()
            data = response.json()

            logs = data.get("logs", [])
            all_logs.extend(logs)

            if not data.get("has_more", False):
                break

            if logs:
                last_log = logs[-1]
                current_since = datetime.fromisoformat(last_log["submitted_at"])

    return all_logs


# ---------------------------------------------------------------------------
# Load — insert fetched data into the local database
# ---------------------------------------------------------------------------


async def load_items(items: list[dict], session: AsyncSession) -> int:
    """Load items (labs and tasks) into the database.

    - Import ItemRecord from app.models.item
    - Process labs first (items where type="lab"):
      - For each lab, check if an item with type="lab" and matching title
        already exists (SELECT)
      - If not, INSERT a new ItemRecord(type="lab", title=lab_title)
      - Build a dict mapping the lab's short ID (the "lab" field, e.g.
        "lab-01") to the lab's database record, so you can look up
        parent IDs when processing tasks
    - Then process tasks (items where type="task"):
      - Find the parent lab item using the task's "lab" field (e.g.
        "lab-01") as the key into the dict you built above
      - Check if a task with this title and parent_id already exists
      - If not, INSERT a new ItemRecord(type="task", title=task_title,
        parent_id=lab_item.id)
    - Commit after all inserts
    - Return the number of newly created items
    """
    from app.models.item import ItemRecord

    new_count = 0
    lab_map: dict[str, ItemRecord] = {}

    # Process labs first
    for item in items:
        if item.get("type") != "lab":
            continue

        lab_title = item.get("title", "")
        lab_short_id = item.get("lab", "")

        # Check if lab already exists
        existing = session.exec(
            ItemRecord.where(ItemRecord.type == "lab").where(
                ItemRecord.title == lab_title
            )
        ).first()

        if not existing:
            lab_record = ItemRecord(type="lab", title=lab_title)
            session.add(lab_record)
            session.flush()  # Get the ID
            lab_map[lab_short_id] = lab_record
            new_count += 1
        else:
            lab_map[lab_short_id] = existing

    # Process tasks
    for item in items:
        if item.get("type") != "task":
            continue

        task_title = item.get("title", "")
        lab_short_id = item.get("lab", "")

        parent_lab = lab_map.get(lab_short_id)
        if not parent_lab:
            continue

        # Check if task already exists
        existing = session.exec(
            ItemRecord.where(ItemRecord.type == "task")
            .where(ItemRecord.title == task_title)
            .where(ItemRecord.parent_id == parent_lab.id)
        ).first()

        if not existing:
            task_record = ItemRecord(
                type="task", title=task_title, parent_id=parent_lab.id
            )
            session.add(task_record)
            new_count += 1

    await session.commit()
    return new_count


async def load_logs(
    logs: list[dict], items_catalog: list[dict], session: AsyncSession
) -> int:
    """Load interaction logs into the database.

    Args:
        logs: Raw log dicts from the API (each has lab, task, student_id, etc.)
        items_catalog: Raw item dicts from fetch_items() — needed to map
            short IDs (e.g. "lab-01", "setup") to item titles stored in the DB.
        session: Database session.

    - Import Learner from app.models.learner
    - Import InteractionLog from app.models.interaction
    - Import ItemRecord from app.models.item
    - Build a lookup from (lab_short_id, task_short_id) to item title
      using items_catalog. For labs, the key is (lab, None). For tasks,
      the key is (lab, task). The value is the item's title.
    - For each log dict:
      1. Find or create a Learner by external_id (log["student_id"])
         - If creating, set student_group from log["group"]
      2. Find the matching item in the database:
         - Use the lookup to get the title for (log["lab"], log["task"])
         - Query the DB for an ItemRecord with that title
         - Skip this log if no matching item is found
      3. Check if an InteractionLog with this external_id already exists
         (for idempotent upsert — skip if it does)
      4. Create InteractionLog with:
         - external_id = log["id"]
         - learner_id = learner.id
         - item_id = item.id
         - kind = "attempt"
         - score = log["score"]
         - checks_passed = log["passed"]
         - checks_total = log["total"]
         - created_at = parsed log["submitted_at"]
    - Commit after all inserts
    - Return the number of newly created interactions
    """
    from app.models.interaction import InteractionLog
    from app.models.learner import Learner
    from app.models.item import ItemRecord

    # Build lookup: (lab_short_id, task_short_id or None) -> title
    title_lookup: dict[tuple[str, str | None], str] = {}
    for item in items_catalog:
        lab_id = item.get("lab", "")
        task_id = item.get("task")  # Can be None for labs
        title = item.get("title", "")
        title_lookup[(lab_id, task_id)] = title

    new_count = 0

    for log in logs:
        # 1. Find or create learner
        student_id = log.get("student_id", "")
        student_group = log.get("group", "")

        learner = session.exec(Learner.where(Learner.external_id == student_id)).first()

        if not learner:
            learner = Learner(external_id=student_id, student_group=student_group)
            session.add(learner)
            session.flush()

        # 2. Find matching item
        lab_id = log.get("lab", "")
        task_id = log.get("task")  # Can be None
        item_title = title_lookup.get((lab_id, task_id))

        if not item_title:
            continue

        item_record = session.exec(
            ItemRecord.where(ItemRecord.title == item_title)
        ).first()

        if not item_record:
            continue

        # 3. Check if log already exists
        log_external_id = log.get("id")
        existing_log = session.exec(
            InteractionLog.where(InteractionLog.external_id == log_external_id)
        ).first()

        if existing_log:
            continue

        # 4. Create new interaction log
        from datetime import datetime

        submitted_at = log.get("submitted_at")
        created_at = datetime.fromisoformat(submitted_at) if submitted_at else None

        interaction = InteractionLog(
            external_id=log_external_id,
            learner_id=learner.id,
            item_id=item_record.id,
            kind="attempt",
            score=log.get("score"),
            checks_passed=log.get("passed"),
            checks_total=log.get("total"),
            created_at=created_at,
        )
        session.add(interaction)
        new_count += 1

    await session.commit()
    return new_count


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def sync(session: AsyncSession) -> dict:
    """Run the full ETL pipeline.

    - Step 1: Fetch items from the API (keep the raw list) and load them
      into the database
    - Step 2: Determine the last synced timestamp
      - Query the most recent created_at from InteractionLog
      - If no records exist, since=None (fetch everything)
    - Step 3: Fetch logs since that timestamp and load them
      - Pass the raw items list to load_logs so it can map short IDs
        to titles
    - Return a dict: {"new_records": <number of new interactions>,
                      "total_records": <total interactions in DB>}
    """
    from app.models.interaction import InteractionLog

    # Step 1: Fetch and load items
    items = await fetch_items()
    new_items = await load_items(items, session)

    # Step 2: Get last synced timestamp
    last_log = session.exec(
        InteractionLog.order_by(InteractionLog.created_at.desc())
    ).first()
    since = last_log.created_at if last_log else None

    # Step 3: Fetch and load logs
    logs = await fetch_logs(since)
    new_logs = await load_logs(logs, items, session)

    # Get total count
    total = session.exec(InteractionLog.select().count()).one()

    return {
        "new_items": new_items,
        "new_records": new_logs,
        "total_records": total,
    }
