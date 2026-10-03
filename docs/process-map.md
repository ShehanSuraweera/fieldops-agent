# Process map: CoolTech field service

CoolTech Services repairs commercial freezers and chillers for supermarkets across Sri Lanka. This is the
coordinator's process that the FieldOps agent runs, from a customer complaint to a booked technician.
How each step maps to code is in [agent-design.md](agent-design.md).

## End-to-end flow

```mermaid
flowchart TD
    A(["Customer reports a fault<br/>email or message"]) --> B{"Known customer?<br/>match sender email"}
    B -- no --> H1["Hand over to a coordinator<br/>no ticket opened"]
    B -- yes --> C["Open CRM ticket"]
    C --> D{"Exactly one asset<br/>matches the description?"}
    D -- "several or none" --> Q["Ask the customer one<br/>clarifying question"]
    Q --> W1(["Wait for the reply<br/>ticket: needs info"])
    D -- yes --> E["Assess the asset<br/>warranty, age, history,<br/>repeat failure in 90 days"]
    E --> F["Diagnose the likely fault<br/>from the fault catalog"]
    F --> G{"Diagnosis<br/>confident enough?"}
    G -- no --> I["Plan an inspection visit<br/>no parts ordered"]
    G -- yes --> J["List the parts and the skill<br/>the repair needs"]
    I --> P
    J --> P["Set priority and SLA deadline<br/>from the contract tier"]
    P --> K{"Parts in stock?"}
    K -- yes --> R["Reserve the parts"]
    K -- no --> V["Choose an approved vendor<br/>and draft a purchase order"]
    V --> M{"PO total above<br/>LKR 100,000?"}
    M -- no --> O["Send the PO"]
    M -- yes --> AP{{"Manager approval"}}
    AP -- approve --> O
    AP -- reject --> MP["Flag for manual procurement<br/>book an inspection visit"]
    R --> S
    O --> S["Book the best technician<br/>after the parts arrive"]
    MP --> S2["Book the earliest suitable<br/>technician for inspection"]
    S --> T["Update the ticket and confirm<br/>the visit to the customer"]
    S2 --> T2["Tell the customer about the<br/>parts delay and the inspection"]
    T --> Z(["Ticket: scheduled"])
    T2 --> Z2(["Ticket: needs manual procurement"])
```

## Who does what

```mermaid
flowchart LR
    subgraph Customer
        c1["Reports the fault"]
        c2["Answers a clarifying question"]
        c3["Receives the visit confirmation"]
    end
    subgraph Agent["FieldOps agent (the coordinator's job)"]
        a1["Reads the complaint"]
        a2["Identifies customer and asset"]
        a3["Diagnoses, prioritises, plans parts"]
        a4["Orders parts, books the technician"]
        a5["Updates the ticket, messages the customer"]
    end
    subgraph Manager
        m1["Approves or rejects large POs"]
    end
    subgraph Coordinator["Human coordinator"]
        h1["Handles unknown senders and anything the agent hands over"]
    end
    subgraph Technician
        t1["Carries out the visit"]
    end
    c1 --> a1 --> a2 --> a3 --> a4 --> a5 --> c3
    a2 -. "unclear asset" .-> c2 -.-> a2
    a4 -. "PO above threshold" .-> m1 -.-> a4
    a2 -. "unknown customer" .-> h1
    a4 --> t1
```

## Business rules

All of them live in `services/agent/app/rules.py`, and the thresholds are in `config/rules.yaml`.

| Rule | Logic |
| --- | --- |
| SLA by contract tier | Technician on site within: gold 4 h, silver 24 h, bronze 72 h |
| Priority | Gold with the asset down = P1; other gold and silver = P2; bronze = P3; a repeat failure within 90 days raises it one level |
| Warranty | If the asset is under warranty, the PO is a warranty claim and the ticket says so |
| Repeat failure | A repair within the last 90 days adds a supervisor-review note to the ticket |
| Vendor choice | Approved vendors only; the cheapest that delivers before the SLA deadline; if none can, the fastest, flagged as an SLA risk |
| PO approval | Above LKR 100,000 needs a manager; at or below is approved automatically |
| Technician choice | Required skill and the asset's region; among visits within the SLA, prefer whoever worked on this asset most recently, then the earliest slot, then the fewest jobs that day; if nobody fits the SLA, take the earliest slot and flag the risk |
| Low-confidence diagnosis | Confidence below 0.6 books an inspection visit and orders no parts |

## Outcomes

| Ticket status | Meaning |
| --- | --- |
| `scheduled` | Technician booked; parts reserved or ordered; customer told the visit time |
| `needs_info` | Waiting for the customer to say which unit |
| `awaiting_approval` | A large PO is waiting for a manager; nothing is booked yet |
| `needs_manual_procurement` | The manager rejected the PO; an inspection is booked and the customer told about the delay |
| `needs_human` | Unknown sender, or something the agent could not resolve; the reason is on the ticket |
