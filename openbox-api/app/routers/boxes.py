"""Box inventory — read-only for now (2 physical boxes, seeded in db.py)."""

from fastapi import APIRouter

from .. import db

router = APIRouter(prefix="/boxes", tags=["boxes"])


@router.get("")
async def list_boxes():
    rows = db.db().execute("SELECT * FROM boxes ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@router.get("/availability")
async def box_availability(size: str | None = None):
    if size:
        cur = db.db().execute(
            "SELECT COUNT(*) AS n FROM boxes WHERE status='FREE' AND size=?", (size,)
        )
        return {"size": size, "free": cur.fetchone()["n"]}
    rows = db.db().execute(
        "SELECT size, COUNT(*) AS free FROM boxes WHERE status='FREE' GROUP BY size"
    ).fetchall()
    return {r["size"]: r["free"] for r in rows}
