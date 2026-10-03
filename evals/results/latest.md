# FieldOps Agent eval report

- Run at: 2026-10-03T06:46:02Z (30 scenarios, seed 2026-10-01)
- LLM: gemini/gemini-3.5-flash-lite
- **Overall pass rate: 100.0%** (30/30 scenarios with every expected field correct)
- Field accuracy: 100.0%
- Tool-call errors: 1 of 467 tool calls
- Average latency per run: 3.68 s
- Average tokens per run: 1896

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
| demo-freshmart-freezer3-repeat-compressor | pass | scheduled | 4.27 | 2094 |
| harbour-chiller1-drain-leak | pass | scheduled | 4.08 | 2099 |
| citygrocers-chiller2-door-gasket | pass | scheduled | 4.07 | 2004 |
| southernsuper-chiller1-temp-sensor | pass | scheduled | 4.08 | 2031 |
| coastline-chiller1-compressor | pass | scheduled | 4.07 | 1970 |
| lankasuper-freezer1-control-board | pass | scheduled | 3.59 | 2193 |
| southernsuper-chiller2-compressor | pass | scheduled | 3.56 | 2139 |
| kandycold-freezer1-defrost-heater | pass | scheduled | 3.57 | 2090 |
| citygrocers-chiller2-evap-fan-po | pass | scheduled | 4.09 | 2068 |
| harbour-chiller1-condenser-fan-po | pass | scheduled | 3.07 | 2157 |
| hillcountry-chiller1-start-relay-po | pass | scheduled | 4.08 | 2099 |
| rockhill-freezer2-door-gasket-po | pass | scheduled | 3.57 | 2095 |
| templeroad-freezer1-refrigerant-leak-po | pass | scheduled | 3.57 | 2264 |
| kandycold-freezer1-compressor-approve | pass | scheduled | 4.1 | 2042 |
| peradeniya-freezer1-kandycity-compressor-reject | pass | needs_manual_procurement | 4.1 | 2269 |
| citygrocers-chiller2-control-board-approve | pass | scheduled | 4.09 | 2010 |
| southernsuper-chiller1-compressor-reject | pass | needs_manual_procurement | 4.62 | 2060 |
| freshmart-chiller1-control-board-approve | pass | scheduled | 4.09 | 1938 |
| peradeniya-freezer1-ambiguous | pass | needs_info | 2.55 | 828 |
| dambulla-chiller1-ambiguous | pass | needs_info | 2.55 | 795 |
| lankasuper-wattala-freezer-ambiguous | pass | needs_info | 2.54 | 808 |
| freshmart-chiller1-odd-noise | pass | scheduled | 3.05 | 1896 |
| harbour-freezer2-something-off | pass | scheduled | 3.05 | 2146 |
| southernsuper-chiller2-strange | pass | scheduled | 3.56 | 2021 |
| citygrocers-chiller1-warranty-evap-fan | pass | scheduled | 3.57 | 2194 |
| greenbasket-chiller1-warranty-condenser-fan | pass | scheduled | 4.1 | 2120 |
| kandycold-freezer2-warranty-compressor-approve | pass | scheduled | 5.12 | 2121 |
| kfoodcity-freezer2-no-tech-in-sla | pass | scheduled | 4.57 | 2223 |
| wayamba-freezer1-no-tech-in-sla | pass | scheduled | 4.57 | 2111 |
| unknown-sender | pass | needs_human | 0.52 | 0 |
