from pyrogram.enums import ChatType
from pyrogram.filters import command, regex
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

from bot import (
    LOGGER,
    non_queued_dl,
    non_queued_up,
    queue_dict_lock,
    queued_dl,
    queued_up,
    sudo_users,
    task_dict,
    task_dict_lock,
    user_data,
)
from bot.core.config_manager import Config
from bot.core.tg_client import TgClient
from bot.helper.ext_utils.bot_utils import new_task, safe_int, update_user_ldata
from bot.helper.ext_utils.db_handler import database
from bot.helper.ext_utils.task_manager import start_from_queued
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import delete_message, send_message, edit_message


async def _is_authorized(user_id, message_or_query):
    if user_id == Config.OWNER_ID:
        return True
    if user_id in sudo_users:
        return True
    if user_data.get(user_id, {}).get("SUDO"):
        return True
    if await CustomFilters.sudo("", message_or_query):
        return True
    return False


async def get_taskm_details(user_id):
    user_dict = user_data.get(user_id, {})
    limit = safe_int(user_dict.get("maxtask", Config.USER_MAX_TASKS))
    limit_str = str(limit) if limit > 0 else "Unlimited"

    running_tasks = []
    queued_tasks = []

    async with task_dict_lock:
        async with queue_dict_lock:
            for mid, task in task_dict.items():
                listener = getattr(task, "listener", None)
                if not listener or listener.user_id != user_id:
                    continue

                task_name = getattr(listener, "name", "Task")
                try:
                    status_str = task.status()
                except Exception:
                    status_str = "Unknown"

                if mid in non_queued_dl or mid in non_queued_up:
                    running_tasks.append((mid, task_name, status_str))
                elif mid in queued_dl:
                    pos = list(queued_dl.keys()).index(mid) + 1
                    queued_tasks.append((mid, task_name, status_str, pos, "DL Queue"))
                elif mid in queued_up:
                    pos = list(queued_up.keys()).index(mid) + 1
                    queued_tasks.append((mid, task_name, status_str, pos, "UP Queue"))

    text = f"⚙️ <b>Task Manager for Authorized User</b>\n\n"
    text += f"• <b>Configured Task Limit:</b> {limit_str}\n"
    text += f"• <b>Current Running Tasks:</b> {len(running_tasks)}\n"
    text += f"• <b>Current Queued Tasks:</b> {len(queued_tasks)}\n\n"

    if running_tasks:
        text += "🏃 <b>Running Tasks:</b>\n"
        for idx, (mid, name, status) in enumerate(running_tasks, 1):
            text += f"{idx}. <b>{name}</b> | Status: <code>{status}</code>\n"
        text += "\n"

    if queued_tasks:
        text += "⏳ <b>Queued Tasks:</b>\n"
        for idx, (mid, name, status, pos, q_type) in enumerate(queued_tasks, 1):
            text += f"{idx}. <b>{name}</b> | Pos: #{pos} ({q_type}) | Status: <code>{status}</code>\n"
        text += "\n"

    buttons = ButtonMaker()
    buttons.data_button("Set Limit", f"taskm set {user_id}")
    buttons.data_button("Refresh", f"taskm refresh {user_id}")
    buttons.data_button("Close", f"taskm close {user_id}")
    return text, buttons.build_menu(2)


@new_task
async def taskm_handler(client, message):
    user_id = (message.from_user or message.sender_chat).id

    if message.chat.type != ChatType.PRIVATE:
        await send_message(
            message,
            "<blockquote>❌ <b>/taskm command can only be used in DMs (Private Chat) by Owner or Sudo users.</b></blockquote>",
        )
        return

    if not await _is_authorized(user_id, message):
        await send_message(
            message,
            "<blockquote>❌ <b>Access Denied!</b> Only Owner and Sudo users can use /taskm.</blockquote>",
        )
        return

    text, reply_markup = await get_taskm_details(user_id)
    await send_message(message, text, reply_markup)


@new_task
async def taskm_callback(client, query):
    user_id = query.from_user.id
    data = query.data.split()
    action = data[1]
    target_user_id = int(data[2])

    if user_id != target_user_id and not await _is_authorized(user_id, query):
        await query.answer("Access denied!", show_alert=True)
        return

    if action == "refresh":
        text, reply_markup = await get_taskm_details(target_user_id)
        await edit_message(query.message, text, reply_markup)
        await query.answer("Refreshed!")
    elif action == "close":
        await query.answer()
        await delete_message(query.message)
    elif action == "set":
        buttons = ButtonMaker()
        for limit_val in [1, 2, 3, 5, 10, 0]:
            lbl = "Unlimited (0)" if limit_val == 0 else str(limit_val)
            buttons.data_button(lbl, f"taskm save {target_user_id} {limit_val}")
        buttons.data_button("Back", f"taskm refresh {target_user_id}")
        await edit_message(
            query.message,
            "<b>Select running task limit for your account:</b>\n"
            "<i>Tasks exceeding this limit will automatically enter the queue.</i>",
            buttons.build_menu(3),
        )
        await query.answer()
    elif action == "save":
        new_limit = int(data[3])
        update_user_ldata(target_user_id, "maxtask", new_limit)
        await database.update_user_data(target_user_id)
        await start_from_queued()

        await query.answer(f"Task limit set to {new_limit if new_limit > 0 else 'Unlimited'}!", show_alert=True)
        text, reply_markup = await get_taskm_details(target_user_id)
        await edit_message(query.message, text, reply_markup)
