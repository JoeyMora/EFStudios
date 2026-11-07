# EF Studios Artist Finder

An app for finding artists to work with, at your level, with their booking or management emails.

- **Discover: just ask.** Type what you want, for example "find me a female punk rock band", "an Asian artist that sings rock and roll", "R&B singers in Atlanta at my level", "indie bands like Alvvays", or "trending drill rappers". The app works out the genres, gender, solo or band, region, city, level and "like" artist you mean, searches the music databases, keeps only artists that fit, and finds their emails. Results stream in as photo tiles, each showing why it matched.
- **Galaxy.** Every artist is a star and you're the sun at the center.
  - The closer a star, the closer that artist is to your level.
  - Bigger stars are bigger artists.
  - Brighter, faster-twinkling stars have more **momentum**: how far their reels and TikToks reach beyond their own followers, and how fast their engagement grows between checks.
  - Genres form constellations. Hover a star for details and click it to open the artist.
- **For you.** Picks based on the artists you star, shortlist, contact or reply to, and steered away from the ones you mark "not a fit". Swipe through them like a deck: save (→) or pass (←). Every swipe teaches it your taste.
- **Every artist has a genre.** Genres come from MusicBrainz, then Spotify, then Last.fm listener tags. If none of those know the artist, genre words in their own bio and link pages are used. You can always edit the genres.
- **Around your level, based on engagement.** Instagram reel views and likes, TikTok views and YouTube views are compared with your own numbers. Follower counts are shown, but never used for level.
- **Works like an app:**
  - Tap any artist for a slide-in panel with photo, level, "you vs them" bars, emails, notes and a one-tap pre-filled email.
  - Drag artists through your **Pipeline**: New → Shortlist → Contacted → Replied.
  - A level map shows where everyone sits.

### How prompts are understood

No AI is involved. A rule-based parser reads your prompt. It knows:
- about 120 genre names and spellings ("rock n roll", "rnb", "hiphop")
- "female", "band", "duo" and "solo"
- regions, nationalities and cities ("in Atlanta", "Chicago-based")
- "like [artist]" and "similar to [artist]"
- "trending" or "blowing up"
- "bigger" or "smaller"
- a number of results

The tags under your search show exactly how it read the prompt.

Identity filters only use what artists publish about themselves:
- **Gender and solo vs band:** the gender and type listed on MusicBrainz.
- **Region:** the country of origin and area listed on MusicBrainz, plus self-identification tags like "asian american".
- **Nothing is inferred from names or photos.** Artists whose details aren't published are kept and ranked lower, rather than guessed about.

### Momentum

Momentum is a score from 0 to 100 that combines two signals:

- **Reach:** typical views per reel or TikTok divided by followers. Content seen by 3× their followers is being pushed by the algorithm.
- **Growth:** how fast their engagement changes between checks. Re-check artists every week or two and the growth half kicks in.

It drives star brightness in the Galaxy, the "▲ Rising" filter, the "trending" prompt word, and gives a boost to For You picks.

## Quick start

```bash
cd artist-email-finder
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env     # add the keys you have (all optional; see below)
artist-email-finder serve
```

This opens http://127.0.0.1:5005. Your leads are saved in `leads.db` in the folder you run it from.

1. **Settings:** set your own engagement (capture your Reels tab and press **This is me**), and edit your email template.
2. **Discover:** describe who you're looking for.
3. **Artists / Pipeline:** star the ones you like and move them along as you reach out.
4. **For you:** after a few stars or shortlists, swipe through picks made for you.
5. **Galaxy:** see everyone at a glance; the bright stars near the center are your best bets.

## Instagram & TikTok

Instagram and TikTok block automated access to profile pages, and getting around that would risk a ban on your accounts. The tool uses three approaches that stay within the rules instead.

1. **Instagram Business Discovery (official Meta API).** Your Instagram business or creator account can look up any other business or creator account's bio, website link, follower count and like/comment counts on recent posts. Most artists who take bookings use one of those account types. The bio's website, usually a Linktree, is then checked for an email. The API doesn't expose the in-app "Email" button, and it doesn't work for personal accounts.
2. **Search-engine snippets.** Google and Brave index public Instagram and TikTok profiles. The tool reads the snippet ("12K Followers … bookings: …") to get follower counts, bio emails and handles. This needs a Brave or SerpAPI key.
3. **Capture bookmarklet (you browse, it reads).** Drag **Capture artist** from the Settings page to your bookmarks bar. While you're on an artist's Instagram **Reels tab** or TikTok profile, click it. Their bio email, follower count, photo and typical views per reel or video are saved to your leads. Any Linktree or website in their bio is then checked automatically. It only reads the page you're already looking at. It also works on Linktree pages and websites.

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
| `META_ACCESS_TOKEN`, `IG_USER_ID` | Instagram reel likes + comments, bio, website, followers, profile photo | see above |
| `BRAVE_API_KEY` or `SERPAPI_API_KEY` | Web search for emails, plus Instagram/TikTok profile snippets | brave.com/search/api, serpapi.com |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` | Discover by genre, Spotify followers | developer.spotify.com/dashboard |
| `LASTFM_API_KEY` | Discover by tag or "similar to", listener counts | last.fm/api/account/create |
| `YOUTUBE_API_KEY` | YouTube views per video (engagement), emails in descriptions | Google Cloud Console → YouTube Data API v3 |

With no keys, it still uses MusicBrainz, artist websites, link pages, the bookmarklet and pasted bios.

**Most important for engagement matching:** the Meta keys (Instagram engagement) and a YouTube key. The bookmarklet covers TikTok. For discovery, use Spotify by genre and Last.fm "similar to". For "similar to", seed it with an artist you've worked with or one at your level, which tends to surface peers.

## How "around my level" works (engagement)

Level is based on how many people actually engage with a typical post, not on followers. An account with 200K followers whose posts get 300 likes is at the same level as one with 5K followers getting 300 likes.

| Platform | Engagement measured | Where it comes from |
|---|---|---|
| Instagram | Median **views per reel**, recent reels with pinned ones skipped | Capture bookmarklet on their **Reels tab** (instagram.com/name/reels) |
| Instagram | Median **likes + comments per reel**, over the last 12 reels | Official API (Meta keys) |
| TikTok | Median views per video, recent videos with pinned ones skipped | Capture bookmarklet on their profile, or typed in by hand |
| YouTube | Median views per video, over the last 10 uploads | YouTube API key |

Medians keep one viral post from skewing the number. Instagram is measured on reels only; photo and carousel posts are ignored. Each number is compared only with the same number of yours. The comparisons are averaged within a platform first, so Instagram counts once even when both reel numbers are known. Then the platforms are averaged (a geometric mean). By default, 0.25× to 4× your engagement counts as "your level"; you can change the range in Settings.

**Setting your own level:**

- **Instagram:** open your own Reels tab, click **Capture artist**, then **This is me**. That sets views per reel. Settings → "Fill in my engagement automatically" sets likes + comments per reel (needs Meta keys).
- **YouTube:** paste your channel link there (needs a YouTube key).
- **TikTok:** open your own profile, click **Capture artist**, then **This is me**.
- **Anything else:** type the numbers in.

Followers, Spotify and Last.fm numbers are still shown as context. Each artist panel also shows the Instagram engagement rate (engagement ÷ followers). A rate under 1% is flagged, because it often means bought followers.

Engagement is only known after an artist is looked up. So with "Only artists around my level" on, discovery looks each artist up and then keeps or skips them. Skipped artists are saved, so they're never checked twice, and they appear under the "Bigger" and "Smaller" filters. Artists whose engagement couldn't be measured are kept and shown as `?`. To capture their TikTok or enter numbers by hand, use the bookmarklet or the artist page. Spotify lists popular artists first, so the optional "skip artists with more than N Spotify followers" field saves time on superstars.

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
