"""Plan the native Production OS workflow and attach relevant local skills.

Logical roles remain Python handlers. Detected CLI harnesses are not invoked unless
a dedicated, tested adapter exists; discovery is not treated as authentication.
"""
from __future__ import annotations

from agents import AGENTS, WORKFLOW_STEPS
import mcp_registry
import runtime_registry
import skill_registry

AGENT_NAMES = {item[0]: item[1] for item in AGENTS}
TEXT_ADAPTERS = {"codex", "opencode"}


def _execution_mode(agent_id: str, workflow: str, runtime_status: dict) -> dict:
    tools = runtime_status.get("tools", {})
    if agent_id == "content":
        try:
            import engine_manager
            providers = engine_manager.available_providers(
                ctx={"workflow": workflow, "active_agent": agent_id}
            )
        except Exception:
            providers = []
        return {
            "mode": "llm_router" if providers else "deterministic_template",
            "available_text_providers": providers,
            "note": "Le moteur de texte existant reste responsable de l'exécution.",
        }
    if agent_id == "video":
        try:
            from agents import stickman_status
            stickman = stickman_status()
        except Exception:
            stickman = {"installed": False, "runtime_ready": False}
        return {
            "mode": "stickman_handoff" if stickman.get("runtime_ready") else "video_preparation",
            "ready": bool(stickman.get("runtime_ready")),
            "note": "Le rendu final Stickman reste un handoff contrôlé ; aucune publication automatique.",
        }
    if agent_id == "creative":
        try:
            import comfyui_bridge
            comfy = comfyui_bridge.get_status()
        except Exception:
            comfy = {"ready": False}
        return {
            "mode": "local_svg",
            "comfyui_available": bool(comfy.get("ready")),
            "note": "Le handler créatif existant produit une maquette SVG ; ComfyUI n'est pas encore branché à ce rôle.",
        }
    if agent_id == "clipping":
        ready = bool(tools.get("ffmpeg", {}).get("installed") and tools.get("ffprobe", {}).get("installed"))
        return {"mode": "ffmpeg_local" if ready else "clipping_blocked", "ready": ready}
    return {"mode": "native_python", "ready": True}


def build_plan(workflow: str, brief: str = "", refresh: bool = False) -> dict:
    if workflow not in WORKFLOW_STEPS:
        raise ValueError("Workflow Production OS inconnu")
    runtimes = runtime_registry.get_status(refresh=refresh)
    skill_state = skill_registry.get_status(refresh=refresh)
    mcp_state = mcp_registry.get_status(refresh=refresh)
    steps = []
    for agent_id in WORKFLOW_STEPS[workflow]:
        selected = skill_registry.select_for_agent(
            agent_id, brief, limit=3, skills=skill_state.get("skills", [])
        )
        steps.append({
            "agent_id": agent_id,
            "agent_name": AGENT_NAMES.get(agent_id, agent_id),
            "skills": selected,
            "skill_count": len(selected),
            "execution": _execution_mode(agent_id, workflow, runtimes),
        })
    harnesses = []
    for name, row in runtimes.get("agents", {}).items():
        harnesses.append({
            "id": name, "label": row.get("label"),
            "installed": row.get("installed", False),
            "adapter": row.get("adapter"),
            "adapter_ready": row.get("adapter_ready", False),
            "status": row.get("status"),
        })
    return {
        "workflow": workflow,
        "brief": str(brief or "")[:1200],
        "steps": steps,
        "skills": {
            "installed_unique": skill_state.get("skill_count", 0),
            "files_detected": skill_state.get("file_count", 0),
            "missing_core": skill_state.get("missing_core_skills", []),
            "automatic_selection": True,
        },
        "runtimes": runtimes.get("summary", {}),
        "mcp": {
            "server_count": mcp_state.get("server_count", 0),
            "names": mcp_state.get("names", []),
            "note": mcp_state.get("note"),
        },
        "harnesses": harnesses,
        "safety": {
            "paid_calls_allowed_by_this_plan": False,
            "external_publication": False,
            "account_mutations": False,
            "skill_execution": False,
            "note": "Ce plan prépare la délégation. Il ne lance pas les CLI externes et ne confère aucune permission.",
        },
    }


def skill_assignments(workflow: str, brief: str = "") -> dict:
    plan = build_plan(workflow, brief)
    return {
        step["agent_id"]: step["skills"]
        for step in plan["steps"]
    }
