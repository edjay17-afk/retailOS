"""Read Lark Daily Inspection reports and record matched submissions locally."""
import re
import time
from datetime import datetime, timezone, timedelta
from threading import Lock
from urllib.parse import urlencode
from ..lark import LarkClient, LarkError

_cache = {}
_lock = Lock()
PH = timezone(timedelta(hours=8))

def store_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name).casefold()).removeprefix("thedailycentro")

def parse_records(records, day):
    reports = []
    for record in records:
        fields = record.get("fields", {})
        date_value = next((v for k,v in fields.items() if re.match(r"^date(?:\s|$)", k, re.I)), None)
        store = next((v for k,v in fields.items() if re.match(r"^store(?:\s|$)", k, re.I) and isinstance(v,str)), None)
        if not store or not isinstance(date_value,(int,float)):
            continue
        try:
            sent = datetime.fromtimestamp(date_value / 1000, PH)
        except (ValueError, OverflowError, OSError):
            continue
        if sent.date() != day:
            continue
        attachments = sum(len(v) for v in fields.values() if isinstance(v,list) and v and isinstance(v[0],dict) and "file_token" in v[0])
        reports.append({"store":store_key(store),"id":record.get("record_id") or record.get("id"),"time":sent.strftime("%H:%M"),"attachments":attachments})
    return reports

def reports_for_day(day):
    client = LarkClient()
    try:
        if not client.configured:
            return {"available":False,"message":"Lark is not configured.","reports":[]}
        key = (client.cfg.lark_base_token,day.isoformat())
        with _lock:
            cached = _cache.get(key)
            if cached and time.monotonic()-cached[0]<60:
                return cached[1]
        tables = client.tables()
        matches = [tid for name,tid in tables.items() if "daily inspection" in name.lower()]
        if len(matches)!=1:
            raise LarkError("Daily Inspection table could not be identified")
        tid = matches[0]
        records, token, seen = [], None, set()
        while True:
            data = client.call("GET",f"{client._base()}/tables/{tid}/records",params={"page_size":500,**({"page_token":token} if token else {})})
            records.extend(data.get("items",[]))
            if not data.get("has_more"):
                break
            token = data.get("page_token")
            if not token or token in seen or len(records)>50000:
                raise LarkError("Could not finish reading reports")
            seen.add(token)
        reports = parse_records(records,day)
        base = client.cfg.lark_base_token.strip()
        if not base.startswith("https://"):
            base = "https://www.larksuite.com/base/"+client.app_token()
        base = base.split("?")[0].split("#")[0]
        for report in reports:
            report["url"] = base+"?"+urlencode({"table":tid,"record":report["id"]})
        result = {"available":True,"reports":reports,"checked_at":datetime.now(PH).strftime("%H:%M")}
        with _lock:
            if len(_cache)>50:
                _cache.clear()
            _cache[key]=(time.monotonic(),result)
        return result
    finally:
        client.http.close()


def mark_submitted(db, day, reports_by_location, actor_id):
    """Persist external submission evidence without inventing checklist outcomes."""
    from sqlalchemy import select
    from ..models import Inspection, InspectionTemplate
    from .. import audit
    from ..timeutil import now_utc
    matches = {int(lid): [r for r in reports if r.get("id")] for lid, reports in reports_by_location.items()}
    matches = {lid: rows for lid, rows in matches.items() if rows}
    if not matches:
        return []
    rows = db.scalars(select(Inspection).join(InspectionTemplate).where(
        Inspection.business_date == day, Inspection.location_id.in_(matches),
        InspectionTemplate.code == "store_daily", Inspection.status.in_(("pending", "in_progress", "missed"))))
    changed = []
    for inspection in rows:
        previous = inspection.status
        inspection.status = "submitted"
        # Lark's date field identifies the report day, not a reliable submission timestamp.
        audit.log(db, actor_type="human", actor_id=actor_id, action="inspection.lark_submitted",
                  entity_type="inspection", entity_id=inspection.id,
                  before={"status": previous}, after={"status": "submitted", "report_date": str(day),
                  "report_ids": [r["id"] for r in matches[inspection.location_id]], "verified_at": now_utc().isoformat()})
        changed.append(inspection.id)
    db.flush()
    return changed
