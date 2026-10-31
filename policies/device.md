# Soundbox and device policy
applies: device

## Diagnosis

### P-DEV-01 · Remote diagnostics first
Every device complaint starts with remote diagnostics (speaker, SIM, network, battery, firmware). Resolve remotely
where possible: a network re-provision, a firmware push or a reset.

### P-DEV-02 · Telecom outages
If a regional telecom outage is active, do not replace the device; tell the merchant the outage status and that
payments are still collected and settled even when the device is silent.

## Replacement and rental

### P-DEV-03 · Replacement
A replacement is raised only when diagnostics show a hardware fault (speaker failure, swollen battery). Replacement is
free within the rental period. Field visits are scheduled within 48 hours in serviceable pin codes.

### P-DEV-04 · Rental waiver
If the device has been offline for more than 7 consecutive days because of a fault on our side, the rental for those
days is waived on request.

### P-DEV-05 · Lost or stolen devices
route: human
trigger: (stolen|chori|lost|kho gaya|missing|snatch)
A device reported lost or stolen is blocked immediately and the case goes to the device-risk team, which handles the
replacement and checks the device's location history.
