import json
import asyncio
from http.server import BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from bot import (
    TELEGRAM_BOT_TOKEN,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    start_command,
    help_command,
    repo_command,
    error_handler,
    analyze_with_gemini,
)


async def answer_general_question(update, context):
    """Answer normal Telegram questions with Gemini."""

    if not update.message or not update.message.text:
        return

    question = update.message.text.strip()

    if not question:
        return

    await update.message.chat.send_action("typing")

    try:
        prompt = f"""
You are DevFix AI, an expert software engineering,
DevOps, debugging, cloud, Docker, Kubernetes,
GitHub, CI/CD, security, and deployment assistant.

Answer the user's question accurately and clearly.

IMPORTANT RULES:
1. Do not invent facts.
2. If information is missing, say what information is needed.
3. Explain technical concepts clearly.
4. Give practical commands or code when useful.
5. Never expose API keys, tokens, passwords, or secrets.
6. Prefer safe, minimal solutions.
7. If the question is unrelated to software/DevOps,
   politely explain that DevFix AI focuses on technical topics.

User question:
{question}
"""

        answer = await asyncio.to_thread(
            analyze_with_gemini,
            prompt,
        )

        # Telegram has a message-size limit.
        limit = 4000

        while answer:
            if len(answer) <= limit:
                chunk = answer
                answer = ""
            else:
                split_at = answer.rfind("\n", 0, limit)

                if split_at < 1000:
                    split_at = limit

                chunk = answer[:split_at]
                answer = answer[split_at:].lstrip()

            await update.message.reply_text(chunk)

            if answer:
                await asyncio.sleep(0.5)

    except Exception as exc:
        print(f"General question error: {exc}")

        await update.message.reply_text(
            "❌ DevFix AI could not answer this question.\n\n"
            f"Error: {str(exc)}"
        )


async def process_telegram_update(data):

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Existing commands
    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("repo", repo_command)
    )

    # NEW: normal Telegram questions
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            answer_general_question,
        )
    )

    application.add_error_handler(error_handler)

    await application.initialize()

    try:
        update = Update.de_json(
            data,
            application.bot,
        )

        await application.process_update(update)

    finally:
        await application.shutdown()


class handler(BaseHTTPRequestHandler):

    def do_GET(self):

        response = {
            "status": "ok",
            "service": "DevFix AI",
            "message": "Telegram webhook is ready.",
        }

        body = json.dumps(response).encode("utf-8")

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.end_headers()

        self.wfile.write(body)

    def do_POST(self):

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0,
                )
            )

            raw_data = self.rfile.read(
                content_length
            )

            data = json.loads(
                raw_data.decode("utf-8")
            )

            asyncio.run(
                process_telegram_update(data)
            )

            response = {
                "ok": True,
            }

            status_code = 200

        except Exception as exc:

            print(
                f"Webhook error: {exc}"
            )

            response = {
                "ok": False,
                "error": str(exc),
            }

            status_code = 500

        body = json.dumps(response).encode("utf-8")

        self.send_response(status_code)

        self.send_header(
            "Content-Type",
            "application/json",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.end_headers()

        self.wfile.write(body)