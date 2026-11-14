
import os
import csv
import io
import sqlite3
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =============== CONFIGURATION ===============

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
DB_PATH = os.environ.get("DB_PATH", "reports.db")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing")

if not OWNER_ID:
    raise ValueError("OWNER_ID is missing")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# =============== DATABASE ===============

def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT NOT NULL,
                category TEXT NOT NULL,
                evidence TEXT NOT NULL,
                notes TEXT,
                status TEXT DEFAULT 'Pending',
                created_at TEXT NOT NULL
            )
        """)


# =============== ACCESS CONTROL ===============

async def authorized(update: Update):
    user = update.effective_user
    chat = update.effective_chat

    if (
        user
        and chat
        and user.id == OWNER_ID
        and chat.type == ChatType.PRIVATE
    ):
        return True

    if update.message:
        await update.message.reply_text(
            "Access denied. Owner-only bot."
        )

    return False


# =============== START / HELP ===============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return

    await update.message.reply_text(
        "Welcome to your private Report Manager.\n\n"
        "Commands:\n"
        "/add - Add a report record\n"
        "/list - View saved records\n"
        "/show ID - View record details\n"
        "/delete ID - Delete a record\n"
        "/status ID STATUS - Update status\n"
        "/export - Export records as CSV\n"
        "/help - Show commands"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return

    await start(update, context)


# =============== ADD RECORD ===============

async def add_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return

    raw = update.message.text.partition(" ")[2]

    parts = [p.strip() for p in raw.split("|", 3)]

    if len(parts) != 4 or not all(parts):
        await update.message.reply_text(
            "Format:\n"
            "/add @username | category | evidence link | notes"
        )
        return

    target, category, evidence, notes = parts

    created = datetime.now(timezone.utc).isoformat()

    with db() as conn:
        cursor = conn.execute("""
            INSERT INTO reports
            (target, category, evidence, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (target, category, evidence, notes, created))

        record_id = cursor.lastrowid

    await update.message.reply_text(
        f"Record saved successfully.\n"
        f"ID: {record_id}\n"
        f"Target: {target}\n"
        f"Category: {category}\n"
        f"Status: Pending"
    )


# =============== LIST RECORDS ===============

async def list_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return

    with db() as conn:
        rows = conn.execute("""
            SELECT id, target, category, status
            FROM reports
            ORDER BY id DESC
            LIMIT 30
        """).fetchall()

    if not rows:
        await update.message.reply_text("No records found.")
        return

    message = "SAVED RECORDS\n\n"

    for record_id, target, category, status in rows:
        message += (
            f"#{record_id} | {target}\n"
            f"Category: {category}\n"
            f"Status: {status}\n\n"
        )

    await update.message.reply_text(message)


# =============== SHOW RECORD ===============

async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /show ID")
        return

    record_id = int(context.args[0])

    with db() as conn:
        row = conn.execute("""
            SELECT * FROM reports WHERE id = ?
        """, (record_id,)).fetchone()

    if not row:
        await update.message.reply_text("Record not found.")
        return

    await update.message.reply_text(
        f"RECORD #{row[0]}\n\n"
        f"Target: {row[1]}\n"
        f"Category: {row[2]}\n"
        f"Evidence: {row[3]}\n"
        f"Notes: {row[4]}\n"
        f"Status: {row[5]}\n"
        f"Created: {row[6]}"
    )


# =============== DELETE RECORD ===============

async def delete_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /delete ID")
        return

    record_id = int(context.args[0])

    with db() as conn:
        cursor = conn.execute(
            "DELETE FROM reports WHERE id = ?",
            (record_id,)
        )

    if cursor.rowcount:
        await update.message.reply_text(
            f"Record #{record_id} deleted."
        )
    else:
        await update.message.reply_text("Record not found.")


# =============== UPDATE STATUS ===============

async def update_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /status ID Pending|Reviewed|Submitted|Closed"
        )
        return

    if not context.args[0].isdigit():
        await update.message.reply_text("Invalid record ID.")
        return

    record_id = int(context.args[0])
    status = " ".join(context.args[1:]).strip()

    allowed = ["Pending", "Reviewed", "Submitted", "Closed"]

    if status.lower() not in [s.lower() for s in allowed]:
        await update.message.reply_text(
            "Allowed statuses: Pending, Reviewed, Submitted, Closed"
        )
        return

    status = next(s for s in allowed if s.lower() == status.lower())

    with db() as conn:
        cursor = conn.execute(
            "UPDATE reports SET status = ? WHERE id = ?",
            (status, record_id)
        )

    if cursor.rowcount:
        await update.message.reply_text(
            f"Record #{record_id} updated to {status}."
        )
    else:
        await update.message.reply_text("Record not found.")


# =============== CSV EXPORT ===============

async def export_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await authorized(update):
        return

    with db() as conn:
        rows = conn.execute("""
            SELECT * FROM reports ORDER BY id DESC
        """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "ID", "Target", "Category", "Evidence",
        "Notes", "Status", "Created At"
    ])

    writer.writerows(rows)

    file = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    file.name = "report_records.csv"

    await update.message.reply_document(
        document=file,
        caption="Your report records export."
    )


# =============== MAIN ===============

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("add", add_report))
    app.add_handler(CommandHandler("list", list_reports))
    app.add_handler(CommandHandler("show", show_report))
    app.add_handler(CommandHandler("delete", delete_report))
    app.add_handler(CommandHandler("status", update_status))
    app.add_handler(CommandHandler("export", export_reports))

    print("Report Manager Bot is running.")

    timeout-minutes: 10


if __name__ == "__main__":
    main()
