"""Admin: billing overview, transactions, refunds."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, ok, paginate
from services import mocks

router = APIRouter(prefix="/admin/billing", tags=["admin-billing"],
                   dependencies=[Depends(get_admin_session)])


@router.get("/overview")
async def overview():
    db = get_db()
    now = datetime.now(timezone.utc)
    this_month = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
    last_month_start = (now.replace(day=1) - timedelta(days=1)).replace(
        day=1).isoformat()
    last_month_end = now.replace(day=1, hour=0, minute=0, second=0).isoformat()

    subs = await db.subscriptions.find({"status": "ACTIVE"},
                                       {"_id": 0}).to_list(5000)
    plan_ids = list({s["planId"] for s in subs if s.get("planId")})
    plans = {p["id"]: p for p in await db.plans.find(
        {"id": {"$in": plan_ids}}, {"_id": 0}).to_list(50)}
    mrr = sum(float(plans.get(s.get("planId"), {}).get("monthlyPrice", 0))
              for s in subs)

    rev_this = sum(float(i.get("amount", 0)) for i in
                   await db.invoices.find(
                       {"status": "PAID", "createdAt": {"$gte": this_month}},
                       {"_id": 0}).to_list(5000))
    rev_last = sum(float(i.get("amount", 0)) for i in
                   await db.invoices.find(
                       {"status": "PAID",
                        "createdAt": {"$gte": last_month_start,
                                       "$lt": last_month_end}},
                       {"_id": 0}).to_list(5000))
    growth = ((rev_this - rev_last) / rev_last * 100) if rev_last else 0
    failed = await db.invoices.count_documents({"status": "FAILED"})

    return ok({
        "MRR": round(mrr, 2),
        "revenueThisMonth": round(rev_this, 2),
        "revenueLastMonth": round(rev_last, 2),
        "growthPercent": round(growth, 2),
        "failedPayments": failed,
    })


@router.get("/transactions")
async def transactions(page: int = 1, limit: int = 20,
                       status: Optional[str] = None,
                       dateRange: Optional[str] = None):
    db = get_db()
    q: dict = {}
    if status:
        q["status"] = status.upper()
    total = await db.invoices.count_documents(q)
    rows = await db.invoices.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.post("/refund/{invoice_id}")
async def refund(invoice_id: str):
    invoice = await get_db().invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        raise APIError("Invoice not found", "NOT_FOUND", 404)
    res = await mocks.lemonsqueezy_refund(
        invoice.get("lemonSqueezyInvoiceId", invoice_id))
    await get_db().invoices.update_one(
        {"id": invoice_id}, {"$set": {"status": "REFUNDED"}})
    return ok(res)
