# FieldOps Agent eval report

- Run at: 2026-10-03T08:53:11Z (30 scenarios, seed 2026-10-01)
- LLM: gemini/gemini-3.5-flash-lite
- Tool transport: rest · chaos mode on ({'enabled': True, 'error_rate': 0.1, 'max_latency_ms': 800})
- **Overall pass rate: 63.3%** (19/30 scenarios with every expected field correct)
- Field accuracy: 77.3%
- Tool-call errors: 12 of 383 tool calls
- Average latency per run: 9.33 s
- Average tokens per run: 1641

## Pass rate per field

| Field | Passed | Checked | Pass rate |
| --- | --- | --- | --- |
| approval_required | 10 | 14 | 71.4% |
| asset_id | 26 | 30 | 86.7% |
| fault_code | 18 | 23 | 78.3% |
| final_status | 19 | 30 | 63.3% |
| inspection | 3 | 3 | 100.0% |
| po_created | 26 | 30 | 86.7% |
| priority | 21 | 25 | 84.0% |
| sla_risk | 3 | 4 | 75.0% |
| technician_id | 15 | 23 | 65.2% |
| warranty_claim | 2 | 3 | 66.7% |

## Pass rate per group

| Group | Passed | Scenarios | Pass rate |
| --- | --- | --- | --- |
| in_stock | 5 | 8 | 62.5% |
| po_under_threshold | 4 | 5 | 80.0% |
| po_over_threshold | 1 | 5 | 20.0% |
| ambiguous_asset | 2 | 3 | 66.7% |
| low_confidence | 2 | 3 | 66.7% |
| warranty_claim | 2 | 3 | 66.7% |
| no_technician_in_sla | 2 | 2 | 100.0% |
| unknown_customer | 1 | 1 | 100.0% |

## Scenarios

| Scenario | Result | Status | Latency (s) | Tokens |
| --- | --- | --- | --- | --- |
| demo-freshmart-freezer3-repeat-compressor | **FAIL** | needs_human | 2.13 | 540 |
| harbour-chiller1-drain-leak | **FAIL** | needs_human | 8.65 | 1699 |
| citygrocers-chiller2-door-gasket | pass | scheduled | 11.19 | 2004 |
| southernsuper-chiller1-temp-sensor | pass | scheduled | 8.13 | 2036 |
| coastline-chiller1-compressor | pass | scheduled | 11.71 | 1956 |
| lankasuper-freezer1-control-board | pass | scheduled | 11.19 | 2183 |
| southernsuper-chiller2-compressor | **FAIL** | needs_human | 2.04 | 584 |
| kandycold-freezer1-defrost-heater | pass | scheduled | 11.2 | 2098 |
| citygrocers-chiller2-evap-fan-po | pass | scheduled | 11.18 | 2070 |
| harbour-chiller1-condenser-fan-po | pass | scheduled | 13.26 | 2157 |
| hillcountry-chiller1-start-relay-po | pass | scheduled | 10.69 | 2120 |
| rockhill-freezer2-door-gasket-po | **FAIL** | needs_human | 11.2 | 2097 |
| templeroad-freezer1-refrigerant-leak-po | pass | scheduled | 10.67 | 2263 |
| kandycold-freezer1-compressor-approve | **FAIL** | needs_human | 12.19 | 2055 |
| peradeniya-freezer1-kandycity-compressor-reject | **FAIL** | needs_human | 12.19 | 1804 |
| citygrocers-chiller2-control-board-approve | pass | scheduled | 13.28 | 2004 |
| southernsuper-chiller1-compressor-reject | **FAIL** | needs_human | 11.19 | 1652 |
| freshmart-chiller1-control-board-approve | **FAIL** | needs_human | 2.55 | 556 |
| peradeniya-freezer1-ambiguous | pass | needs_info | 5.09 | 795 |
| dambulla-chiller1-ambiguous | pass | needs_info | 4.08 | 816 |
| lankasuper-wattala-freezer-ambiguous | **FAIL** | needs_human | 2.04 | 533 |
| freshmart-chiller1-odd-noise | pass | scheduled | 12.19 | 1899 |
| harbour-freezer2-something-off | pass | scheduled | 10.68 | 2112 |
| southernsuper-chiller2-strange | **FAIL** | needs_human | 10.69 | 2030 |
| citygrocers-chiller1-warranty-evap-fan | pass | scheduled | 12.72 | 2172 |
| greenbasket-chiller1-warranty-condenser-fan | **FAIL** | needs_human | 2.04 | 598 |
| kandycold-freezer2-warranty-compressor-approve | pass | scheduled | 18.81 | 2135 |
| kfoodcity-freezer2-no-tech-in-sla | pass | scheduled | 14.75 | 2182 |
| wayamba-freezer1-no-tech-in-sla | pass | scheduled | 11.7 | 2094 |
| unknown-sender | pass | needs_human | 0.52 | 0 |

## Failures

### demo-freshmart-freezer3-repeat-compressor  (run `run_63e9fcfdb886`)
- `asset_id`: expected `FRZ-1043`, got `None`
- `fault_code`: expected `COMP_FAIL`, got `None`
- `priority`: expected `P1`, got `None`
- `technician_id`: expected `TECH-02`, got `None`
- `final_status`: expected `scheduled`, got `needs_human`
- error: intake: create_ticket failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### harbour-chiller1-drain-leak  (run `run_78e006a9f4b0`)
- `technician_id`: expected `TECH-02`, got `None`
- `final_status`: expected `scheduled`, got `needs_human`
- error: schedule: create_work_order failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### southernsuper-chiller2-compressor  (run `run_e59c1d84e94b`)
- `asset_id`: expected `CHL-1027`, got `None`
- `fault_code`: expected `COMP_FAIL`, got `None`
- `priority`: expected `P2`, got `None`
- `technician_id`: expected `TECH-06`, got `None`
- `final_status`: expected `scheduled`, got `needs_human`
- error: intake: create_ticket failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### rockhill-freezer2-door-gasket-po  (run `run_8e87a5dbf088`)
- `final_status`: expected `scheduled`, got `needs_human`
- error: finalize: send_customer_message failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### kandycold-freezer1-compressor-approve  (run `run_31705aed38a6`)
- `fault_code`: expected `COMP_FAIL`, got `START_RELAY`
- `approval_required`: expected `True`, got `False`
- `technician_id`: expected `TECH-04`, got `TECH-05`
- `final_status`: expected `scheduled`, got `needs_human`
- error: finalize: send_customer_message failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### peradeniya-freezer1-kandycity-compressor-reject  (run `run_531a59554cec`)
- `po_created`: expected `True`, got `False`
- `approval_required`: expected `True`, got `False`
- `technician_id`: expected `TECH-04`, got `None`
- `final_status`: expected `needs_manual_procurement`, got `needs_human`
- error: procure: create_purchase_order failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### southernsuper-chiller1-compressor-reject  (run `run_e01ed58cfba9`)
- `po_created`: expected `True`, got `False`
- `approval_required`: expected `True`, got `False`
- `technician_id`: expected `TECH-06`, got `None`
- `final_status`: expected `needs_manual_procurement`, got `needs_human`
- error: procure: create_purchase_order failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### freshmart-chiller1-control-board-approve  (run `run_06d224f18222`)
- `asset_id`: expected `CHL-1044`, got `None`
- `fault_code`: expected `CTRL_BOARD`, got `None`
- `priority`: expected `P1`, got `None`
- `po_created`: expected `True`, got `False`
- `approval_required`: expected `True`, got `False`
- `technician_id`: expected `TECH-01`, got `None`
- `final_status`: expected `scheduled`, got `needs_human`
- `sla_risk`: expected `True`, got `False`
- error: intake: create_ticket failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### lankasuper-wattala-freezer-ambiguous  (run `run_7db745225e7b`)
- `final_status`: expected `needs_info`, got `needs_human`
- error: intake: create_ticket failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### southernsuper-chiller2-strange  (run `run_ef8e51caaa9b`)
- `final_status`: expected `scheduled`, got `needs_human`
- error: finalize: send_customer_message failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable

### greenbasket-chiller1-warranty-condenser-fan  (run `run_587180da1066`)
- `asset_id`: expected `CHL-1004`, got `None`
- `fault_code`: expected `COND_FAN`, got `None`
- `priority`: expected `P2`, got `None`
- `po_created`: expected `True`, got `False`
- `warranty_claim`: expected `True`, got `None`
- `technician_id`: expected `TECH-02`, got `None`
- `final_status`: expected `scheduled`, got `needs_human`
- error: intake: create_ticket failed (chaos_injected): Injected failure (CHAOS_MODE): service temporarily unavailable
