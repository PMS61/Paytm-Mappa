"""Can this scenario be solved with the MCP tools we have? Answers `need_human` for the clarity flow and Jev.

    1 playbook   the scenario's action item has an autopilot playbook          (autopilot.PLAYBOOKS)
    2 tools      every tool that playbook may call is served by the Paytm actions MCP server right now (live tools/list)
    3 details    every ticket detail the playbook's tool calls need is present (e.g. txn_id to look up a refund)

    automatable  1 and 2: the scenario type can be automated at all. If not, a human has to take it: need_human = True
    solvable     1, 2 and 3: the autopilot can start on this ticket now
    need_human   not automatable. Missing details alone are not a reason for a human: clarity asks for them.

Point PAYTM_MCP_CMD at Paytm's real server and the check follows whatever tools it actually exposes.
"""

from . import autopilot
from .schema import Details

QUESTIONS = {  # what to ask when a playbook's tool call needs a detail the customer has not given
    "txn_id": "What is the transaction ID (or UTR / RRN) of the payment?",
}


def check(action_code: str, details: Details | None) -> dict:
    pb = autopilot.PLAYBOOKS.get(action_code)
    out = {"action": action_code, "playbook": pb is not None, "tools_required": list(pb[1]) if pb else [],
           "tools_missing": [], "details_missing": [], "server": None}
    if pb is None:
        return out | {"automatable": False, "solvable": False, "need_human": True,
                      "reason": f"no MCP playbook for {action_code}"}
    try:
        served = autopilot.CLIENT.list_tools()
        out["server"] = getattr(autopilot.CLIENT, "server", None)
    except Exception as e:  # the actions server is down: nothing can be automated right now
        return out | {"automatable": False, "solvable": False, "need_human": True, "tools_missing": list(pb[1]),
                      "reason": f"Paytm actions MCP server unavailable ({type(e).__name__})"}
    out["tools_missing"] = [t for t in pb[1] if t not in served]
    out["details_missing"] = [k for k in pb[2] if getattr(details or Details(), k) in (None, "")]
    automatable = not out["tools_missing"]
    solvable = automatable and not out["details_missing"]
    reason = (f"MCP tools not available: {', '.join(out['tools_missing'])}" if not automatable
              else f"needs {', '.join(out['details_missing'])} for the MCP calls" if not solvable
              else f"solvable with {len(pb[1])} MCP tools ({action_code})")
    return out | {"automatable": automatable, "solvable": solvable, "need_human": not automatable, "reason": reason}


def questions(missing: list[str]) -> list[str]:
    return [QUESTIONS.get(k, f"Could you tell me the {k.replace('_', ' ')}?") for k in missing]
