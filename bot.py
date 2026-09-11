import os
import re
import asyncio
import logging
import time
from pathlib import Path

from dotenv import load_dotenv
from github import Github, Auth
from github.GithubException import GithubException

from google import genai
from google.genai import types

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from telegram.request import HTTPXRequest


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash"
)

GITHUB_BRANCH = os.getenv(
    "GITHUB_BRANCH",
    ""
).strip()

MAX_FILES = int(
    os.getenv("MAX_FILES", "30")
)

MAX_FILE_CHARS = int(
    os.getenv("MAX_FILE_CHARS", "12000")
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# VALIDATE ENVIRONMENT
# ============================================================

missing = []

if not TELEGRAM_BOT_TOKEN:
    missing.append("TELEGRAM_BOT_TOKEN")

if not GITHUB_TOKEN:
    missing.append("GITHUB_TOKEN")

if not GEMINI_API_KEY:
    missing.append("GEMINI_API_KEY")

if missing:
    raise RuntimeError(
        "Missing environment variables: "
        + ", ".join(missing)
    )


# ============================================================
# INITIALIZE GITHUB
# ============================================================

github_auth = Auth.Token(GITHUB_TOKEN)

github = Github(
    auth=github_auth
)


# ============================================================
# INITIALIZE GEMINI
# ============================================================

gemini_client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# FILE FILTERS
# ============================================================

IGNORED_DIRECTORIES = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".next",
    "coverage",
    "target",
}

IGNORED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".mp4",
    ".mov",
    ".avi",
    ".mp3",
    ".wav",
    ".zip",
    ".rar",
    ".7z",
    ".pdf",
    ".exe",
    ".dll",
    ".so",
    ".bin",
    ".lock",
}

IMPORTANT_FILES = {
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "Pipfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    "README.md",
    "README",
}


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def is_text_file(path: str) -> bool:

    filename = Path(path).name
    extension = Path(path).suffix.lower()

    if filename in IMPORTANT_FILES:
        return True

    if extension in IGNORED_EXTENSIONS:
        return False

    return True


def clean_code(
    text: str,
    max_chars: int = MAX_FILE_CHARS
) -> str:

    if not text:
        return ""

    if len(text) <= max_chars:
        return text

    return (
        text[:max_chars]
        + "\n\n"
        + "===== FILE TRUNCATED =====\n"
        + f"Only the first {max_chars} characters were analyzed."
    )


def extract_repo_name(
    text: str
) -> str | None:

    text = text.strip()

    text = re.sub(
        r"https?://github\.com/",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = text.rstrip("/")

    if text.endswith(".git"):
        text = text[:-4]

    match = re.search(
        r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
        text,
    )

    if not match:
        return None

    return (
        f"{match.group(1)}/"
        f"{match.group(2)}"
    )


def split_telegram_message(
    text: str,
    limit: int = 3900
):

    if len(text) <= limit:
        return [text]

    chunks = []

    while len(text) > limit:

        split_at = text.rfind(
            "\n",
            0,
            limit
        )

        if split_at < 1000:
            split_at = limit

        chunks.append(
            text[:split_at]
        )

        text = text[
            split_at:
        ].lstrip()

    if text:
        chunks.append(text)

    return chunks


# ============================================================
# GITHUB FUNCTIONS
# ============================================================

def get_repository(
    repo_name: str
):

    logger.info(
        "Requesting GitHub repository: %s",
        repo_name
    )

    try:

        repo = github.get_repo(
            repo_name
        )

        _ = repo.full_name

        logger.info(
            "Repository found: %s | private=%s",
            repo.full_name,
            repo.private,
        )

        return repo

    except GithubException as exc:

        logger.error(
            "GitHub error %s: %s",
            exc.status,
            exc.data,
        )

        if exc.status == 404:

            raise RuntimeError(
                "GitHub returned 404. "
                "The repository does not exist "
                "or the GitHub token does not "
                "have access to it."
            )

        if exc.status == 401:

            raise RuntimeError(
                "GitHub token is invalid or expired."
            )

        if exc.status == 403:

            raise RuntimeError(
                "GitHub denied access. "
                "Check the token permissions."
            )

        raise RuntimeError(
            f"GitHub error {exc.status}: "
            f"{exc.data}"
        )


def get_branch(repo):

    if GITHUB_BRANCH:
        branch_name = GITHUB_BRANCH
    else:
        branch_name = repo.default_branch

    try:

        branch = repo.get_branch(
            branch_name
        )

        logger.info(
            "Using GitHub branch: %s",
            branch.name
        )

        return branch

    except GithubException as exc:

        if exc.status == 404:

            raise RuntimeError(
                f"Branch '{branch_name}' "
                f"was not found in "
                f"{repo.full_name}."
            )

        raise RuntimeError(
            f"Could not access branch "
            f"'{branch_name}': "
            f"{exc.data}"
        )


def collect_repository_files(
    repo,
    branch_name: str
):

    logger.info(
        "Collecting repository files "
        "from branch: %s",
        branch_name
    )

    try:

        contents = repo.get_contents(
            "",
            ref=branch_name
        )

    except GithubException as exc:

        raise RuntimeError(
            "Could not read repository "
            f"contents: {exc.data}"
        )

    collected = []

    def walk(items):

        for item in items:

            if len(collected) >= MAX_FILES:
                return

            item_path = item.path

            parts = Path(
                item_path
            ).parts

            if any(
                part in IGNORED_DIRECTORIES
                for part in parts
            ):
                continue

            if item.type == "dir":

                try:

                    children = (
                        repo.get_contents(
                            item_path,
                            ref=branch_name
                        )
                    )

                    walk(children)

                except GithubException as exc:

                    logger.warning(
                        "Skipping directory %s: %s",
                        item_path,
                        exc,
                    )

            elif item.type == "file":

                if not is_text_file(
                    item_path
                ):
                    continue

                collected.append(item)

    walk(contents)

    logger.info(
        "Collected %d repository files.",
        len(collected)
    )

    return collected


def read_repository_files(
    repo,
    files
):

    results = []

    for file in files:

        try:

            decoded = (
                file.decoded_content
                .decode(
                    "utf-8",
                    errors="replace"
                )
            )

            decoded = clean_code(
                decoded
            )

            results.append(
                {
                    "path": file.path,
                    "content": decoded,
                }
            )

        except Exception as exc:

            logger.warning(
                "Could not read %s: %s",
                file.path,
                exc,
            )

    return results


# ============================================================
# GEMINI PROMPT
# ============================================================

def build_analysis_prompt(
    repo_name: str,
    branch_name: str,
    files_data
):

    source = []

    for file in files_data:

        source.append(
            f"""
==============================
FILE: {file["path"]}
==============================

{file["content"]}
"""
        )

    repository_text = "\n".join(
        source
    )

    prompt = f"""
You are DevFix AI, an expert senior
software engineer, DevOps engineer,
security reviewer, and debugging assistant.

You are analyzing a real GitHub repository.

Repository:
{repo_name}

Branch:
{branch_name}

Your job is to analyze the code carefully
and identify actual problems instead of
inventing problems.

IMPORTANT RULES:

1. Do not claim something is broken
   unless there is evidence.

2. Distinguish between:
   - confirmed bug
   - likely bug
   - possible improvement

3. Never expose API keys, tokens,
   passwords, secrets, or credentials.

4. Do not recommend destructive changes.

5. Preserve existing functionality.

6. Prefer minimal and safe fixes.

7. Explain technical reasoning clearly.

8. If the repository looks healthy,
   say so.

9. Focus on:
   backend
   deployment
   API
   dependency
   configuration
   runtime
   integration problems

10. Pay special attention to issues
    that could cause deployment failures
    or production crashes.

Return your answer using this structure:

# DevFix AI Repository Analysis

## 1. Repository Overview

Briefly explain what the project appears
to do.

## 2. Technology Stack

List the languages, frameworks, libraries,
databases, APIs, and deployment technologies
you can identify.

## 3. Critical Problems

List confirmed high-impact problems.

For every problem include:

- File
- Problem
- Evidence
- Why it matters
- Recommended fix

## 4. Potential Problems

List issues that need verification.

## 5. Security Review

Check for:

- hard-coded secrets
- unsafe environment handling
- authentication problems
- authorization problems
- exposed endpoints
- dependency risks
- unsafe GitHub usage
- unsafe deployment configuration

Do NOT reproduce actual secrets.

## 6. Deployment Review

Look for likely problems involving:

- Vercel
- Railway
- Docker
- environment variables
- ports
- startup commands
- CORS
- build commands
- runtime configuration

Only mention platforms that are actually relevant.

## 7. Recommended Fix Plan

Give a numbered practical fix plan.

## 8. Final Assessment

Use one of:

HEALTHY
MINOR ISSUES
NEEDS ATTENTION
CRITICAL

Then explain why.

Here is the repository source:

{repository_text}
"""

    return prompt


# ============================================================
# GEMINI ANALYSIS WITH RETRIES
# ============================================================

def analyze_with_gemini(
    prompt: str
) -> str:

    # Primary model first.
    # Fallback models are tried only if the
    # primary model is temporarily unavailable.

    models = [
        GEMINI_MODEL,
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]

    # Remove duplicates while preserving order
    models = list(
        dict.fromkeys(models)
    )

    last_error = None

    for model in models:

        for attempt in range(3):

            try:

                logger.info(
                    "Trying Gemini model: %s "
                    "| attempt %d/3",
                    model,
                    attempt + 1,
                )

                response = (
                    gemini_client
                    .models
                    .generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.2,
                            max_output_tokens=6000,
                        ),
                    )
                )

                text = response.text

                if not text:

                    raise RuntimeError(
                        "Gemini returned "
                        "an empty response."
                    )

                logger.info(
                    "Gemini analysis successful "
                    "using %s",
                    model
                )

                return text

            except Exception as exc:

                last_error = exc

                error_text = str(exc)

                logger.warning(
                    "Gemini model %s attempt %d failed: %s",
                    model,
                    attempt + 1,
                    error_text,
                )

                upper_error = (
                    error_text.upper()
                )

                # Temporary errors:
                # 503 = service unavailable
                # 429 = rate limit/quota
                temporary_error = (
                    "503" in error_text
                    or "UNAVAILABLE" in upper_error
                    or "HIGH DEMAND" in upper_error
                    or "429" in error_text
                    or "RESOURCE_EXHAUSTED"
                    in upper_error
                )

                if temporary_error:

                    if attempt < 2:

                        wait_time = (
                            5 * (2 ** attempt)
                        )

                        logger.info(
                            "Temporary Gemini "
                            "problem. Retrying "
                            "in %d seconds...",
                            wait_time,
                        )

                        time.sleep(
                            wait_time
                        )

                        continue

                # Model not found:
                # immediately try the next model.
                model_not_found = (
                    "404" in error_text
                    or "NOT_FOUND"
                    in upper_error
                    or "NO LONGER AVAILABLE"
                    in upper_error
                )

                if model_not_found:
                    break

                # Authentication errors should
                # not be retried repeatedly.
                if (
                    "API KEY"
                    in upper_error
                    or "INVALID_ARGUMENT"
                    in upper_error
                ):
                    break

                break

        logger.warning(
            "Model %s failed. "
            "Trying next available model...",
            model
        )

    raise RuntimeError(
        "All configured Gemini models "
        "are currently unavailable.\n\n"
        f"Last error: {last_error}"
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = """
🤖 DevFix AI

I analyze GitHub repositories and help
identify:

🔎 Bugs
🧠 Root causes
🔐 Security problems
🚀 Deployment problems
📦 Dependency issues
🛠️ Recommended fixes

Usage:

/repo owner/repository

Example:

/repo Saideepthi228/devops-ai-bot-demo

You can also provide a GitHub URL.
"""

    await update.message.reply_text(
        message
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await start_command(
        update,
        context
    )


async def repo_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not context.args:

        await update.message.reply_text(
            "Please provide a GitHub repository.\n\n"
            "Example:\n"
            "/repo Saideepthi228/devops-ai-bot-demo"
        )

        return

    repo_input = " ".join(
        context.args
    )

    repo_name = extract_repo_name(
        repo_input
    )

    if not repo_name:

        await update.message.reply_text(
            "❌ I couldn't understand "
            "that repository.\n\n"
            "Use:\n"
            "/repo owner/repository"
        )

        return

    await update.message.chat.send_action(
        action=ChatAction.TYPING
    )

    status_message = (
        await update.message.reply_text(
            f"📦 Repository: {repo_name}\n\n"
            "🔐 Checking GitHub access..."
        )
    )

    try:

        # ----------------------------------------------------
        # 1. GitHub repository
        # ----------------------------------------------------

        repo = await asyncio.to_thread(
            get_repository,
            repo_name
        )

        # ----------------------------------------------------
        # 2. Branch
        # ----------------------------------------------------

        branch = await asyncio.to_thread(
            get_branch,
            repo
        )

        await status_message.edit_text(
            f"📦 Repository: {repo.full_name}\n"
            f"🌿 Branch: {branch.name}\n\n"
            "📖 Reading repository..."
        )

        # ----------------------------------------------------
        # 3. Collect files
        # ----------------------------------------------------

        files = await asyncio.to_thread(
            collect_repository_files,
            repo,
            branch.name
        )

        if not files:

            await status_message.edit_text(
                f"📦 Repository: {repo.full_name}\n"
                f"🌿 Branch: {branch.name}\n\n"
                "❌ No readable source files "
                "were found."
            )

            return

        await status_message.edit_text(
            f"📦 Repository: {repo.full_name}\n"
            f"🌿 Branch: {branch.name}\n\n"
            f"📁 Found {len(files)} "
            "relevant files.\n"
            "🧠 Preparing repository analysis..."
        )

        # ----------------------------------------------------
        # 4. Read files
        # ----------------------------------------------------

        files_data = await asyncio.to_thread(
            read_repository_files,
            repo,
            files
        )

        if not files_data:

            await status_message.edit_text(
                "❌ I found repository files, "
                "but couldn't read their contents."
            )

            return

        # ----------------------------------------------------
        # 5. Build Gemini prompt
        # ----------------------------------------------------

        prompt = build_analysis_prompt(
            repo.full_name,
            branch.name,
            files_data
        )

        # ----------------------------------------------------
        # 6. Gemini analysis
        # ----------------------------------------------------

        await status_message.edit_text(
            f"📦 Repository: {repo.full_name}\n"
            f"🌿 Branch: {branch.name}\n\n"
            f"📁 Files analyzed: "
            f"{len(files_data)}\n"
            "🤖 Gemini is analyzing the code..."
        )

        analysis = await asyncio.to_thread(
            analyze_with_gemini,
            prompt
        )

        # ----------------------------------------------------
        # 7. Send result
        # ----------------------------------------------------

        header = (
            "🤖 DevFix AI Analysis\n\n"
            f"📦 Repository: {repo.full_name}\n"
            f"🌿 Branch: {branch.name}\n"
            f"📁 Files analyzed: "
            f"{len(files_data)}\n\n"
        )

        try:
            await status_message.delete()
        except Exception:
            pass

        chunks = split_telegram_message(
            header + analysis
        )

        for index, chunk in enumerate(
            chunks
        ):

            await update.message.reply_text(
                chunk
            )

            if index < len(chunks) - 1:

                await asyncio.sleep(
                    0.5
                )

    except Exception as exc:

        logger.exception(
            "Repository analysis failed."
        )

        error_message = (
            "❌ DevFix AI analysis failed.\n\n"
            f"Error: {str(exc)}"
        )

        try:

            await status_message.edit_text(
                error_message
            )

        except Exception:

            await update.message.reply_text(
                error_message
            )


# ============================================================
# TELEGRAM ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Telegram error: %s",
        context.error
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "🚀 Starting DevFix AI..."
    )

    logger.info(
        "Gemini model: %s",
        GEMINI_MODEL
    )

    # --------------------------------------------------------
    # Test GitHub authentication
    # --------------------------------------------------------

    try:

        user = github.get_user()

        logger.info(
            "GitHub authenticated as: %s",
            user.login
        )

    except Exception as exc:

        logger.error(
            "GitHub authentication failed: %s",
            exc
        )

        raise

    logger.info(
        "Gemini client initialized."
    )

    # --------------------------------------------------------
    # Telegram network configuration
    # --------------------------------------------------------

    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
    )

    get_updates_request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
    )

    # --------------------------------------------------------
    # Telegram application
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .get_updates_request(
            get_updates_request
        )
        .build()
    )

    # --------------------------------------------------------
    # Commands
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "repo",
            repo_command
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "✅ DevFix AI is running..."
    )

    # --------------------------------------------------------
    # Start polling
    # --------------------------------------------------------

    application.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=-1
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()