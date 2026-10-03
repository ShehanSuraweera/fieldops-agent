# FieldOps Agent eval report

- Run at: 2026-10-03T08:23:35Z (30 scenarios, seed 2026-10-01)
- LLM: gemini/gemini-3.5-flash-lite
- Tool transport: rest
- **Overall pass rate: 96.7%** (29/30 scenarios with every expected field correct)
- Field accuracy: 97.8%
- Tool-call errors: 1 of 465 tool calls
- Average latency per run: 3.68 s
- Average tokens per run: 1890

## Pass rate per field

| Field | Passed | Checked | Pass rate |
| --- | --- | --- | --- |
| approval_required | 13 | 14 | 92.9% |
| asset_id | 30 | 30 | 100.0% |
| fault_code | 22 | 23 | 95.7% |
| final_status | 29 | 30 | 96.7% |
| inspection | 3 | 3 | 100.0% |
| po_created | 30 | 30 | 100.0% |
| priority | 25 | 25 | 100.0% |
| sla_risk | 4 | 4 | 100.0% |
| technician_id | 22 | 23 | 95.7% |
| warranty_claim | 3 | 3 | 100.0% |

## Pass rate per group

| Group | Passed | Scenarios | Pass rate |
| --- | --- | --- | --- |
| in_stock | 8 | 8 | 100.0% |
| po_under_threshold | 5 | 5 | 100.0% |
| po_over_threshold | 4 | 5 | 80.0% |
| ambiguous_asset | 3 | 3 | 100.0% |
| low_confidence | 3 | 3 | 100.0% |
| warranty_claim | 3 | 3 | 100.0% |
| no_technician_in_sla | 2 | 2 | 100.0% |
| unknown_customer | 1 | 1 | 100.0% |

## Scenarios

| Scenario | Result | Status | Latency (s) | Tokens |
| --- | --- | --- | --- | --- |
| demo-freshmart-freezer3-repeat-compressor | pass | scheduled | 5.36 | 2117 |
| harbour-chiller1-drain-leak | pass | scheduled | 3.62 | 2072 |
| citygrocers-chiller2-door-gasket | pass | scheduled | 4.96 | 2022 |
| southernsuper-chiller1-temp-sensor | pass | scheduled | 3.59 | 2040 |
| coastline-chiller1-compressor | pass | scheduled | 3.59 | 1962 |
| lankasuper-freezer1-control-board | pass | scheduled | 3.57 | 2185 |
| southernsuper-chiller2-compressor | pass | scheduled | 3.56 | 2106 |
| kandycold-freezer1-defrost-heater | pass | scheduled | 3.57 | 2092 |
| citygrocers-chiller2-evap-fan-po | pass | scheduled | 4.08 | 2062 |
| harbour-chiller1-condenser-fan-po | pass | scheduled | 4.1 | 2138 |
| hillcountry-chiller1-start-relay-po | pass | scheduled | 4.1 | 2091 |
| rockhill-freezer2-door-gasket-po | pass | scheduled | 3.65 | 2103 |
| templeroad-freezer1-refrigerant-leak-po | pass | scheduled | 3.58 | 2267 |
| kandycold-freezer1-compressor-approve | pass | scheduled | 4.63 | 2035 |
| peradeniya-freezer1-kandycity-compressor-reject | **FAIL** | scheduled | 3.58 | 2228 |
| citygrocers-chiller2-control-board-approve | pass | scheduled | 4.65 | 2028 |
| southernsuper-chiller1-compressor-reject | pass | needs_manual_procurement | 4.11 | 2063 |
| freshmart-chiller1-control-board-approve | pass | scheduled | 4.1 | 1922 |
| peradeniya-freezer1-ambiguous | pass | needs_info | 2.55 | 813 |
| dambulla-chiller1-ambiguous | pass | needs_info | 2.55 | 816 |
| lankasuper-wattala-freezer-ambiguous | pass | needs_info | 2.55 | 808 |
| freshmart-chiller1-odd-noise | pass | scheduled | 4.09 | 1879 |
| harbour-freezer2-something-off | pass | scheduled | 3.56 | 2126 |
| southernsuper-chiller2-strange | pass | scheduled | 3.56 | 2039 |
| citygrocers-chiller1-warranty-evap-fan | pass | scheduled | 3.59 | 2168 |
| greenbasket-chiller1-warranty-condenser-fan | pass | scheduled | 3.57 | 2107 |
| kandycold-freezer2-warranty-compressor-approve | pass | scheduled | 4.11 | 2134 |
| kfoodcity-freezer2-no-tech-in-sla | pass | scheduled | 3.57 | 2182 |
| wayamba-freezer1-no-tech-in-sla | pass | scheduled | 3.65 | 2101 |
| unknown-sender | pass | needs_human | 0.52 | 0 |

## Failures

### peradeniya-freezer1-kandycity-compressor-reject  (run `run_e0a11ec05c80`)
- `fault_code`: expected `COMP_FAIL`, got `START_RELAY`
- `approval_required`: expected `True`, got `False`
- `technician_id`: expected `TECH-04`, got `TECH-05`
- `final_status`: expected `needs_manual_procurement`, got `scheduled`
