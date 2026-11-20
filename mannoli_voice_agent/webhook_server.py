"""Webhook the voice platform calls for the save_enquiry tool.

Run:  MANNOLI_WEBHOOK_TOKEN=<long-random-string> uvicorn webhook_server:app --port 8000
Expose it with HTTPS (a tunnel or your host) and paste the URL + token into the platform's tool settings.
"""
import hmac
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from enquiries import EnquiryError, save_enquiry

ROOT = Path(__file__).resolve().parent
CSV_PATH = Path(os.getenv("MANNOLI_ENQUIRY_CSV", ROOT / "data" / "enquiries.csv"))
MAX_BODY = 8_000

app = FastAPI(title="Mannoli voice agent tools", docs_url=None, redoc_url=None)


def _check_token(token: str | None) -> None:
    expected = os.getenv("MANNOLI_WEBHOOK_TOKEN", "")
    if len(expected) < 16:
        raise HTTPException(503, "Server token not configured.")
    if not token or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, "Unauthorized.")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/tools/save_enquiry")
async def save_enquiry_tool(request: Request, x_mannoli_token: str | None = Header(default=None)):
    _check_token(x_mannoli_token)
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "Body too large.")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "Body must be JSON.")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Body must be a JSON object.")
    # Platforms send the tool arguments either flat or under "arguments"; the caller's
    # number comes from the platform (call metadata), never from the LLM.
    args = payload.get("arguments", payload)
    caller = payload.get("caller_number") or payload.get("from_number")
    try:
        return save_enquiry(args, CSV_PATH, caller_number=caller)
    except EnquiryError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
