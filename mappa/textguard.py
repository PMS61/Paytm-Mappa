"""Deterministic checks on untrusted customer text: secret stripping, PII redaction, injection and off-topic patterns.

Deterministic on purpose: nothing a customer types or says can switch these off. A hit can only push a ticket towards a
human, never away from one.
"""

import re

# Precise signals only: bare "RBI", "urgent" or "@ybl" flag honest merchants. Each pattern targets an attempt to steer the
# agent (instructions to the model, rule changes, verification waivers), in English, Hinglish and Hindi.
INJECTION = [
    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(instructions?|rules?|sop|policy|polic(y|ies)|guidelines?|prompt)",
    r"(previous|prior|earlier|above) (instructions|rules)", r"system (notice|override|prompt|message)", r"\bassistant:",
    r"<!--", r"<information>", r"before you can solve the task",
    r"policy (updated|update|change)", r"\b(admin|developer) (mode|override)",
    r"\b(skip|bypass|disable|waive|without|no need (for|of)|ignore)\b.{0,25}\b(penny[- ]?drop|verification|verify|kyc|otp|security|checks?|escalat\w*)",
    r"(verification|penny[- ]?drop|kyc|otp)\b.{0,20}\b(already (done|completed|verified)|not (needed|required))",
    r"\b(forget|ignore|disregard)\b.{0,20}\b(everything|all|the above|above|before|previous|preceding|prior)\b",
    r"\bvergiss\b|ignorieren sie|abweichend (zu|von) (den )?vorherigen|\bolvid[ae]\b", r"repeat after me|\byou are now\b",
    r"(print|reveal|show|repeat)\b.{0,20}\b(your|the above|initial|system) (prompt|instructions)",
    r"\bwhitelist", r"must not escalate|do not escalate|don'?t escalate",
    r"\bbina\b.{0,20}\b(otp|verification|verify|penny|kyc|check|escalation)", r"(zaroorat|jarurat|zarurat) nahi",
    r"(instructions?|niyam|rules?)\b.{0,15}\b(ignore|bhool)", r"अनदेखा", r"नज़रअंदाज़|नजरअंदाज", r"बायपास",
    r"बिना\s*\S*\s*(OTP|सत्यापन|वेरिफिकेशन|पेनी|केवाईसी|जांच|जाँच)",
]
_INJ = [re.compile(p, re.I) for p in INJECTION]

OFF_TOPIC = re.compile(
    r"python|javascript|c\+\+|code (likh|bana|likho|likh do)|write (me )?(a |some )?(code|program)|programming|"
    r"fibonacci|\bsql\b|essay|poem|poetry|kavita|shayari|kahani|chutkula|recipe|homework|capital of|"
    r"chatgpt|which model|system prompt|"
    r"पाइथन|पायथन|कोड (लिख|बना)|प्रोग्रामिंग|कविता|कहानी|चुटकुला|निबंध|रेसिपी|प्रॉम्प्ट", re.I)

HUMAN_ASK = re.compile(r"\b(talk|speak) to (a )?(human|person|someone|agent|executive)|real person|"
                       r"insaan se|kisi (insaan|aadmi|banda)|agent se baat|executive se baat|इंसान से", re.I)

SECRET = re.compile(r"\b(otp|o\.?t\.?p|pin|cvv|password|passcode|mpin|upi pin)\b|ओटीपी|पिन|पासवर्ड|सीवीवी", re.I)
DIGITS = re.compile(r"\d[\d\s-]{2,}\d|\b\d{4,}\b")


def injection_count(text: str) -> int:
    return sum(bool(rx.search(text)) for rx in _INJ)


def guard_hits(text: str) -> list[str]:
    hits = []
    if injection_count(text):
        hits.append("injection pattern")
    if OFF_TOPIC.search(text):
        hits.append("off-topic request")
    return hits


def has_secret(text: str) -> bool:
    """The customer is giving out an OTP / PIN / CVV / password: never store it."""
    return bool(SECRET.search(text) and DIGITS.search(text))


# ---------------- India-specific PII ----------------
# Aadhaar is checked with Verhoeff (cuts false positives on 12-digit order IDs), cards with Luhn.

_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def _verhoeff(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def _luhn(num: str) -> bool:
    s, alt = 0, False
    for ch in reversed(num):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        s, alt = s + d, not alt
    return s % 10 == 0


PII = [
    ("AADHAAR", re.compile(r"(?<!\d)([2-9]\d{3})[ -]?(\d{4})[ -]?(\d{4})(?!\d)"), lambda m: _verhoeff("".join(m.groups()))),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){15,16}(?!\d)"), lambda m: _luhn(re.sub(r"\D", "", m.group()))),
    ("PAN", re.compile(r"\b[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]\b"), None),
    ("UPI", re.compile(r"\b[\w.\-]{2,64}@(?:ybl|okhdfcbank|okicici|oksbi|okaxis|paytm|ptyes|ptaxis|upi|ibl|axl|apl)\b", re.I), None),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[ -]?)?[6-9]\d{9}(?!\d)"), None),
    ("ACCOUNT", re.compile(r"(?i)(?:a/?c|account|khata)\s*(?:no\.?|number|#)?\s*[:\-]?\s*(\d{9,18})"), None),
]


def redact(text: str) -> tuple[str, int]:
    """(text with PII replaced by <LABEL>, number of replacements)."""
    n = 0
    for label, rx, valid in PII:
        def sub(m, label=label, valid=valid):
            nonlocal n
            if valid and not valid(m):
                return m.group()
            n += 1
            return f"<{label}>"
        text = rx.sub(sub, text)
    return text, n


def sanitise(text: str) -> tuple[str, bool, int]:
    """(stored text, secret removed?, PII count). Applied to every free-text field before a ticket is stored."""
    if has_secret(text):
        return "[secret removed]", True, 0
    t, n = redact(text)
    return t, False, n
