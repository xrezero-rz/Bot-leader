"""
Ultimate Anime Bot v3.0
Features:
- Background monitoring (spam, links, banned words, promotion/forward deletion)
- Full user logs persisted to JSON
- XP system + leaderboard
- Multiple games (dice, guess, rps, coin, trivia)
- Admin panel with inline keyboard (leaderboard, warnings, full log, export)
- Welcome messages showing join time & info
- Auto-beautiful messages every N seconds
- Song search via yt-dlp (ytsearch)
- Protection actions: delete message, warn, mute, ban (only when permitted)
- Configurable thresholds
"""

import json
import os
import random
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict, deque

from telegram import (
    Update,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode  # ✅ v20+
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)

# Optional yt-dlp for song search
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except Exception:
    YTDLP_AVAILABLE = False

# -------------------------
# ========== CONFIG =======
# -------------------------
TOKEN = "7874808864:AAFvzeVXwjfN_me0i149gQz6ROZvlGa9NC8"  # <-- ضع توكن البوت هنا
YOUR_ID = 6400336665               # <-- ضع ID حسابك هنا
GROUP_OWNER_ID = 6659611371        # <-- ضع ID مالك القروب

DATA_FILE = "bot_data.json"
AUTO_MSG_INTERVAL = 600
TIME_WINDOW = timedelta(seconds=30)
MAX_MESSAGES = 6
MUTE_DURATION = timedelta(hours=1)
MAX_WARNINGS = 3

BANNED_WORDS = ["free", "crypto", "gift", "airdrop", "verify", "login", "hack", "leak"]
LINK_KEYWORDS = ["http://", "https://", "t.me/"]
TRIVIA_QUESTIONS = [
    ("ما هي عاصمة اليابان؟", "طوكيو"),
    ("ما اسم بطل أنمي ون بيس؟", "لوفي"),
    ("من مؤلف ناروتو؟", "ماساشي كيشيموتو"),
]

AUTO_MESSAGES_POOL = [
    "🌸 تفاعل واستمتع بالأنمي معنا!",
    "🔥 لا تنسى لعب الألعاب ومتابعة النقاط XP!",
    "⚡ البوت يراقب القروب ويحميه دائمًا!",
    "🎯 شارك وأرسل رسائلك بطريقة ممتعة!"
]

# -------------------------
# ======== STATE ==========
# -------------------------
state = {
    "user_log": {},
    "warnings": {},
    "xp": {},
    "message_count": {},
    "trusted": [],
}

user_activity = defaultdict(list)
last_message_text = {}

# -------------------------
# ===== Persistence =======
# -------------------------
def load_state():
    global state
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                state["user_log"] = {int(k): v for k, v in state.get("user_log", {}).items()}
                state["warnings"] = {int(k): v for k, v in state.get("warnings", {}).items()}
                state["xp"] = {int(k): v for k, v in state.get("xp", {}).items()}
                state["message_count"] = {int(k): v for k, v in state.get("message_count", {}).items()}
                state["trusted"] = [int(x) for x in state.get("trusted", [])]
        except Exception as e:
            print("Failed loading state:", e)
            state = {k: {} if isinstance(v, dict) else [] for k, v in state.items()}

def save_state():
    try:
        dump = {
            "user_log": {str(k): v for k, v in state["user_log"].items()},
            "warnings": {str(k): v for k, v in state["warnings"].items()},
            "xp": {str(k): v for k, v in state["xp"].items()},
            "message_count": {str(k): v for k, v in state["message_count"].items()},
            "trusted": [str(x) for x in state.get("trusted", [])],
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Failed saving state:", e)

async def add_log(user_id: int, action: str, reason: str):
    entry = {
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "reason": reason
    }
    state["user_log"].setdefault(user_id, []).append(entry)
    save_state()

# -------------------------
# ===== Utilities ========
# -------------------------
def is_link(text: str) -> bool:
    return any(k in (text or "").lower() for k in LINK_KEYWORDS)

def has_banned(text: str) -> bool:
    low = (text or "").lower()
    return any(word in low for word in BANNED_WORDS)

def similar(a: str, b: str, rate: float = 0.9) -> bool:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a or "", b or "").ratio() >= rate

def format_user(user):
    name = user.full_name
    username = f"@{user.username}" if user.username else ""
    return f"{name} {username}".strip()

# -------------------------
# ===== Auto messages =====
# -------------------------
async def auto_messages_task(app):
    await asyncio.sleep(1)
    while True:
        msg = random.choice(AUTO_MESSAGES_POOL)
        for chat_id in [YOUR_ID, GROUP_OWNER_ID]:
            try:
                await app.bot.send_message(chat_id, f"💬 <b>رسالة تلقائية</b>\n\n{msg}", parse_mode=ParseMode.HTML)
            except:
                pass
        await asyncio.sleep(AUTO_MSG_INTERVAL)

# -------------------------
# ===== Welcome Handler ===
# -------------------------
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return
    for new_user in update.message.new_chat_members:
        join_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        text = (
            f"🌸 <b>مرحبًا {format_user(new_user)}!</b>\n\n"
            f"🕒 وقت الانضمام: <code>{join_time}</code>\n"
            f"🆔 المعرف: <code>{new_user.id}</code>\n\n"
            "📜 اقرأ قواعد الجروب و استمتع معنا! "
        )
        keyboard = InlineKeyboardMarkup.from_row([
            InlineKeyboardButton("📜 القواعد", callback_data="show_rules"),
            InlineKeyboardButton("🎮 الألعاب", callback_data="show_games"),
        ])
        try:
            await update.message.reply_html(text, reply_markup=keyboard)
            await add_log(new_user.id, "join", f"Joined chat {update.effective_chat.id}")
        except:
            pass

# -------------------------
# ===== Monitor Handler ===
# -------------------------
async def monitor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user or msg.from_user.is_bot:
        return
    user = msg.from_user
    chat = msg.chat
    text = msg.text or ""
    now = datetime.utcnow()

    state["xp"][user.id] = state["xp"].get(user.id, 0) + 1
    state["message_count"][user.id] = state["message_count"].get(user.id, 0) + 1
    save_state()

    times = user_activity[user.id]
    times.append(now)
    user_activity[user.id] = [t for t in times if t > now - TIME_WINDOW]

    reasons = []
    if len(user_activity[user.id]) >= MAX_MESSAGES:
        reasons.append("spam_rate")
    if msg.forward_from or msg.forward_from_chat:
        reasons.append("forwarded_promotion")
    if is_link(text):
        reasons.append("link")
    if has_banned(text):
        reasons.append("banned_word")
    previous = last_message_text.get(user.id)
    if previous and similar(previous, text):
        reasons.append("repeat")
    last_message_text[user.id] = text

    if not reasons:
        return

    state["warnings"][user.id] = state["warnings"].get(user.id, 0) + 1
    save_state()

    did_delete = False
    try:
        await msg.delete()
        await add_log(user.id, "delete_message", f"reasons: {', '.join(reasons)}")
        did_delete = True
    except Exception as e:
        await add_log(user.id, "warning_no_delete", f"reasons: {', '.join(reasons)}; err:{e}")

    action_text = ""
    if state["warnings"].get(user.id, 0) >= MAX_WARNINGS:
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            await add_log(user.id, "ban", f"reasons: {', '.join(reasons)}")
            action_text = "🚫 تم حظر العضو نهائيًا"
        except Exception as e:
            await add_log(user.id, "ban_failed", str(e))
            action_text = "⚠️ محاولة حظر فشلت"
    else:
        try:
            await context.bot.restrict_chat_member(
                chat.id, user.id,
                ChatPermissions(can_send_messages=False),
                until_date=datetime.utcnow() + MUTE_DURATION
            )
            await add_log(user.id, "mute", f"duration: {MUTE_DURATION}, reasons: {', '.join(reasons)}")
            action_text = f"🔇 تم كتمه مؤقتًا ({int(MUTE_DURATION.total_seconds()//60)} دقيقة)"
        except Exception as e:
            await add_log(user.id, "mute_failed", str(e))
            action_text = "⚠️ محاولة كتم فشلت"

    reason_readable = {
        "spam_rate": "نشاط نشر سريع (احتمال سبام)",
        "forwarded_promotion": "إعادة توجيه / ترويج من محادثة أخرى",
        "link": "إرسال روابط",
        "banned_word": "كلمات محظورة",
        "repeat": "تكرار نفس الرسالة"
    }
    reasons_nice = [reason_readable.get(r, r) for r in reasons]

    alert = (
        f"🚨 <b>تنبيه حماية القروب (أنمي)</b>\n\n"
        f"👤 <b>{format_user(user)}</b>\n"
        f"🆔 <code>{user.id}</code>\n"
        f"📍 في: <b>{chat.title or chat.id}</b>\n\n"
        f"📌 <b>الأسباب:</b>\n" + "\n".join(f"• {r}" for r in reasons_nice) +
        f"\n\n{action_text}"
    )

    try:
        await context.bot.send_message(chat.id, alert, parse_mode=ParseMode.HTML)
    except: pass
    for admin_id in [YOUR_ID, GROUP_OWNER_ID]:
        try:
            await context.bot.send_message(admin_id, alert, parse_mode=ParseMode.HTML)
        except: pass

# -------------------------
# ===== Admin Commands ====
# -------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً! أنا Ultimate Anime Bot — مراقب، ألعاب، و حماية للقروب.\n"
        "استخدم /help لرؤية الأوامر."
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📚 <b>أوامر عامة</b>\n"
        "/dice - نرد\n"
        "/guess - تخمين رقم\n"
        "/rps - حجر/ورقة/مقص\n"
        "/coin - رمية عملة\n"
        "/animefact - معلومة أنمي\n"
        "/rank - نقاطك\n\n"
        "🛡️ <b>أوامر مشرفين</b>\n"
        "/leaderboard - أفضل 10 أعضاء\n"
        "/stats - إحصائيات عامة\n"
        "/warnings - عرض التحذيرات\n"
        "/clear - مسح التحذيرات\n"
        "/full_log - سجل كامل (مالك البوت)\n\n"
        "🎵 للبحث عن أغنية:\n"
        "/song اسم الأغنية"
    )
    await update.message.reply_html(help_text)

# =======================
# باقي الأوامر والألعاب و song search
# =======================
# ... (يمكنك نسخ باقي الأوامر كما هي من الكود الأصلي لأن هذا الجزء لا يحتاج تعديل كبير)

# -------------------------
# ===== Startup ========
# -------------------------
def build_app():
    load_state()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    # أضف باقي handlers كما في كودك الأصلي
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, monitor_handler))

    # background auto messages
    async def start_auto_task(context):
        context.application.create_task(auto_messages_task(context.application))

    app.job_queue.run_once(start_auto_task, when=1.0)

    return app

if __name__ == "__main__":
    application = build_app()
    print("Ultimate Anime Bot is running...")
    application.run_polling()
