import pytest
from unittest.mock import MagicMock
from bot.modules.ytdlp import extract_info, YtSelection, find_node_executable, setup_js_runtimes, log_ytdlp_startup_info


def test_find_node_executable():
    node_path = find_node_executable()
    assert node_path is not None
    assert "node" in node_path.lower() or "deno" in node_path.lower() or "bun" in node_path.lower()


def test_setup_js_runtimes(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda rt: f"/usr/bin/{rt}" if rt in ("node", "deno") else None)
    opts = {}
    setup_js_runtimes(opts)
    assert "js_runtimes" in opts
    assert "deno" in opts["js_runtimes"]
    assert opts["js_runtimes"]["deno"]["path"] == "/usr/bin/deno"
    assert list(opts["js_runtimes"].keys())[0] == "deno"


def test_ytdl_leech_command_alias_and_suffix(monkeypatch):
    from bot.helper.telegram_helper.bot_commands import BotCommands
    from bot.core.config_manager import Config

    assert "yl" in BotCommands._static_commands["YtdlLeech"]
    assert "ytdlleech" in BotCommands._static_commands["YtdlLeech"]

    monkeypatch.setattr(Config, "CMD_SUFFIX", "_bot")
    BotCommands.refresh_commands()
    assert "yl_bot" in BotCommands.YtdlLeechCommand
    assert "ytdlleech_bot" in BotCommands.YtdlLeechCommand

    monkeypatch.setattr(Config, "CMD_SUFFIX", "")
    BotCommands.refresh_commands()
    assert "yl" in BotCommands.YtdlLeechCommand
    assert "ytdlleech" in BotCommands.YtdlLeechCommand


def test_youtubedl_helper_opts_setup(tmp_path, monkeypatch):
    from bot.helper.mirror_leech_utils.download_utils.yt_dlp_download import YoutubeDLHelper

    cookie_file = str(tmp_path / "cookies.txt")
    with open(cookie_file, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")

    monkeypatch.setattr("shutil.which", lambda rt: "/usr/bin/deno" if rt == "deno" else None)

    mock_listener = MagicMock()
    mock_listener.user_dict = {"USER_COOKIE_FILE": cookie_file}
    mock_listener.user_id = 9999

    helper = YoutubeDLHelper(mock_listener)
    assert helper.opts.get("cookiefile") == cookie_file
    assert "js_runtimes" in helper.opts
    assert "deno" in helper.opts["js_runtimes"]
    assert helper.opts["js_runtimes"]["deno"]["path"] == "/usr/bin/deno"




@pytest.mark.asyncio
async def test_yt_selection_formats_dynamic():
    mock_listener = MagicMock()
    mock_listener.user_id = 12345
    mock_listener.message = MagicMock()

    selection = YtSelection(mock_listener)

    mock_result = {
        "formats": [
            {
                "format_id": "audio1",
                "ext": "m4a",
                "video_ext": "none",
                "acodec": "mp4a.40.2",
                "tbr": 128,
                "filesize": 1000000,
            },
            {
                "format_id": "v360",
                "ext": "mp4",
                "height": 360,
                "vcodec": "avc1.4d401e",
                "acodec": "none",
                "tbr": 500,
                "filesize": 5000000,
            },
            {
                "format_id": "v720",
                "ext": "mp4",
                "height": 720,
                "vcodec": "avc1.4d401f",
                "acodec": "none",
                "tbr": 1500,
                "filesize": 15000000,
            },
        ]
    }

    from bot.modules import ytdlp
    original_send_message = ytdlp.send_message
    ytdlp.send_message = AsyncMockReturn(MagicMock())
    ytdlp.delete_message = AsyncMockReturn(True)

    try:
        selection.event.set()
        await selection.get_quality(mock_result)

        format_keys = list(selection.formats.keys())

        assert any("360p" in name for name in format_keys)
        assert any("720p" in name for name in format_keys)

        assert not any("1080p" in name for name in format_keys)
        assert not any("1440p" in name for name in format_keys)

        all_vformats = []
        for fmt in selection.formats.values():
            if isinstance(fmt, dict):
                for item in fmt.values():
                    if isinstance(item, list) and len(item) == 2:
                        all_vformats.append(item[1])

        assert any("v360+ba" in fmt or "v360" in fmt for fmt in all_vformats)
        assert any("v720+ba" in fmt or "v720" in fmt for fmt in all_vformats)
    finally:
        ytdlp.send_message = original_send_message


@pytest.mark.asyncio
async def test_yt_selection_playlist_formats_existed_only():
    mock_listener = MagicMock()
    mock_listener.user_id = 12345
    mock_listener.message = MagicMock()

    selection = YtSelection(mock_listener)

    mock_result = {
        "entries": [
            {
                "id": "item1",
                "formats": [
                    {"height": 480, "width": 854, "ext": "mp4", "vcodec": "avc1"},
                    {"height": 720, "width": 1280, "ext": "mp4", "vcodec": "avc1"},
                ],
            }
        ]
    }

    from bot.modules import ytdlp
    original_send_message = ytdlp.send_message
    ytdlp.send_message = AsyncMockReturn(MagicMock())
    ytdlp.delete_message = AsyncMockReturn(True)

    try:
        selection.event.set()
        await selection.get_quality(mock_result)

        keys = list(selection.formats.keys())
        assert "144|mp4" in keys
        assert "720|mp4" in keys
        assert "1080|mp4" in keys
    finally:
        ytdlp.send_message = original_send_message


def test_get_cookie_file(tmp_path, monkeypatch):
    from bot.helper.mirror_leech_utils.download_utils.yt_dlp_download import get_cookie_file

    usr_cookie = str(tmp_path / "user_cookies.txt")
    with open(usr_cookie, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")

    user_dict = {"USER_COOKIE_FILE": usr_cookie}
    cookie, err = get_cookie_file(user_dict, user_id=123)
    assert cookie == usr_cookie
    assert err is None

    owner_cookie = str(tmp_path / "cookies.txt")
    with open(owner_cookie, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")

    monkeypatch.chdir(tmp_path)
    cookie_fallback, err = get_cookie_file({}, user_id=123)
    assert cookie_fallback == "cookies.txt"
    assert err is None

    import os
    os.remove(owner_cookie)
    cookie_none, err = get_cookie_file({}, user_id=123)
    assert cookie_none is None
    assert err is None


class AsyncMockReturn:
    def __init__(self, return_value):
        self.return_value = return_value

    async def __call__(self, *args, **kwargs):
        return self.return_value
