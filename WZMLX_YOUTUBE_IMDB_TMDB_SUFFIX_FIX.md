# HTR-X WZML-X YouTube / IMDb / TMDb patch

- Keeps the YouTube extraction probe and configured-cookie behavior from the existing YouTube fix.
- Adds `/tmdb <title>` search with up to five movie/TV results, year, rating, overview, and TMDb links.
- Keeps `/imdb <title-or-tt-id>` and its callback flow from the existing IMDb plugin.
- Both plugin commands follow `CMD_SUFFIX` because plugin command filters are built through `PluginManager._suffixed()`. For example, with `CMD_SUFFIX = "1"`, use `/imdb1` and `/tmdb1`.
- `/tmdb` requires `TMDB_ACCESS_TOKEN` in `config.py`; use a TMDb API key (v3) or Read Access Token (v4).

YouTube still requires valid cookies for videos that require sign-in. No code can make expired cookies valid; refresh the Netscape-format cookie file if logs say the cookies are invalid.
