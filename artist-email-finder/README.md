# EF Studios Artist Finder

Finds artists around your size to reach out to, along with their public booking or management emails. You work through them in a local web app.

- **Find artists:** by genre (Spotify, Last.fm, MusicBrainz), by city (MusicBrainz), or "similar to" an artist you name (Last.fm). You can also paste a list of artists you already know.
- **"Around my level":** enter your own follower counts. Every artist gets a size compared with yours (for example, `1.8×`), and discovery can skip anyone far bigger or smaller.
- **Find emails:** from their website, Linktree-style pages, Instagram and TikTok (see below), YouTube descriptions and web search. Each email gets a confidence rating.
- **Work through leads:** filter and sort, star artists, set a status (New → Shortlist → Contacted → Replied / Not a fit), keep notes, copy an email, open a pre-filled email from your template, and export to CSV.

## Quick start

```bash
cd artist-email-finder
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env     # add the keys you have (all optional; see below)
artist-email-finder serve
```

This opens http://127.0.0.1:5005. Your leads are saved in `leads.db` in the folder you run it from.

1. **My level & settings:** enter your Instagram, Spotify and TikTok numbers, or fetch them automatically. Then edit your email template.
2. **Find artists:** pick a source and genre, keep "Only artists around my level" ticked, and press Start. Progress shows in the top bar.
3. **Leads:** go through the list, closest to your size first.

## Instagram & TikTok

Instagram and TikTok block automated access to profile pages, and getting around that would risk a ban on your accounts. The tool uses three approaches that stay within the rules instead.

1. **Instagram Business Discovery (official Meta API).** Your Instagram business or creator account can look up any other business or creator account's bio, website link and follower count. Most artists who take bookings use one of those account types. The bio's website, usually a Linktree, is then checked for an email. The API doesn't expose the in-app "Email" button, and it doesn't work for personal accounts.
2. **Search-engine snippets.** Google and Brave index public Instagram and TikTok profiles. The tool reads the snippet ("12K Followers … bookings: …") to get follower counts, bio emails and handles. This needs a Brave or SerpAPI key.
3. **Capture bookmarklet (you browse, it reads).** Drag **Capture artist** from the Settings page to your bookmarks bar. While you're on an artist's Instagram or TikTok profile, click it. Their bio email, follower count and size compared with yours are saved to your leads. Any Linktree or website in their bio is then checked automatically. It only reads the page you're already looking at. It also works on Linktree pages and websites.

You can also paste a bio into an artist's page (**Paste a bio**), or add an email you saw in the Instagram app's Email button by hand.

### Setting up Instagram Business Discovery

You need an Instagram professional (business or creator) account linked to a Facebook Page. Your ad account setup already has this.

1. At https://developers.facebook.com, create an app (type "Business") and add the **Instagram Graph API** product. In the current dashboard this may be called "Instagram API with Facebook Login".
2. In the **Graph API Explorer**, select your app. Request the permissions `instagram_basic`, `pages_show_list`, `pages_read_engagement` and `business_management`, then generate a user token.
3. Exchange it for a long-lived token, which lasts about 60 days. Use the Access Token Debugger's "Extend Access Token" button. Put the result in `META_ACCESS_TOKEN`.
4. Find your Instagram user ID. In the Explorer, run `me/accounts?fields=instagram_business_account{id,username}`. Put that `id` in `IG_USER_ID`.

The limit is about 200 lookups per hour. When the token expires, lookups quietly stop returning Instagram data, so renew it every couple of months.

## API keys (all optional)

| Key | Unlocks | Where |
|---|---|---|
| `META_ACCESS_TOKEN`, `IG_USER_ID` | Instagram bio, website and followers | see above |
| `BRAVE_API_KEY` or `SERPAPI_API_KEY` | Web search for emails, plus Instagram/TikTok profile snippets | brave.com/search/api, serpapi.com |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | Discover by genre, Spotify followers | developer.spotify.com/dashboard |
| `LASTFM_API_KEY` | Discover by tag or "similar to", listener counts | last.fm/api/account/create |
| `YOUTUBE_API_KEY` | Emails in YouTube channel and video descriptions, subscriber counts | Google Cloud Console → YouTube Data API v3 |

With no keys, it still uses MusicBrainz, artist websites, link pages, the bookmarklet and pasted bios.

**Best value for finding artists your size:** Spotify (genre discovery plus follower counts) and Last.fm "similar to". For "similar to", seed it with an artist you've worked with or one at your level, which tends to surface peers.

## How "around my level" works

For each platform where both you and the artist have a number, the tool divides theirs by yours. It then averages those ratios across platforms (a geometric mean). For example, if they have 2× your Instagram followers and 1× your Spotify followers, the result is about 1.4×. By default, 0.25× to 4× counts as "your level"; you can change the range in Settings. Artists with no comparable numbers show as `?`. The default Leads view includes them, so add a handle and re-check, or capture them, to get their size.

When "Only artists around my level" is on, discovery first gets a cheap size number (Spotify or Last.fm) and skips far-off artists before spending a full email lookup on them. Skipped artists are still saved, so they're never checked twice. They appear under the "Bigger" and "Smaller" filters.

## Command line

Everything is also available without the web app:

```bash
artist-email-finder lookup "Artist One" "Artist Two, @artisttwo, linktr.ee/artisttwo" -o emails.csv
artist-email-finder lookup -i examples/artists.csv -o emails.csv --resume
artist-email-finder discover --genre drill --max-followers 50000 --limit 50 -o leads.csv
artist-email-finder discover --genre "hip hop" --area Atlanta --source musicbrainz -o atl.csv
```

## Being a good citizen

- The tool obeys `robots.txt`, waits between requests to the same site, uses official APIs where they exist, and never logs in or gets around blocks.
- These are business contacts that artists published. Cold email still has rules: CAN-SPAM in the US, or GDPR/PECR for the UK and EU. Use your real name and business details, send a relevant personal pitch, and honour opt-outs right away.
- Check where an email came from before sending. A `low` email found only in web search may belong to a blog or venue.

## Development

```bash
pip install -e '.[dev]'
pytest
```

The code lives in `src/artist_email_finder/`:

- `web.py` and `templates/`: the web app
- `store.py`: the SQLite database
- `jobs.py`: background discovery and lookups
- `finder.py`: runs every source for one artist
- `level.py`: size comparison
- `extract.py` and `scoring.py`: finding emails and rating them
- `sources/`: Instagram, web search, Spotify, Last.fm, MusicBrainz, YouTube, and the page crawler
