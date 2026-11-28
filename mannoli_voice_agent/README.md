# Mannoli — AI customer-service voice agent (Malar)

Phone agent for Mannoli's cow-dung statues and decor. Built from the
*AI Voice Agent Playbook for Service Bookings* (8-part prompt, tools, 15-call test, India rules).

Speaks **Tamil (default), English, Hindi, Malayalam, Kannada, Telugu** and switches when the caller switches.

**Its job:** answer product questions from confirmed facts, then save each interested caller as an
**enquiry** so the team calls back. It does **not** take orders, quote unconfirmed prices or promise delivery.

## Files
| File | What it is |
|---|---|
| `mannoli_facts.md` | The only facts the agent may say. **Fill every `TO CONFIRM` with the owner.** |
| `system_prompt.md` | Identity, goal, style, call flow, tools, rules, edge cases |
| `languages.json` | Greeting, "don't know", callback, "am I AI", closing lines in all 6 languages |
| `tools.json` | `save_enquiry`, `transfer_call`, `end_call` schemas |
| `build_agent.py` | Combines the above into `build/system_prompt.txt` + `build/agent_config.json` |
| `webhook_server.py` / `enquiries.py` | Endpoint the platform calls for `save_enquiry`; writes `data/enquiries.csv` |
| `simulate_call.py` | Text rehearsal of a call with the same prompt and tools |
| `TEST_CALLS.md` | Test checklist (playbook 15 + Mannoli-specific per language) |

## Steps
```sh
cd mannoli_voice_agent
pip install -r requirements.txt
cp .env.example .env            # add OPENAI_API_KEY for rehearsal, MANNOLI_WEBHOOK_TOKEN for the webhook
python -m pytest -q tests       # 11 checks
python simulate_call.py         # rehearse calls in text (uses draft facts)
```
1. **Confirm facts** with the owner and edit `mannoli_facts.md`. Then `python build_agent.py` (it refuses while anything is `TO CONFIRM`).
2. **Platform** (playbook recommends Sarvam first, Bolna next): paste `build/system_prompt.txt`, set the first message from `agent_config.json`, enable multilingual STT/TTS for ta, en, hi, ml, kn, te, and turn on interruptions.
3. **Tool:** add `save_enquiry` from `tools.json`, URL `https://<your-host>/tools/save_enquiry`, header `X-Mannoli-Token`. Pass the caller's number from call metadata as `caller_number`. Run `uvicorn webhook_server:app --port 8000` behind HTTPS. (No server? Point the tool at an n8n/Make webhook that adds a Google Sheet row instead — same fields.)
4. **Transfer:** set the platform's built-in transfer to the owner's number.
5. **Test** with `TEST_CALLS.md`. Then a number (Vobiz/Bolna, KYC needed). Start with **after-hours and missed calls only**.

## Rules kept (India)
Says it is AI and that the call may be recorded (in the greeting). Offers a human. No payment details on call.
WhatsApp only with clear consent. Inbound only — do not use for cold outbound calls (TRAI/DND).
`data/enquiries.csv` holds personal data (DPDP Act): keep it private, delete old rows. Not legal advice.

## Live test agent (ElevenLabs)
- Agent: `Mannoli - Malar (customer service, TEST)`, id `agent_1801m80w14r7f9dsnk7vsy3yqewt`
- Talk to it: https://elevenlabs.io/app/talk-to?agent_id=agent_1801m80w14r7f9dsnk7vsy3yqewt
- Female voices: Tamil Janani (default), English Anshika, Hindi Rida M, Malayalam Sunaina, Kannada Kampana, Telugu Madhavi.
- Language detection is on, so the voice switches when the caller switches language.
- Test version only: facts are still TO CONFIRM, and save_enquiry and transfer are not connected. Not for real customers yet.
