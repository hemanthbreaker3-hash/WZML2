from asyncio import Event, wait_for, wrap_future
from ast import literal_eval
from functools import partial
from os import path as ospath
from time import time

from niquests import AsyncSession
from yt_dlp import YoutubeDL
from pyrogram.filters import regex, user
from pyrogram.handlers import CallbackQueryHandler

from .. import DOWNLOAD_DIR, LOGGER, bot_loop, task_dict_lock
from ..core.config_manager import Config
from ..helper.ext_utils.bot_utils import (
    COMMAND_USAGE,
    arg_parser,
    new_task,
    sync_to_async,
)
from ..helper.ext_utils.links_utils import is_url
from ..helper.ext_utils.task_manager import pre_task_check
from ..helper.ext_utils.status_utils import get_readable_file_size, get_readable_time
from ..helper.listeners.task_listener import TaskListener
from ..helper.mirror_leech_utils.download_utils.yt_dlp_download import (
    YoutubeDLHelper,
    get_cookie_file,
)
from ..helper.telegram_helper.button_build import ButtonMaker
from ..helper.telegram_helper.message_utils import (
    auto_delete_message,
    delete_links,
    delete_message,
    edit_message,
    send_message,
)


@new_task
async def select_format(_, query, obj):
    data = query.data.split()
    message = query.message
    await query.answer()

    if data[1] == "dict":
        b_name = data[2]
        await obj.qual_subbuttons(b_name)
    elif data[1] == "mp3":
        await obj.mp3_subbuttons()
    elif data[1] == "audio":
        await obj.audio_format()
    elif data[1] == "aq":
        if data[2] == "back":
            await obj.audio_format()
        else:
            await obj.audio_quality(data[2])
    elif data[1] == "back":
        await obj.back_to_main()
    elif data[1] == "cancel":
        await edit_message(message, "Task has been cancelled.")
        obj.qual = None
        obj.is_cancelled = True
        if hasattr(obj.listener, "is_cancelled"):
            obj.listener.is_cancelled = True
        obj.event.set()
    else:
        if data[1] == "sub":
            obj.qual = obj.formats[data[2]][data[3]][1]
        elif "|" in data[1]:
            obj.qual = obj.formats[data[1]]
        else:
            obj.qual = data[1]
        obj.event.set()


class YtSelection:
    def __init__(self, listener):
        self.listener = listener
        if hasattr(listener, "client"):
            self._client = listener.client
            self._user_id = listener.user_id
            self._message = listener.message
        else:
            self._client = getattr(listener, "client", None)
            self._user_id = getattr(listener, "user_id", None)
            self._message = getattr(listener, "message", None)
        self._is_m4a = False
        self._reply_to = None
        self._time = time()
        self._timeout = 120
        self._is_playlist = False
        self.is_cancelled = False
        self._main_buttons = None
        self.event = Event()
        self.formats = {}
        self.qual = None

    async def _event_handler(self):
        pfunc = partial(select_format, obj=self)
        handler = self._client.add_handler(
            CallbackQueryHandler(
                pfunc, filters=regex("^ytq") & user(self._user_id)
            ),
            group=-1,
        )
        try:
            await wait_for(self.event.wait(), timeout=self._timeout)
        except Exception:
            await edit_message(self._reply_to, "Timed Out. Task has been cancelled!")
            self.qual = None
            self.is_cancelled = True
            if hasattr(self.listener, "is_cancelled"):
                self.listener.is_cancelled = True
            self.event.set()
        finally:
            self._client.remove_handler(*handler)

    async def get_quality(self, result):
        buttons = ButtonMaker()
        if "entries" in result:
            self._is_playlist = True
            for i in ["144", "240", "360", "480", "720", "1080", "1440", "2160"]:
                video_format = f"bv*[height<=?{i}][ext=mp4]+ba[ext=m4a]/b[height<=?{i}]"
                b_data = f"{i}|mp4"
                self.formats[b_data] = video_format
                buttons.data_button(f"{i}-mp4", f"ytq {b_data}")
                video_format = f"bv*[height<=?{i}][ext=webm]+ba/b[height<=?{i}]"
                b_data = f"{i}|webm"
                self.formats[b_data] = video_format
                buttons.data_button(f"{i}-webm", f"ytq {b_data}")
            buttons.data_button("MP3", "ytq mp3")
            buttons.data_button("Audio Formats", "ytq audio")
            buttons.data_button("Best Videos", "ytq bv*+ba/b")
            buttons.data_button("Best Audios", "ytq ba/b")
            buttons.data_button("Cancel", "ytq cancel", "footer")
            self._main_buttons = buttons.build_menu(3)
            msg = f"Choose Playlist Videos Quality:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        else:
            format_dict = result.get("formats")
            if format_dict is not None:
                self._is_m4a = any(
                    item.get("audio_ext") == "m4a" or item.get("ext") == "m4a"
                    for item in format_dict
                    if item.get("video_ext") == "none" or item.get("vcodec") == "none"
                )
                for item in format_dict:
                    if (
                        item.get("video_ext") == "none"
                        and item.get("acodec") != "none"
                    ):
                        b_name = f"{item['acodec']}-{item['ext']}"
                        v_format = item["format_id"]
                        tbr_val = item.get("tbr") or item.get("abr") or 0
                    elif item.get("height"):
                        height = item["height"]
                        ext = item["ext"]
                        fps = item["fps"] if item.get("fps") else ""
                        b_name = f"{height}p{fps}-{ext}"
                        ba_ext = (
                            "[ext=m4a]" if self._is_m4a and ext == "mp4" else ""
                        )
                        v_format = f"{item['format_id']}+ba{ba_ext}/b[height=?{height}]"
                        tbr_val = item.get("tbr") or (
                            (item.get("vbr") or 0) + (item.get("abr") or 0)
                        ) or 0
                    else:
                        continue

                    if item.get("filesize"):
                        size = item["filesize"]
                    elif item.get("filesize_approx"):
                        size = item["filesize_approx"]
                    else:
                        size = 0

                    self.formats.setdefault(b_name, {})[f"{tbr_val}"] = [
                        size,
                        v_format,
                    ]

                for b_name, tbr_dict in self.formats.items():
                    if len(tbr_dict) == 1:
                        tbr, v_list = next(iter(tbr_dict.items()))
                        buttonName = f"{b_name} ({get_readable_file_size(v_list[0])})"
                        buttons.data_button(buttonName, f"ytq sub {b_name} {tbr}")
                    else:
                        buttons.data_button(b_name, f"ytq dict {b_name}")
            buttons.data_button("MP3", "ytq mp3")
            buttons.data_button("Audio Formats", "ytq audio")
            buttons.data_button("Best Video", "ytq bv*+ba/b")
            buttons.data_button("Best Audio", "ytq ba/b")
            buttons.data_button("Cancel", "ytq cancel", "footer")
            self._main_buttons = buttons.build_menu(2)
            msg = f"Choose Video Quality:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        self._reply_to = await send_message(
            self._message, msg, self._main_buttons
        )
        await self._event_handler()
        if not self.is_cancelled:
            await delete_message(self._reply_to)
        return self.qual

    async def back_to_main(self):
        if self._is_playlist:
            msg = f"Choose Playlist Videos Quality:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        else:
            msg = f"Choose Video Quality:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await edit_message(self._reply_to, msg, self._main_buttons)

    async def qual_subbuttons(self, b_name):
        buttons = ButtonMaker()
        tbr_dict = self.formats[b_name]
        for tbr, d_data in tbr_dict.items():
            button_name = f"{tbr}K ({get_readable_file_size(d_data[0])})"
            buttons.data_button(button_name, f"ytq sub {b_name} {tbr}")
        buttons.data_button("Back", "ytq back", "footer")
        buttons.data_button("Cancel", "ytq cancel", "footer")
        subbuttons = buttons.build_menu(2)
        msg = f"Choose Bit rate for <b>{b_name}</b>:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await edit_message(self._reply_to, msg, subbuttons)

    async def mp3_subbuttons(self):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        audio_qualities = [64, 128, 320]
        for q in audio_qualities:
            audio_format = f"ba/b-mp3-{q}"
            buttons.data_button(f"{q}K-mp3", f"ytq {audio_format}")
        buttons.data_button("Back", "ytq back")
        buttons.data_button("Cancel", "ytq cancel")
        subbuttons = buttons.build_menu(3)
        msg = f"Choose mp3 Audio{i} Bitrate:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await edit_message(self._reply_to, msg, subbuttons)

    async def audio_format(self):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        for frmt in ["aac", "alac", "flac", "m4a", "opus", "vorbis", "wav"]:
            audio_format = f"ba/b-{frmt}-"
            buttons.data_button(frmt, f"ytq aq {audio_format}")
        buttons.data_button("Back", "ytq back", "footer")
        buttons.data_button("Cancel", "ytq cancel", "footer")
        subbuttons = buttons.build_menu(3)
        msg = f"Choose Audio{i} Format:\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await edit_message(self._reply_to, msg, subbuttons)

    async def audio_quality(self, format):
        i = "s" if self._is_playlist else ""
        buttons = ButtonMaker()
        for qual in range(11):
            audio_format = f"{format}{qual}"
            buttons.data_button(str(qual), f"ytq {audio_format}")
        buttons.data_button("Back", "ytq aq back")
        buttons.data_button("Cancel", "ytq cancel")
        subbuttons = buttons.build_menu(5)
        msg = f"Choose Audio{i} Quality:\n0 is best and 10 is worst\nTimeout: {get_readable_time(self._timeout - (time() - self._time))}"
        await edit_message(self._reply_to, msg, subbuttons)


import importlib.metadata
import shutil
import yt_dlp.version

_LOGGED_STARTUP_INFO = False


def log_ytdlp_startup_info():
    global _LOGGED_STARTUP_INFO
    if _LOGGED_STARTUP_INFO:
        return
    _LOGGED_STARTUP_INFO = True

    try:
        ytdlp_ver = yt_dlp.version.__version__
    except Exception:
        ytdlp_ver = "unknown"

    try:
        ejs_ver = importlib.metadata.version("yt-dlp-ejs")
    except Exception:
        ejs_ver = "not installed"

    detected_runtimes = {
        rt: path for rt in ("deno", "node", "bun") if (path := shutil.which(rt))
    }
    runtimes_str = (
        ", ".join(f"{k} ({v})" for k, v in detected_runtimes.items()) or "none"
    )

    LOGGER.info(
        f"yt-dlp version: {ytdlp_ver} | yt-dlp-ejs version: {ejs_ver} | JS runtimes: {runtimes_str}"
    )


def find_node_executable():
    for rt in ("deno", "node", "bun"):
        if path := shutil.which(rt):
            return path
    return None


def setup_js_runtimes(opts):
    log_ytdlp_startup_info()
    if "js_runtimes" not in opts:
        js_runtimes = {}
        for rt in ("deno", "node", "bun"):
            if path := shutil.which(rt):
                js_runtimes[rt] = {"path": path}
        if js_runtimes:
            opts["js_runtimes"] = js_runtimes


def extract_info(link, options):
    opts = dict(options)
    setup_js_runtimes(opts)

    with YoutubeDL(opts) as ydl:
        result = ydl.extract_info(link, download=False)
        if result is None:
            raise ValueError("Info result is None")
        return result


async def _mdisk(link, name):
    key = link.split("/")[-1]
    async with AsyncSession() as client:
        resp = await client.get(
            f"https://diskuploader.entertainvideo.com/v1/file/cdnurl?param={key}"
        )
    if resp.status_code == 200:
        resp_json = resp.json()
        link = resp_json["source"]
        if not name:
            name = resp_json["filename"]
    return name, link


class YtDlp(TaskListener):
    def __init__(
        self,
        client,
        message,
        is_leech=False,
        same_dir=None,
        bulk=None,
        multi_tag=None,
        options="",
        **kwargs,
    ):
        if same_dir is None:
            same_dir = {}
        if bulk is None:
            bulk = []
        self.message = message
        self.client = client
        self.multi_tag = multi_tag
        self.options = options
        self.same_dir = same_dir
        self.bulk = bulk
        super().__init__()
        self.is_ytdlp = True
        self.is_leech = is_leech

    async def new_event(self):
        text = self.message.text.split("\n")
        input_list = text[0].split(" ")
        qual = ""

        check_msg, check_button = await pre_task_check(self.message)
        if check_msg:
            await delete_links(self.message)
            await auto_delete_message(
                await send_message(self.message, check_msg, check_button)
            )
            return

        args = {
            "-doc": False,
            "-med": False,
            "-s": False,
            "-b": False,
            "-z": False,
            "-sv": False,
            "-ss": False,
            "-f": False,
            "-fd": False,
            "-fu": False,
            "-hl": False,
            "-bt": False,
            "-ut": False,
            "-i": 0,
            "-sp": 0,
            "link": "",
            "-m": "",
            "-meta": "",
            "-opt": "",
            "-n": "",
            "-up": "",
            "-ud": "",
            "-gc": "",
            "-rcf": "",
            "-t": "",
            "-ca": "",
            "-cv": "",
            "-ns": "",
            "-tl": "",
            "-ff": set(),
        }

        arg_parser(input_list[1:], args)

        if Config.DISABLE_FF_MODE and args.get("-ff"):
            await send_message(self.message, "FFmpeg commands are currently disabled.")
            return

        try:
            self.multi = int(args["-i"])
        except Exception:
            self.multi = 0

        try:
            if args["-ff"]:
                if isinstance(args["-ff"], set):
                    self.ffmpeg_cmds = args["-ff"]
                else:
                    value = literal_eval(args["-ff"])
                    if not isinstance(value, (dict, set, list, tuple)):
                        raise ValueError("ffmpeg_cmds must be a dict/set/list/tuple")
                    self.ffmpeg_cmds = value
        except Exception as e:
            self.ffmpeg_cmds = None
            LOGGER.error(e)

        opt = args["-opt"]

        self.select = args["-s"]
        self.name = args["-n"]
        self.up_dest = args["-up"]
        self.dump_dest = args["-ud"]
        self.category = args["-gc"]
        self.rc_flags = args["-rcf"]
        self.link = args["link"]
        self.compress = args["-z"]
        self.thumb = args["-t"]
        self.split_size = args["-sp"]
        self.sample_video = args["-sv"]
        self.screen_shots = args["-ss"]
        self.force_run = args["-f"]
        self.force_download = args["-fd"]
        self.force_upload = args["-fu"]
        self.convert_audio = args["-ca"]
        self.convert_video = args["-cv"]
        self.name_swap = args["-ns"]
        self.hybrid_leech = args["-hl"]
        self.thumbnail_layout = args["-tl"]
        self.as_doc = args["-doc"]
        self.as_med = args["-med"]
        self.folder_name = f"/{args['-m']}".rstrip("/") if len(args["-m"]) > 0 else ""
        self.bot_trans = args["-bt"]
        self.user_trans = args["-ut"]
        self.metadata_dict = self.default_metadata_dict.copy()
        self.audio_metadata_dict = self.audio_metadata_dict.copy()
        self.video_metadata_dict = self.video_metadata_dict.copy()
        self.subtitle_metadata_dict = self.subtitle_metadata_dict.copy()
        if meta := args["-meta"]:
            self.metadata_dict = self.metadata_processor.merge_dicts(
                self.default_metadata_dict, self.metadata_processor.parse_string(meta)
            )

        is_bulk = args["-b"]

        bulk_start = 0
        bulk_end = 0
        reply_to = None

        if not isinstance(is_bulk, bool):
            dargs = is_bulk.split(":")
            bulk_start = dargs[0] or None
            if len(dargs) == 2:
                bulk_end = dargs[1] or None
            is_bulk = True

        if not is_bulk:
            if self.multi > 0:
                if self.folder_name:
                    async with task_dict_lock:
                        if self.folder_name in self.same_dir:
                            self.same_dir[self.folder_name]["tasks"].add(self.mid)
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        elif self.same_dir:
                            self.same_dir[self.folder_name] = {
                                "total": self.multi,
                                "tasks": {self.mid},
                            }
                            for fd_name in self.same_dir:
                                if fd_name != self.folder_name:
                                    self.same_dir[fd_name]["total"] -= 1
                        else:
                            self.same_dir = {
                                self.folder_name: {
                                    "total": self.multi,
                                    "tasks": {self.mid},
                                }
                            }
                elif self.same_dir:
                    async with task_dict_lock:
                        for fd_name in self.same_dir:
                            self.same_dir[fd_name]["total"] -= 1
        else:
            await self.init_bulk(input_list, bulk_start, bulk_end, YtDlp)
            return

        if len(self.bulk) != 0:
            del self.bulk[0]

        path = f"{DOWNLOAD_DIR}{self.mid}{self.folder_name}"

        await self.get_tag(text)

        opt = opt or self.user_dict.get("yt_opt") or self.user_dict.get("YT_DLP_OPTIONS") or Config.YT_DLP_OPTIONS

        if not self.link and (reply_to := self.message.reply_to_message):
            if reply_to.text:
                self.link = reply_to.text.split("\n", 1)[0].strip()

        if not is_url(self.link):
            await send_message(
                self.message, COMMAND_USAGE["yt"][0], COMMAND_USAGE["yt"][1]
            )
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        if "mdisk.me" in self.link:
            self.name, self.link = await _mdisk(self.link, self.name)

        try:
            await self.before_start()
        except Exception as e:
            await send_message(self.message, e)
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return

        self._set_mode_engine()

        options = {}
        if ospath.exists(ospath.expanduser("~/.netrc")):
            options["usenetrc"] = True
        cookie_to_use, _ = get_cookie_file(self.user_dict, self.user_id)
        if cookie_to_use:
            options["cookiefile"] = cookie_to_use
            LOGGER.info(f"Using cookies file: {cookie_to_use} | User ID : {self.user_id}")

        if opt:
            yt_opt = opt.split("|") if isinstance(opt, str) else []
            for ytopt in yt_opt:
                if ":" not in ytopt:
                    continue
                key, value = map(str.strip, ytopt.split(":", 1))
                if key == "format":
                    if self.select:
                        qual = ""
                    elif value.startswith("ba/b-"):
                        qual = value
                        continue
                if value.startswith("^"):
                    if "." in value or value == "^inf":
                        value = float(value.split("^", 1)[1])
                    else:
                        value = int(value.split("^", 1)[1])
                elif value.lower() == "true":
                    value = True
                elif value.lower() == "false":
                    value = False
                elif value.startswith(("{", "[", "(")) and value.endswith(("}", "]", ")")):
                    try:
                        value = literal_eval(value)
                    except Exception:
                        pass
                options[key] = value

        options["playlist_items"] = "0"

        try:
            result = await sync_to_async(extract_info, self.link, options)
        except Exception as e:
            msg = str(e).replace("<", " ").replace(">", " ")
            await send_message(self.message, f"{self.tag} {msg}")
            await self.remove_from_same_dir()
            await delete_links(self.message)
            return
        finally:
            await self.run_multi(input_list, YtDlp)

        if not self.select and (not qual and "format" in options):
            qual = options["format"]

        if not qual:
            qual = await YtSelection(self).get_quality(result)
            if qual is None:
                await self.remove_from_same_dir()
                return

        LOGGER.info(f"Downloading with YT-DLP: {self.link}")
        playlist = "entries" in result

        ydl = YoutubeDLHelper(self)
        await ydl.add_download(self.link, path, self.name, qual, playlist, opt)
        await delete_links(self.message)


async def ytdl(client, message):
    if Config.DISABLE_YTDLP:
        await message.reply("YT-DLP downloads are currently disabled by the Bot Owner.")
        return
    bot_loop.create_task(YtDlp(client, message).new_event())


async def ytdl_leech(client, message):
    if Config.DISABLE_YTDLP:
        await message.reply("YT-DLP downloads are currently disabled by the Bot Owner.")
        return
    bot_loop.create_task(YtDlp(client, message, is_leech=True).new_event())
