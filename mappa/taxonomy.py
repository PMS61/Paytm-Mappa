"""Closed sets the clarity module maps every ticket onto: categories, required details, scenarios and action items.

A scenario is picked by the first matching rule within its category (rules are ordered, the last one is the default),
so the same ticket always gets the same scenario and action item.
"""

import re

from .schema import Action, Details

CATEGORIES = {
    "settlement": "Settlement / payout not received in the merchant's bank account",
    "refund": "Refund to a customer: not received, or a new refund to make",
    "device": "Soundbox / payment device not working",
    "kyc": "KYC documents or verification",
    "payment": "Payment failed or money debited but not received",
    "other": "Anything else",
}

# details a resolver needs before acting, per category
REQUIRED = {
    "settlement": ["amount", "bank_name", "account_changed"],
    "refund": ["amount", "refund_kind"],
    "device": ["device_issue", "offline_days"],
    "kyc": ["kyc_issue"],
    "payment": ["amount", "txn_id"],
    "other": [],
}

# categories the Jev engine was trained on; any other category always goes to a human
AUTOMATABLE = {"settlement", "refund", "device", "kyc"}

# a money-moving action at or above this amount always goes to a human, whatever the confidence
AUTO_CEILING_INR = 50_000


def _a(code, summary, steps, money=False):
    return Action(code=code, summary=summary, steps=steps, money_moving=money)


def _has(rx: str, *texts) -> bool:
    return any(t and re.search(rx, t, re.I) for t in texts)


# (scenario key, title, rule(details, text) -> bool, action)
SCENARIOS = {
    "settlement": [
        ("settlement_after_account_change", "Settlement not received after the bank account was changed",
         lambda d, t: d.account_changed is True,
         _a("VERIFY_ACCOUNT_THEN_SETTLE", "Verify the new bank account before re-sending the settlement",
            ["Check that the new account is verified (penny drop) and the holder name matches the merchant",
             "Check for a recent new-device login or password reset",
             "Re-send the settlement only to a verified account in the merchant's name"], True)),
        ("settlement_not_received", "Settlement not received",
         lambda d, t: True,
         _a("TRACE_AND_RETRY_SETTLEMENT", "Trace the settlement and retry it to the account on file",
            ["Look up the settlement status and failure code", "Check the bank's status (a bank outage means wait, not retry)",
             "Retry the settlement to the verified account on file, once"], True)),
    ],
    "refund": [
        ("refund_exceeds_transaction", "Refund requested for more than the transaction amount",
         lambda d, t: d.refund_amount is not None and d.amount is not None and d.refund_amount > d.amount,
         _a("REVIEW_REFUND_AMOUNT", "Do not refund; confirm the amount with the merchant",
            ["Compare the requested refund with the transaction amount", "Refund at most the transaction amount, after confirmation"])),
        ("refund_new_request", "New refund to a customer",
         lambda d, t: d.refund_kind == "new request",
         _a("INITIATE_REFUND", "Refund the customer for the transaction",
            ["Check the transaction succeeded and has no open chargeback", "Check no refund already exists for it",
             "Refund to the original payment source only"], True)),
        ("refund_not_received", "Customer has not received a refund",
         lambda d, t: True,
         _a("TRACE_REFUND", "Trace the refund and tell the customer its status",
            ["Look up the refund status", "If pending within the bank's turnaround time, share the expected date",
             "If failed at the bank, re-initiate to the original source"])),
    ],
    "device": [
        ("device_no_sound", "Soundbox not announcing payments / no sound",
         lambda d, t: _has(r"sound|speaker|awaaz|aawaz|announce|volume|आवाज़|आवाज", d.device_issue),
         _a("DIAGNOSE_AUDIO", "Run remote diagnostics on the speaker",
            ["Run remote diagnostics", "If the speaker fails diagnostics, raise a replacement", "Else push a reset and test payment"])),
        ("device_offline", "Soundbox offline / no network",
         lambda d, t: _has(r"offline|network|signal|sim|internet|connect|नेटवर्क", d.device_issue),
         _a("DIAGNOSE_CONNECTIVITY", "Check the SIM / network and bring the device back online",
            ["Run remote diagnostics", "Check for a regional telecom outage", "Re-provision the SIM or update firmware"])),
        ("device_other", "Soundbox problem",
         lambda d, t: True,
         _a("DIAGNOSE_DEVICE", "Run remote diagnostics on the device", ["Run remote diagnostics", "Act on the failing component"])),
    ],
    "kyc": [
        ("kyc_rejected", "KYC rejected",
         lambda d, t: _has(r"reject|mismatch|match nahi|galat|wrong", d.kyc_issue),
         _a("REVIEW_KYC_REJECTION", "Explain the rejection reason and the document that fixes it",
            ["Look up the rejection reason", "Ask for the one document that resolves it"])),
        ("kyc_document_pending", "KYC pending: document needed",
         lambda d, t: True,
         _a("REQUEST_KYC_DOCUMENT", "Tell the merchant which document is pending",
            ["Look up the pending documents", "Send the upload link for the missing document"])),
    ],
    "payment": [
        ("payment_debited_not_received", "Money debited but payment not received",
         lambda d, t: True,
         _a("TRACE_PAYMENT", "Trace the payment by its transaction ID",
            ["Look up the transaction", "If it failed after debit, confirm the auto-reversal date",
             "If it succeeded, share the proof with both parties"])),
    ],
    "other": [
        ("general", "General query", lambda d, t: True,
         _a("MANUAL_REVIEW", "A support executive reads and classifies the ticket", ["Read the ticket", "Re-file it under the right category"])),
    ],
}

KEYWORDS = {
    "settlement": r"settle|payout|सेटलमेंट|भुगतान खाते",
    "refund": r"refund|wapas|vapas|रिफंड|वापस",
    "device": r"sound\s?box|speaker|device|machine|साउंडबॉक्स|स्पीकर",
    "kyc": r"\bkyc\b|केवाईसी|re-?kyc|verification document",
    "payment": r"debited|deducted|kat gaya|cut from my|payment (failed|pending|stuck)|transaction failed|पैसे कट",
}


def keyword_category(text: str) -> str | None:
    hits = [c for c, rx in KEYWORDS.items() if re.search(rx, text, re.I)]
    return hits[0] if len(hits) == 1 else None


def scenario_for(category: str, d: Details, text: str):
    for key, title, rule, action in SCENARIOS[category]:
        if rule(d, text):
            return key, title, action


def missing(category: str, d: Details) -> list[str]:
    return [k for k in REQUIRED[category] if getattr(d, k) in (None, "")]


def describe() -> dict:
    """The whole taxonomy as JSON, for forms and integrations."""
    return {"categories": CATEGORIES, "required": REQUIRED, "automatable": sorted(AUTOMATABLE),
            "auto_ceiling_inr": AUTO_CEILING_INR,
            "scenarios": {c: [{"key": k, "title": t, "action": a.model_dump()} for k, t, _, a in rows]
                          for c, rows in SCENARIOS.items()},
            "details": {k: str(f.annotation) for k, f in Details.model_fields.items()}}
