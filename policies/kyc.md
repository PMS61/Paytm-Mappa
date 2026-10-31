# KYC policy
applies: kyc

## Documents

### P-KYC-01 · Accepted documents
Merchant KYC needs a PAN (individual or business), a proof of address (Aadhaar, voter ID, passport, driving licence)
and, for registered businesses, a business proof (GST certificate, Udyam registration, shop licence).

### P-KYC-02 · Pending documents
When KYC is pending, tell the merchant exactly which document is missing and send the upload link in the app. Never
ask for documents over the phone or chat, and never accept them as images in a ticket.

### P-KYC-03 · Name mismatch
A rejection for name mismatch is fixed by uploading a document whose name matches the PAN, or by updating the PAN
name. Explain the reason; do not override a rejection.

## Periodic re-KYC

### P-KYC-04 · Re-KYC
Re-KYC is due periodically according to the merchant's risk category. Collections continue during the grace period;
settlements are held after it lapses.

### P-KYC-05 · Override requests
route: human
trigger: (skip|bypass|without|bina|override|approve).{0,25}(kyc|document|verification)
Support cannot approve, skip or override KYC. A request to do so is recorded and reviewed by the compliance team.
