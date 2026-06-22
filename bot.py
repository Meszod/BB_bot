import os
import sys
import socket
import asyncio
import logging
import json
import psutil
from datetime import datetime, timedelta
from collections import defaultdict

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.error import BadRequest, Forbidden
from telegram import BotCommandScopeChat, BotCommandScopeDefault

# ══════════════════════════════════════════════
#  SOZLAMALAR  (env yoki to'g'ridan-to'g'ri)
# ══════════════════════════════════════════════
TOKEN        = os.getenv("BOT_TOKEN", "8718700659:AAGg7NApw9Hm2V3tHHE8HRJeUtzhPvikeHY")
ADMIN_ID     = int(os.getenv("ADMIN_ID", "8517530604"))
BATTLE_CHANNEL  = "@manolisi_19"
BOOST_LINK      = "https://t.me/boost/manolisi_19"
start_number    = 1

# Maksimal timer (daqiqada) — 1 oy = 30 kun
MAX_TIMER_MINUTES = 30 * 24 * 60  # 43200 daqiqa

# Ma'lumotlar bazasi fayli (Railway volume ga ulanadi)
DATA_FILE = os.getenv("DATA_FILE", "data.json")

REQUIRED_CHANNELS = [
    ('@manolisi_ozmdan', 'Kanal'),
    ('@bedeutungslosM', 'Kanal'),
    ('@onlinebattlee',  'Battle-Kanal'),
]

# ══════════════════════════════════════════════
#  STATE
# ══════════════════════════════════════════════
registered_users: list   = []
user_data:        dict   = {}
user_stats:       dict   = {}
daily_stats:      dict   = {}
battle_history:   list   = []
banned_users:     set    = set()
user_warnings:    dict   = defaultdict(int)
announcements:    str    = ""
battle_active:    bool   = False
battle_timer:     int    = 0
battle_start_time         = None

bot_stats = {
    'total_users':   0,
    'total_battles': 0,
    'messages_sent': 0,
    'start_date':    datetime.now().isoformat(),
}

# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  SINGLE-INSTANCE LOCK
# ══════════════════════════════════════════════
_lock_socket = None

def ensure_single_instance():
    global _lock_socket
    # Railway/Docker konteynerlarida bitta jarayon ishlaydi, shuning uchun
    # lock muvaffaqiyatsiz bo'lsa, faqat ogohlantirib davom etamiz.
    try:
        _lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_socket.bind(('127.0.0.1', 47291))
        _lock_socket.listen(1)
    except OSError:
        if os.getenv("ALLOW_MULTI_INSTANCE", "0") == "1":
            logger.warning("Single-instance lock o'tkazib yuborildi (ALLOW_MULTI_INSTANCE=1).")
            return
        print("❌ Bot allaqachon ishlamoqda! Avval uni to'xtating.")
        sys.exit(1)

# ══════════════════════════════════════════════
#  DATA — LOAD / SAVE  (oddiy JSON "DB")
#  Bot qayta ishga tushganda barcha holat
#  (battle holati, timer, ishtirokchilar va h.k.)
#  shu fayldan tiklanadi.
# ══════════════════════════════════════════════
def load_data():
    global registered_users, start_number, BATTLE_CHANNEL, BOOST_LINK
    global user_data, user_stats, daily_stats, battle_history
    global banned_users, user_warnings, bot_stats, announcements
    global battle_active, battle_timer, battle_start_time
    try:
        with open(DATA_FILE, encoding='utf-8') as f:
            d = json.load(f)
        registered_users = d.get('users', [])
        start_number      = d.get('start_number', 1)
        BATTLE_CHANNEL    = d.get('battle_channel', BATTLE_CHANNEL)
        BOOST_LINK        = d.get('boost_link', BOOST_LINK)
        user_data         = {int(k): v for k, v in d.get('user_data', {}).items()}
        user_stats        = {int(k): v for k, v in d.get('user_stats', {}).items()}
        daily_stats       = d.get('daily_stats', {})
        battle_history    = d.get('battle_history', [])
        banned_users      = set(d.get('banned_users', []))
        user_warnings     = defaultdict(int, {int(k): v for k, v in d.get('user_warnings', {}).items()})
        bot_stats         = d.get('bot_stats', bot_stats)
        announcements     = d.get('announcements', "")

        # Battle holatini tiklash (bot to'xtab qolsa ham davom etishi uchun)
        battle_active = d.get('battle_active', False)
        battle_timer  = d.get('battle_timer', 0)
        bst = d.get('battle_start_time')
        battle_start_time = datetime.fromisoformat(bst) if bst else None

        logger.info("data.json dan ma'lumotlar yuklandi.")
    except FileNotFoundError:
        logger.info(f"{DATA_FILE} topilmadi — yangi fayl yaratiladi.")

def save_data():
    try:
        tmp_file = DATA_FILE + ".tmp"
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump({
                'users':          registered_users,
                'start_number':   start_number,
                'battle_channel': BATTLE_CHANNEL,
                'boost_link':     BOOST_LINK,
                'user_data':      {str(k): v for k, v in user_data.items()},
                'user_stats':     {str(k): v for k, v in user_stats.items()},
                'daily_stats':    daily_stats,
                'battle_history': battle_history,
                'banned_users':   list(banned_users),
                'user_warnings':  {str(k): v for k, v in user_warnings.items()},
                'bot_stats':      bot_stats,
                'announcements':  announcements,
                'battle_active':  battle_active,
                'battle_timer':   battle_timer,
                'battle_start_time': battle_start_time.isoformat() if battle_start_time else None,
            }, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, DATA_FILE)  # atomik yozish — fayl buzilib qolmasligi uchun
    except Exception as e:
        logger.error(f"save_data xatosi: {e}")

# ══════════════════════════════════════════════
#  YORDAMCHI FUNKSIYALAR
# ══════════════════════════════════════════════
def format_time(seconds: float) -> str:
    if seconds <= 0:
        return "0:00"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if d:
        return f"{d}k {h:02d}:{m:02d}:{s:02d}"
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def start_battle_timer(minutes: int):
    global battle_timer, battle_start_time
    minutes = max(1, min(minutes, MAX_TIMER_MINUTES))
    battle_timer      = minutes * 60
    battle_start_time = datetime.now()

def extend_battle_timer(extra_minutes: int) -> int:
    """Joriy battle timerini cho'zish (max MAX_TIMER_MINUTES gacha). Yangi umumiy daqiqani qaytaradi."""
    global battle_timer
    remaining = get_battle_time_remaining()
    new_total_seconds = remaining + extra_minutes * 60
    max_seconds = MAX_TIMER_MINUTES * 60
    new_total_seconds = min(new_total_seconds, max_seconds)
    battle_timer = int((datetime.now() - battle_start_time).total_seconds() + new_total_seconds) if battle_start_time else int(new_total_seconds)
    return int(new_total_seconds // 60)

def get_battle_time_remaining() -> float:
    if not battle_start_time or battle_timer <= 0:
        return 0
    elapsed = (datetime.now() - battle_start_time).total_seconds()
    return max(0.0, battle_timer - elapsed)

def is_valid_username(username: str) -> bool:
    if not username.startswith('@'):
        return False
    un = username[1:]
    return 5 <= len(un) <= 32 and all(c.isalnum() or c == '_' for c in un)

def get_channel_buttons(not_subscribed):
    if not not_subscribed:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text=name, url=f"https://t.me/{ch[1:]}")]
        for ch, name in not_subscribed
    ])

def get_boost_button() -> InlineKeyboardMarkup:
    """Inline Boost tugmasi"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Boost!", url=BOOST_LINK)]
    ])

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list:
    if user_id in banned_users:
        return [("banned", "Siz bloklangansiz")]
    not_sub = []
    for ch, name in REQUIRED_CHANNELS:
        try:
            m = await context.bot.get_chat_member(chat_id=ch, user_id=user_id)
            if m.status not in ('member', 'administrator', 'creator'):
                not_sub.append((ch, name))
        except (BadRequest, Forbidden):
            not_sub.append((ch, name))
    return not_sub

def ordinal_uz(pos: int) -> str:
    """1 -> '1-o'rin', 2 -> '2-o'rin' va h.k."""
    return f"{pos}-o'rin"

# ══════════════════════════════════════════════
#  STATISTIKA
# ══════════════════════════════════════════════
def update_user_stats(user_id: int, action: str):
    now_iso = datetime.now().isoformat()
    if user_id not in user_stats:
        user_stats[user_id] = {
            'battles_joined': 0,
            'battles_won':    0,
            'total_messages': 0,
            'join_date':      now_iso,
            'last_activity':  now_iso,
        }
    user_stats[user_id]['last_activity'] = now_iso
    if action == 'battle_join':
        user_stats[user_id]['battles_joined'] += 1
    elif action == 'battle_win':
        user_stats[user_id]['battles_won'] += 1
    elif action == 'message':
        user_stats[user_id]['total_messages'] += 1

def get_today_stats() -> dict:
    today = datetime.now().strftime('%Y-%m-%d')
    if today not in daily_stats:
        daily_stats[today] = {
            'new_users':       0,
            'battles_started': 0,
            'total_messages':  0,
            'active_users':    [],
        }
    # Har doim list bo'lishini kafolatlaymiz
    if isinstance(daily_stats[today].get('active_users'), set):
        daily_stats[today]['active_users'] = list(daily_stats[today]['active_users'])
    return daily_stats[today]

# ══════════════════════════════════════════════
#  NOTIFICATION
# ══════════════════════════════════════════════
async def send_notification(
    context: ContextTypes.DEFAULT_TYPE,
    message: str,
    users: list = None,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> tuple[int, int]:
    if users is None:
        users = list(user_data.keys())
    ok = err = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, message, reply_markup=reply_markup, parse_mode=parse_mode)
            ok += 1
        except Exception as e:
            err += 1
            logger.debug(f"send_notification uid={uid}: {e}")
        await asyncio.sleep(0.05)
    logger.info(f"Notification: ok={ok} err={err}")
    return ok, err

# ══════════════════════════════════════════════
#  INLINE KEYBOARD — MENYULAR
# ══════════════════════════════════════════════
def kb_admin_main():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔥 Battle",         callback_data="admin_battle_menu"),
            InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users_menu"),
        ],
        [
            InlineKeyboardButton("📊 Statistika",     callback_data="admin_stats_menu"),
            InlineKeyboardButton("⚙️ Sozlamalar",     callback_data="admin_settings_menu"),
        ],
        [
            InlineKeyboardButton("📢 E'lonlar",       callback_data="admin_announcements_menu"),
            InlineKeyboardButton("🔧 Texnik",         callback_data="admin_technical_menu"),
        ],
    ])

def kb_battle():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Boshlash",       callback_data="start_battle"),
            InlineKeyboardButton("⏹️ To'xtatish",     callback_data="stop_battle"),
        ],
        [
            InlineKeyboardButton("⏰ Timer",          callback_data="set_timer"),
            InlineKeyboardButton("➕ Cho'zish",       callback_data="extend_timer"),
        ],
        [
            InlineKeyboardButton("📋 Ro'yxat",        callback_data="view_participants"),
            InlineKeyboardButton("🏆 G'oliblar",      callback_data="select_winner"),
        ],
        [InlineKeyboardButton("🗑️ Tozalash",         callback_data="clear_list")],
        [InlineKeyboardButton("🔙 Orqaga",            callback_data="admin_main_menu")],
    ])

def kb_users():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚫 Ban",            callback_data="ban_user_input"),
            InlineKeyboardButton("✅ Ban olish",       callback_data="unban_user_input"),
        ],
        [
            InlineKeyboardButton("⚠️ Ogohlantirish",  callback_data="warn_user_input"),
            InlineKeyboardButton("👤 Ma'lumot",        callback_data="user_info_input"),
        ],
        [
            InlineKeyboardButton("🏆 TOP",            callback_data="show_top_users"),
            InlineKeyboardButton("📊 Faollar",        callback_data="show_active_users"),
        ],
        [InlineKeyboardButton("🔙 Orqaga",            callback_data="admin_main_menu")],
    ])

def kb_stats():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 Umumiy",         callback_data="general_stats"),
            InlineKeyboardButton("📅 Kunlik",         callback_data="daily_stats_admin"),
        ],
        [
            InlineKeyboardButton("🔥 Battle tarixi",  callback_data="battle_history_admin"),
            InlineKeyboardButton("💾 Export",         callback_data="export_data"),
        ],
        [InlineKeyboardButton("🔙 Orqaga",            callback_data="admin_main_menu")],
    ])

def kb_settings():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📺 Kanal",          callback_data="change_channel"),
            InlineKeyboardButton("🔗 Boost Link",     callback_data="change_boost_link"),
        ],
        [
            InlineKeyboardButton("🔢 Start raqami",   callback_data="change_start_number"),
            InlineKeyboardButton("📋 Kanallar",       callback_data="manage_channels"),
        ],
        [InlineKeyboardButton("🔙 Orqaga",            callback_data="admin_main_menu")],
    ])

def kb_announcements():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 E'lon qo'shish", callback_data="add_announcement"),
            InlineKeyboardButton("📬 Broadcast",      callback_data="send_broadcast"),
        ],
        [InlineKeyboardButton("🔙 Orqaga",            callback_data="admin_main_menu")],
    ])

def kb_technical():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Backup",         callback_data="create_backup"),
            InlineKeyboardButton("📊 Server status",  callback_data="server_status"),
        ],
        [
            InlineKeyboardButton("📋 Loglar",         callback_data="view_logs"),
            InlineKeyboardButton("🗑️ Cache",          callback_data="clear_cache"),
        ],
        [InlineKeyboardButton("🔙 Orqaga",            callback_data="admin_main_menu")],
    ])

def kb_timer():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("5 daq",  callback_data="timer_5"),
            InlineKeyboardButton("10 daq", callback_data="timer_10"),
            InlineKeyboardButton("15 daq", callback_data="timer_15"),
        ],
        [
            InlineKeyboardButton("30 daq", callback_data="timer_30"),
            InlineKeyboardButton("1 soat", callback_data="timer_60"),
            InlineKeyboardButton("6 soat", callback_data="timer_360"),
        ],
        [
            InlineKeyboardButton("1 kun",  callback_data="timer_1440"),
            InlineKeyboardButton("1 hafta", callback_data="timer_10080"),
            InlineKeyboardButton("1 oy",   callback_data=f"timer_{MAX_TIMER_MINUTES}"),
        ],
        [InlineKeyboardButton("✏️ Custom", callback_data="timer_custom")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_battle_menu")],
    ])

def kb_extend_timer():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("+5 daq",  callback_data="extend_5"),
            InlineKeyboardButton("+15 daq", callback_data="extend_15"),
            InlineKeyboardButton("+30 daq", callback_data="extend_30"),
        ],
        [
            InlineKeyboardButton("+1 soat", callback_data="extend_60"),
            InlineKeyboardButton("+1 kun",  callback_data="extend_1440"),
            InlineKeyboardButton("+1 hafta", callback_data="extend_10080"),
        ],
        [InlineKeyboardButton("✏️ Custom", callback_data="extend_custom")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_battle_menu")],
    ])

def kb_back(target: str):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=target)]])

# ══════════════════════════════════════════════
#  MONITORING
# ══════════════════════════════════════════════
async def check_and_remove(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    not_sub = await check_membership(user_id, context)
    if not not_sub or user_id not in user_data:
        return

    info     = user_data[user_id]
    username = info["username"]
    msg_id   = info["message_id"]

    try:
        await context.bot.send_message(
            user_id,
            f"⚠️ Diqqat, <b>{username}</b>!\n\n"
            "Siz homiy yoki battle kanaldan chiqib ketdingiz!\n"
            "10 soniya ichida qayta obuna bo'lmasangiz, konkursdan chiqarilasiz. ⏳",
            parse_mode="HTML",
            reply_markup=get_channel_buttons(not_sub),
        )
    except Exception:
        pass

    await asyncio.sleep(10)

    still_not_sub = await check_membership(user_id, context)
    if still_not_sub:
        if username in registered_users:
            registered_users.remove(username)
        try:
            await context.bot.delete_message(BATTLE_CHANNEL, msg_id)
        except Exception:
            pass
        try:
            await context.bot.send_message(
                user_id,
                f"❌ <b>{username}</b>, siz konkursdan chiqarildingiz!\n\n"
                "Sabab: kanaldan obunani bekor qildingiz.\n"
                "Keyingi battleda qatnashish uchun kanallarga obuna bo'lib turing. 🔔",
                parse_mode="HTML",
            )
        except Exception:
            pass
        user_data.pop(user_id, None)
        save_data()


async def monitor_all(context: ContextTypes.DEFAULT_TYPE):
    global battle_active

    if not battle_active:
        return

    remaining = get_battle_time_remaining()

    # Vaqt tugadi
    if remaining <= 0:
        battle_active = False
        for job in context.job_queue.get_jobs_by_name("battle_monitor"):
            job.schedule_removal()
        if battle_history:
            battle_history[-1].update({
                'end_time':    datetime.now().isoformat(),
                'participants': registered_users.copy(),
                'status':      'auto_finished',
            })
        save_data()
        await send_notification(
            context,
            f"⏰ <b>Battle vaqti tugadi!</b>\n\n"
            f"👥 Ishtirokchilar: {len(registered_users)}\n"
            f"🏆 G'oliblar tez orada e'lon qilinadi!",
        )
        return

    # Countdown xabarnoma
    if 295 < remaining <= 300:   # 5 daqiqa
        await send_notification(
            context,
            f"⏰ Battle tugashiga <b>5 daqiqa</b> qoldi!\nShoshiling!",
        )
    elif 55 < remaining <= 60:   # 1 daqiqa
        await send_notification(
            context,
            f"🚨 Battle tugashiga <b>1 daqiqa</b> qoldi!\nOxirgi imkoniyat!",
        )

    # Foydalanuvchilarni tekshirish
    for uid in list(user_data.keys()):
        asyncio.create_task(check_and_remove(context, uid))

def schedule_battle_monitor(app_or_context):
    """battle_monitor jobini qayta ishga tushirish (restart/resume uchun)."""
    jq = app_or_context.job_queue
    if jq is None:
        logger.error("JobQueue mavjud emas! pip install \"python-telegram-bot[job-queue]\" bajaring")
        return
    if not jq.get_jobs_by_name("battle_monitor"):
        jq.run_repeating(monitor_all, interval=10, first=0, name="battle_monitor")

# ══════════════════════════════════════════════
#  ADMIN CALLBACK HANDLER
# ══════════════════════════════════════════════
async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global battle_active, BATTLE_CHANNEL, BOOST_LINK, start_number, announcements

    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ Ruxsat yo'q!", show_alert=True)
        return
    await query.answer()
    cb = query.data

    # ── Asosiy menyu ──────────────────────────────
    if cb == "admin_main_menu":
        await query.edit_message_text(
            "🔧 <b>Admin Panel</b>\n\nKerakli bo'limni tanlang:",
            parse_mode="HTML",
            reply_markup=kb_admin_main(),
        )

    # ── Battle menyu ──────────────────────────────
    elif cb == "admin_battle_menu":
        status    = "✅ FAOL" if battle_active else "❌ FAOL EMAS"
        remaining = format_time(get_battle_time_remaining()) if battle_active else "—"
        await query.edit_message_text(
            f"🔥 <b>Battle Boshqaruvi</b>\n\n"
            f"Holat: {status}\n"
            f"⏰ Qolgan: {remaining}\n"
            f"👥 Ishtirokchilar: {len(registered_users)}",
            parse_mode="HTML",
            reply_markup=kb_battle(),
        )

    # ── Battle boshlash ───────────────────────────
    elif cb == "start_battle":
        if battle_active:
            await query.answer("⚠️ Battle allaqachon faol!", show_alert=True)
            return
        battle_active = True
        bot_stats['total_battles'] += 1
        start_battle_timer(30)
        today = get_today_stats()
        today['battles_started'] += 1
        battle_history.append({
            'id':          len(battle_history) + 1,
            'start_time':  datetime.now().isoformat(),
            'participants': [],
            'winners':     [],
            'winner':      None,
            'status':      'active',
        })
        schedule_battle_monitor(context)
        save_data()
        ok, fail = await send_notification(
            context,
            "🚀 <b>Battle boshlandi!</b>\n\n"
            "⏰ Davomiyligi: 30 daqiqa\n"
            "📝 @usernamengizni yuboring!\n"
            "🏃 Tezroq bo'ling, joylar cheklangan!",
        )
        await query.edit_message_text(
            f"✅ <b>Battle boshlandi!</b>\n\n"
            f"📨 Xabar: {ok} ta yuborildi, {fail} ta xato\n"
            f"⏰ Timer: 30 daqiqa",
            parse_mode="HTML",
            reply_markup=kb_battle(),
        )

    # ── Battle to'xtatish ─────────────────────────
    elif cb == "stop_battle":
        if not battle_active:
            await query.answer("⚠️ Battle faol emas!", show_alert=True)
            return
        battle_active = False
        for job in context.job_queue.get_jobs_by_name("battle_monitor"):
            job.schedule_removal()
        if battle_history:
            battle_history[-1].update({
                'end_time':    datetime.now().isoformat(),
                'participants': registered_users.copy(),
                'status':      'finished',
            })
        save_data()
        await query.edit_message_text(
            f"⏹️ <b>Battle to'xtatildi!</b>\n\n"
            f"👥 Ishtirokchilar: {len(registered_users)}",
            parse_mode="HTML",
            reply_markup=kb_battle(),
        )

    # ── Timer o'rnatish ───────────────────────────
    elif cb == "set_timer":
        await query.edit_message_text(
            "⏰ <b>Timer O'rnatish</b>\n\nDavomiylikni tanlang (max 1 oy):",
            parse_mode="HTML",
            reply_markup=kb_timer(),
        )

    elif cb.startswith("timer_"):
        val = cb.split("_")[1]
        if val == "custom":
            context.user_data['waiting_custom_timer'] = True
            await query.edit_message_text(
                f"⏰ <b>Custom Timer</b>\n\nDaqiqada qiymat yuboring (1–{MAX_TIMER_MINUTES}):",
                parse_mode="HTML",
                reply_markup=kb_back("set_timer"),
            )
            return
        minutes = min(int(val), MAX_TIMER_MINUTES)
        start_battle_timer(minutes)
        save_data()
        await query.edit_message_text(
            f"✅ <b>Timer o'rnatildi: {minutes} daqiqa</b>",
            parse_mode="HTML",
            reply_markup=kb_battle(),
        )

    # ── Timerni cho'zish ──────────────────────────
    elif cb == "extend_timer":
        if not battle_active:
            await query.answer("⚠️ Battle faol emas!", show_alert=True)
            return
        await query.edit_message_text(
            "➕ <b>Timerni Cho'zish</b>\n\nQancha vaqt qo'shmoqchisiz?",
            parse_mode="HTML",
            reply_markup=kb_extend_timer(),
        )

    elif cb.startswith("extend_"):
        val = cb.split("_")[1]
        if not battle_active:
            await query.answer("⚠️ Battle faol emas!", show_alert=True)
            return
        if val == "custom":
            context.user_data['waiting_custom_extend'] = True
            await query.edit_message_text(
                f"➕ <b>Custom Cho'zish</b>\n\nQancha daqiqa qo'shilsin? (1–{MAX_TIMER_MINUTES}):",
                parse_mode="HTML",
                reply_markup=kb_back("extend_timer"),
            )
            return
        extra = int(val)
        new_total = extend_battle_timer(extra)
        save_data()
        await query.edit_message_text(
            f"✅ <b>Timer cho'zildi!</b>\n\nYangi qolgan vaqt: {format_time(get_battle_time_remaining())}",
            parse_mode="HTML",
            reply_markup=kb_battle(),
        )
        await send_notification(
            context,
            f"⏰ <b>E'tibor!</b> Battle vaqti {extra} daqiqaga uzaytirildi!\n"
            f"Qolgan vaqt: {format_time(get_battle_time_remaining())}",
        )

    # ── Ro'yxat ───────────────────────────────────
    elif cb == "view_participants":
        if not registered_users:
            await query.edit_message_text(
                "📋 Ro'yxat bo'sh.", reply_markup=kb_battle()
            )
            return
        lines = "\n".join(
            f"{start_number + i}⃣ {u}" for i, u in enumerate(registered_users)
        )
        remaining = format_time(get_battle_time_remaining()) if battle_active else "—"
        text = (
            f"📋 <b>Battle Ro'yxati</b>\n\n"
            f"👥 Jami: {len(registered_users)}\n"
            f"⏰ Qolgan: {remaining}\n\n"
            + lines[:3800]
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb_battle())

    # ── G'oliblarni tanlash (1-o'rin, 2-o'rin, 3-o'rin...) ─────
    elif cb == "select_winner":
        if not battle_history:
            await query.answer("❌ Battle tarixi yo'q!", show_alert=True)
            return
        winners = battle_history[-1].setdefault('winners', [])
        chosen_usernames = {w['username'] for w in winners}
        candidates = [u for u in registered_users if u not in chosen_usernames]

        if not candidates:
            await query.answer("❌ Tanlash uchun ishtirokchi qolmadi!", show_alert=True)
            return

        rows = []
        for i, uname in enumerate(candidates[:15]):
            rows.append([InlineKeyboardButton(
                f"{len(winners)+1}-o'rin → {uname}", callback_data=f"winner_{i}"
            )])
        action_row = []
        if winners:
            action_row.append(InlineKeyboardButton("✅ Yakunlash va E'lon qilish", callback_data="finish_winners"))
        action_row.append(InlineKeyboardButton("🔙 Orqaga", callback_data="admin_battle_menu"))
        rows.append(action_row)

        chosen_text = ""
        if winners:
            chosen_text = "\n".join(f"🏆 {w['position']}-o'rin: {w['username']}" for w in winners) + "\n\n"

        await query.edit_message_text(
            f"🏆 <b>G'oliblarni Tanlash</b>\n\n{chosen_text}"
            f"Keyingi ({len(winners)+1}-o'rin) uchun tanlang:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif cb.startswith("winner_"):
        idx = int(cb.split("_")[1])
        if not battle_history:
            await query.answer("❌ Xato!", show_alert=True)
            return
        winners = battle_history[-1].setdefault('winners', [])
        chosen_usernames = {w['username'] for w in winners}
        candidates = [u for u in registered_users if u not in chosen_usernames]
        if idx >= len(candidates):
            await query.answer("❌ Xato indeks!", show_alert=True)
            return

        winner_uname = candidates[idx]
        winner_pos   = len(winners) + 1
        winners.append({
            'username':      winner_uname,
            'position':      winner_pos,
            'selected_time': datetime.now().isoformat(),
        })
        for uid, info in user_data.items():
            if info.get('username') == winner_uname:
                update_user_stats(uid, 'battle_win')
                break
        save_data()

        # Shu zahoti g'olibga shaxsiy xabar yuboramiz
        winner_uid = None
        for uid, info in user_data.items():
            if info.get('username') == winner_uname:
                winner_uid = uid
                break
        if winner_uid:
            try:
                await context.bot.send_message(
                    winner_uid,
                    f"🎉 Tabriklaymiz, <b>{winner_uname}</b>!\n\n"
                    f"🏆 Siz battleda <b>{ordinal_uz(winner_pos)}</b>ni egalladingiz!\n\n"
                    f"👑 Ajoyib natija! Keyingi battleda ham kutamiz!",
                    parse_mode="HTML",
                )
            except Exception:
                pass

        # Menyuni yangilab, keyingi o'rinni tanlashni taklif qilamiz
        remaining_candidates = [u for u in registered_users if u not in {w['username'] for w in winners}]
        rows = []
        for i, uname in enumerate(remaining_candidates[:15]):
            rows.append([InlineKeyboardButton(
                f"{len(winners)+1}-o'rin → {uname}", callback_data=f"winner_{i}"
            )])
        action_row = [InlineKeyboardButton("✅ Yakunlash va E'lon qilish", callback_data="finish_winners")]
        action_row.append(InlineKeyboardButton("🔙 Orqaga", callback_data="admin_battle_menu"))
        rows.append(action_row)

        chosen_text = "\n".join(f"🏆 {w['position']}-o'rin: {w['username']}" for w in winners) + "\n\n"
        text = f"✅ {ordinal_uz(winner_pos)}: {winner_uname} saqlandi!\n\n{chosen_text}"
        if remaining_candidates:
            text += f"Keyingi ({len(winners)+1}-o'rin) uchun tanlang yoki yakunlang:"
            reply_markup = InlineKeyboardMarkup(rows)
        else:
            text += "Boshqa ishtirokchi qolmadi. Yakunlang:"
            reply_markup = InlineKeyboardMarkup([action_row])

        await query.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)

    elif cb == "finish_winners":
        if not battle_history:
            await query.answer("❌ Xato!", show_alert=True)
            return
        winners = battle_history[-1].get('winners', [])
        if not winners:
            await query.answer("❌ Hali g'olib tanlanmagan!", show_alert=True)
            return

        winners_sorted = sorted(winners, key=lambda w: w['position'])
        # Eski 'winner' maydoniga ham 1-o'rinni yozib qo'yamiz (orqaga moslik uchun)
        battle_history[-1]['winner'] = winners_sorted[0]
        save_data()

        lines = "\n".join(f"{ordinal_uz(w['position'])}: {w['username']}" for w in winners_sorted)
        channel_msg = (
            f"🏆 BATTLE G'OLIBLARI!\n\n"
            f"{lines}\n\n"
            f"🎊 Barchaga tabriklar!"
        )
        try:
            await context.bot.send_message(BATTLE_CHANNEL, channel_msg)
        except Exception as e:
            logger.warning(f"Kanalga yuborishda xato: {e}")

        winner_usernames = {w['username'] for w in winners_sorted}
        others_msg = (
            f"🏁 Battle yakunlandi!\n\n"
            f"🥇 G'oliblar:\n{lines}\n\n"
            f"💪 Keyingi battleda omadingizni sinab ko'ring!\n"
            f"🔔 Yangiliklardan xabardor bo'lish uchun kanalga obuna bo'ling."
        )
        for uid, info in list(user_data.items()):
            if info.get('username') in winner_usernames:
                continue
            try:
                await context.bot.send_message(uid, others_msg)
                await asyncio.sleep(0.05)
            except Exception:
                pass

        await query.edit_message_text(
            f"✅ <b>G'oliblar e'lon qilindi!</b>\n\n{lines}",
            parse_mode="HTML",
            reply_markup=kb_battle(),
        )

    # ── Ro'yxatni tozalash ────────────────────────
    elif cb == "clear_list":
        if not registered_users:
            await query.answer("Ro'yxat allaqachon bo'sh!", show_alert=True)
            return
        await query.edit_message_text(
            f"⚠️ <b>Tasdiqlang</b>\n\n{len(registered_users)} ta ishtirokchini o'chirasizmi?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Ha", callback_data="confirm_clear"),
                    InlineKeyboardButton("❌ Yo'q", callback_data="admin_battle_menu"),
                ]
            ]),
        )

    elif cb == "confirm_clear":
        deleted = 0
        for uid, info in list(user_data.items()):
            try:
                await context.bot.delete_message(BATTLE_CHANNEL, info["message_id"])
                deleted += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)
        registered_users.clear()
        user_data.clear()
        save_data()
        await query.edit_message_text(
            f"✅ Ro'yxat tozalandi! O'chirilgan: {deleted}",
            reply_markup=kb_battle(),
        )

    # ── Foydalanuvchilar ──────────────────────────
    elif cb == "admin_users_menu":
        await query.edit_message_text(
            f"👥 <b>Foydalanuvchilar</b>\n\n"
            f"Jami: {len(user_stats)}\n"
            f"🚫 Bloklangan: {len(banned_users)}\n"
            f"⚠️ Ogohlantirilgan: {sum(1 for w in user_warnings.values() if w > 0)}",
            parse_mode="HTML",
            reply_markup=kb_users(),
        )

    elif cb == "show_top_users":
        if not user_stats:
            await query.edit_message_text("📊 Statistika yo'q.", reply_markup=kb_users())
            return
        top = sorted(user_stats.items(), key=lambda x: x[1].get('battles_joined', 0), reverse=True)[:10]
        lines = []
        for i, (uid, s) in enumerate(top, 1):
            b = s.get('battles_joined', 0)
            w = s.get('battles_won', 0)
            wr = f"{w/b*100:.0f}%" if b else "0%"
            lines.append(f"{i}. ID{uid}: {b} battle, {wr} g'alaba")
        await query.edit_message_text(
            "🏆 <b>TOP 10</b>\n\n" + "\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb_back("admin_users_menu"),
        )

    elif cb == "show_active_users":
        today = get_today_stats()
        active = today.get('active_users', [])
        await query.edit_message_text(
            f"📊 <b>Bugun faol foydalanuvchilar:</b> {len(active)}",
            parse_mode="HTML",
            reply_markup=kb_back("admin_users_menu"),
        )

    elif cb in ("ban_user_input", "unban_user_input", "warn_user_input", "user_info_input"):
        actions = {
            "ban_user_input":    ("ban",    "Ban qilish uchun"),
            "unban_user_input":  ("unban",  "Ban olib tashlash uchun"),
            "warn_user_input":   ("warn",   "Ogohlantirish uchun"),
            "user_info_input":   ("info",   "Ma'lumot olish uchun"),
        }
        action, label = actions[cb]
        context.user_data['admin_action'] = action
        await query.edit_message_text(
            f"👤 <b>{label}</b> foydalanuvchi ID sini yuboring:",
            parse_mode="HTML",
            reply_markup=kb_back("admin_users_menu"),
        )

    # ── Statistika ────────────────────────────────
    elif cb == "admin_stats_menu":
        today = get_today_stats()
        await query.edit_message_text(
            f"📊 <b>Statistika</b>\n\n"
            f"🔥 Jami battle: {bot_stats.get('total_battles', 0)}\n"
            f"👥 Jami foydalanuvchi: {len(user_stats)}\n"
            f"🆕 Bugun yangi: {today.get('new_users', 0)}\n"
            f"💬 Bugun xabar: {today.get('total_messages', 0)}",
            parse_mode="HTML",
            reply_markup=kb_stats(),
        )

    elif cb == "general_stats":
        active_7d = sum(
            1 for s in user_stats.values()
            if (datetime.now() - datetime.fromisoformat(
                s.get('last_activity', datetime.now().isoformat())
            )).days < 7
        )
        top_item = max(user_stats.items(), key=lambda x: x[1].get('battles_joined', 0), default=(None, {}))
        top_line = ""
        if top_item[0]:
            top_line = f"\n🏆 Eng faol: ID{top_item[0]} ({top_item[1].get('battles_joined',0)} battle)"
        await query.edit_message_text(
            f"📊 <b>Umumiy Statistika</b>\n\n"
            f"👥 Jami: {len(user_stats)}\n"
            f"✅ Faol (7 kun): {active_7d}\n"
            f"🔥 Battle: {bot_stats.get('total_battles',0)}\n"
            f"📋 Hozirgi ro'yxat: {len(registered_users)}\n"
            f"🚫 Ban: {len(banned_users)}"
            + top_line,
            parse_mode="HTML",
            reply_markup=kb_back("admin_stats_menu"),
        )

    elif cb == "daily_stats_admin":
        today_key  = datetime.now().strftime('%Y-%m-%d')
        yest_key   = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        td = daily_stats.get(today_key, {})
        yd = daily_stats.get(yest_key, {})
        diff_u = td.get('new_users', 0) - yd.get('new_users', 0)
        diff_m = td.get('total_messages', 0) - yd.get('total_messages', 0)
        await query.edit_message_text(
            f"📅 <b>Kunlik Statistika ({today_key})</b>\n\n"
            f"🆕 Yangi: {td.get('new_users',0)} ({diff_u:+d})\n"
            f"🔥 Battle: {td.get('battles_started',0)}\n"
            f"💬 Xabarlar: {td.get('total_messages',0)} ({diff_m:+d})\n"
            f"👥 Faollar: {len(td.get('active_users',[]))}",
            parse_mode="HTML",
            reply_markup=kb_back("admin_stats_menu"),
        )

    elif cb == "battle_history_admin":
        if not battle_history:
            await query.edit_message_text("📜 Tarix bo'sh.", reply_markup=kb_back("admin_stats_menu"))
            return
        lines = []
        for b in battle_history[-10:][::-1]:
            winners = b.get('winners') or []
            if winners:
                w_text = ", ".join(f"{w['position']}-{w['username']}" for w in sorted(winners, key=lambda x: x['position']))
            else:
                legacy = b.get('winner') or {}
                w_text = legacy.get('username', '—')
            lines.append(
                f"#{b['id']} | {b.get('start_time','')[:10]} | "
                f"{len(b.get('participants',[]))} kishi | "
                f"G'oliblar: {w_text}"
            )
        await query.edit_message_text(
            "🔥 <b>So'nggi 10 battle:</b>\n\n" + "\n".join(lines),
            parse_mode="HTML",
            reply_markup=kb_back("admin_stats_menu"),
        )

    elif cb == "export_data":
        # Admin ga JSON fayl sifatida yuborish
        try:
            export = {
                'exported_at':   datetime.now().isoformat(),
                'registered_users': registered_users,
                'user_stats':    {str(k): v for k, v in user_stats.items()},
                'battle_history': battle_history,
                'bot_stats':     bot_stats,
            }
            fname = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(fname, 'w', encoding='utf-8') as f:
                json.dump(export, f, ensure_ascii=False, indent=2)
            with open(fname, 'rb') as f:
                await context.bot.send_document(ADMIN_ID, f, filename=fname, caption="📊 Ma'lumotlar eksport qilindi")
            os.remove(fname)
            await query.answer("✅ Export yuborildi!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ Xato: {e}", show_alert=True)

    # ── Sozlamalar ────────────────────────────────
    elif cb == "admin_settings_menu":
        await query.edit_message_text(
            f"⚙️ <b>Sozlamalar</b>\n\n"
            f"📺 Kanal: <code>{BATTLE_CHANNEL}</code>\n"
            f"🔗 Boost: {BOOST_LINK}\n"
            f"🔢 Start raqam: {start_number}\n"
            f"📋 Majburiy kanallar: {len(REQUIRED_CHANNELS)}",
            parse_mode="HTML",
            reply_markup=kb_settings(),
        )

    elif cb == "change_channel":
        context.user_data['waiting_change'] = 'channel'
        await query.edit_message_text(
            "📺 Yangi kanal username yuboring (masalan: @mychann):",
            reply_markup=kb_back("admin_settings_menu"),
        )

    elif cb == "change_boost_link":
        context.user_data['waiting_change'] = 'boost_link'
        await query.edit_message_text(
            "🔗 Yangi Boost linkni yuboring:",
            reply_markup=kb_back("admin_settings_menu"),
        )

    elif cb == "change_start_number":
        context.user_data['waiting_change'] = 'start_number'
        await query.edit_message_text(
            "🔢 Yangi start raqamini yuboring:",
            reply_markup=kb_back("admin_settings_menu"),
        )

    # ── E'lonlar ──────────────────────────────────
    elif cb == "admin_announcements_menu":
        await query.edit_message_text(
            f"📢 <b>E'lonlar</b>\n\nJoriy: {'✅ Mavjud' if announcements else '❌ Yoq'}\n"
            f"👥 Foydalanuvchilar: {len(user_stats)}",
            parse_mode="HTML",
            reply_markup=kb_announcements(),
        )

    elif cb == "send_broadcast":
        context.user_data['waiting_broadcast'] = True
        await query.edit_message_text(
            "📢 Broadcast xabarni yuboring:",
            reply_markup=kb_back("admin_announcements_menu"),
        )

    elif cb == "add_announcement":
        context.user_data['waiting_announcement'] = True
        await query.edit_message_text(
            "📝 E'lon matnini yuboring:",
            reply_markup=kb_back("admin_announcements_menu"),
        )

    # ── Texnik ────────────────────────────────────
    elif cb == "admin_technical_menu":
        cpu  = psutil.cpu_percent(interval=0.5)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        await query.edit_message_text(
            f"🔧 <b>Texnik Ma'lumotlar</b>\n\n"
            f"💻 CPU: {cpu}%\n"
            f"🧠 RAM: {mem.percent}% ({mem.used//1024//1024} MB)\n"
            f"💾 Disk: {disk.percent}%\n"
            f"📊 Uptime: Bot faol",
            parse_mode="HTML",
            reply_markup=kb_technical(),
        )

    elif cb == "create_backup":
        try:
            bname = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            save_data()
            import shutil
            shutil.copy(DATA_FILE, bname)
            with open(bname, 'rb') as f:
                await context.bot.send_document(ADMIN_ID, f, filename=bname, caption="💾 Backup")
            os.remove(bname)
            await query.answer("✅ Backup yuborildi!", show_alert=True)
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)

    elif cb == "server_status":
        cpu  = psutil.cpu_percent(interval=1)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        net  = psutil.net_io_counters()
        await query.edit_message_text(
            f"📊 <b>Server Status</b>\n\n"
            f"CPU: {cpu}%\n"
            f"RAM: {mem.percent}% ({mem.available//1024//1024} MB bo'sh)\n"
            f"Disk: {disk.percent}% ({disk.free//1024//1024//1024} GB bo'sh)\n"
            f"↑ Yuklangan: {net.bytes_sent//1024//1024} MB\n"
            f"↓ Yuklab olingan: {net.bytes_recv//1024//1024} MB",
            parse_mode="HTML",
            reply_markup=kb_back("admin_technical_menu"),
        )

    elif cb == "view_logs":
        try:
            with open('bot.log', encoding='utf-8') as f:
                lines = f.readlines()
            last = "".join(lines[-30:])[-3500:]
            await query.edit_message_text(
                f"📋 <b>So'nggi loglar:</b>\n\n<pre>{last}</pre>",
                parse_mode="HTML",
                reply_markup=kb_back("admin_technical_menu"),
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Log o'qib bo'lmadi: {e}", reply_markup=kb_back("admin_technical_menu"))

    elif cb == "clear_cache":
        user_data_copy = len(user_data)
        # Faqat xotirada tozalash
        import gc
        gc.collect()
        await query.answer(f"✅ Cache tozalandi! ({user_data_copy} yozuv)", show_alert=True)

    elif cb == "manage_channels":
        channels_text = "\n".join(f"• {ch} — {name}" for ch, name in REQUIRED_CHANNELS)
        await query.edit_message_text(
            f"📋 <b>Majburiy Kanallar:</b>\n\n{channels_text}",
            parse_mode="HTML",
            reply_markup=kb_back("admin_settings_menu"),
        )

# ══════════════════════════════════════════════
#  FOYDALANUVCHI BUYRUQLARI
# ══════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id == ADMIN_ID:
        await update.message.reply_text(
            "👑 <b>Admin Panel</b>\n\nXush kelibsiz!",
            parse_mode="HTML",
            reply_markup=kb_admin_main(),
        )
        return

    if user_id in banned_users:
        await update.message.reply_text("❌ Siz bu botdan foydalanish huquqidan mahrum etilgansiz.")
        return

    not_sub = await check_membership(user_id, context)
    if not_sub:
        await update.message.reply_text(
            "❗ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:\n\n"
            "✅ Obuna bo'lgach, /start yuboring.",
            reply_markup=get_channel_buttons(not_sub),
        )
        return

    if user_id not in user_stats:
        update_user_stats(user_id, 'message')
        today = get_today_stats()
        today['new_users'] += 1
        bot_stats['total_users'] += 1
        save_data()

    name   = update.effective_user.first_name or "Foydalanuvchi"
    status = "✅ FAOL" if battle_active else "❌ FAOL EMAS"
    time_line = ""
    if battle_active:
        rem = get_battle_time_remaining()
        if rem > 0:
            time_line = f"\n⏰ Qolgan vaqt: {format_time(rem)}"

    ann_line = f"\n\n📢 {announcements}" if announcements else ""

    message_text = (
        f"👋 Salom, <b>{name}</b>!\n\n"
        f"🎯 Battle holati: {status}{time_line}\n\n"
        f"📝 Faqat o'z @usernamengizni yuboring.\n"
        f"📋 Misol: @mening_username"
        f"{ann_line}"
    )

    await update.message.reply_text(
        message_text,
        parse_mode="HTML",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global battle_active, BATTLE_CHANNEL, BOOST_LINK, start_number, announcements

    user_id = update.effective_user.id
    text    = update.message.text.strip()

    # ── Admin maxsus kutishlar ─────────────────────
    if user_id == ADMIN_ID:
        # Custom timer
        if context.user_data.get('waiting_custom_timer'):
            context.user_data.pop('waiting_custom_timer')
            try:
                minutes = int(text)
                if 1 <= minutes <= MAX_TIMER_MINUTES:
                    start_battle_timer(minutes)
                    save_data()
                    await update.message.reply_text(
                        f"✅ Timer: {minutes} daqiqa",
                        reply_markup=kb_battle(),
                    )
                else:
                    await update.message.reply_text(f"❌ 1–{MAX_TIMER_MINUTES} oraliq!")
            except ValueError:
                await update.message.reply_text("❌ Faqat raqam!")
            return

        # Custom timer cho'zish
        if context.user_data.get('waiting_custom_extend'):
            context.user_data.pop('waiting_custom_extend')
            try:
                minutes = int(text)
                if 1 <= minutes <= MAX_TIMER_MINUTES:
                    extend_battle_timer(minutes)
                    save_data()
                    await update.message.reply_text(
                        f"✅ Timer {minutes} daqiqaga cho'zildi!\nYangi qolgan vaqt: {format_time(get_battle_time_remaining())}",
                        reply_markup=kb_battle(),
                    )
                    await send_notification(
                        context,
                        f"⏰ <b>E'tibor!</b> Battle vaqti {minutes} daqiqaga uzaytirildi!\n"
                        f"Qolgan vaqt: {format_time(get_battle_time_remaining())}",
                    )
                else:
                    await update.message.reply_text(f"❌ 1–{MAX_TIMER_MINUTES} oraliq!")
            except ValueError:
                await update.message.reply_text("❌ Faqat raqam!")
            return

        # Broadcast
        if context.user_data.get('waiting_broadcast'):
            context.user_data.pop('waiting_broadcast')
            users = list(user_stats.keys())
            await update.message.reply_text(f"📢 {len(users)} ta kishi ga yuborilmoqda…")
            ok, fail = await send_notification(context, f"📢 <b>Admin xabari:</b>\n\n{text}")
            await update.message.reply_text(
                f"✅ Broadcast: {ok} muvaffaqiyatli, {fail} xato.",
                reply_markup=kb_announcements(),
            )
            return

        # E'lon
        if context.user_data.get('waiting_announcement'):
            context.user_data.pop('waiting_announcement')
            announcements = text
            save_data()
            await update.message.reply_text(
                f"✅ E'lon saqlandi:\n{announcements}",
                reply_markup=kb_announcements(),
            )
            return

        # Sozlama o'zgartirish
        waiting_change = context.user_data.get('waiting_change')
        if waiting_change:
            context.user_data.pop('waiting_change')
            if waiting_change == 'channel':
                if not text.startswith('@'):
                    await update.message.reply_text("❌ @ bilan boshlang!")
                    return
                BATTLE_CHANNEL = text
                save_data()
                await update.message.reply_text(f"✅ Kanal: {BATTLE_CHANNEL}", reply_markup=kb_settings())
            elif waiting_change == 'boost_link':
                BOOST_LINK = text
                save_data()
                await update.message.reply_text(f"✅ Boost link: {BOOST_LINK}", reply_markup=kb_settings())
            elif waiting_change == 'start_number':
                try:
                    start_number = int(text)
                    save_data()
                    await update.message.reply_text(f"✅ Start raqam: {start_number}", reply_markup=kb_settings())
                except ValueError:
                    await update.message.reply_text("❌ Faqat raqam!")
            return

        # Admin action (ban/unban/warn/info)
        admin_action = context.user_data.get('admin_action')
        if admin_action:
            context.user_data.pop('admin_action')
            try:
                target_id = int(text)
            except ValueError:
                await update.message.reply_text("❌ Faqat ID raqam!")
                return

            if admin_action == 'ban':
                banned_users.add(target_id)
                save_data()
                try:
                    await context.bot.send_message(target_id, "🚫 Siz botdan bloklandingiz.")
                except Exception:
                    pass
                await update.message.reply_text(f"✅ {target_id} bloklandi.", reply_markup=kb_users())

            elif admin_action == 'unban':
                banned_users.discard(target_id)
                save_data()
                try:
                    await context.bot.send_message(target_id, "✅ Sizning blokirovkangiz olib tashlandi.")
                except Exception:
                    pass
                await update.message.reply_text(f"✅ {target_id} blokdan chiqarildi.", reply_markup=kb_users())

            elif admin_action == 'warn':
                user_warnings[target_id] += 1
                w = user_warnings[target_id]
                save_data()
                try:
                    await context.bot.send_message(
                        target_id, f"⚠️ Ogohlantirish #{w}!\nQoidalarga rioya qiling."
                    )
                except Exception:
                    pass
                if w >= 3:
                    banned_users.add(target_id)
                    save_data()
                    await update.message.reply_text(
                        f"⚠️ {target_id} — {w} ogohlantirish. Avtomatik bloklandi!", reply_markup=kb_users()
                    )
                else:
                    await update.message.reply_text(
                        f"⚠️ {target_id} ga ogohlantirish #{w} yuborildi.", reply_markup=kb_users()
                    )

            elif admin_action == 'info':
                stats = user_stats.get(target_id, {})
                warns = user_warnings.get(target_id, 0)
                is_banned = target_id in banned_users
                info_text = (
                    f"👤 <b>Foydalanuvchi: {target_id}</b>\n\n"
                    f"Battle: {stats.get('battles_joined',0)}\n"
                    f"G'alaba: {stats.get('battles_won',0)}\n"
                    f"Xabarlar: {stats.get('total_messages',0)}\n"
                    f"Ogohlantirish: {warns}\n"
                    f"Ban: {'✅ Ha' if is_banned else '❌ Yoq'}\n"
                    f"Qo'shilgan: {stats.get('join_date','—')[:10]}"
                )
                await update.message.reply_text(info_text, parse_mode="HTML", reply_markup=kb_users())
            return

    # ── Oddiy foydalanuvchi ───────────────────────
    if user_id in banned_users:
        await update.message.reply_text("❌ Siz bloklangansiz.")
        return

    text_lower = text.lower()

    update_user_stats(user_id, 'message')
    today = get_today_stats()
    today['total_messages'] += 1
    if user_id not in today['active_users']:
        today['active_users'].append(user_id)

    if not battle_active:
        await update.message.reply_text(
            "❌ Hozir battle faol emas.\n"
            "Battle boshlansa, sizga xabar beriladi! 🔔"
        )
        return

    if get_battle_time_remaining() <= 0:
        await update.message.reply_text("⏰ Battle vaqti tugadi! Keyingi battleni kuting.")
        return

    not_sub = await check_membership(user_id, context)
    if not_sub:
        if any(ch == "banned" for ch, _ in not_sub):
            await update.message.reply_text("❌ Siz bloklangansiz.")
            return
        await update.message.reply_text(
            "❗ Avval kanallarga obuna bo'ling:",
            reply_markup=get_channel_buttons(not_sub),
        )
        return

    if not is_valid_username(text_lower):
        await update.message.reply_text(
            "❗ Noto'g'ri format!\n\n"
            "✅ To'g'ri: @username\n"
            "📏 Kamida 5 belgi\n"
            "🔤 Faqat harf, raqam, _"
        )
        return

    real = update.effective_user.username
    if real is None or text_lower != f"@{real.lower()}":
        suggested = f"@{real}" if real else "username yo'q (Telegram sozlamalarida o'rnating)"
        await update.message.reply_text(
            f"❌ Bu sizning usernamengiz emas!\n\n"
            f"💡 Sizniki: {suggested}"
        )
        return

    if text_lower in registered_users:
        await update.message.reply_text("⚠️ Siz allaqachon ro'yxatdasiz!")
        return

    registered_users.append(text_lower)
    pos       = start_number + len(registered_users) - 1
    remaining = get_battle_time_remaining()

    msg_txt = (
        f"🎯 <b>Stars Battle!</b>\n\n"
        f"{pos}\u20e3 — {text_lower}\n\n"
        f"⭐ Stars — <b>5 ball</b>\n"
        f"👍 Reaksiya — <b>1 ball</b>\n"
        f"🚀 Boost — <b>15 ball</b>\n\n"
        f"⏰ Qolgan vaqt: <b>{format_time(remaining)}</b>"
    )

    # Ikkita tugma: Konkursga qo'shilish + Boost
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Boost!", url=BOOST_LINK)],
        [InlineKeyboardButton("➕ Konkursga Qo'shilish", url=f"https://t.me/{context.bot.username}")]
    ])

    try:
        sent = await context.bot.send_message(
            chat_id=BATTLE_CHANNEL,
            text=msg_txt,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
        user_data[user_id] = {
            "username":   text_lower,
            "message_id": sent.message_id,
            "join_time":  datetime.now().isoformat(),
        }
        update_user_stats(user_id, 'battle_join')
        save_data()

        await update.message.reply_text(
            f"✅ Ro'yxatga olindingiz!\n"
            f"📍 Raqamingiz: <b>{pos}</b>\n"
            f"⏰ Qolgan: {format_time(remaining)}",
            parse_mode="HTML",
        )
    except Exception as e:
        registered_users.remove(text_lower)
        logger.error(f"Kanalga yuborishda xato: {e}")
        await update.message.reply_text(
            f"❗ Xatolik yuz berdi. Bot kanalda admin ekanligini tekshiring.\nXato: {e}"
        )



# ══════════════════════════════════════════════
#  BUYRUQLAR
# ══════════════════════════════════════════════
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today   = get_today_stats()

    personal = ""
    if user_id in user_stats:
        s  = user_stats[user_id]
        b  = s.get('battles_joined', 0)
        w  = s.get('battles_won', 0)
        wr = f"{w/b*100:.0f}%" if b else "0%"
        personal = (
            f"👤 <b>Sizning statistikangiz:</b>\n"
            f"🎯 Battle: {b}\n"
            f"🏆 G'alaba: {w} ({wr})\n"
            f"💬 Xabarlar: {s.get('total_messages',0)}\n\n"
        )

    general = (
        f"📊 <b>Umumiy:</b>\n"
        f"👥 Jami: {bot_stats.get('total_users',0)}\n"
        f"🔥 Battle: {bot_stats.get('total_battles',0)}\n"
        f"📋 Hozir ro'yxatda: {len(registered_users)}\n"
        f"🎮 Holat: {'✅ FAOL' if battle_active else '❌ FAOL EMAS'}\n\n"
        f"📅 <b>Bugun:</b>\n"
        f"🆕 Yangi: {today.get('new_users',0)}\n"
        f"💬 Xabarlar: {today.get('total_messages',0)}\n"
        f"👥 Faol: {len(today.get('active_users',[]))}"
    )
    if battle_active:
        general += f"\n⏰ Qolgan: {format_time(get_battle_time_remaining())}"

    await update.message.reply_text(personal + general, parse_mode="HTML")


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    is_admin = user_id == ADMIN_ID
    rem_line = f"\n⏰ Qolgan: {format_time(get_battle_time_remaining())}" if battle_active else ""
    msg = (
        f"ℹ️ <b>Battle Bot</b>\n\n"
        f"📺 Kanal: {BATTLE_CHANNEL}\n"
        f"🎮 Battle: {'✅ FAOL' if battle_active else '❌ FAOL EMAS'}{rem_line}\n"
        f"👥 Ro'yxat: {len(registered_users)}"
    )
    if is_admin:
        msg += (
            f"\n\n👑 <b>Admin:</b>\n"
            f"Jami foydalanuvchi: {len(user_stats)}\n"
            f"Ban: {len(banned_users)}"
        )
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_daily_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    today_key = datetime.now().strftime('%Y-%m-%d')
    yest_key  = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    td = daily_stats.get(today_key, {})
    yd = daily_stats.get(yest_key, {})
    du = td.get('new_users', 0) - yd.get('new_users', 0)
    dm = td.get('total_messages', 0) - yd.get('total_messages', 0)
    await update.message.reply_text(
        f"📅 <b>Kunlik statistika ({today_key}):</b>\n\n"
        f"🆕 Yangi: {td.get('new_users',0)} ({du:+d})\n"
        f"🔥 Battle: {td.get('battles_started',0)}\n"
        f"💬 Xabarlar: {td.get('total_messages',0)} ({dm:+d})\n"
        f"👥 Faol: {len(td.get('active_users',[]))}",
        parse_mode="HTML",
    )


# ══════════════════════════════════════════════
#  BOT COMMANDS
# ══════════════════════════════════════════════
async def set_bot_commands(app: Application):
    admin_cmds = [
        BotCommand("start",       "Admin panel"),
        BotCommand("stats",       "Statistika"),
        BotCommand("daily_stats", "Kunlik statistika"),
        BotCommand("about",       "Bot haqida"),
    ]
    user_cmds = [
        BotCommand("start", "Botni ishga tushirish"),
        BotCommand("stats", "Statistika"),
        BotCommand("about", "Bot haqida"),
    ]
    try:
        await app.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    except Exception as e:
        logger.warning(f"Admin commands: {e}")
    await app.bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())


# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════
def main():
    ensure_single_instance()
    load_data()

    if TOKEN == "YOUR_TOKEN_HERE":
        print("❌ TOKEN o'rnatilmagan! BOT_TOKEN env o'zgaruvchisini yoki kodni to'g'rilang.")
        sys.exit(1)

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("daily_stats", cmd_daily_stats))
    app.add_handler(CommandHandler("about",       cmd_about))
    app.add_handler(CallbackQueryHandler(admin_callback_handler))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_message,
    ))

    async def post_init(application: Application):
        await set_bot_commands(application)
        # Agar bot to'xtab qolganda battle faol bo'lsa — monitoringni qayta ishga tushiramiz
        if battle_active and get_battle_time_remaining() > 0:
            schedule_battle_monitor(application)
            logger.info("Faol battle aniqlandi — monitoring qayta ishga tushirildi.")
        elif battle_active:
            # Vaqti tugagan bo'lsa, holatni yopib qo'yamiz
            global_battle_finalize()

    app.post_init = post_init

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Update xatosi: {context.error}", exc_info=context.error)

    app.add_error_handler(error_handler)

    logger.info("🤖 Bot ishga tushdi...")
    print("🤖 Bot ishga tushdi...")
    app.run_polling(drop_pending_updates=True)


def global_battle_finalize():
    """Bot o'chiq turgan vaqtda battle tugagan bo'lsa, holatni to'g'rilash."""
    global battle_active
    battle_active = False
    if battle_history:
        battle_history[-1].setdefault('status', 'auto_finished')
        battle_history[-1].setdefault('end_time', datetime.now().isoformat())
    save_data()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Bot to'xtatildi.")
