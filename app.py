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
    ParseMode,
)
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
TOKEN = "7874808864:AAE8duSMo7E56V8CVRjxwfHU-JohNGncyLg"          # <-- ضع توكن البوت هنا
YOUR_ID = 6400336665               # <-- ضع ID حسابك هنا (مالك البوت الذي يستقبل المراسلات)
GROUP_OWNER_ID = 6659611371        # <-- ضع ID مالك القروب (يمكن أن يكون نفس YOUR_ID)

DATA_FILE = "bot_data.json"       # ملف حفظ بيانات (logs, warnings, xp...)
AUTO_MSG_INTERVAL = 600           # رسالة تلقائية كل 600 ثانية = 10 دقائق
TIME_WINDOW = timedelta(seconds=30)
MAX_MESSAGES = 6                  # سبام threshold
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
# We'll persist these in DATA_FILE to survive restarts
state = {
    "user_log": {},         # user_id -> list of entries {time, action, reason}
    "warnings": {},         # user_id -> int
    "xp": {},               # user_id -> int
    "message_count": {},    # user_id -> int
    "trusted": [],          # admins or whitelisted users
}

# In-memory runtime data (not persisted)
user_activity = defaultdict(list)  # user_id -> list of datetimes in window
last_message_text = {}             # user_id -> last message text


# -------------------------
# ===== Persistence =======
# -------------------------
def load_state():
    global state
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                # convert keys back to int
                state["user_log"] = {int(k): v for k, v in state.get("user_log", {}).items()}
                state["warnings"] = {int(k): v for k, v in state.get("warnings", {}).items()}
                state["xp"] = {int(k): v for k, v in state.get("xp", {}).items()}
                state["message_count"] = {int(k): v for k, v in state.get("message_count", {}).items()}
                state["trusted"] = [int(x) for x in state.get("trusted", [])]
        except Exception as e:
            print("Failed loading state:", e)
            state = {
                "user_log": {},
                "warnings": {},
                "xp": {},
                "message_count": {},
                "trusted": [],
            }
    else:
        # ensure keys exist
        state = {
            "user_log": {},
            "warnings": {},
            "xp": {},
            "message_count": {},
            "trusted": [],
        }


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
    if not text:
        return False
    return any(k in text.lower() for k in LINK_KEYWORDS)


def has_banned(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(word in low for word in BANNED_WORDS)


def similar(a: str, b: str, rate: float = 0.9) -> bool:
    if not a or not b:
        return False
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() >= rate


def format_user(user):
    name = user.full_name
    username = f"@{user.username}" if user.username else ""
    return f"{name} {username}".strip()


# -------------------------
# ===== Auto messages =====
# -------------------------
async def auto_messages_task(app):
    await app.bot.wait_until_ready() if hasattr(app.bot, "wait_until_ready") else asyncio.sleep(0)
    while True:
        msg = random.choice(AUTO_MESSAGES_POOL)
        # send to owner and group owner
        for chat_id in [YOUR_ID, GROUP_OWNER_ID]:
            try:
                await app.bot.send_message(chat_id, f"💬 <b>رسالة تلقائية</b>\n\n{msg}", parse_mode=ParseMode.HTML)
            except Exception:
                pass
        await asyncio.sleep(AUTO_MSG_INTERVAL)


# -------------------------
# ===== Welcome Handler ===
# -------------------------
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return
    for new_user in update.message.new_chat_members:
        # time of join is now (we can't know earlier)
        join_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        text = (
            f"🌸 <b>مرحبًا {format_user(new_user)}!</b>\n\n"
            f"🕒 وقت الانضمام: <code>{join_time}</code>\n"
            f"🆔 المعرف: <code>{new_user.id}</code>\n\n"
            "📜 اقرأ قواعد الجروب و استمتع معنا! "
        )
        # Welcome keyboard (optional)
        keyboard = InlineKeyboardMarkup.from_row([
            InlineKeyboardButton("📜 القواعد", callback_data="show_rules"),
            InlineKeyboardButton("🎮 الألعاب", callback_data="show_games"),
        ])
        try:
            await update.message.reply_html(text, reply_markup=keyboard)
            # log
            await add_log(new_user.id, "join", f"Joined chat {update.effective_chat.id}")
        except Exception:
            pass


# -------------------------
# ===== Monitor Handler ===
# -------------------------
async def monitor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return

    user = msg.from_user
    chat = msg.chat
    text = msg.text or ""
    now = datetime.utcnow()

    # ignore bots
    if user.is_bot:
        return

    # update xp and message count
    state["xp"][user.id] = state["xp"].get(user.id, 0) + 1
    state["message_count"][user.id] = state["message_count"].get(user.id, 0) + 1
    save_state()

    # track activity window
    times = user_activity[user.id]
    times.append(now)
    # keep only within TIME_WINDOW
    user_activity[user.id] = [t for t in times if t > now - TIME_WINDOW]

    reasons = []

    # spam detection
    if len(user_activity[user.id]) >= MAX_MESSAGES:
        reasons.append("spam_rate")

    # forward detection (promotion from other convo)
    if msg.forward_from or msg.forward_from_chat:
        reasons.append("forwarded_promotion")

    # link detection
    if is_link(text):
        reasons.append("link")

    # banned word
    if has_banned(text):
        reasons.append("banned_word")

    # repeated message
    previous = last_message_text.get(user.id)
    if previous and similar(previous, text):
        reasons.append("repeat")
    last_message_text[user.id] = text

    # if no reason, nothing to do
    if not reasons:
        return

    # increase warning
    state["warnings"][user.id] = state["warnings"].get(user.id, 0) + 1
    save_state()

    # delete message when it's promotion/link/banned or spam etc.
    did_delete = False
    try:
        await msg.delete()
        await add_log(user.id, "delete_message", f"reasons: {', '.join(reasons)}")
        did_delete = True
    except Exception as e:
        # can't delete -> still log warning
        await add_log(user.id, "warning_no_delete", f"reasons: {', '.join(reasons)}; err:{e}")

    # decide action: mute or ban depending on warnings
    action_text = ""
    if state["warnings"].get(user.id, 0) >= MAX_WARNINGS:
        # ban
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            await add_log(user.id, "ban", f"reasons: {', '.join(reasons)}")
            action_text = "🚫 تم حظر العضو نهائيًا"
        except Exception as e:
            await add_log(user.id, "ban_failed", str(e))
            action_text = "⚠️ محاولة حظر فشلت (قد لا يملك البوت صلاحيات كافية)"
    else:
        # mute
        try:
            await context.bot.restrict_chat_member(
                chat.id,
                user.id,
                ChatPermissions(can_send_messages=False),
                until_date=datetime.utcnow() + MUTE_DURATION
            )
            await add_log(user.id, "mute", f"duration: {MUTE_DURATION}, reasons: {', '.join(reasons)}")
            action_text = f"🔇 تم كتمه مؤقتًا ({int(MUTE_DURATION.total_seconds()//60)} دقيقة)"
        except Exception as e:
            await add_log(user.id, "mute_failed", str(e))
            action_text = "⚠️ محاولة كتم فشلت (قد لا يملك البوت صلاحيات كافية)"

    # prepare a pretty alert
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

    # send alert to chat and to owners
    try:
        await context.bot.send_message(chat.id, alert, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    # notify owner & group owner privately
    for admin_id in [YOUR_ID, GROUP_OWNER_ID]:
        try:
            await context.bot.send_message(admin_id, alert, parse_mode=ParseMode.HTML)
        except Exception:
            pass


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


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    member = await context.bot.get_chat_member(update.effective_chat.id, update.message.from_user.id)
    if not is_admin(member):
        return await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
    sorted_users = sorted(state["message_count"].items(), key=lambda x: x[1], reverse=True)[:10]
    text = "<b>🏆 Leaderboard (Top 10)</b>\n\n"
    rank = 1
    for uid, cnt in sorted_users:
        # try to fetch user's name
        try:
            user_obj = await context.bot.get_chat_member(update.effective_chat.id, uid)
            name = user_obj.user.full_name
        except Exception:
            name = f"User {uid}"
        text += f"{rank}. {name} — {cnt} رسالة\n"
        rank += 1
    await update.message.reply_html(text)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    member = await context.bot.get_chat_member(update.effective_chat.id, update.message.from_user.id)
    if not is_admin(member):
        return await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
    total_msgs = sum(state["message_count"].values())
    total_warns = sum(state["warnings"].values())
    text = (
        f"📊 <b>إحصائيات</b>\n\n"
        f"👥 أعضاء مشاركون: {len(state['message_count'])}\n"
        f"✉️ إجمالي الرسائل المحسوبة: {total_msgs}\n"
        f"⚠️ إجمالي التحذيرات: {total_warns}\n"
    )
    await update.message.reply_html(text)


async def cmd_warnings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    member = await context.bot.get_chat_member(update.effective_chat.id, update.message.from_user.id)
    if not is_admin(member):
        return await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
    if not state["warnings"]:
        return await update.message.reply_text("لا توجد تحذيرات.")
    text = "<b>⚠️ تحذيرات الأعضاء:</b>\n\n"
    for uid, w in state["warnings"].items():
        text += f"• {uid}: {w}\n"
    await update.message.reply_html(text)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    member = await context.bot.get_chat_member(update.effective_chat.id, update.message.from_user.id)
    if not is_admin(member):
        return await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
    state["warnings"].clear()
    save_state()
    await update.message.reply_text("🧹 تم مسح كل التحذيرات.")


async def cmd_full_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # only bot owner can ask full log
    if update.message.from_user.id != YOUR_ID:
        await update.message.reply_text("❌ هذا الأمر للمالك فقط.")
        return
    text = "<b>📜 السجل الكامل للمستخدمين</b>\n\n"
    for uid, entries in state["user_log"].items():
        text += f"— User {uid} ({state['xp'].get(uid,0)} XP)\n"
        for e in entries[-10:]:  # show last 10 entries per user (to avoid huge text)
            text += f"   • {e['time']} | {e['action']} | {e['reason']}\n"
        text += "\n"
    await update.message.reply_html(text)


# -------------------------
# ===== Games Commands ====
# -------------------------
async def cmd_dice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_dice()


async def cmd_guess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    number = random.randint(1, 6)
    # store in context for reply handling if needed
    await update.message.reply_text(f"🎯 تخمين تم: {number} — (نموذج بسيط)")

async def cmd_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = random.choice(["رأس", "ذيل"])
    await update.message.reply_text(f"🪙 النتيجة: {result}")

async def cmd_rps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # show inline keyboard to play
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✂️ مقص", callback_data="rps_scissors"),
             InlineKeyboardButton("🪨 حجر", callback_data="rps_rock"),
             InlineKeyboardButton("📄 ورقة", callback_data="rps_paper")]
        ]
    )
    await update.message.reply_text("اختر: حجر / ورقة / مقص", reply_markup=keyboard)


# handle rps callbacks
async def cb_rps(choice: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    comp = random.choice(["scissors", "rock", "paper"])
    mapping = {"scissors": "✂️ مقص", "rock": "🪨 حجر", "paper": "📄 ورقة"}
    user_choice = choice
    # determine outcome
    win = None
    if user_choice == comp:
        outcome = "تعادل"
    elif (user_choice == "scissors" and comp == "paper") or \
         (user_choice == "rock" and comp == "scissors") or \
         (user_choice == "paper" and comp == "rock"):
        outcome = "فزت 🎉"
    else:
        outcome = "خسرت 😢"

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        f"أنت اخترت: {mapping[user_choice]}\nالكمبيوتر اختر: {mapping[comp]}\n\nنتيجة: {outcome}"
    )


# callback dispatcher
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data

    if data == "show_rules":
        await q.answer()
        await q.message.reply_text("📜 قواعد الجروب: 1) الاحترام 2) عدم الترويج 3) عدم السبام")
        return
    if data == "show_games":
        await q.answer()
        kb = InlineKeyboardMarkup.from_column([
            InlineKeyboardButton("🎲 نرد", callback_data="game_dice"),
            InlineKeyboardButton("🎯 تخمين", callback_data="game_guess"),
            InlineKeyboardButton("🎮 Trivia", callback_data="game_trivia"),
        ])
        await q.message.reply_text("اختر لعبة:", reply_markup=kb)
        return

    # RPS callbacks
    if data.startswith("rps_"):
        choice = data.split("_")[1]
        await cb_rps(choice, update, context)
        return

    if data == "game_dice":
        await q.answer()
        await q.message.reply_dice()
        return
    if data == "game_guess":
        await q.answer()
        num = random.randint(1, 6)
        await q.message.reply_text(f"🎯 رقم عشوائي: {num}")
        return
    if data == "game_trivia":
        await q.answer()
        qn, ans = random.choice(TRIVIA_QUESTIONS)
        await q.message.reply_text(f"❓ {qn}\n✅ الإجابة: {ans}")
        return


# -------------------------
# ===== Song search =======
# -------------------------
async def cmd_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not YTDLP_AVAILABLE:
        return await update.message.reply_text("ميزة البحث عن أغاني غير متاحة (yt-dlp غير منصّب).")
    q = " ".join(context.args)
    if not q:
        return await update.message.reply_text("استخدم: /song اسم الأغنية")
    await update.message.reply_text(f"🔎 جارٍ البحث عن: {q} ...")
    # use yt-dlp to search youtube
    ydl_opts = {"quiet": True, "skip_download": True, "format": "bestaudio/best", "noplaylist": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            results = ydl.extract_info(f"ytsearch5:{q}", download=False)
            entries = results.get("entries", [])
            if not entries:
                return await update.message.reply_text("لم أجد نتائج.")
            # pick first result
            first = entries[0]
            title = first.get("title")
            uploader = first.get("uploader")
            duration = first.get("duration")
            webpage = first.get("webpage_url")
            desc = f"🎵 <b>{title}</b>\n👤 {uploader}\n⏱️ {duration} ثانية\n🔗 {webpage}"
            await update.message.reply_html(desc)
        except Exception as e:
            await update.message.reply_text(f"حدث خطأ أثناء البحث: {e}")


# -------------------------
# ===== Startup ========
# -------------------------
def build_app():
    load_state()
    app = ApplicationBuilder().token(TOKEN).build()

    # core handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # admin commands
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("warnings", cmd_warnings))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("full_log", cmd_full_log))

    # games
    app.add_handler(CommandHandler("dice", cmd_dice))
    app.add_handler(CommandHandler("guess", cmd_guess))
    app.add_handler(CommandHandler("coin", cmd_coin))
    app.add_handler(CommandHandler("rps", cmd_rps))
    app.add_handler(CommandHandler("rank", rank))
    app.add_handler(CommandHandler("animefact", animefact))
    app.add_handler(CommandHandler("song", cmd_song))

    # message handlers
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, monitor_handler))

    # callbacks
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # background auto messages
    # schedule as job_queue task
    async def start_auto_task(context):
        context.application.create_task(auto_messages_task(context.application))

    app.job_queue.run_once(start_auto_task, when=1.0)

    return app


# -------------------------
# ===== Run ========
# -------------------------
if __name__ == "__main__":
    application = build_app()
    print("Ultimate Anime Bot is running...")
    application.run_polling()