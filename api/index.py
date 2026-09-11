import json
import asyncio
from http.server import BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
)

from bot import (
    TELEGRAM_BOT_TOKEN,
    start_command,
    help_command,
    repo_command,
    error_handler,
)


async def process_telegram_update(data):
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("repo", repo_command)
    )

    application.add_error_handler(error_handler)

    await application.initialize()

    try:
        update = Update.de_json(
            data,
            application.bot
        )

        await application.process_update(update)

    finally:
        await application.shutdown()


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        response = {
            "status": "ok",
            "service": "DevFix AI",
            "message": "Telegram webhook is ready."
        }

        body = json.dumps(response).encode("utf-8")

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/json"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        self.wfile.write(body)

    def do_POST(self):
        try:
            content_length = int(
                self.headers.get("Content-Length", 0)
            )

            raw_data = self.rfile.read(content_length)

            data = json.loads(
                raw_data.decode("utf-8")
            )

            asyncio.run(
                process_telegram_update(data)
            )

            response = {
                "ok": True
            }

            status_code = 200

        except Exception as exc:
            print(
                f"Webhook error: {exc}"
            )

            response = {
                "ok": False,
                "error": str(exc)
            }

            status_code = 500

        body = json.dumps(response).encode("utf-8")

        self.send_response(status_code)

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(body)