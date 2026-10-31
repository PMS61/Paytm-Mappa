# Merchant settlement policy
applies: settlement

## Settlement timelines

### P-SET-01 · Settlement cycle
Merchant collections are settled to the merchant's primary verified bank account on T+1 working day. Instant
settlement, where enabled, is credited within 30 minutes and is subject to the instant-settlement fee.

### P-SET-02 · Failed settlement
A settlement that fails at the beneficiary bank (account closed, invalid IFSC, bank timeout) is held and retried
automatically on the next cycle once the cause is fixed. A support resolver may retry a failed settlement once, and
only to the verified account on file.

### P-SET-03 · Bank outage
If the merchant's bank is reported down, do not retry; the settlement is retried automatically when the bank is back.
Tell the merchant the expected time and do not promise a credit time.

## Bank account changes

### P-SET-04 · New bank account verification
A newly added bank account must pass penny-drop verification, and the account holder name must match the merchant's
registered business or owner name, before any settlement is sent to it.

### P-SET-05 · Account change after a new-device login
route: human
trigger: (new|naya|naye|nayi|another|dusre|doosre|other) (phone|mobile|device).{0,40}(login|logged|account)|(login|logged).{0,30}(new|naya|naye|nayi|another|dusre|other) (phone|mobile|device)
A bank account added or changed within 72 hours of a login from a new device, or a password reset, is a possible
account takeover. A risk specialist must review it; no settlement is sent to the new account until then.

### P-SET-06 · Third-party accounts
route: human
trigger: (someone else|another person|third.?party|kisi aur|dusre ke|relative|brother|bhai|friend|dost).{0,30}(account|khata|upi)
Settlements are paid only to an account in the merchant's own name. A request to pay a settlement to anyone else's
account is refused and reviewed by a specialist.

## Holds

### P-SET-07 · KYC and legal holds
route: human
trigger: (police|court|legal|freeze|frozen|lien|cyber cell|law.?enforcement)
Settlements on hold because KYC is pending are released automatically when KYC is approved. Holds placed on a
law-enforcement or court instruction can only be handled by the nodal officer team; support never releases them.
