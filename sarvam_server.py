import os
import sys
import json
import sqlite3
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import httpx
import uvicorn
from fastapi import FastAPI, APIRouter, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# Optional integration with core Paytm-Mappa ticket store
try:
    from mappa import store as mappa_store, schema, pipeline
except ImportError:
    mappa_store = None
    schema = None
    pipeline = None

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
ORG_ID = os.getenv("SARVAM_ORG_ID", "")
WORKSPACE_ID = os.getenv("SARVAM_WORKSPACE_ID", "")
APP_ID = os.getenv("SARVAM_APP_ID", "")
APP_VERSION = int(os.getenv("SARVAM_APP_VERSION", "1"))
CONNECTION_ID = os.getenv("SARVAM_CONNECTION_ID", "")
AGENT_PHONE = os.getenv("SARVAM_AGENT_PHONE", "")
FORWARD_TO_PHONE = os.getenv("FORWARD_TO_PHONE", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "support_calls.db")

router = APIRouter(tags=["Sarvam Telephony"])
app = FastAPI(title="Sarvam Inbound/Outbound Support Engine")


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                customer_name TEXT,
                user_phone TEXT,
                issue_summary TEXT,
                status TEXT,
                escalation_phone TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS call_logs (
                interaction_id TEXT PRIMARY KEY,
                direction TEXT,
                ticket_id TEXT,
                user_phone TEXT,
                status TEXT,
                duration_seconds INTEGER,
                forwarded INTEGER DEFAULT 0,
                extracted_variables TEXT,
                transcript TEXT,
                recording_url TEXT,
                forwarded_recording_url TEXT,
                raw_payload TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO tickets (ticket_id, customer_name, user_phone, issue_summary, status, escalation_phone)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("TKT-101", "Aarav Sharma", "+919876543210", "Double charged on UPI order #4412", "OPEN", FORWARD_TO_PHONE))
        conn.commit()


init_db()


class OutboundCallPayload(BaseModel):
    user_phone_number: Optional[str] = None
    ticket_id: str = "TKT-101"
    customer_name: Optional[str] = None
    issue_summary: Optional[str] = None
    transfer_number: Optional[str] = None
    extra_variables: Optional[Dict[str, Any]] = None


class InboundLookupPayload(BaseModel):
    caller_phone: Optional[str] = None
    ticket_id: Optional[str] = None


class TicketUpdatePayload(BaseModel):
    ticket_id: str
    new_status: str
    request_human_transfer: bool = False


async def sarvam_get(path: str) -> Dict[str, Any]:
    url = f"https://apps.sarvam.ai/api/analytics/v1/{ORG_ID}/{WORKSPACE_ID}/{APP_ID}/{path.lstrip('/')}"
    headers = {"X-API-Key": SARVAM_API_KEY}
    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            return resp.json()
        return {}


async def fetch_full_interaction_details(interaction_id: str) -> Dict[str, Any]:
    transcript_data, rec_data, fwd_rec_data = await asyncio.gather(
        sarvam_get(f"transcripts/{interaction_id}"),
        sarvam_get(f"recordings/{interaction_id}"),
        sarvam_get(f"forwarded-recordings/{interaction_id}")
    )
    return {
        "transcript": transcript_data.get("messages", transcript_data if isinstance(transcript_data, list) else []),
        "recording_url": rec_data.get("url") or rec_data.get("recording_url") or "",
        "forwarded_recording_url": fwd_rec_data.get("url") or fwd_rec_data.get("recording_url") or ""
    }


@router.post("/api/calls/outbound")
async def trigger_outbound_call(req: OutboundCallPayload):
    url = f"https://apps.sarvam.ai/api/outbounds/v1/orgs/{ORG_ID}/workspaces/{WORKSPACE_ID}/outbounds"
    headers = {
        "X-API-Key": SARVAM_API_KEY,
        "Content-Type": "application/json"
    }

    target_phone = req.user_phone_number
    cust_name = req.customer_name
    summary = req.issue_summary

    # Resolve from core ticket store if available, or auto-create a ticket if "NEW"
    if mappa_store:
        try:
            t = mappa_store.get(req.ticket_id) if (req.ticket_id and req.ticket_id not in ("NEW", "TKT-101")) else None
            if t:
                if not target_phone:
                    target_phone = t.raised_by.phone
                if not cust_name:
                    cust_name = t.raised_by.name
                if not summary:
                    summary = t.subject
            elif pipeline and schema:
                # Auto-create ticket for new outbound call request
                raw_phone = (target_phone or "9876543210").replace("+91", "").replace(" ", "").replace("-", "")
                if len(raw_phone) > 10:
                    raw_phone = raw_phone[-10:]
                elif len(raw_phone) < 10:
                    raw_phone = "9876543210"
                
                role_val = "customer"
                if req.extra_variables and req.extra_variables.get("role") in ("merchant", "customer"):
                    role_val = req.extra_variables["role"]

                new_t = pipeline.raise_ticket(schema.TicketIn(
                    channel="call",
                    raised_by=schema.Party(
                        role=role_val,
                        name=cust_name or "Voice Caller",
                        phone=raw_phone
                    ),
                    subject=summary or "Support dispute via Sarvam AI Voice Agent",
                    description=f"Voice call requested to {target_phone or raw_phone}",
                    ask="Autonomous resolution or human escalation via phone",
                    transcript=[schema.Turn(speaker="customer", text=summary or "Voice call initiated")]
                ))
                req.ticket_id = new_t.id

                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("""
                            INSERT OR REPLACE INTO tickets (
                                ticket_id, customer_name, user_phone, issue_summary, status, escalation_phone
                            ) VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            new_t.id,
                            cust_name or "Voice Caller",
                            target_phone or ("+91" + raw_phone),
                            summary or "Voice Call Support",
                            new_t.status.upper(),
                            FORWARD_TO_PHONE
                        ))
                        conn.commit()
                except Exception:
                    pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Could not auto-create ticket for voice call: %s", e)

    if not target_phone:
        target_phone = "+919876543210"
    if not target_phone.startswith("+"):
        target_phone = "+91" + target_phone.lstrip("0")
    if not cust_name:
        cust_name = "Valued Customer"
    if not summary:
        summary = "Support inquiry"

    target_transfer = req.transfer_number or FORWARD_TO_PHONE

    initial_variables = {
        "customer_name": cust_name,
        "ticket_id": req.ticket_id,
        "issue_summary": summary,
        "transfer_phone_number": target_transfer
    }
    if req.extra_variables:
        initial_variables.update(req.extra_variables)

    payload = {
        "app_config": {
            "app_id": APP_ID,
            "app_version": APP_VERSION,
            "connection_config": {
                "connection_id": CONNECTION_ID,
                "agent_phone_number": AGENT_PHONE
            },
            "initial_variables": initial_variables
        },
        "user_config": {
            "user_phone_number": target_phone
        },
        "webhook_config": {
            "url": f"{PUBLIC_BASE_URL}/api/webhooks/sarvam",
            "metadata": {
                "direction": "OUTBOUND",
                "ticket_id": req.ticket_id,
                "user_phone": target_phone
            }
        }
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            res_json = resp.json()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Sarvam Outbound API connection error: {e}")

    interaction_id = res_json.get("interaction_id") or res_json.get("attempt_id") or f"call_{int(datetime.now(timezone.utc).timestamp())}"

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO call_logs (
                interaction_id, direction, ticket_id, user_phone, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            interaction_id,
            "OUTBOUND",
            req.ticket_id,
            target_phone,
            "DISPATCHED",
            datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()

    if mappa_store and schema and req.ticket_id.startswith("TKT-"):
        try:
            t = mappa_store.get(req.ticket_id)
            if t:
                t.history.append(schema.Event(
                    by="system",
                    status=t.status,
                    note=f"Outbound AI call dispatched to {target_phone} (id: {interaction_id})"
                ))
                mappa_store.save(t)
        except Exception:
            pass

    return {
        "status": "dispatched",
        "interaction_id": interaction_id,
        "phone": target_phone,
        "ticket_id": req.ticket_id,
        "agent_phone": AGENT_PHONE,
        "sarvam_response": res_json
    }


@router.post("/api/tools/lookup-context")
async def tool_lookup_caller_context(req: InboundLookupPayload):
    # 1. First check core mappa_store for real tickets
    if mappa_store:
        try:
            matched = None
            if req.ticket_id and req.ticket_id not in ("NEW", "NEW-INBOUND", "INBOUND-CALL"):
                matched = mappa_store.get(req.ticket_id)
            elif req.caller_phone:
                clean_phone = req.caller_phone.replace("+91", "").replace(" ", "").replace("-", "").strip()
                if len(clean_phone) >= 10:
                    clean_phone = clean_phone[-10:]
                # Check for existing active ticket (most recent first)
                for t in mappa_store.all_tickets():
                    p = t.raised_by.phone.replace("+91", "").replace(" ", "").replace("-", "").strip()
                    if p.endswith(clean_phone) and t.status not in ("closed", "resolved"):
                        matched = t
                        break
            if matched:
                return {
                    "found": True,
                    "ticket_id": matched.id,
                    "customer_name": matched.raised_by.name,
                    "issue_summary": matched.subject,
                    "status": matched.status,
                    "transfer_phone_number": FORWARD_TO_PHONE
                }
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("mappa_store lookup failed: %s", e)

    # 2. Fall back to local SQLite tickets
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = None
        if req.ticket_id and req.ticket_id not in ("NEW", "NEW-INBOUND", "INBOUND-CALL"):
            row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (req.ticket_id,)).fetchone()
        elif req.caller_phone:
            row = conn.execute(
                "SELECT * FROM tickets WHERE user_phone = ? AND status NOT IN ('CLOSED', 'RESOLVED') ORDER BY rowid DESC LIMIT 1",
                (req.caller_phone,)
            ).fetchone()

        if row:
            data = dict(row)
            return {
                "found": True,
                "ticket_id": data["ticket_id"],
                "customer_name": data["customer_name"],
                "issue_summary": data["issue_summary"],
                "status": data["status"],
                "transfer_phone_number": data["escalation_phone"] or FORWARD_TO_PHONE
            }

    # 3. DIRECT CALL TO RAISE A NEW TICKET:
    # When a caller dials directly without an active ticket, auto-raise a dispute ticket immediately!
    clean_p = (req.caller_phone or "9876543210").replace("+91", "").replace(" ", "").replace("-", "").strip()
    if len(clean_p) > 10:
        clean_p = clean_p[-10:]
    elif len(clean_p) < 10:
        clean_p = "9876543210"

    caller_name = f"Phone Caller ({clean_p})"
    issue_sum = f"Inbound phone inquiry from {req.caller_phone or clean_p}"
    new_ticket_id = f"TKT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-CALL"
    ticket_status = "open"

    if pipeline and schema:
        try:
            new_t = pipeline.raise_ticket(schema.TicketIn(
                channel="call",
                raised_by=schema.Party(
                    role="customer",
                    name=caller_name,
                    phone=clean_p
                ),
                subject=issue_sum,
                description=f"Direct inbound phone call received on hotline {AGENT_PHONE}",
                ask="Autonomous resolution or human escalation via phone",
                transcript=[schema.Turn(speaker="customer", text=f"Direct inbound phone call connected to hotline {AGENT_PHONE}")]
            ))
            new_ticket_id = new_t.id
            ticket_status = new_t.status
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Failed to auto-raise ticket for inbound call: %s", e)

    # Insert into SQLite tickets table
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO tickets (
                    ticket_id, customer_name, user_phone, issue_summary, status, escalation_phone
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                new_ticket_id,
                caller_name,
                req.caller_phone or ("+91" + clean_p),
                issue_sum,
                ticket_status.upper(),
                FORWARD_TO_PHONE
            ))
            conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("SQLite ticket save failed: %s", e)

    return {
        "found": False,
        "is_new_ticket": True,
        "ticket_id": new_ticket_id,
        "customer_name": caller_name,
        "issue_summary": issue_sum,
        "status": ticket_status,
        "transfer_phone_number": FORWARD_TO_PHONE
    }


@router.post("/api/tools/update-or-escalate")
async def tool_update_or_escalate(req: TicketUpdatePayload):
    # Update core store if present
    if mappa_store and schema:
        try:
            t = mappa_store.get(req.ticket_id)
            if t:
                t.status = req.new_status
                if req.request_human_transfer:
                    t.history.append(schema.Event(by="customer", status=t.status, note="Caller requested live human transfer"))
                mappa_store.save(t)
        except Exception:
            pass

    # Update SQLite
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE tickets SET status = ? WHERE ticket_id = ?",
            (req.new_status, req.ticket_id)
        )
        conn.commit()

    return {
        "updated": True,
        "ticket_id": req.ticket_id,
        "new_status": req.new_status,
        "should_invoke_call_transfer": req.request_human_transfer,
        "transfer_destination": FORWARD_TO_PHONE
    }


@router.post("/api/webhooks/sarvam")
async def sarvam_webhook_receiver(request: Request):
    body = await request.json()

    interaction_id = body.get("interaction_id")
    if not interaction_id:
        raise HTTPException(status_code=400, detail="Missing interaction_id")

    metadata = body.get("metadata") or {}
    direction = metadata.get("direction") or body.get("direction", "INBOUND").upper()
    ticket_id = metadata.get("ticket_id") or (body.get("variables") or {}).get("ticket_id", "INBOUND-CALL")
    user_phone = metadata.get("user_phone") or body.get("user_phone_number") or body.get("caller_number", "")
    status = body.get("status", "COMPLETED")
    duration = body.get("duration") or body.get("duration_seconds") or 0
    extracted_vars = body.get("variables") or body.get("extracted_variables") or {}

    details = await fetch_full_interaction_details(interaction_id)
    forwarded_flag = 1 if (details["forwarded_recording_url"] or body.get("is_transferred")) else 0

    # Parse conversation turns into schema.Turn
    turns = []
    if details.get("transcript"):
        for msg in details["transcript"]:
            if isinstance(msg, dict):
                role = (msg.get("role") or msg.get("speaker") or "customer").lower()
                speaker = "agent" if role in ("assistant", "agent", "bot", "system") else "customer"
                text = msg.get("text") or msg.get("message") or msg.get("content") or ""
                if text.strip():
                    turns.append(schema.Turn(speaker=speaker, text=text.strip()))

    # Find ticket or create if this was a direct inbound call without prior ticket
    t = mappa_store.get(ticket_id) if (mappa_store and ticket_id.startswith("TKT-")) else None

    if not t and mappa_store and pipeline and schema:
        clean_p = (user_phone or "9876543210").replace("+91", "").replace(" ", "").replace("-", "").strip()
        if len(clean_p) > 10:
            clean_p = clean_p[-10:]
        elif len(clean_p) < 10:
            clean_p = "9876543210"

        call_turns = turns if turns else [schema.Turn(speaker="customer", text=f"Direct voice call interaction completed ({duration}s)")]
        try:
            t = pipeline.raise_ticket(schema.TicketIn(
                channel="call",
                raised_by=schema.Party(
                    role="customer",
                    name=f"Inbound Caller ({clean_p})",
                    phone=clean_p
                ),
                subject=f"Inbound Call from {user_phone or clean_p}",
                description=f"Direct phone call to hotline {AGENT_PHONE} ({duration}s)",
                ask="Autonomous resolution or human escalation",
                transcript=call_turns
            ))
            ticket_id = t.id
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Failed to raise ticket from webhook call: %s", e)

    # If ticket exists, attach transcript, run clarity/Jev update, and record history event
    if t and mappa_store and schema:
        try:
            if turns:
                t.transcript = turns
                try:
                    from mappa import clarity as clarity_mod
                    t = clarity_mod.run(t)
                    if pipeline:
                        t = pipeline.process(t)
                except Exception as ce:
                    import logging
                    logging.getLogger(__name__).warning("Clarity re-run on completed call failed: %s", ce)

            dur_str = f"{duration}s" if duration else "completed"
            rec_str = f" Recording: {details['recording_url']}" if details['recording_url'] else ""
            t.history.append(schema.Event(
                by="system",
                status=t.status,
                note=f"Sarvam voice call {direction} ({dur_str}). Transfer: {bool(forwarded_flag)}.{rec_str}"
            ))
            mappa_store.save(t)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to update ticket from webhook: %s", e)

    # Save to SQLite call_logs
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO call_logs (
                interaction_id, direction, ticket_id, user_phone, status,
                duration_seconds, forwarded, extracted_variables, transcript,
                recording_url, forwarded_recording_url, raw_payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(interaction_id) DO UPDATE SET
                direction = excluded.direction,
                ticket_id = excluded.ticket_id,
                user_phone = excluded.user_phone,
                status = excluded.status,
                duration_seconds = excluded.duration_seconds,
                forwarded = excluded.forwarded,
                extracted_variables = excluded.extracted_variables,
                transcript = excluded.transcript,
                recording_url = excluded.recording_url,
                forwarded_recording_url = excluded.forwarded_recording_url,
                raw_payload = excluded.raw_payload,
                updated_at = excluded.updated_at
        """, (
            interaction_id,
            direction,
            ticket_id,
            user_phone,
            status,
            duration,
            forwarded_flag,
            json.dumps(extracted_vars),
            json.dumps(details["transcript"]),
            details["recording_url"],
            details["forwarded_recording_url"],
            json.dumps(body),
            datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()

    return {"saved": True, "interaction_id": interaction_id, "ticket_id": ticket_id, "forwarded": bool(forwarded_flag)}


@router.post("/api/calls/sync")
async def sync_all_logs_from_sarvam():
    interactions_resp = await sarvam_get("interactions")
    items = interactions_resp.get("interactions") or (interactions_resp if isinstance(interactions_resp, list) else [])
    synced_ids = []

    for item in items:
        iid = item.get("interaction_id") or item.get("id")
        if not iid:
            continue
        details = await fetch_full_interaction_details(iid)
        direction = (item.get("direction") or "UNKNOWN").upper()
        user_phone = item.get("user_phone_number") or item.get("caller_number") or ""
        duration = item.get("duration") or item.get("duration_seconds") or 0
        status = item.get("status") or "SYNCED"
        forwarded_flag = 1 if details["forwarded_recording_url"] else 0

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO call_logs (
                    interaction_id, direction, ticket_id, user_phone, status,
                    duration_seconds, forwarded, transcript, recording_url,
                    forwarded_recording_url, raw_payload, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(interaction_id) DO UPDATE SET
                    status = excluded.status,
                    duration_seconds = excluded.duration_seconds,
                    forwarded = excluded.forwarded,
                    transcript = excluded.transcript,
                    recording_url = excluded.recording_url,
                    forwarded_recording_url = excluded.forwarded_recording_url,
                    updated_at = excluded.updated_at
            """, (
                iid,
                direction,
                "SYNCED",
                user_phone,
                status,
                duration,
                forwarded_flag,
                json.dumps(details["transcript"]),
                details["recording_url"],
                details["forwarded_recording_url"],
                json.dumps(item),
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
        synced_ids.append(iid)

    return {"synced_count": len(synced_ids), "interaction_ids": synced_ids}


@router.get("/api/calls/logs")
async def get_local_call_logs(ticket_id: Optional[str] = None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if ticket_id:
            rows = conn.execute("SELECT * FROM call_logs WHERE ticket_id = ? ORDER BY updated_at DESC", (ticket_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM call_logs ORDER BY updated_at DESC").fetchall()
        output = []
        for r in rows:
            d = dict(r)
            d["transcript"] = json.loads(d["transcript"]) if d["transcript"] else []
            d["extracted_variables"] = json.loads(d["extracted_variables"]) if d["extracted_variables"] else {}
            output.append(d)
        return output


@router.get("/api/calls/ticket/{ticket_id}")
async def get_ticket_calls(ticket_id: str):
    return await get_local_call_logs(ticket_id=ticket_id)


# Register routes to the standalone app as well
app.include_router(router)


async def cli_main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"

    if cmd == "call":
        target_phone = sys.argv[2] if len(sys.argv) > 2 else "+919876543210"
        ticket = sys.argv[3] if len(sys.argv) > 3 else "TKT-101"
        payload = OutboundCallPayload(user_phone_number=target_phone, ticket_id=ticket)
        res = await trigger_outbound_call(payload)
        print(json.dumps(res, indent=2))

    elif cmd == "sync":
        res = await sync_all_logs_from_sarvam()
        print(json.dumps(res, indent=2))

    elif cmd == "logs":
        res = await get_local_call_logs()
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("call", "sync", "logs"):
        asyncio.run(cli_main())
    else:
        uvicorn.run("sarvam_server:app", host="0.0.0.0", port=8000, reload=True)
