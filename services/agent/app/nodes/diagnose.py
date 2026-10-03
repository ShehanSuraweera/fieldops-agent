"""diagnose: the LLM picks a fault code from the catalog; Python derives parts and skill."""

from typing import Any

from app.nodes.common import Deps, NodeFailure, fail, require
from app.rules import needs_inspection
from app.state import Diagnosis, IntakeExtraction, TicketState


def _history_lines(history: list[dict[str, Any]]) -> str:
    if not history:
        return "No previous service records."
    return "\n".join(
        f"- {h['date']}: {h['fault_code']} - {h['notes']} (technician {h['technician_id']})" for h in history
    )


def diagnose(state: TicketState, deps: Deps) -> dict[str, Any]:
    asset = state["asset"]
    codes = require(deps.tools.list_fault_codes(model=asset["model"]), "list_fault_codes")
    if not codes:
        return fail(state, f"No fault codes are defined for model {asset['model']}.")
    by_code = {c.code: c for c in codes}
    catalog = [{"code": c.code, "name": c.name, "symptoms": c.symptoms} for c in codes]

    extraction = IntakeExtraction.model_validate(state["intake"])
    result = deps.llm.generate(
        "diagnose",
        {
            "asset_name": asset["name"],
            "model": asset["model"],
            "complaint": state["raw_text"],
            "symptoms": extraction.symptoms,
            "history": _history_lines(state.get("history", [])),
            "catalog": catalog,
        },
        Diagnosis,
        context={"allowed_codes": set(by_code)},
    )
    fault = by_code[result.fault_code]
    inspection = needs_inspection(result.confidence, deps.rules)

    parts: list[dict[str, Any]] = []
    if not inspection:
        for sku in fault.likely_parts:
            part = deps.tools.check_part_stock(sku=sku)
            if not part.ok:
                if part.error.code == "not_found":
                    continue
                raise NodeFailure(f"check_part_stock failed ({part.error.code}): {part.error.message}")
            if asset["model"] in part.data.compatible_models:
                parts.append({"sku": sku, "qty": 1})

    return {
        "diagnosis": {
            "fault_code": fault.code,
            "fault_name": fault.name,
            "confidence": result.confidence,
            "reasoning": result.reasoning,
            "inspection": inspection,
            "skill": fault.skill_required,
            "est_hours": deps.rules.inspection_hours if inspection else fault.est_hours,
            "parts": parts,
        }
    }
