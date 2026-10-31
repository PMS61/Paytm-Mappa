# Refund policy
applies: refund

## Refund timelines

### P-REF-01 · Refund turnaround
UPI refunds reach the customer within 1 to 3 working days of initiation; card refunds within 5 to 7 working days, as
the card network and issuing bank process them. A refund pending at the bank within this window needs no action
beyond sharing the expected date.

### P-REF-02 · Failed refunds
A refund that failed at the bank is re-initiated to the original payment source once. If it fails again the ticket goes
to the payments operations team.

### P-REF-03 · Refund already successful
If the refund shows as successful, share the bank reference number (RRN) with the merchant so the customer can
trace it with their bank. Never refund again on the customer's word alone: that pays twice.

## New refunds

### P-REF-04 · Refund to the original source only
route: human
trigger: (different|other|new|another|alag|dusre|naye).{0,25}(upi|vpa|account|number|card)|(upi|vpa) (id )?(change|badal)
Refunds go only to the original payment source of the transaction. A request to refund to a different UPI ID, account
or card is a common social-engineering pattern and must be reviewed by a specialist.

### P-REF-05 · Refund amount limit
A refund can never exceed the amount of the original transaction, net of earlier refunds on it. Partial refunds are
allowed.

### P-REF-06 · Disputes and chargebacks
route: human
trigger: chargeback|dispute|bank complaint|fraud complaint
No refund is issued on a transaction with an open chargeback or dispute: the dispute process decides the outcome, and
a refund on top of it would pay the customer twice.
