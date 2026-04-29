# redsweather
A Bluesky bot that posts the weather forecast for Cincinnati Reds baseball games.

# about
This is a small project I originally did to help learn Python. The code is awful and should be used by nobody, ever. (Note, master coder and college professor Alex Kuhl (https://github.com/alexkuhl) cleaned it up on the original Twitter version! Thanks Alex!) It was an experiment to help figure out how to tie several different open source Python libraries together, to make something useful. Well, useful to Reds fans. The original posted to Twitter; this version posts to Bluesky and pulls the schedule live instead of reading a CSV, and was written wholly by Claude's Opus 4.7.

# install
This is written for Python 3.9 and greater (we use `zoneinfo` from the standard library). You need a few libraries to make this work — they're listed in `requirements.txt` and will be installed by the setup script:
- MLB-StatsAPI, from https://github.com/toddrob99/MLB-StatsAPI — pulls today's schedule, opponent, venue, and first-pitch time directly from MLB.
- atproto, from https://github.com/MarshalX/atproto — Python SDK for Bluesky / AT Protocol.
- requests + python-dotenv — HTTP for Open-Meteo and `.env` loading.

No more CSV — `bsky_weather.py` queries the MLB Stats API for the Reds (team id 113) every time it runs, so the schedule stays correct through doubleheaders, postponements, and rescheduled games.

You will need a Bluesky **app password** (not your account password). Generate one at https://bsky.app/settings/app-passwords and drop your handle and the password into a `.env` file (see `.env.example`). App passwords can post on your behalf but cannot read DMs or change account settings — see https://blueskyweb.zendesk.com/hc/en-us/articles/16443448861709-What-are-App-Passwords. Weather comes from Open-Meteo (https://open-meteo.com/), which needs no API key.

To install on a Raspberry Pi (Debian) or a VPS (Ubuntu), clone the repo and run:

```
./setup.sh
```

That creates a virtualenv, installs the dependencies, copies `.env.example` to `.env` (mode 600), and adds a daily cron entry that runs the script at 09:00 local time. Override the time with `CRON_HOUR=8 CRON_MINUTE=30 ./setup.sh`.

# use
Run once a day for best results — `setup.sh` does this for you via cron. Before turning it loose, smoke-test it with:

```
DRY_RUN=1 .venv/bin/python bsky_weather.py
```

That prints the post text without publishing. On no-game days the script logs the reason and exits 0, so cron stays quiet. Bluesky doesn't have Twitter's duplicate-status rule, so re-running the script won't error out — it'll just post the same forecast again, which you probably don't want, hence the once-a-day cron.
