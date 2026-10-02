from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pyrogram.enums import ChatType

from bot import (
    non_queued_dl,
    non_queued_up,
    queued_dl,
    queued_up,
    sudo_users,
    task_dict,
    user_data,
)
from bot.core.config_manager import Config
from bot.helper.ext_utils.task_manager import check_running_tasks, start_from_queued
from bot.modules.taskm import _is_authorized, get_taskm_details, taskm_callback, taskm_handler


@pytest.mark.asyncio
async def test_is_authorized():
    Config.OWNER_ID = 11111
    sudo_users.clear()
    sudo_users.append(22222)
    user_data.clear()
    user_data[33333] = {"SUDO": True}

    msg_owner = MagicMock()
    msg_owner.from_user.id = 11111

    msg_sudo = MagicMock()
    msg_sudo.from_user.id = 22222

    msg_user_sudo = MagicMock()
    msg_user_sudo.from_user.id = 33333

    msg_normal = MagicMock()
    msg_normal.from_user.id = 44444

    assert await _is_authorized(11111, msg_owner) is True
    assert await _is_authorized(22222, msg_sudo) is True
    assert await _is_authorized(33333, msg_user_sudo) is True
    with patch("bot.helper.telegram_helper.filters.CustomFilters.sudo", new=AsyncMock(return_value=False)):
        assert await _is_authorized(44444, msg_normal) is False


@pytest.mark.asyncio
async def test_taskm_handler_dm_and_authorization():
    Config.OWNER_ID = 10001
    user_data.clear()

    # Unwrap decorator to execute directly in same loop
    unwrapped_handler = getattr(taskm_handler, "__wrapped__", taskm_handler)

    # Case 1: Non-DM
    msg_group = AsyncMock()
    msg_group.from_user.id = 10001
    msg_group.chat.type = ChatType.GROUP

    with patch("bot.modules.taskm.send_message", new_callable=AsyncMock) as mock_send:
        await unwrapped_handler(None, msg_group)
        mock_send.assert_called_once()
        assert "only be used in DMs" in mock_send.call_args[0][1]

    # Case 2: DM but unauthorized
    msg_dm_unauth = AsyncMock()
    msg_dm_unauth.from_user.id = 99999
    msg_dm_unauth.sender_chat = None
    msg_dm_unauth.chat.type = ChatType.PRIVATE

    with patch("bot.modules.taskm.send_message", new_callable=AsyncMock) as mock_send, \
         patch("bot.modules.taskm.CustomFilters.sudo", new=AsyncMock(return_value=False)):
        await unwrapped_handler(None, msg_dm_unauth)
        mock_send.assert_called_once()
        assert "Access Denied" in mock_send.call_args[0][1]

    # Case 3: DM & Authorized
    msg_dm_auth = AsyncMock()
    msg_dm_auth.from_user.id = 10001
    msg_dm_auth.sender_chat = None
    msg_dm_auth.chat.type = ChatType.PRIVATE

    with patch("bot.modules.taskm.send_message", new_callable=AsyncMock) as mock_send:
        await unwrapped_handler(None, msg_dm_auth)
        mock_send.assert_called_once()
        assert "Task Manager for Authorized User" in mock_send.call_args[0][1]


@pytest.mark.asyncio
async def test_taskm_limit_queue_enforcement():
    user_id = 77777
    user_data[user_id] = {"maxtask": 2}
    non_queued_dl.clear()
    non_queued_up.clear()
    queued_dl.clear()
    queued_up.clear()
    task_dict.clear()

    listener_1 = MagicMock(user_id=user_id, mid=101, force_run=False, force_download=False, force_upload=False)
    listener_2 = MagicMock(user_id=user_id, mid=102, force_run=False, force_download=False, force_upload=False)
    listener_3 = MagicMock(user_id=user_id, mid=103, force_run=False, force_download=False, force_upload=False)

    task_1 = MagicMock(listener=listener_1)
    task_2 = MagicMock(listener=listener_2)
    task_3 = MagicMock(listener=listener_3)

    task_dict[101] = task_1
    task_dict[102] = task_2
    task_dict[103] = task_3

    # Submit task 1 -> should run
    over1, evt1 = await check_running_tasks(listener_1, "dl")
    assert bool(over1) is False
    assert 101 in non_queued_dl

    # Submit task 2 -> should run
    over2, evt2 = await check_running_tasks(listener_2, "dl")
    assert bool(over2) is False
    assert 102 in non_queued_dl

    # Submit task 3 -> should queue
    over3, evt3 = await check_running_tasks(listener_3, "dl")
    assert bool(over3) is True
    assert 103 in queued_dl

    # Finish task 1 and start next queued task
    non_queued_dl.remove(101)
    del task_dict[101]

    await start_from_queued()

    assert 103 in non_queued_dl
    assert 103 not in queued_dl


@pytest.mark.asyncio
async def test_taskm_callback_save():
    user_id = 88888
    query = AsyncMock()
    query.from_user.id = user_id
    query.data = f"taskm save {user_id} 3"

    unwrapped_callback = getattr(taskm_callback, "__wrapped__", taskm_callback)

    with patch("bot.modules.taskm.database.update_user_data", new=AsyncMock()), \
         patch("bot.modules.taskm.edit_message", new=AsyncMock()), \
         patch("bot.modules.taskm.get_taskm_details", new=AsyncMock(return_value=("text", None))):
        await unwrapped_callback(None, query)
        assert user_data[user_id]["maxtask"] == 3
