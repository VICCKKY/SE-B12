"""Text rehearsal of a Mannoli call: same prompt and tools as the phone agent.

Use it to run the test-call checklist cheaply before spending voice minutes.
Needs OPENAI_API_KEY in mannoli_voice_agent/.env. Saves enquiries to data/simulated_enquiries.csv.
"""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from build_agent import build
from enquiries import EnquiryError, save_enquiry

ROOT = Path(__file__).resolve().parent
SIM_CSV = ROOT / "data" / "simulated_enquiries.csv"


def to_openai_tools(tools):
    return [{"type": "function", "function": t} for t in tools]


def run_tool(name, raw_args):
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "Arguments were not valid JSON."}
    if name == "save_enquiry":
        try:
            return save_enquiry(args, SIM_CSV, caller_number="+919999999999")
        except EnquiryError as exc:
            return {"ok": False, "error": str(exc)}
    if name == "transfer_call":
        print(f"\n[TRANSFER to team requested: {args.get('reason', '')}] (simulated)")
        return {"ok": True, "say": "Transferring now."}
    if name == "end_call":
        return {"ok": True, "ended": True}
    return {"ok": False, "error": f"Unknown tool {name}."}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY missing. Copy .env.example to .env and add it.")
    config = build(draft=True)
    client = OpenAI(timeout=45, max_retries=1)
    model = args.model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    messages = [{"role": "system", "content": config["system_prompt"]},
                {"role": "assistant", "content": config["first_message"]}]
    tools = to_openai_tools(config["tools"])
    print("MANNOLI voice agent - text rehearsal. Type as the caller. /exit to stop.")
    if config["draft"]:
        print(f"(Draft facts: {len(config['pending_facts'])} still TO CONFIRM.)")
    print("\nMalar:", config["first_message"])
    while True:
        try:
            said = input("\nCaller: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if said.lower() in {"/exit", "exit", "quit"}:
            break
        if not said:
            said = "(silence)"
        messages.append({"role": "user", "content": said})
        ended = False
        for _ in range(4):  # allow a few tool round-trips per caller turn
            reply = client.chat.completions.create(model=model, messages=messages, tools=tools,
                                                   temperature=0.3).choices[0].message
            messages.append(reply.model_dump(exclude_none=True))
            if not reply.tool_calls:
                print("\nMalar:", reply.content)
                break
            for call in reply.tool_calls:
                result = run_tool(call.function.name, call.function.arguments)
                print(f"\n[tool {call.function.name} -> {json.dumps(result, ensure_ascii=False)}]")
                ended = ended or bool(result.get("ended"))
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})
            if ended:
                break
        if ended:
            print("\n[Call ended]")
            break


if __name__ == "__main__":
    main()
