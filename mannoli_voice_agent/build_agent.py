"""Assemble the final Mannoli voice-agent prompt + config from the source files.

python build_agent.py           -> refuses while mannoli_facts.md has TO CONFIRM lines
python build_agent.py --draft   -> builds anyway (testing only; never take real calls with it)
Outputs build/system_prompt.txt (paste into Sarvam / Bolna / Vapi) and build/agent_config.json.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PENDING = "TO CONFIRM"


def load_sources():
    facts = (ROOT / "mannoli_facts.md").read_text(encoding="utf-8")
    template = (ROOT / "system_prompt.md").read_text(encoding="utf-8")
    langs = json.loads((ROOT / "languages.json").read_text(encoding="utf-8"))
    tools = json.loads((ROOT / "tools.json").read_text(encoding="utf-8"))
    return facts, template, langs, tools


def pending_facts(facts: str) -> list[str]:
    return [line.strip("- ").strip() for line in facts.splitlines() if PENDING in line
            and not line.lstrip().startswith(("#", "Lines marked", "live config"))]


def facts_for_prompt(facts: str) -> str:
    # Keep only fact bullets; drop the file's editing instructions and headings' markdown.
    lines = []
    for line in facts.splitlines():
        s = line.strip()
        if s.startswith("## "):
            lines.append(s[3:] + ":")
        elif s.startswith("- "):
            lines.append(s[2:])
    return "\n".join(lines)


def language_guide(langs: dict) -> str:
    out = ["Language notes and exact lines to use (adapt only if the situation needs it):"]
    for code, L in langs["languages"].items():
        out.append(f"{L['name']} ({code}): {L['style']}")
        for key in ("greeting", "dont_know", "callback_taken", "am_i_ai", "closing"):
            out.append(f"  {key}: {L[key]}")
    return "\n".join(out)


def build(draft: bool = False) -> dict:
    facts, template, langs, tools = load_sources()
    pending = pending_facts(facts)
    if pending and not draft:
        raise SystemExit("Not building a live config. Confirm these facts with the owner first:\n  - "
                         + "\n  - ".join(pending) + "\n(Use --draft only for testing.)")
    prompt = (template.replace("{{FACTS}}", facts_for_prompt(facts))
              .replace("{{LANGUAGE_GUIDE}}", language_guide(langs)))
    default = langs["default"]
    return {
        "name": "Mannoli - Malar (customer service)",
        "draft": bool(pending),
        "pending_facts": pending,
        "first_message": langs["languages"][default]["greeting"],
        "languages": list(langs["languages"]),
        "system_prompt": prompt,
        "tools": tools,
        "recommended_settings": {
            "stt": "An Indian-language multilingual STT with auto language detection (e.g. Sarvam).",
            "tts": "An Indian-language voice that supports ta, en, hi, ml, kn, te (e.g. Sarvam). Test each language.",
            "llm": "A fast model; reply latency matters more than size on a phone call.",
            "interruptions": "on",
            "silence_timeout_seconds": 8,
            "max_call_minutes": 6,
            "recording_notice": "included in greeting",
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft", action="store_true")
    args = ap.parse_args()
    config = build(draft=args.draft)
    out = ROOT / "build"
    out.mkdir(exist_ok=True)
    (out / "system_prompt.txt").write_text(config["system_prompt"], encoding="utf-8")
    (out / "agent_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    words = len(config["system_prompt"].split())
    print(f"Built build/system_prompt.txt ({words} words) and build/agent_config.json")
    if config["draft"]:
        print(f"DRAFT: {len(config['pending_facts'])} facts still TO CONFIRM. Do not take real calls.", file=sys.stderr)


if __name__ == "__main__":
    main()
