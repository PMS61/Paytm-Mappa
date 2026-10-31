"""MCP server (stdio, JSON-RPC 2.0, no extra dependencies) so an AI agent (the chatbot, the voice agent) can use the
ticket system, the policy RAG and Jev as tools.   python -m mappa.mcp_server

Tools: search_policies, triage (clarity + Jev without storing), raise_ticket, get_ticket, list_tickets.
Protocol messages go to stdout only; logs go to stderr.
"""

import json
import logging
import sys

from . import api, clarity, policies
from .schema import Event, Ticket, TicketIn, now

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("mappa-mcp")
PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}
TICKET_IN = TicketIn.model_json_schema()

TOOLS = [
    {"name": "search_policies", "description": "Search the support policies (ALPHA retrieval). Returns cited clauses.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"},
                     "category": {"type": "string", "enum": ["settlement", "refund", "device", "kyc", "payment", "other"]},
                     "k": {"type": "integer", "default": 3}}, "required": ["query"]}},
    {"name": "triage", "description": "Run the clarity module and Jev on a ticket without storing it: scenario, action "
                                      "item, missing details, policy holds and the AI/HUMAN decision. Use it during a "
                                      "chat or call to see which details are still missing.",
     "inputSchema": TICKET_IN},
    {"name": "raise_ticket", "description": "Raise a support ticket. Returns the stored ticket with its clarity result.",
     "inputSchema": TICKET_IN},
    {"name": "get_ticket", "description": "Get a ticket by id (e.g. TKT-000001).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "list_tickets", "description": "List tickets, optionally by status or the customer's phone number.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "phone": {"type": "string"}}}},
]


class RpcError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def _call(name: str, a: dict):
    if name == "search_policies":
        return policies.tree().search(a["query"], a.get("category"), int(a.get("k", 3)))
    if name == "triage":
        clean, secret, pii = clarity.sanitise_ticket(TicketIn(**a))
        t = Ticket(**clean.model_dump(), id="DRY-RUN", created_at=now(), updated_at=now(),
                   history=[Event(by="system", status="open")])
        return clarity.run(t, secret, pii).model_dump(mode="json", exclude={"history"})
    if name == "raise_ticket":
        return api.raise_ticket(TicketIn(**a)).model_dump(mode="json")
    if name == "get_ticket":
        return api.get_ticket(a["id"]).model_dump(mode="json")
    if name == "list_tickets":
        return [{"id": t.id, "status": t.status, "subject": t.subject,
                 "route": t.clarity.decision.route if t.clarity else None}
                for t in api.list_tickets(status=a.get("status"), phone=a.get("phone"))]
    raise RpcError(-32602, f"Unknown tool: {name}")


def handle(msg: dict) -> dict | None:
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if rid is None:
        return None  # notifications get no response
    if method == "initialize":
        v = params.get("protocolVersion")
        result = {"protocolVersion": v if v in PROTOCOLS else "2024-11-05",
                  "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "mappa", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            out = _call(params.get("name"), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}
        except RpcError:
            raise
        except Exception as e:  # tool errors are results, not protocol errors
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    else:
        raise RpcError(-32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = None
        try:
            msg = json.loads(line)
            resp = handle(msg)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        except RpcError as e:
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": e.code, "message": e.message}}
        except Exception as e:
            log.exception("unhandled")
            resp = {"jsonrpc": "2.0", "id": (msg or {}).get("id"), "error": {"code": -32603, "message": type(e).__name__}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
