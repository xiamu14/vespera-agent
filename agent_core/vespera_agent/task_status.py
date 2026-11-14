"""Compact native delegation events for the desktop status panel."""


def task_status(record):
    return {
        "id": record.id, "role": record.agent_name, "prompt": record.prompt.splitlines()[0][:160],
        "status": "waiting" if record.pending_question else record.status,
        "outcome": record.outcome,
    }


def task_event(event):
    result = task_status(event.task)
    part = getattr(event.event, "part", None)
    if part is None or part.part_kind not in {"tool-call", "tool-return", "retry-prompt"}:
        return result
    result["tool"] = {
        "id": part.tool_call_id,
        "name": part.tool_name,
        "status": ("running" if part.part_kind == "tool-call" else
                   "done" if getattr(part, "outcome", None) == "success" else "failed"),
    }
    return result
