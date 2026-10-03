# FieldOps Agent eval report

- Run at: 2026-10-03T08:31:23Z (30 scenarios, seed 2026-10-01)
- LLM: gemini/gemini-3.5-flash-lite
- Tool transport: mcp
- **Overall pass rate: 100.0%** (30/30 scenarios with every expected field correct)
- Field accuracy: 100.0%
- Tool-call errors: 1 of 467 tool calls
- Average latency per run: 3.83 s
- Average tokens per run: 1895

## Pass rate per field

| Field | Passed | Checked | Pass rate |
| --- | --- | --- | --- |
| approval_required | 14 | 14 | 100.0% |
| asset_id | 30 | 30 | 100.0% |
| fault_code | 23 | 23 | 100.0% |
| final_status | 30 | 30 | 100.0% |
| inspection | 3 | 3 | 100.0% |
| po_created | 30 | 30 | 100.0% |
| priority | 25 | 25 | 100.0% |
| sla_risk | 4 | 4 | 100.0% |
| technician_id | 23 | 23 | 100.0% |
| warranty_claim | 3 | 3 | 100.0% |

## Pass rate per group

| Group | Passed | Scenarios | Pass rate |
| --- | --- | --- | --- |
| in_stock | 8 | 8 | 100.0% |
| po_under_threshold | 5 | 5 | 100.0% |
| po_over_threshold | 5 | 5 | 100.0% |
| ambiguous_asset | 3 | 3 | 100.0% |
| low_confidence | 3 | 3 | 100.0% |
| warranty_claim | 3 | 3 | 100.0% |
| no_technician_in_sla | 2 | 2 | 100.0% |
| unknown_customer | 1 | 1 | 100.0% |

## Scenarios

| Scenario | Result | Status | Latency (s) | Tokens |
| --- | --- | --- | --- | --- |
| demo-freshmart-freezer3-repeat-compressor | pass | scheduled | 5.54 | 2103 |
| harbour-chiller1-drain-leak | pass | scheduled | 4.07 | 2107 |
| citygrocers-chiller2-door-gasket | pass | scheduled | 4.1 | 2006 |
| southernsuper-chiller1-temp-sensor | pass | scheduled | 4.09 | 2017 |
| coastline-chiller1-compressor | pass | scheduled | 3.57 | 1944 |
| lankasuper-freezer1-control-board | pass | scheduled | 3.57 | 2177 |
| southernsuper-chiller2-compressor | pass | scheduled | 3.58 | 2116 |
| kandycold-freezer1-defrost-heater | pass | scheduled | 4.07 | 2098 |
| citygrocers-chiller2-evap-fan-po | pass | scheduled | 3.58 | 2054 |
| harbour-chiller1-condenser-fan-po | pass | scheduled | 3.57 | 2147 |
| hillcountry-chiller1-start-relay-po | pass | scheduled | 4.08 | 2113 |
| rockhill-freezer2-door-gasket-po | pass | scheduled | 4.09 | 2097 |
| templeroad-freezer1-refrigerant-leak-po | pass | scheduled | 3.57 | 2252 |
| kandycold-freezer1-compressor-approve | pass | scheduled | 4.11 | 2033 |
| peradeniya-freezer1-kandycity-compressor-reject | pass | needs_manual_procurement | 4.62 | 2277 |
| citygrocers-chiller2-control-board-approve | pass | scheduled | 4.1 | 2005 |
| southernsuper-chiller1-compressor-reject | pass | needs_manual_procurement | 5.13 | 2100 |
| freshmart-chiller1-control-board-approve | pass | scheduled | 4.11 | 1933 |
| peradeniya-freezer1-ambiguous | pass | needs_info | 2.04 | 813 |
| dambulla-chiller1-ambiguous | pass | needs_info | 2.04 | 796 |
| lankasuper-wattala-freezer-ambiguous | pass | needs_info | 2.55 | 810 |
| freshmart-chiller1-odd-noise | pass | scheduled | 3.56 | 1902 |
| harbour-freezer2-something-off | pass | scheduled | 3.57 | 2137 |
| southernsuper-chiller2-strange | pass | scheduled | 4.07 | 2036 |
| citygrocers-chiller1-warranty-evap-fan | pass | scheduled | 3.57 | 2192 |
| greenbasket-chiller1-warranty-condenser-fan | pass | scheduled | 4.09 | 2108 |
| kandycold-freezer2-warranty-compressor-approve | pass | scheduled | 4.1 | 2163 |
| kfoodcity-freezer2-no-tech-in-sla | pass | scheduled | 7.14 | 2218 |
| wayamba-freezer1-no-tech-in-sla | pass | scheduled | 4.1 | 2102 |
| unknown-sender | pass | needs_human | 0.52 | 0 |
