import os
import sys
import json
import sqlite3
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

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
    user_phone_number: str
    ticket_id: str = "TKT-101"
    customer_name: str = "Aarav Sharma"
    issue_summary: str = "Double charged on UPI order #4412"
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


@app.post("/api/calls/outbound")
async def trigger_outbound_call(req: OutboundCallPayload):
    url = f"https://apps.sarvam.ai/api/outbounds/v1/orgs/{ORG_ID}/workspaces/{WORKSPACE_ID}/outbounds"
    headers = {
        "X-API-Key": SARVAM_API_KEY,
        "Content-Type": "application/json"
    }

    target_transfer = req.transfer_number or FORWARD_TO_PHONE

    initial_variables = {
        "customer_name": req.customer_name,
        "ticket_id": req.ticket_id,
        "issue_summary": req.issue_summary,
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
            "user_phone_number": req.user_phone_number
        },
        "webhook_config": {
            "url": f"{PUBLIC_BASE_URL}/api/webhooks/sarvam",
            "metadata": {
                "direction": "OUTBOUND",
                "ticket_id": req.ticket_id,
                "user_phone": req.user_phone_number
            }
        }
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        res_json = resp.json()

    interaction_id = res_json.get("interaction_id") or f"pending_{int(datetime.now(timezone.utc).timestamp())}"

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO call_logs (
                interaction_id, direction, ticket_id, user_phone, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            interaction_id,
            "OUTBOUND",
            req.ticket_id,
            req.user_phone_number,
            "DISPATCHED",
            datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()

    return {"status": "dispatched", "interaction_id": interaction_id, "sarvam_response": res_json}


@app.post("/api/tools/lookup-context")
async def tool_lookup_caller_context(req: InboundLookupPayload):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = None
        if req.ticket_id:
            row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (req.ticket_id,)).fetchone()
        elif req.caller_phone:
            row = conn.execute("SELECT * FROM tickets WHERE user_phone = ?", (req.caller_phone,)).fetchone()

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

    return {
        "found": False,
        "ticket_id": "NEW-INBOUND",
        "customer_name": "Valued Customer",
        "issue_summary": "General Inquiry",
        "status": "OPEN",
        "transfer_phone_number": FORWARD_TO_PHONE
    }


@app.post("/api/tools/update-or-escalate")
async def tool_update_or_escalate(req: TicketUpdatePayload):
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


@app.post("/api/webhooks/sarvam")
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

    return {"saved": True, "interaction_id": interaction_id, "forwarded": bool(forwarded_flag)}


@app.post("/api/calls/sync")
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


@app.get("/api/calls/logs")
async def get_local_call_logs():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM call_logs ORDER BY updated_at DESC").fetchall()
        output = []
        for r in rows:
            d = dict(r)
            d["transcript"] = json.loads(d["transcript"]) if d["transcript"] else []
            d["extracted_variables"] = json.loads(d["extracted_variables"]) if d["extracted_variables"] else {}
            output.append(d)
        return output


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
