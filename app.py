import os
import logging

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator
import anthropic

from notion_search import search_notion_hr

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

claude = anthropic.Anthropic()

SYSTEM_PROMPT = """You are the Big Mamma Group HR assistant on WhatsApp.
Your role is to help employees with HR questions (contracts, leave, benefits,
onboarding, policies, etc.) using the provided Notion HR hub content.

Rules:
- Detect the language of the incoming message. Reply in the SAME language
  (French or Italian). Default to French if ambiguous.
- Be friendly, concise, and professional. Use a warm tone that matches
  Big Mamma's culture.
- Base your answers strictly on the Notion content provided. If the content
  doesn't cover the question, say you don't have that information and suggest
  contacting HR directly (rh@bigmamma.com).
- Keep responses short — this is WhatsApp, not email. Aim for 1-3 short
  paragraphs max.
- Never invent policies or numbers. Accuracy matters more than completeness.
- You can use emojis sparingly to keep the tone warm 🙂
"""


def validate_twilio_request(f):
    """Validate that requests actually come from Twilio."""
    from functools import wraps

    @wraps(f)
    def wrapper(*args, **kwargs):
        validator = RequestValidator(os.environ["TWILIO_AUTH_TOKEN"])
        url = request.url
        post_vars = request.form.to_dict()
        signature = request.headers.get("X-Twilio-Signature", "")

        if not validator.validate(url, post_vars, signature):
            logger.warning("Invalid Twilio signature")
            return "Forbidden", 403

        return f(*args, **kwargs)

    return wrapper


def detect_language(text: str) -> str:
    """Quick heuristic to detect FR vs IT. Defaults to FR."""
    italian_markers = [
        "come", "sono", "vorrei", "posso", "quando", "dove", "cosa",
        "perché", "grazie", "buongiorno", "ciao", "lavoro", "contratto",
        "ferie", "permesso", "stipendio", "busta paga", "malattia",
    ]
    text_lower = text.lower()
    italian_hits = sum(1 for m in italian_markers if m in text_lower)
    return "it" if italian_hits >= 2 else "fr"


def build_answer(user_message: str) -> str:
    """Search Notion, then ask Claude to answer based on the results."""
    lang = detect_language(user_message)
    notion_results = search_notion_hr(user_message)

    if not notion_results:
        context_block = "(No relevant content found in the HR Notion hub.)"
    else:
        context_block = "\n\n---\n\n".join(notion_results)

    lang_instruction = (
        "Respond in Italian." if lang == "it" else "Respond in French."
    )

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": (
                    f"{lang_instruction}\n\n"
                    f"## Notion HR Hub content\n\n{context_block}\n\n"
                    f"## Employee question\n\n{user_message}"
                ),
            }
        ],
    )
    return response.content[0].text


@app.route("/webhook", methods=["POST"])
@validate_twilio_request
def webhook():
    """Twilio WhatsApp webhook endpoint."""
    incoming_msg = request.form.get("Body", "").strip()
    sender = request.form.get("From", "")
    logger.info("Message from %s: %s", sender, incoming_msg[:80])

    if not incoming_msg:
        resp = MessagingResponse()
        resp.message("Je n'ai pas reçu de message. Peux-tu réessayer ? 🙂")
        return str(resp), 200, {"Content-Type": "text/xml"}

    try:
        answer = build_answer(incoming_msg)
    except Exception:
        logger.exception("Error building answer")
        answer = (
            "Désolé, une erreur est survenue. "
            "Réessaie dans quelques instants ou contacte rh@bigmamma.com 🙏"
        )

    # Twilio WhatsApp has a 1600-char limit per message
    if len(answer) > 1550:
        answer = answer[:1547] + "..."

    resp = MessagingResponse()
    resp.message(answer)
    return str(resp), 200, {"Content-Type": "text/xml"}


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
