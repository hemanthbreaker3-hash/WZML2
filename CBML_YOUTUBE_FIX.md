# CBML-inspired YouTube fix for HTR-X

## What changed
- Uses the CBML downloader's Deno/Node runtime detection and yt-dlp JS options already present in this project.
- Runs the YouTube stream probe in the actual metadata-extraction path.
- If a user has configured a cookie file, the probe always passes that file and does not silently retry anonymously.
- If no cookie file is configured, uses unauthenticated client fallbacks.
- Keeps diagnostics for invalid/rotated cookies and missing downloadable formats.

## Important
The bot cannot repair expired or rotated YouTube login cookies. Re-export a fresh Netscape-format `cookies.txt` while signed in, then configure/upload it in the bot. Do not share cookie files.

## Apply
Back up your current project, extract this ZIP, and deploy from the extracted project directory. Test a public video and a video accessible to the configured account.
