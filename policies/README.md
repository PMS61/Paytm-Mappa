# Policy set

Demo policies for the support desk. They are written by the team for this prototype and are **not Paytm's official
policies**; turnaround times and compensation follow RBI's public turnaround-time framework for failed digital
transactions. Replace these files with the real policy documents; nothing else needs to change.

Format, read by `mappa/policies.py`:

    # <document title>          one file = one document; `applies:` lists the ticket categories it covers
    applies: refund
    ## <topic>                  a page (ALPHA scores pages from their best clauses and their neighbours)
    ### <ID> · <clause title>   a clause: the unit that is retrieved and cited
    route: human                optional: this clause reserves the ticket for a human specialist ...
    trigger: <regex>            ... when this pattern matches the customer's words or the ticket details
    <clause text>
