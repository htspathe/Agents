"""Explicit local LLM adapter and noncommercial Kaggle handoff.

No automatic cloud submission, no account creation, no model downloads.
A local OpenAI-compatible endpoint is not proof of local inference; configure
only an offline Flyx/llama.cpp/Ollama instance, never a cloud proxy.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def _local_completion(topic, system, max_tokens):
    base = os.environ.get("PRODUCTION_LOCAL_LLM_URL", "").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1","localhost","::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"","/v1"}):
        raise ValueError("Configurer une API LLM locale HTTP, sans proxy.")
    if os.environ.get("PRODUCTION_LOCAL_LLM_OFFLINE_CONFIRMED") != "1":
        raise ValueError("Confirmer que le moteur est hors ligne et ne relaie pas les requêtes au cloud.")
    model = os.environ.get("PRODUCTION_LOCAL_LLM_MODEL", "").strip()
    if not model or not 3 <= len(topic.strip()) <= 16000:
        raise ValueError("Modèle, sujet ou langue invalide.")
    if not base.endswith("/v1"):
        base += "/v1"
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(base+"/models"), timeout=10) as response:
        catalog = json.loads(response.read(1000000))
    if model not in [row.get("id") for row in catalog.get("data",[]) if isinstance(row,dict)]:
        raise RuntimeError("Le modèle configuré ne figure pas dans le catalogue local.")
    payload = {"model":model,"messages":[{"role":"system","content":system},{"role":"user","content":topic}],"stream":False,"max_tokens":max_tokens}
    req = Request(base+"/chat/completions",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    with opener.open(req,timeout=120) as response:
        result=json.loads(response.read(1000000))
    content=result["choices"][0]["message"]["content"]
    if not isinstance(content,str) or not 12<=len(content.strip())<=16000:
        raise RuntimeError("Narration locale invalide.")
    return content.strip()

def local_narration(topic, language="fr"):
    if language not in {"fr","en"} or not 3 <= len(topic.strip()) <= 2000:
        raise ValueError("Sujet ou langue invalide.")
    return _local_completion(topic, "Write a 25-35 word spoken narration, no headings, no notes, no unsupported factual claims. Language: "+language, 256)

def local_storyboard(narration, count, ratio):
    if type(count) is not int or not 1 <= count <= 12 or ratio not in {"9:16","16:9"}:
        raise ValueError("Nombre de scènes ou format invalide.")
    answer = _local_completion(narration, f"Create exactly {count} distinct chronological text-to-video prompts based on this French narration, aspect ratio {ratio}. Keep subjects and visual style consistent. Return only a JSON array of {count} English prompt strings. No text overlays, logos or narration. Each prompt 15-90 words.", min(1600, 180*count))
    answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip(), flags=re.I)
    prompts = json.loads(answer)
    if not isinstance(prompts,list) or len(prompts)!=count or any(not isinstance(p,str) or not 8<=len(p.strip())<=2000 for p in prompts):
        raise ValueError("Le LLM n'a pas produit un storyboard JSON valide.")
    return [p.strip() for p in prompts]

def kaggle_command(action, kernel, destination=None):
    if os.environ.get("PRODUCTION_KAGGLE_NONCOMMERCIAL_CONFIRMED")!="1":
        raise ValueError("Kaggle réservé ici aux expériences non commerciales explicitement confirmées.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+",kernel):
        raise ValueError("Identifiant Kaggle invalide.")
    cli=shutil.which("kaggle")
    if not cli:
        raise RuntimeError("CLI Kaggle absent.")
    if action=="status":
        command=[cli,"kernels","status",kernel]
    elif action=="output":
        target=Path(destination).resolve()
        if target.exists() and any(target.iterdir()):
            raise ValueError("Utiliser un dossier de téléchargement vide.")
        target.mkdir(parents=True,exist_ok=True)
        command=[cli,"kernels","output",kernel,"-p",str(target)]
    else:
        raise ValueError("Action Kaggle non autorisée.")
    result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=600,check=False)
    if result.returncode:
        raise RuntimeError("Échec CLI Kaggle. Vérifier la session et les quotas localement.")
    return result.stdout.strip()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="action",required=True)
    llm=sub.add_parser("narration")
    llm.add_argument("topic")
    llm.add_argument("--language",choices=["fr","en"],default="fr")
    llm.add_argument("--output",type=Path,required=True)
    for action in ("status","output"):
        item=sub.add_parser("kaggle-"+action)
        item.add_argument("kernel")
        if action=="output":
            item.add_argument("destination")
    args=parser.parse_args()
    if args.action=="narration":
        args.output.write_text(local_narration(args.topic,args.language),encoding="utf-8")
    else:
        print(kaggle_command(args.action[7:],args.kernel,getattr(args,"destination",None)))

if __name__=="__main__":
    main()
