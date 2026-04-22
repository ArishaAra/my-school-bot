"""
Telegram-бот для вчителя
• Баланс уроків (пакетна оплата) — нагадування при 2 і 0 уроків
• Розклад + автонагадування до уроку
• Нотатки після уроку → розсилка учням
• ДЗ: учень здає, вчитель підтверджує
"""
import os, logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

db = Database("school.db")

DAYS_UA   = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"]
DAYS_FULL = ["Понеділок","Вівторок","Середа","Четвер","П'ятниця","Субота","Неділя"]
LOW_BALANCE_THRESHOLD = 1   # нагадувати при залишку <= цього числа

# ── Стани ConversationHandler ─────────────────────────────────────────────────
(S_STU_NAME, S_STU_GROUP, S_STU_TYPE, S_STU_TG,
 S_SCH_TARGET, S_SCH_GROUP_SEL, S_SCH_STU_SEL,
 S_SCH_WEEKDAY, S_SCH_TIME, S_SCH_SUBJECT, S_SCH_REMIND,
 S_LESSON_STU, S_LESSON_SUBJECT, S_LESSON_NOTE, S_LESSON_HW,
 S_PAY_STU, S_PAY_COUNT, S_PAY_NOTE,
 S_GROUP_LESSON_GROUP, S_GROUP_LESSON_SUBJECT,
 S_GROUP_LESSON_NOTE, S_GROUP_LESSON_HW) = range(22)

# ═══════════════════════════════════════════════════════════════════════════════
# ДОПОМІЖНІ
# ═══════════════════════════════════════════════════════════════════════════════

def tid() -> int:
    return db.get_teacher_id()

def is_teacher(uid: int) -> bool:
    return db.is_teacher(uid)

def balance_icon(b: int) -> str:
    if b == 0:   return "🔴"
    if b <= 2:   return "🟡"
    return "🟢"

def students_kb(prefix: str, group: str = None) -> InlineKeyboardMarkup:
    ss = db.get_group_students(group) if group else db.get_all_students()
    rows = [[InlineKeyboardButton(
        f"{s['name']} {balance_icon(s['balance'])} {s['balance']} ур.",
        callback_data=f"{prefix}:{s['id']}"
    )] for s in ss]
    return InlineKeyboardMarkup(rows)

def groups_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(g, callback_data=f"{prefix}:{g}")]
        for g in db.get_groups()
    ])

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано.")
    ctx.user_data.clear()
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if is_teacher(uid):
        await update.message.reply_text(
            "👩‍🏫 *Панель вчителя*\n\n"
            "👤 /add\\_student — додати учня\n"
            "📅 /add\\_schedule — додати заняття до розкладу\n"
            "📋 /schedules — переглянути розклад\n"
            "✅ /done\\_lesson — урок відбувся (1 учень)\n"
            "✅ /done\\_group — урок відбувся (група)\n"
            "💰 /add\\_payment — отримав оплату\n"
            "📊 /balances — баланси всіх учнів\n"
            "📝 /homework — перевірити ДЗ",
            parse_mode="Markdown"
        )
        return

    student = db.get_student_by_tg(uid)
    if student:
        b = student["balance"]
        await update.message.reply_text(
            f"👋 Привіт, *{student['name']}*!\n\n"
            f"{balance_icon(b)} Залишок уроків: *{b}*\n\n"
            "📚 /notes — мої нотатки та ДЗ\n"
            "✅ /hw\\_done — здати домашнє завдання\n"
            "💰 /my\\_balance — мій баланс",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            f"👋 Привіт!\n\nВаш Telegram ID: `{uid}`\n\n"
            "Передайте його вчителю — вас додадуть до бота.",
            parse_mode="Markdown"
        )

# ═══════════════════════════════════════════════════════════════════════════════
# ДОДАТИ УЧНЯ
# ═══════════════════════════════════════════════════════════════════════════════

async def add_student(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_teacher(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("👤 Ім'я учня:")
    return S_STU_NAME

async def stu_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["stu_name"] = update.message.text.strip()
    await update.message.reply_text("📚 Група або предмет (наприклад: *7-А* або *Англійська*):", parse_mode="Markdown")
    return S_STU_GROUP

async def stu_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["stu_group"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👥 Групове", callback_data="styp:group"),
        InlineKeyboardButton("👤 Індивідуальне", callback_data="styp:individual"),
    ]])
    await update.message.reply_text("Тип занять:", reply_markup=kb)
    return S_STU_TYPE

async def stu_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["stu_type"] = q.data.split(":")[1]
    await q.edit_message_text("📱 Telegram ID учня (число) або напишіть *пропустити*:", parse_mode="Markdown")
    return S_STU_TG

async def stu_tg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    tg_id = None
    if text.lower() not in ("пропустити", "-"):
        try: tg_id = int(text)
        except ValueError:
            await update.message.reply_text("Невірний ID. Введіть число або 'пропустити'.")
            return S_STU_TG
    sid = db.add_student(ctx.user_data["stu_name"], ctx.user_data["stu_group"],
                         ctx.user_data["stu_type"], tg_id)
    await update.message.reply_text(
        f"✅ *{ctx.user_data['stu_name']}* додано!\nID в базі: `{sid}`", parse_mode="Markdown")
    if tg_id:
        try:
            await ctx.bot.send_message(tg_id,
                "🎉 Вас додано до бота вчителя!\n\nНапишіть /start")
        except Exception: pass
    ctx.user_data.clear()
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════════════════
# ДОДАТИ РОЗКЛАД
# ═══════════════════════════════════════════════════════════════════════════════

async def add_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_teacher(update.effective_user.id): return ConversationHandler.END
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👥 Для групи", callback_data="sch:group"),
        InlineKeyboardButton("👤 Для учня", callback_data="sch:student"),
    ]])
    await update.message.reply_text("Для кого заняття?", reply_markup=kb)
    return S_SCH_TARGET

async def sch_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["sch_for"] = q.data.split(":")[1]
    if ctx.user_data["sch_for"] == "group":
        if not db.get_groups():
            await q.edit_message_text("Немає груп. Спочатку додайте учнів.")
            return ConversationHandler.END
        await q.edit_message_text("Оберіть групу:", reply_markup=groups_kb("schg"))
        return S_SCH_GROUP_SEL
    await q.edit_message_text("Оберіть учня:", reply_markup=students_kb("schs"))
    return S_SCH_STU_SEL

async def sch_group_sel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["sch_group"] = q.data.split(":")[1]
    ctx.user_data["sch_student"] = None
    await q.edit_message_text("День тижня? (1=Пн … 7=Нд):")
    return S_SCH_WEEKDAY

async def sch_stu_sel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["sch_student"] = int(q.data.split(":")[1])
    ctx.user_data["sch_group"] = None
    await q.edit_message_text("День тижня? (1=Пн … 7=Нд):")
    return S_SCH_WEEKDAY

async def sch_weekday(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        d = int(update.message.text.strip())
        assert 1 <= d <= 7
    except Exception:
        await update.message.reply_text("Введіть число від 1 до 7.")
        return S_SCH_WEEKDAY
    ctx.user_data["sch_wd"] = d - 1
    await update.message.reply_text("Час (ГГ:ХХ, наприклад 16:00):")
    return S_SCH_TIME

async def sch_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h, m = map(int, update.message.text.strip().split(":"))
        assert 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        await update.message.reply_text("Формат: ГГ:ХХ")
        return S_SCH_TIME
    ctx.user_data["sch_h"], ctx.user_data["sch_m"] = h, m
    await update.message.reply_text("Назва предмету:")
    return S_SCH_SUBJECT

async def sch_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["sch_subj"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("30 хв", callback_data="rem:30"),
        InlineKeyboardButton("1 год", callback_data="rem:60"),
        InlineKeyboardButton("2 год", callback_data="rem:120"),
    ]])
    await update.message.reply_text("Нагадати за:", reply_markup=kb)
    return S_SCH_REMIND

async def sch_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    remind = int(q.data.split(":")[1])
    d = ctx.user_data
    db.add_schedule(d.get("sch_student"), d.get("sch_group"),
                    d["sch_wd"], d["sch_h"], d["sch_m"], d["sch_subj"], remind)
    who = f"групу {d['sch_group']}" if d.get("sch_group") else f"учня #{d['sch_student']}"
    await q.edit_message_text(
        f"✅ Заняття додано!\n"
        f"📅 {DAYS_FULL[d['sch_wd']]} {d['sch_h']:02d}:{d['sch_m']:02d} — {d['sch_subj']}\n"
        f"👥 {who} · 🔔 за {remind} хв"
    )
    ctx.user_data.clear()
    return ConversationHandler.END

async def show_schedules(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_teacher(update.effective_user.id): return
    scheds = db.get_all_schedules()
    if not scheds:
        await update.message.reply_text("Розклад порожній. Додайте: /add\\_schedule", parse_mode="Markdown")
        return
    lines = []
    for s in scheds:
        who = f"👥 {s['group_name']}" if s["group_name"] else f"👤 #{s['student_id']}"
        lines.append(f"{DAYS_UA[s['weekday']]} {s['hour']:02d}:{s['minute']:02d} — *{s['subject']}* {who}")
    await update.message.reply_text("📅 *Розклад:*\n\n" + "\n".join(lines), parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════════════════
# УРОК ВІДБУВСЯ — ОДИН УЧЕНЬ
# ═══════════════════════════════════════════════════════════════════════════════

async def done_lesson(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_teacher(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("Оберіть учня:", reply_markup=students_kb("dlstu"))
    return S_LESSON_STU

async def dl_student(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["dl_sid"] = int(q.data.split(":")[1])
    s = db.get_student(ctx.user_data["dl_sid"])
    await q.edit_message_text(f"👤 *{s['name']}* (баланс: {s['balance']} ур.)\n\nПредмет:", parse_mode="Markdown")
    return S_LESSON_SUBJECT

async def dl_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["dl_subj"] = update.message.text.strip()
    await update.message.reply_text("Нотатка після уроку (що проходили):")
    return S_LESSON_NOTE

async def dl_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["dl_note"] = update.message.text.strip()
    await update.message.reply_text("Домашнє завдання (або *немає*):", parse_mode="Markdown")
    return S_LESSON_HW

async def dl_hw(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hw = update.message.text.strip()
    if hw.lower() == "немає": hw = None
    d = ctx.user_data
    sid = d["dl_sid"]
    teacher = update.effective_user.id

    # Зберігаємо урок
    lesson_id = db.add_lesson(sid, d["dl_subj"], d["dl_note"], hw, teacher)
    # Мінусуємо баланс
    db.deduct_lesson(sid)
    new_balance = db.get_balance(sid)
    student = db.get_student(sid)

    await update.message.reply_text(
        f"✅ Урок зафіксовано!\n"
        f"👤 {student['name']}\n"
        f"{balance_icon(new_balance)} Залишок: *{new_balance}* уроків",
        parse_mode="Markdown"
    )

    # Надіслати нотатку учню
    if student["telegram_id"]:
        hw_text = f"\n\n📋 *ДЗ:* {hw}" if hw else ""
        msg = (f"📝 *Нотатка після уроку — {d['dl_subj']}*\n\n"
               f"{d['dl_note']}{hw_text}")
        if hw:
            msg += "\n\nКоли виконаєте ДЗ — натисніть /hw\\_done"
        try:
            await ctx.bot.send_message(student["telegram_id"], msg, parse_mode="Markdown")
        except Exception: pass

        # Нагадування про баланс учню
        if new_balance == 0:
            try:
                await ctx.bot.send_message(
                    student["telegram_id"],
                    "🔴 *Уроки закінчились!*\n\nБудь ласка, поповніть пакет занять.",
                    parse_mode="Markdown"
                )
            except Exception: pass
        elif new_balance <= LOW_BALANCE_THRESHOLD:
            try:
                await ctx.bot.send_message(
                    student["telegram_id"],
                    f"🟡 Залишилось *{new_balance}* урок(и). Час поповнити пакет!",
                    parse_mode="Markdown"
                )
            except Exception: pass

    ctx.user_data.clear()
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════════════════
# УРОК ВІДБУВСЯ — ГРУПА
# ═══════════════════════════════════════════════════════════════════════════════

async def done_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_teacher(update.effective_user.id): return ConversationHandler.END
    if not db.get_groups():
        await update.message.reply_text("Немає груп.")
        return ConversationHandler.END
    await update.message.reply_text("Оберіть групу:", reply_markup=groups_kb("dgrp"))
    return S_GROUP_LESSON_GROUP

async def dg_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["dg_group"] = q.data.split(":")[1]
    students = db.get_group_students(ctx.user_data["dg_group"])
    names = ", ".join(s["name"] for s in students)
    await q.edit_message_text(f"Група: *{ctx.user_data['dg_group']}*\nУчні: {names}\n\nПредмет:", parse_mode="Markdown")
    return S_GROUP_LESSON_SUBJECT

async def dg_subject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["dg_subj"] = update.message.text.strip()
    await update.message.reply_text("Нотатка після уроку:")
    return S_GROUP_LESSON_NOTE

async def dg_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["dg_note"] = update.message.text.strip()
    await update.message.reply_text("Домашнє завдання (або *немає*):", parse_mode="Markdown")
    return S_GROUP_LESSON_HW

async def dg_hw(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hw = update.message.text.strip()
    if hw.lower() == "немає": hw = None
    d = ctx.user_data
    teacher = update.effective_user.id
    students = db.get_group_students(d["dg_group"])

    summary = []
    for s in students:
        db.add_lesson(s["id"], d["dg_subj"], d["dg_note"], hw, teacher)
        db.deduct_lesson(s["id"])
        new_balance = db.get_balance(s["id"])
        icon = balance_icon(new_balance)
        summary.append(f"{icon} {s['name']}: {new_balance} ур.")

        if s["telegram_id"]:
            hw_text = f"\n\n📋 *ДЗ:* {hw}" if hw else ""
            msg = (f"📝 *Нотатка — {d['dg_subj']}*\n\n{d['dg_note']}{hw_text}")
            if hw:
                msg += "\n\nКоли виконаєте ДЗ — /hw\\_done"
            try:
                await ctx.bot.send_message(s["telegram_id"], msg, parse_mode="Markdown")
            except Exception: pass

            if new_balance == 0:
                try:
                    await ctx.bot.send_message(s["telegram_id"],
                        "🔴 *Уроки закінчились!* Поповніть пакет.", parse_mode="Markdown")
                except Exception: pass
            elif new_balance <= LOW_BALANCE_THRESHOLD:
                try:
                    await ctx.bot.send_message(s["telegram_id"],
                        f"🟡 Залишилось *{new_balance}* урок(и). Час поповнити!",
                        parse_mode="Markdown")
                except Exception: pass

    await update.message.reply_text(
        f"✅ Урок для групи *{d['dg_group']}* зафіксовано!\n\n" + "\n".join(summary),
        parse_mode="Markdown"
    )
    ctx.user_data.clear()
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════════════════
# ОПЛАТА (ВЧИТЕЛЬ ОТРИМАВ ГРОШІ)
# ═══════════════════════════════════════════════════════════════════════════════

async def add_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_teacher(update.effective_user.id): return ConversationHandler.END
    await update.message.reply_text("Оберіть учня:", reply_markup=students_kb("paystu"))
    return S_PAY_STU

async def pay_student(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["pay_sid"] = int(q.data.split(":")[1])
    s = db.get_student(ctx.user_data["pay_sid"])
    await q.edit_message_text(
        f"👤 *{s['name']}*\nПоточний баланс: {s['balance']} ур.\n\nСкільки уроків купив?",
        parse_mode="Markdown"
    )
    return S_PAY_COUNT

async def pay_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        n = int(update.message.text.strip())
        assert n > 0
    except Exception:
        await update.message.reply_text("Введіть ціле число більше 0.")
        return S_PAY_COUNT
    ctx.user_data["pay_count"] = n
    await update.message.reply_text("Нотатка до оплати (необов'язково, або *-*):", parse_mode="Markdown")
    return S_PAY_NOTE

async def pay_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    if note == "-": note = None
    d = ctx.user_data
    db.add_payment(d["pay_sid"], d["pay_count"], note)
    new_balance = db.get_balance(d["pay_sid"])
    student = db.get_student(d["pay_sid"])
    await update.message.reply_text(
        f"✅ Оплату зафіксовано!\n"
        f"👤 {student['name']}\n"
        f"➕ +{d['pay_count']} уроків\n"
        f"🟢 Новий баланс: *{new_balance}*",
        parse_mode="Markdown"
    )
    if student["telegram_id"]:
        try:
            await ctx.bot.send_message(
                student["telegram_id"],
                f"✅ Оплату отримано!\n➕ *+{d['pay_count']} уроків*\n🟢 Баланс: *{new_balance}*",
                parse_mode="Markdown"
            )
        except Exception: pass
    ctx.user_data.clear()
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════════════════════
# БАЛАНСИ (ВЧИТЕЛЬ)
# ═══════════════════════════════════════════════════════════════════════════════

async def show_balances(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_teacher(update.effective_user.id): return
    students = db.get_all_students()
    if not students:
        await update.message.reply_text("Учнів ще немає.")
        return
    lines = ["📊 *Баланси уроків:*\n"]
    current_group = None
    for s in students:
        if s["group_name"] != current_group:
            current_group = s["group_name"]
            lines.append(f"\n__{current_group}__")
        lines.append(f"{balance_icon(s['balance'])} {s['name']}: *{s['balance']}* ур.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════════════════════════
# БАЛАНС (УЧЕНЬ)
# ═══════════════════════════════════════════════════════════════════════════════

async def my_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    student = db.get_student_by_tg(update.effective_user.id)
    if not student:
        await update.message.reply_text("Вас не знайдено. Зверніться до вчителя.")
        return
    b = student["balance"]
    history = db.get_payment_history(student["id"])
    hist_text = ""
    if history:
        lines = [f"  +{p['lessons_count']} ур. ({p['added_at'][:10]})" for p in history[:5]]
        hist_text = "\n\n📋 Останні поповнення:\n" + "\n".join(lines)
    await update.message.reply_text(
        f"{balance_icon(b)} *Залишок уроків: {b}*\n"
        f"📈 Всього проведено: {student['total_done']}{hist_text}",
        parse_mode="Markdown"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# НОТАТКИ (УЧЕНЬ)
# ═══════════════════════════════════════════════════════════════════════════════

async def student_notes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    student = db.get_student_by_tg(update.effective_user.id)
    if not student:
        await update.message.reply_text("Вас не знайдено.")
        return
    lessons = db.get_student_lessons(student["id"])
    if not lessons:
        await update.message.reply_text("Нотаток ще немає.")
        return
    btns = []
    for l in lessons:
        hw_icon = "📋" if l["homework"] else "📝"
        btns.append([InlineKeyboardButton(
            f"{hw_icon} {l['subject']} ({l['done_at'][:10]})",
            callback_data=f"vnote:{l['id']}"
        )])
    await update.message.reply_text("📚 *Мої нотатки:*", parse_mode="Markdown",
                                     reply_markup=InlineKeyboardMarkup(btns))

async def view_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    lesson = db.get_lesson(int(q.data.split(":")[1]))
    if not lesson: return
    hw_text = f"\n\n📋 *ДЗ:* {lesson['homework']}" if lesson["homework"] else ""
    await q.edit_message_text(
        f"📝 *{lesson['subject']}*\n🗓 {lesson['done_at'][:10]}\n\n{lesson['note_text']}{hw_text}",
        parse_mode="Markdown"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# ДЗ — УЧЕНЬ
# ═══════════════════════════════════════════════════════════════════════════════

async def hw_done_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    student = db.get_student_by_tg(update.effective_user.id)
    if not student:
        await update.message.reply_text("Вас не знайдено.")
        return
    lessons = [l for l in db.get_student_lessons(student["id"]) if l["homework"]]
    if not lessons:
        await update.message.reply_text("Немає активних домашніх завдань.")
        return
    btns = [[InlineKeyboardButton(
        f"📋 {l['subject']} ({l['done_at'][:10]})",
        callback_data=f"hwsubmit:{l['id']}"
    )] for l in lessons[:10]]
    await update.message.reply_text("Яке ДЗ виконали?", reply_markup=InlineKeyboardMarkup(btns))

async def hw_submit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    student = db.get_student_by_tg(update.effective_user.id)
    lesson_id = int(q.data.split(":")[1])
    lesson = db.get_lesson(lesson_id)
    sub_id = db.submit_homework(lesson_id, student["id"])
    await q.edit_message_text("✅ Надіслано вчителю! Чекайте підтвердження.")
    teacher = tid()
    if teacher:
        btns = [[InlineKeyboardButton("✅ Перевірено", callback_data=f"hwcheck:{sub_id}")]]
        await ctx.bot.send_message(
            teacher,
            f"📬 *{student['name']}* здав ДЗ!\n📖 {lesson['subject']}\n📋 {lesson['homework']}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns)
        )

# ═══════════════════════════════════════════════════════════════════════════════
# ДЗ — ВЧИТЕЛЬ
# ═══════════════════════════════════════════════════════════════════════════════

async def teacher_homework(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_teacher(update.effective_user.id): return
    pending = db.get_pending_homework()
    if not pending:
        await update.message.reply_text("✅ Усі ДЗ перевірені!")
        return
    for p in pending:
        btns = [[InlineKeyboardButton("✅ Перевірено", callback_data=f"hwcheck:{p['id']}")]]
        await update.message.reply_text(
            f"📬 *{p['student_name']}*\n📖 {p['subject']}\n📋 {p['homework']}\n🕐 {p['submitted_at']}",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns)
        )

async def hw_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    sub_id = int(q.data.split(":")[1])
    sub = db.get_submission(sub_id)
    db.check_homework(sub_id)
    await q.edit_message_text(q.message.text + "\n\n✅ *Перевірено!*", parse_mode="Markdown")
    if sub and sub["student_tg"]:
        try:
            await ctx.bot.send_message(
                sub["student_tg"],
                f"🎉 Вчитель перевірив ваше ДЗ з *{sub['subject']}*! ✅",
                parse_mode="Markdown"
            )
        except Exception: pass

# ═══════════════════════════════════════════════════════════════════════════════
# АВТОМАТИЧНІ НАГАДУВАННЯ
# ═══════════════════════════════════════════════════════════════════════════════

async def check_lesson_reminders(app):
    """Щохвилини перевіряємо розклад"""
    now = datetime.now()
    for s in db.get_all_schedules():
        if s["weekday"] != now.weekday(): continue
        lesson_dt = now.replace(hour=s["hour"], minute=s["minute"], second=0, microsecond=0)
        remind_dt = lesson_dt - timedelta(minutes=s["remind_min"])
        if abs((now - remind_dt).total_seconds()) >= 60: continue

        msg = (f"🔔 *Нагадування про заняття!*\n\n"
               f"📖 {s['subject']}\n"
               f"🕐 Сьогодні о {s['hour']:02d}:{s['minute']:02d}")

        if s["student_id"]:
            st = db.get_student(s["student_id"])
            if st and st["telegram_id"]:
                try: await app.bot.send_message(st["telegram_id"], msg, parse_mode="Markdown")
                except Exception: pass
        elif s["group_name"]:
            for st in db.get_group_students(s["group_name"]):
                if st["telegram_id"]:
                    try: await app.bot.send_message(st["telegram_id"], msg, parse_mode="Markdown")
                    except Exception: pass

# ═══════════════════════════════════════════════════════════════════════════════
# ЗБІРКА І ЗАПУСК
# ═══════════════════════════════════════════════════════════════════════════════

def build_app():
    token = os.getenv("BOT_TOKEN")
    if not token: raise RuntimeError("Потрібна змінна BOT_TOKEN")
    teacher_env = os.getenv("TEACHER_ID")
    if teacher_env:
        db.add_teacher(int(teacher_env), "Вчитель")
        log.info(f"Вчитель зареєстрований: {teacher_env}")

    app = Application.builder().token(token).build()

    # Планувальник
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    scheduler.add_job(lambda: app.create_task(check_lesson_reminders(app)),
                      CronTrigger(minute="*"))
    scheduler.start()

    # ConversationHandler: учень
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_student", add_student)],
        states={
            S_STU_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, stu_name)],
            S_STU_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, stu_group)],
            S_STU_TYPE:  [CallbackQueryHandler(stu_type, pattern="^styp:")],
            S_STU_TG:    [MessageHandler(filters.TEXT & ~filters.COMMAND, stu_tg)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # ConversationHandler: розклад
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_schedule", add_schedule)],
        states={
            S_SCH_TARGET:    [CallbackQueryHandler(sch_target, pattern="^sch:")],
            S_SCH_GROUP_SEL: [CallbackQueryHandler(sch_group_sel, pattern="^schg:")],
            S_SCH_STU_SEL:   [CallbackQueryHandler(sch_stu_sel, pattern="^schs:")],
            S_SCH_WEEKDAY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, sch_weekday)],
            S_SCH_TIME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, sch_time)],
            S_SCH_SUBJECT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, sch_subject)],
            S_SCH_REMIND:    [CallbackQueryHandler(sch_remind, pattern="^rem:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # ConversationHandler: урок (один учень)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("done_lesson", done_lesson)],
        states={
            S_LESSON_STU:     [CallbackQueryHandler(dl_student, pattern="^dlstu:")],
            S_LESSON_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, dl_subject)],
            S_LESSON_NOTE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, dl_note)],
            S_LESSON_HW:      [MessageHandler(filters.TEXT & ~filters.COMMAND, dl_hw)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # ConversationHandler: урок (група)
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("done_group", done_group)],
        states={
            S_GROUP_LESSON_GROUP:   [CallbackQueryHandler(dg_group, pattern="^dgrp:")],
            S_GROUP_LESSON_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, dg_subject)],
            S_GROUP_LESSON_NOTE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, dg_note)],
            S_GROUP_LESSON_HW:      [MessageHandler(filters.TEXT & ~filters.COMMAND, dg_hw)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # ConversationHandler: оплата
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_payment", add_payment)],
        states={
            S_PAY_STU:   [CallbackQueryHandler(pay_student, pattern="^paystu:")],
            S_PAY_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, pay_count)],
            S_PAY_NOTE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, pay_note)],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    ))

    # Прості команди
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("schedules", show_schedules))
    app.add_handler(CommandHandler("balances", show_balances))
    app.add_handler(CommandHandler("homework", teacher_homework))
    app.add_handler(CommandHandler("notes", student_notes))
    app.add_handler(CommandHandler("hw_done", hw_done_start))
    app.add_handler(CommandHandler("my_balance", my_balance))

    # Callback
    app.add_handler(CallbackQueryHandler(view_note,  pattern="^vnote:"))
    app.add_handler(CallbackQueryHandler(hw_submit,  pattern="^hwsubmit:"))
    app.add_handler(CallbackQueryHandler(hw_check,   pattern="^hwcheck:"))

    return app

if __name__ == "__main__":
    build_app().run_polling()
