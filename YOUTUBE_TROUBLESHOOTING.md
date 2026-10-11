# YouTube / yt-dlp recovery (HTR-X)

The log provided shows `LOGIN_REQUIRED`, `No video formats found`, and explicitly says the YouTube cookies are no longer valid. This is not a missing FFmpeg or Deno installation error. Deno is detected and yt-dlp's EJS challenge provider is loaded, but YouTube rejects the current session.

1. Export a fresh `cookies.txt` from a browser where YouTube is signed in. Use a private/incognito window for the export if possible, and do not share the file publicly.
2. Upload the new file through the bot's cookie settings. Confirm the bot reports the newly normalized file path and cookie count.
3. On the VPS, from the project directory, run:
   ```bash
   ./venv/bin/python -m pip install -U "yt-dlp[default]" curl-cffi
   ./venv/bin/yt-dlp --version
   /usr/local/bin/deno --version
   ```
4. Restart and inspect logs:
   ```bash
   systemctl restart cantarellabots_bot.service
   journalctl -u cantarellabots_bot.service -n 150 --no-pager
   ```
5. If fresh cookies still produce `LOGIN_REQUIRED`, test another authorized network/IP or wait before retrying. YouTube can reject datacenter IPs and may require a PO token for some player clients. Do not repeatedly retry the same invalid cookie file.

`KeyboardInterrupt` during the systemd stop sequence usually indicates Python received the configured SIGINT. The important symptom is that the process did not exit within systemd's stop timeout; this deploy script sets a bounded `TimeoutStopSec`, but if shutdown still hangs inspect child processes started by `start.sh` and ensure it uses `exec` for the Python process.


## What `LOGIN_REQUIRED` means

If the log says `The provided YouTube account cookies are no longer valid` or the player status is `LOGIN_REQUIRED`, the current cookie session has been rejected by YouTube. Deno and `yt-dlp-ejs` can solve supported JavaScript challenges, but they cannot make expired cookies valid. Export a new Netscape-format `cookies.txt` from a browser signed in to YouTube, upload it privately via the bot, and retry once. Never post or send your cookies to anyone.

The downloader now tries a web-client fallback, a `web_embedded` fallback, and `android_vr` without cookies, and reports invalid-cookie/login failures explicitly. If every client returns no downloadable formats with fresh cookies, the cause may be video restrictions, a YouTube PO-token/SABR requirement, or the VPS network/IP.

## Systemd stop timeout

The deployment unit now uses `SIGTERM`, `KillMode=mixed`, and `TimeoutStopSec=30` rather than sending `SIGINT` to the Python event loop. `start.sh` uses `exec`, so systemd manages the Python process directly. After redeploy, inspect the service with `systemctl cat cantarellabots_bot.service` and `journalctl -u cantarellabots_bot.service -n 100 --no-pager`.
