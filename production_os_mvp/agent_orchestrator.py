"""Plan native workflows and discover optional specialist capabilities.

Logical roles remain Python handlers. Detected CLI/MCP capabilities are not invoked
unless a dedicated, tested adapter exists; discovery is not authentication.
"""
from __future__ import annotations

from agents import AGENTS, WORKFLOW_STEPS
import mcp_registry
import runtime_registry
import skill_registry

AGENT_NAMES = {item[0]: item[1] for item in AGENTS}

SPECIALISTS = (
    {
        "id": "desktop", "name": "Desktop Automation",
        "triggers": ("desktop", "écran", "screen", "application", "clic", "click", "naviguer", "contrôler une app"),
        "mcp_hints": ("agent-desktop", "desktop"),
    },
    {
        "id": "browser", "name": "Browser Research",
        "triggers": ("navigateur", "browser", "site web", "web research", "navigation web", "scraper"),
        "mcp_hints": ("browser", "playwright"),
    },
    {
        "id": "blender", "name": "Blender Artist",
        "triggers": ("blender", "3d", "modèle 3d", "personnage 3d", "rendu 3d"),
        "mcp_hints": ("blender",),
    },
    {
        "id": "roblox", "name": "Roblox Developer",
        "triggers": ("roblox", "luau", "rojo", "roblox studio"),
        "mcp_hints": ("roblox", "rojo"),
    },
    {
        "id": "devsecops", "name": "Cyber DevSecOps",
        "triggers": ("cyber", "pentest", "devsecops", "sécurité", "linux", "réseau"),
        "mcp_hints": ("terminal", "ssh"),
    },
    {
        "id": "voice", "name": "Voice Studio",
        "triggers": ("voix", "narration", "podcast", "audio", "lip sync", "synchronisation labiale"),
        "mcp_hints": ("voice", "audio"),
    },
    {
        "id": "gui", "name": "GUI / UX Designer",
        "triggers": ("interface", "dashboard", "figma", "gui", "ui ux", "maquette"),
        "mcp_hints": ("figma",),
    },
    {
        "id": "opportunity", "name": "Opportunity Hunter",
        "triggers": ("opportunité", "opportunités", "tendance marché", "business idea", "étude de marché"),
        "mcp_hints": (),
    },
    {
        "id": "file", "name": "File Agent",
        "triggers": ("fichier", "document", "drive", "dossier", "bibliothèque de fichiers"),
        "mcp_hints": ("drive", "filesystem", "file"),
    },
)


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


def select_specialists(brief: str, skills=None, mcp_names=None, runtime_agents=None) -> list:
    """Select optional specialist capabilities; never dispatch them automatically."""
    skills = skills if skills is not None else skill_registry.get_status().get("skills", [])
    mcp_names = [str(name).lower() for name in (mcp_names or [])]
    runtime_agents = runtime_agents or {}
    text = str(brief or "").lower()
    selected = []
    for spec in SPECIALISTS:
        if not any(trigger in text for trigger in spec["triggers"]):
            continue
        assigned = skill_registry.select_for_agent(
            spec["id"], brief, limit=3, skills=skills
        )
        mcp_hits = [
            name for name in mcp_names
            if any(hint in name for hint in spec["mcp_hints"])
        ]
        runtime_key = "agent-desktop" if spec["id"] == "desktop" else (
            "agent-browser" if spec["id"] == "browser" else None
        )
        runtime_detected = bool(runtime_agents.get(runtime_key, {}).get("installed")) if runtime_key else False
        selected.append({
            "id": spec["id"],
            "name": spec["name"],
            "skills": assigned,
            "mcp_declarations": mcp_hits,
            "runtime_detected": runtime_detected,
            "dispatch_ready": False,
            "status": "capability_detected_adapter_pending" if assigned or mcp_hits or runtime_detected else "capability_not_installed",
            "note": "Spécialiste proposé ; aucun processus externe n'est lancé sans adaptateur testé et autorisation.",
        })
    return selected


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
    specialists = select_specialists(
        brief,
        skills=skill_state.get("skills", []),
        mcp_names=mcp_state.get("names", []),
        runtime_agents=runtimes.get("agents", {}),
    )
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
        "specialists": specialists,
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
            "specialist_dispatch": False,
            "note": "Ce plan prépare la délégation. Il ne lance pas les CLI externes et ne confère aucune permission.",
        },
    }


def skill_assignments(workflow: str, brief: str = "") -> dict:
    plan = build_plan(workflow, brief)
    result = {step["agent_id"]: step["skills"] for step in plan["steps"]}
    result["specialists"] = plan["specialists"]
    return result
