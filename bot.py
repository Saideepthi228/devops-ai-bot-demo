import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from google import genai

# Load environment variables from .env
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Check required keys
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing from .env")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY is missing from .env")

# Create Gemini client
client = genai.Client(api_key=GEMINI_API_KEY)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 DevFix AI is online!\n\n"
        "Send me a DevOps error using:\n"
        "/diagnose <your error>\n\n"
        "Example:\n"
        "/diagnose Docker container keeps restarting with exit code 1"
    )


async def diagnose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Please provide a deployment error.\n\n"
            "Example:\n"
            "/diagnose Docker container keeps restarting with exit code 1"
        )
        return

    error_text = " ".join(context.args)

    await update.message.reply_text(
        "🔍 Analyzing the DevOps error..."
    )

    prompt = f"""
You are DevFix AI, an expert DevOps troubleshooting assistant.

Analyze this developer/deployment error:

{error_text}

Give a concise but useful response using exactly these sections:

🧠 Root Cause
Explain the most likely cause.

🔧 Recommended Fix
Give practical steps to fix it.

💻 Example Solution
Provide a relevant command or code example when useful.

⚠️ Risk Level
Low, Medium, or High, with a short explanation.

📌 Next Step
Tell the developer what to check or do next.

Important rules:
- Do not invent information that is not supported by the error.
- If important information is missing, clearly say what information is needed.
- Keep the response practical and easy for a developer to follow.
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )

        answer = response.text

        if not answer:
            answer = "The AI returned an empty response. Please try again."

        await update.message.reply_text(
            "🤖 DevFix AI Diagnosis\n\n" + answer
        )

    except Exception as e:
        print(f"Gemini error: {e}")

        await update.message.reply_text(
            "❌ AI service error.\n\n"
            "Please check the Gemini API key, quota, model availability, "
            "and network connection."
        )


def main():
    print("🚀 DevFix AI starting...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Telegram commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("diagnose", diagnose))

    print("✅ Bot is running. Press Ctrl+C to stop.")

    # Start Telegram polling
    app.run_polling()


if __name__ == "__main__":
    main()