"""Twelve founding agent roles and safe local production workflows.
No agent publishes or modifies external accounts in this MVP.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
import textwrap
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent

AGENTS = [
    ("orchestrator", "Orchestrator", "Pilotage et traçabilité"),
    ("research", "Research", "Recherche dans la base documentaire locale"),
    ("content", "Content Strategist", "Angles, scripts et plans éditoriaux"),
    ("creative", "Creative", "Création de maquettes SVG"),
    ("video", "Video Production", "Storyboards et prévisualisations FFmpeg"),
    ("clipping", "Clipping", "Extraction et recadrage vertical"),
    ("publishing", "Publishing", "Préparation des fichiers de publication"),
    ("shopify", "Shopify Commerce", "Audit CSV hors ligne sans modification Shopify"),
    ("support", "Customer Support", "Brouillons de réponses à approuver"),
    ("kdp", "KDP Publishing", "Plan de livre, métadonnées et contrôle de soumission"),
    ("analytics", "Analytics", "Rapports d'exécution et indicateurs locaux"),
    ("quality", "Quality Control", "Contrôles des livrables et des autorisations"),
]

WORKFLOW_STEPS = {
    "clogz": ["research", "content", "shopify", "support", "creative", "video", "publishing", "quality", "analytics"],
    "kdp": ["research", "content", "kdp", "creative", "publishing", "quality", "analytics"],
    "youtube": ["research", "content", "creative", "video", "publishing", "quality", "analytics"],
    "clipping": ["clipping", "creative", "publishing", "quality", "analytics"],
}

LABELS = {"clogz": "CLOGZ · Campagne", "kdp": "KDP · Livre", "youtube": "YouTube · Vidéo", "clipping": "Clipping · Short"}


def write_text(folder: Path, name: str, content: str):
    (folder / name).write_text(content.strip() + "\n", encoding="utf-8")
    return name


def write_json(folder: Path, name: str, data):
    (folder / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return name


def safe_text(value, maxlen=180):
    return str(value or "").strip()[:maxlen]


def llm(prompt: str, fallback: str, ctx: dict) -> str:
    """Optional LOCAL Ollama only; honest deterministic fallback."""
    model = os.environ.get("OLLAMA_MODEL", "").strip()
    if not model:
        ctx["notes"].append("Ollama non configuré : gabarits déterministes, sans génération IA.")
        return fallback
    payload = json.dumps({"model": model, "prompt": prompt[:9000], "stream": False, "options": {"temperature": 0.45}}).encode()
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=80) as resp:
            answer = json.loads(resp.read(2_000_000).decode("utf-8"))
        out = answer.get("response", "").strip()
        if out:
            ctx["notes"].append(f"Génération locale via Ollama : {model}.")
            return out[:18000]
        raise ValueError("Réponse vide")
    except (TimeoutError, ValueError, OSError, urllib.error.URLError) as exc:
        ctx["notes"].append(f"Ollama indisponible ({type(exc).__name__}) : mode gabarit utilisé.")
        return fallback


def research(ctx, folder):
    brief = ctx["brief"]
    terms = set(re.findall(r"\w+", (brief + " " + ctx["workflow"]).lower()))
    scored = []
    for path in (ROOT / "knowledge").glob("*.md"):
        txt = path.read_text(encoding="utf-8")
        score = len(terms.intersection(set(re.findall(r"\w+", txt.lower()))))
        if ctx["workflow"] in path.name:
            score += 5
        scored.append((score, path.name, txt))
    scored.sort(reverse=True)
    sources = scored[:2]
    content = "# Recherche interne (MVP hors ligne)\n\n"
    content += "**Attention : aucune recherche Web en temps réel n'a été exécutée.**\n\n"
    content += f"Brief : {brief}\n\n## Contexte disponible\n"
    for _, name, excerpt in sources:
        content += f"\n### {name}\n{excerpt[:1300]}\n"
    write_text(folder, "research.md", content)
    ctx["sources"] = [n for _, n, _ in sources]
    return "research.md"


def content(ctx, folder):
    brief, typ = ctx["brief"], ctx["workflow"]
    fallback = {
        "clogz": f"# Plan de campagne CLOGZ\n\nProduit / intention : {brief}\n\n## Positionnement\nConfort quotidien, style urbain et preuve produit à documenter.\n\n## Angle 1\nMontrer la paire en situation réelle à Dakar.\n\n## Angle 2\nComparer les coloris et inviter à choisir sa pointure.\n\n## Storyboard publicitaire (15 s)\n0–3 s : gros plan de la paire ; 3–9 s : démonstration ; 9–12 s : prix à vérifier ; 12–15 s : appel à consulter clogz.sn.\n\n## Vérifications\nNe pas promettre livraison gratuite, stocks ou promotion sans confirmation Shopify.",
        "kdp": f"# Concept de livre\n\nIdée : {brief}\n\n## Public cible\nÀ préciser avec des recherches marché indépendantes.\n\n## Table des matières (proposition)\n1. Introduction\n2. Les principes fondamentaux\n3. Les méthodes essentielles\n4. Mise en pratique\n5. Exercices ou exemples\n6. Synthèse et ressources\n\n## Originalité\nAjouter des exemples vérifiés, une expertise éditoriale et une relecture humaine.\n\n**Ce fichier n'est pas un manuscrit complet.**",
        "youtube": f"# Script et structure YouTube\n\nSujet : {brief}\n\n## Accroche\nUne question concrète qui ouvre la problématique, sans chiffres non sourcés.\n\n## Découpage\n1. Introduction (0:00–0:30)\n2. Contexte et définitions\n3. Trois mécanismes ou exemples vérifiables\n4. Limites, risques et contre-arguments\n5. Conclusion et appel à l'action\n\n## Voix off (brouillon)\nAujourd'hui, nous allons comprendre : {brief}. Nous examinerons les mécanismes, les faits à vérifier et leurs implications.\n\n## Sources\nAjouter les références externes avant une publication factuelle."
    }[typ]
    prompt = (f"Tu es un agent de production éditoriale responsable. Rédige en français un document Markdown exploitable pour le projet {typ}. "
              "N'invente pas de prix, données ou sources. Ajoute une liste des points à valider. "
              f"Brief : {brief}. Sources internes : {ctx.get('sources', [])}.")
    draft = llm(prompt, fallback, ctx)
    return write_text(folder, "content.md", draft)


def svg_card(title: str, label: str, footnote: str, workflow: str) -> str:
    palettes = {
        "clogz": ("#102a37", "#d3eaf0", "#f8f5e8"),
        "kdp": ("#172535", "#eedfc0", "#b85d4c"),
        "youtube": ("#111827", "#f1eee7", "#68e1bd"),
        "clipping": ("#191a28", "#ffffff", "#f5b468")
    }
    bg, foreground, accent = palettes[workflow]
    # simple SVG cover is a design DRAFT, not a print-ready KDP or actual screenshot
    lines = textwrap.wrap(title, width=22, break_long_words=False)[:5]
    text_lines = ''.join(f'<text x="90" y="{570 + i*108}" font-size="78" font-weight="800" fill="{foreground}">{escape(line)}</text>' for i, line in enumerate(lines))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350" viewBox="0 0 1080 1350">
    <rect width="1080" height="1350" fill="{bg}"/>
    <rect x="55" y="55" width="970" height="1240" fill="none" stroke="{foreground}" stroke-opacity="0.3" stroke-width="2"/>
    <circle cx="820" cy="300" r="228" fill="{accent}" opacity="0.84"/>
    <circle cx="830" cy="310" r="173" fill="none" stroke="{bg}" stroke-width="3"/>
    <text x="92" y="170" font-size="32" letter-spacing="6" font-family="Arial, sans-serif" fill="{foreground}">{escape(label[:30].upper())}</text>
    <g font-family="Arial, sans-serif">{text_lines}
    <path d="M90 1110 L980 1110" stroke="{foreground}" opacity="0.5" stroke-width="2"/>
    <text x="90" y="1190" font-size="28" fill="{foreground}">{escape(footnote[:65])}</text>
    <text x="90" y="1262" font-size="21" fill="{foreground}" opacity="0.65">MAQUETTE · VALIDATION REQUISE</text></g>
    </svg>'''


def creative(ctx, folder):
    brand = {"clogz":"CLOGZ", "kdp":"KDP STUDIO", "youtube":"YOUTUBE STUDIO", "clipping":"SHORTS STUDIO"}[ctx["workflow"]]
    title = ctx["brief"][:110] or "Nouvelle création"
    svg = svg_card(title, brand, "Production OS / Avant publication", ctx["workflow"])
    write_text(folder, "visual.svg", svg)
    ctx["notes"].append("Création SVG conceptuelle : ComfyUI non relié dans ce MVP.")
    return "visual.svg"


def ffmpeg_ready():
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe(path: Path) -> dict:
    cp = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                         "-of", "json", str(path)], capture_output=True, text=True, timeout=25, check=True)
    return json.loads(cp.stdout)


def video(ctx, folder):
    storyboard = {
        "title": ctx["brief"], "type": "title_preview_only", "requires_original_footage": True,
        "scenes": [
            {"seconds": "0-3", "visual": "Introduction et accroche"},
            {"seconds": "3-10", "visual": "B-roll original ou sous licence"},
            {"seconds": "10-15", "visual": "Conclusion et CTA"},
        ], "notes": "La vidéo démo ne constitue pas le montage final et n'est pas prête à publier."
    }
    write_json(folder, "storyboard.json", storyboard)
    if ffmpeg_ready():
        try:
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "color=c=0x172535:s=540x960:r=24", "-t", "3", "-an", "-c:v", "libx264",
                            "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", str(folder / "video_preview.mp4")],
                           capture_output=True, text=True, timeout=55, check=True)
            ctx["notes"].append("Prévisualisation FFmpeg de 3 s créée (fond uni, aucun métrage final).")
        except (OSError, subprocess.SubprocessError) as exc:
            ctx["notes"].append(f"Prévisualisation indisponible : {type(exc).__name__}")
    else:
        ctx["notes"].append("FFmpeg absent : storyboard créé sans rendu vidéo.")
    return "storyboard.json"


def clipping(ctx, folder):
    if not ctx.get("rights_confirmed"):
        raise ValueError("Droits d'utilisation non confirmés : export interdit.")
    filename = Path(ctx.get("source_file", "")).name
    if not filename or filename != ctx.get("source_file"):
        raise ValueError("Fichier source invalide")
    src = (ROOT / "inbox" / filename).resolve()
    inbox = (ROOT / "inbox").resolve()
    if inbox not in src.parents or not src.is_file():
        raise ValueError("Fichier introuvable dans inbox")
    if src.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise ValueError("Format vidéo non autorisé")
    if not ffmpeg_ready():
        raise RuntimeError("FFmpeg + FFprobe requis pour un export Clipping")
    meta = probe(src)
    streams = [s for s in meta.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise ValueError("Source sans piste vidéo")
    duration = float(meta["format"].get("duration", "0"))
    start, end = float(ctx.get("start", 0)), float(ctx.get("end", 10))
    if not (0 <= start < end <= duration + 0.02 and (end - start) <= 120):
        raise ValueError(f"Plage invalide : source de {duration:.2f} s, maximum 120 s par clip")
    output = folder / "short_vertical.mp4"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(src),
           "-t", f"{end-start:.3f}", "-vf", "scale=540:960:force_original_aspect_ratio=increase,crop=540:960,setsar=1",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(output)]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if cp.returncode or not output.is_file():
        raise RuntimeError("FFmpeg : " + (cp.stderr[-500:] or "échec d'export"))
    result = probe(output)
    vs = [s for s in result["streams"] if s.get("codec_type") == "video"]
    if not vs or int(vs[0].get("width", 0)) != 540 or int(vs[0].get("height", 0)) != 960:
        raise RuntimeError("Le format vertical n'a pas passé le contrôle")
    write_json(folder, "clip_details.json", {"input": filename, "start": start, "end": end,
                                             "output": "short_vertical.mp4", "resolution": "540x960",
                                             "rights_confirmed_by_user": True,
                                             "publication": "MANUAL_APPROVAL_REQUIRED"})
    ctx["notes"].append("Export vertical généré avec FFmpeg, diffusion non autorisée automatiquement.")
    return "short_vertical.mp4"


def shopify(ctx, folder):
    path = ROOT / "samples" / "clogz_catalog_demo.csv"
    warnings, n, total = [], 0, 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            n += 1
            sku = row.get("sku", "?")
            try:
                price, stock = int(row["price_xof"]), int(row["stock_demo"])
                total += stock
                if price <= 0 or stock < 0:
                    warnings.append(f"Valeur invalide : {sku}")
                if stock < 5:
                    warnings.append(f"Seuil de stock faible dans la démo : {sku}")
            except (ValueError, KeyError):
                warnings.append(f"Donnée invalide : {sku}")
    write_json(folder, "shopify_offline_audit.json", {"mode": "demo_csv_only", "live_shopify_connected": False,
                                                       "products_demo": n, "units_demo": total, "warnings": warnings,
                                                       "no_real_inventory_assertion": True})
    ctx["notes"].append("Shopify : simulation sur CSV de démonstration, aucun accès au stock réel.")
    return "shopify_offline_audit.json"


def support(ctx, folder):
    body = (f"# Réponse client à personnaliser\n\nBonjour, merci de votre intérêt pour CLOGZ. "
            f"Concernant « {ctx['brief']} », nous pouvons vérifier la disponibilité, "
            "la pointure et les options de livraison avant de confirmer votre commande. "
            "Pouvez-vous nous préciser la pointure et le coloris souhaités ?\n\n"
            "**Statut : brouillon non envoyé.**")
    return write_text(folder, "customer_reply_draft.md", body)


def kdp(ctx, folder):
    title = ctx["brief"][:110]
    metadata = {
        "working_title": title, "subtitle": "À définir", "language": "fr",
        "target_audience": "À identifier", "keywords": [], "categories": [],
        "ai_generated_content_disclosure": "TO_REVIEW_BEFORE_SUBMISSION",
        "cover_status": "CONCEPT_ONLY", "interior_status": "OUTLINE_ONLY",
        "copyright_and_originality": "HUMAN_REVIEW_REQUIRED",
        "amazon_submission": "NOT_READY"
    }
    write_json(folder, "kdp_metadata_draft.json", metadata)
    ctx["notes"].append("KDP : concept et métadonnées uniquement ; pas de livre final ou upload.")
    return "kdp_metadata_draft.json"


def publishing(ctx, folder):
    items = sorted(p.name for p in folder.iterdir() if p.is_file())
    manifest = {"workflow": ctx["workflow"], "brief": ctx["brief"],
                "status": "AWAITING_MANUAL_APPROVAL", "auto_publish": False,
                "files_at_manifest_time": items,
                "external_integrations": "DISABLED", "notes": ctx["notes"][-8:]}
    write_json(folder, "publishing_manifest.json", manifest)
    return "publishing_manifest.json"


def quality(ctx, folder):
    files = [p.name for p in folder.iterdir() if p.is_file()]
    issues = []
    if "publishing_manifest.json" not in files:
        issues.append("Manifeste de publication absent")
    if "visual.svg" not in files:
        issues.append("Maquette visuelle absente")
    if ctx["workflow"] == "clipping" and "short_vertical.mp4" not in files:
        issues.append("Export vertical absent")
    if ctx["workflow"] == "kdp":
        issues.append("Manuscrit complet, droits et couverture imprimable non vérifiés")
    if ctx["workflow"] == "youtube":
        issues.append("Sources factuelles et métrages définitifs non vérifiés")
    if ctx["workflow"] == "clogz":
        issues.append("Prix, stocks et conditions commerciales réelles non vérifiés")
    report = {"passed_technical_checks": not any("absent" in x or "absente" in x for x in issues),
              "publication_ready": False, "pending_human_checks": issues,
              "files": sorted(files), "external_actions": "NONE"}
    write_json(folder, "qa_report.json", report)
    return "qa_report.json"


def analytics(ctx, folder):
    files = [p for p in folder.iterdir() if p.is_file()]
    data = {"domain": ctx["workflow"], "artifacts_created": len(files), "video_exports": len([p for p in files if p.suffix == ".mp4"]),
            "campaign_results": "NO_REAL_PERFORMANCE_DATA_YET", "ad_spend_xof": 0,
            "notes": "Mesures locales de production, pas données de conversion réelles."}
    write_json(folder, "analytics.json", data)
    return "analytics.json"


HANDLERS = {"research": research, "content": content, "creative": creative, "video": video,
            "clipping": clipping, "publishing": publishing, "shopify": shopify,
            "support": support, "kdp": kdp, "analytics": analytics, "quality": quality}
