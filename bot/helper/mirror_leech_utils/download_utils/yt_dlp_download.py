from logging import getLogger
from os import path as ospath, listdir
from re import search as re_search
from secrets import token_hex
from yt_dlp import YoutubeDL, DownloadError

from .... import task_dict_lock, task_dict
from ...ext_utils.bot_utils import sync_to_async, async_to_sync
from ...ext_utils.task_manager import (
    check_running_tasks,
    stop_duplicate_check,
    limit_checker,
)
from ...mirror_leech_utils.status_utils.queue_status import QueueStatus
from ...telegram_helper.message_utils import send_status_message
from ..status_utils.yt_dlp_status import YtDlpStatus

LOGGER = getLogger(__name__)


def get_cookie_file(user_dict=None, user_id=0):
    user_dict = user_dict or {}
    if not user_dict.get("USE_DEFAULT_COOKIE", False):
        usr_cookie = user_dict.get("USER_COOKIE_FILE", "")
        if usr_cookie and ospath.exists(usr_cookie):
            return usr_cookie, None
        if user_id:
            user_cookie_path = f"cookies/{user_id}/cookies.txt"
            if ospath.exists(user_cookie_path):
                return user_cookie_path, None
    if ospath.exists("cookies.txt"):
        return "cookies.txt", None
    return None, None


class MyLogger:
    def __init__(self, obj, listener):
        self.obj = obj
        self._listener = listener

    def debug(self, msg):
        # Hack to fix changing extension
        if not self.obj.is_playlist:
            if match := re_search(
                r".Merger..Merging formats into..(.*?).$", msg
            ) or re_search(r".ExtractAudio..Destination..(.*?)$", msg):
                LOGGER.info(msg)
                newname = match.group(1)
                newname = newname.rsplit("/", 1)[-1]
                self.obj.name = newname
                self._listener.name = newname

    @staticmethod
    def warning(msg):
        LOGGER.warning(msg)

    @staticmethod
    def error(msg):
        if msg != "ERROR: Cancelling...":
            LOGGER.error(msg)


class YoutubeDLHelper:
    def __init__(self, listener):
        self._last_downloaded = 0
        self._size = 0
        self._progress = 0
        self._downloaded_bytes = 0
        self._download_speed = 0
        self._eta = "-"
        self._listener = listener
        self._gid = ""
        self._is_cancelled = False
        self._downloading = False
        self._ext = ""
        self.name = ""
        self.is_playlist = False
        self.playlist_count = 0
        self.opts = {
            "progress_hooks": [self._on_download_progress],
            "logger": MyLogger(self, self._listener),
            "allow_multiple_video_streams": True,
            "allow_multiple_audio_streams": True,
            "noprogress": True,
            "allow_playlist_files": True,
            "overwrites": True,
            "writethumbnail": True,
            "trim_file_name": 220,
            "fragment_retries": 10,
            "retries": 10,
            "retry_sleep_functions": {
                "http": lambda n: 3,
                "fragment": lambda n: 3,
                "file_access": lambda n: 3,
                "extractor": lambda n: 3,
            },
        }
        if "extractor_args" not in self.opts:
            self.opts["extractor_args"] = {}
        self.opts["extractor_args"]["youtube"] = {
            "player_client": ["default", "web_embedded", "web_safari", "-tv_downgraded"]
        }
        if ospath.exists(ospath.expanduser("~/.netrc")):
            self.opts["usenetrc"] = True

        cookie_to_use, _ = get_cookie_file(
            getattr(listener, "user_dict", {}), getattr(listener, "user_id", 0)
        )
        if cookie_to_use:
            self.opts["cookiefile"] = cookie_to_use

        from ....modules.ytdlp import setup_js_runtimes
        setup_js_runtimes(self.opts)

    @property
    def download_speed(self):
        return self._download_speed

    @property
    def downloaded_bytes(self):
        return self._downloaded_bytes

    @property
    def size(self):
        return self._size

    @property
    def progress(self):
        return self._progress

    @property
    def eta(self):
        return self._eta

    def _on_download_progress(self, d):
        self._downloading = True
        if self._is_cancelled or self._listener.is_cancelled:
            raise ValueError("Cancelling...")
        if d["status"] == "finished":
            if self.is_playlist:
                self._last_downloaded = 0
        elif d["status"] == "downloading":
            self._download_speed = d.get("speed") or 0
            if self.is_playlist:
                downloadedBytes = d.get("downloaded_bytes") or 0
                chunk_size = downloadedBytes - self._last_downloaded
                self._last_downloaded = downloadedBytes
                self._downloaded_bytes += chunk_size
            else:
                if d.get("total_bytes"):
                    self._size = d["total_bytes"]
                    self._listener.size = d["total_bytes"]
                elif d.get("total_bytes_estimate"):
                    self._size = d["total_bytes_estimate"]
                    self._listener.size = d["total_bytes_estimate"]
                self._downloaded_bytes = d.get("downloaded_bytes") or 0
                self._eta = d.get("eta", "-") or "-"
            try:
                self._progress = (self._downloaded_bytes / self._size) * 100
            except Exception:
                pass

    async def _on_download_start(self, from_queue=False):
        async with task_dict_lock:
            task_dict[self._listener.mid] = YtDlpStatus(self._listener, self, self._gid)
        if not from_queue:
            await self._listener.on_download_start()
            if self._listener.multi <= 1 and not self._listener.is_rss:
                await send_status_message(self._listener.message)

    def _on_download_error(self, error):
        self._is_cancelled = True
        self._listener.is_cancelled = True
        async_to_sync(self._listener.on_download_error, error)

    def extract_meta_data(self, link, name):
        opts = dict(self.opts)

        if link.startswith(("rtmp", "mms", "rstp", "rtmps")):
            opts["external_downloader"] = "ffmpeg"

        result = None
        try:
            with YoutubeDL(opts) as ydl:
                result = ydl.extract_info(link, download=False)
                if result is None:
                    raise ValueError("Info result is None")
        except Exception as e:
            return self._on_download_error(str(e))
        if self.is_playlist:
            self.playlist_count = result.get("playlist_count", 0)
        if "entries" in result:
            self.name = name
            for entry in result["entries"]:
                if not entry:
                    continue
                elif "filesize_approx" in entry:
                    self._size += entry.get("filesize_approx") or 0
                elif "filesize" in entry:
                    self._size += entry.get("filesize") or 0
                if not self.name:
                    outtmpl_ = "%(series,playlist_title,channel)s%(season_number& |)s%(season_number&S|)s%(season_number|)02d.%(ext)s"
                    self.name, ext = ospath.splitext(
                        ydl.prepare_filename(entry, outtmpl=outtmpl_)
                    )
                    if not self._ext:
                        self._ext = ext
            self._listener.name = self.name
            self._listener.size = self._size
        else:
            outtmpl_ = "%(title,fulltitle,alt_title)s%(season_number& |)s%(season_number&S|)s%(season_number|)02d%(episode_number&E|)s%(episode_number|)02d%(height& |)s%(height|)s%(height&p|)s%(fps|)s%(fps&fps|)s%(tbr& |)s%(tbr|)d.%(ext)s"
            realName = ydl.prepare_filename(result, outtmpl=outtmpl_)
            ext = ospath.splitext(realName)[-1]
            self.name = f"{name}{ext}" if name else realName
            if not self._ext:
                self._ext = ext
            if result.get("filesize"):
                self._size = result["filesize"]
            elif result.get("filesize_approx"):
                self._size = result["filesize_approx"]
            self._listener.name = self.name
            self._listener.size = self._size


    def _download(self, link, path):
        opts = dict(self.opts)

        try:
            try:
                with YoutubeDL(opts) as ydl:
                    ydl.download([link])
            except DownloadError as e:
                if not self._is_cancelled and not self._listener.is_cancelled:
                    self._on_download_error(str(e))
                return
            if self.is_playlist and (
                not ospath.exists(path) or len(listdir(path)) == 0
            ):
                self._on_download_error(
                    "No video available to download from this playlist. Check logs for more details"
                )
                return
            if self._is_cancelled or self._listener.is_cancelled:
                raise ValueError
            async_to_sync(self._listener.on_download_complete)
        except ValueError:
            self._on_download_error("Download Stopped by User!")

    async def add_download(self, link, path, name, qual, playlist, options):
        if playlist:
            self.opts["ignoreerrors"] = True
            self.is_playlist = True

        self._gid = token_hex(5)
        await self._on_download_start()

        self.opts["postprocessors"] = [
            {
                "add_chapters": True,
                "add_infojson": "if_exists",
                "add_metadata": True,
                "key": "FFmpegMetadata",
            }
        ]

        if qual.startswith("ba/b-"):
            audio_info = qual.split("-")
            qual = audio_info[0]
            audio_format = audio_info[1]
            rate = audio_info[2]
            self.opts["postprocessors"].append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": rate,
                }
            )
            if audio_format == "vorbis":
                self._ext = ".ogg"
            elif audio_format == "alac":
                self._ext = ".m4a"
            else:
                self._ext = f".{audio_format}"

        self.opts["format"] = qual

        if options:
            self._set_options(options)

        await sync_to_async(self.extract_meta_data, link, name)
        if self._is_cancelled or self._listener.is_cancelled:
            return

        base_name, ext = ospath.splitext(self.name)
        trim_name = self.name if self.is_playlist else base_name
        if len(trim_name.encode()) > 200:
            self.name = (
                self.name[:200] if self.is_playlist else f"{base_name[:200]}{ext}"
            )
            base_name = ospath.splitext(self.name)[0]
            self._listener.name = self.name

        start_path = f"{path}/yt-dlp-thumb"
        if self.is_playlist:
            self.opts["outtmpl"] = {
                "default": f"{path}/{self.name}/%(title,fulltitle,alt_title)s%(season_number& |)s%(season_number&S|)s%(season_number|)02d%(episode_number&E|)s%(episode_number|)02d%(height& |)s%(height|)s%(height&p|)s%(fps|)s%(fps&fps|)s%(tbr& |)s%(tbr|)d.%(ext)s",
                "thumbnail": f"{start_path}/%(title,fulltitle,alt_title)s%(season_number& |)s%(season_number&S|)s%(season_number|)02d%(episode_number&E|)s%(episode_number|)02d%(height& |)s%(height|)s%(height&p|)s%(fps|)s%(fps&fps|)s%(tbr& |)s%(tbr|)d.%(ext)s",
            }
        elif any(
            key in (options if isinstance(options, dict) else {})
            for key in [
                "writedescription",
                "writeinfojson",
                "writeannotations",
                "writedesktoplink",
                "writewebloclink",
                "writeurllink",
                "writesubtitles",
                "writeautomaticsub",
            ]
        ):
            self.opts["outtmpl"] = {
                "default": f"{path}/{base_name}/{self.name}",
                "thumbnail": f"{start_path}/{base_name}.%(ext)s",
            }
        else:
            self.opts["outtmpl"] = {
                "default": f"{path}/{self.name}",
                "thumbnail": f"{start_path}/{base_name}.%(ext)s",
            }

        if qual.startswith("ba/b"):
            self.name = f"{base_name}{self._ext}"
            self._listener.name = self.name

        is_leech = getattr(self._listener, "is_leech", False)
        if is_leech:
            self.opts["postprocessors"].append(
                {
                    "format": "jpg",
                    "key": "FFmpegThumbnailsConvertor",
                    "when": "before_dl",
                }
            )
        if self._ext in [
            ".mp3",
            ".mkv",
            ".mka",
            ".ogg",
            ".opus",
            ".flac",
            ".m4a",
            ".mp4",
            ".mov",
            "m4v",
        ]:
            self.opts["postprocessors"].append(
                {
                    "already_have_thumbnail": is_leech,
                    "key": "EmbedThumbnail",
                }
            )
        elif not is_leech:
            self.opts["writethumbnail"] = False

        msg, button = await stop_duplicate_check(self._listener)
        if msg:
            await self._listener.on_download_error(msg, button)
            return

        if limit_exceeded := await limit_checker(self._listener, self.playlist_count):
            await self._listener.on_download_error(limit_exceeded, is_limit=True)
            return

        add_to_queue, event = await check_running_tasks(self._listener)
        if add_to_queue:
            LOGGER.info(f"Added to Queue/Download: {self.name}")
            async with task_dict_lock:
                task_dict[self._listener.mid] = QueueStatus(
                    self._listener, self._gid, "dl"
                )
            await event.wait()
            if self._listener.is_cancelled:
                return
            LOGGER.info(f"Start Queued Download from YT_DLP: {self.name}")
            await self._on_download_start(True)

        if not add_to_queue:
            LOGGER.info(f"Download with YT_DLP: {self.name}")

        await sync_to_async(self._download, link, path)

    async def cancel_task(self):
        self._is_cancelled = True
        self._listener.is_cancelled = True
        LOGGER.info(f"Cancelling Download: {self.name}")
        if not self._downloading:
            await self._listener.on_download_error("Download Cancelled by User!")

    def _set_options(self, options):
        if isinstance(options, str):
            options_list = options.split("|")
            for opt in options_list:
                if ":" not in opt:
                    continue
                key, value = map(str.strip, opt.split(":", 1))
                if key == "format" and value.startswith("ba/b-"):
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
                        from ast import literal_eval
                        value = literal_eval(value)
                    except Exception:
                        pass

                if key == "postprocessors":
                    if isinstance(value, list):
                        self.opts[key].extend(tuple(value))
                    elif isinstance(value, dict):
                        self.opts[key].append(value)
                else:
                    self.opts[key] = value
        elif isinstance(options, dict):
            for key, value in options.items():
                if key == "postprocessors":
                    if isinstance(value, list):
                        self.opts[key].extend(tuple(value))
                    elif isinstance(value, dict):
                        self.opts[key].append(value)
                else:
                    self.opts[key] = value
