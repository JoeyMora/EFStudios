# Artist Email Finder

A command-line tool that finds **public contact emails** (booking, management, business) for music artists. It writes a CSV you can work from for outreach.

It can:

- **Look up artists you name.** Pass names, or a CSV or text file. You can add their links, such as a website, Linktree, `@instagram` or YouTube.
- **Discover artists for you.** Search by genre on Spotify (with follower filters to target independent artists), or by genre and city/region on MusicBrainz. Then it looks up their emails.

## Where it looks

| Source | What it reads | Key needed |
|---|---|---|
| Artist links (MusicBrainz) | Website, socials and YouTube links listed for the artist | none |
| Websites | Home page, then contact, booking, about, press and EPK pages | none |
| Link-in-bio pages | Linktree, Beacons, lnk.bio, hoo.be, Stan, Carrd, etc., plus the sites they link to | none |
| Social bios | Public profile pages whose `robots.txt` allows it (SoundCloud, Bandcamp, ...) | none |
| YouTube | Channel description and the last 5 video descriptions | `YOUTUBE_API_KEY` |
| Web search | `"Artist" booking/management email` results | `BRAVE_API_KEY` or `SERPAPI_API_KEY` |
| Discovery | Spotify genre search | `SPOTIFY_CLIENT_ID` + `SPOTIFY_CLIENT_SECRET` |

It catches `mailto:` links, plain-text emails and obfuscated ones such as `booking [at] artist [dot] com`. It drops junk: platform addresses, `noreply@`, image filenames and tracking IDs.

**Instagram and TikTok** block automated access (login walls and `robots.txt`), so the tool doesn't read those bios directly. It finds the same info through the artist's Linktree, website, YouTube and web search, which is usually where the bio email is copied from anyway.

## Setup

```bash
cd artist-email-finder
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env    # then paste in whichever API keys you have
```

All keys are optional. Without any keys, it still checks MusicBrainz links and any links you give it.

- **YouTube:** in Google Cloud Console, enable "YouTube Data API v3" and create an API key. The free tier is 10,000 units/day. A channel search costs 100, so that's about 90 artists/day. Use `--no-youtube-search` and pass YouTube links yourself to use almost no quota.
- **Brave Search:** https://brave.com/search/api/ (has a free tier). Or use **SerpAPI** for Google results.
- **Spotify:** create an app at https://developer.spotify.com/dashboard and copy its client ID and secret.

## Usage

```bash
# A few artists by name
artist-email-finder lookup "Artist One" "Artist Two"

# Give links to help it (comma-separated after the name)
artist-email-finder lookup "Artist One, https://linktr.ee/artistone, @artistone"

# From a file -> CSV
artist-email-finder lookup -i examples/artists.csv -o emails.csv

# Discover 50 independent drill artists on Spotify, then find emails
artist-email-finder discover --genre drill --max-followers 50000 --limit 50 -o leads.csv

# Discover by genre + city (MusicBrainz)
artist-email-finder discover --genre "hip hop" --area "Atlanta" --limit 30 -o atl.csv

# Just list discovered artists, no email search
artist-email-finder discover --genre "r&b" --limit 100 --no-emails -o rnb_artists.csv

# Long run got interrupted? Continue where it left off
artist-email-finder lookup -i big_list.csv -o emails.csv --resume
```

Without installing, run it as `PYTHONPATH=src python3 -m artist_email_finder ...`.

### Input files

- **CSV:** needs a `name` (or `artist`) column. Every other column that looks like a link or `@handle` is used as a link. Other columns, like notes, are ignored.
- **Text:** one artist per line, such as `Name`, `Name, link, link`, or just a link like `https://linktr.ee/someone`.

### Output columns

`artist, email, confidence, score, sources, found_on, context, genres, followers, location, spotify_url, discovered_via, pages_checked`

There is one row per email found. An artist with no email still gets one row with the email column empty, so you can see who was missed.

**Confidence** (`high` ≥ 80, `medium` ≥ 55, `low` below that) goes up when:
- the email is on the artist's own pages (bio, link page, site, YouTube) rather than in a search snippet,
- the email's domain matches the artist's website,
- it's a role address like `booking@`, `mgmt@` or `info@`,
- it contains the artist's name,
- it was seen in more than one source.

Use `--min-confidence medium` to keep only the stronger matches.

## Being a good citizen

- It obeys `robots.txt`, waits 1s between requests to the same site (`--delay`), and never logs in or gets around blocks.
- These are business contacts that artists published. When you email them, follow the anti-spam rules: CAN-SPAM in the US, or GDPR/PECR if you contact people in the UK or EU. Use your real name and business address, write a relevant personal pitch, and honour opt-outs right away.
- Check `found_on` before you send. A `low` result from web search may belong to a blog or venue, not the artist.

## Development

```bash
pip install -e '.[dev]'
pytest
```

The code lives in `src/artist_email_finder/`:

- `cli.py`: commands, input and output
- `finder.py`: runs the sources for each artist
- `extract.py`: finds emails in text and HTML, and filters out junk
- `scoring.py`: confidence scoring
- `sources/`: one module per source (`pages`, `musicbrainz`, `spotify`, `youtube`, `websearch`)
