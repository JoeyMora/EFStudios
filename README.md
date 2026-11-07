# EF Studios

## Artist Finder

Find artists at your level to reach out to, with their booking emails. It runs on your own computer and opens in your web browser.

### How to run it

**1. Get Python (one time).** You need Python 3.10 or newer.
- **Mac:** download it from https://www.python.org/downloads/ and install it.
- **Windows:** download it from the same page. During install, tick **"Add python.exe to PATH"**.

**2. Download this project.** On GitHub, switch to the `claude/artist-email-finder-lypxd3` branch, then click **Code → Download ZIP** and unzip it. Or, if you use Git:

```
git clone -b claude/artist-email-finder-lypxd3 https://github.com/JoeyMora/EFStudios
```

**3. Start it.** Open the `artist-email-finder` folder:
- **Mac:** double-click **`start.command`**. The first time, macOS may block it because it's downloaded: right-click it, choose **Open**, then **Open** again.
- **Windows:** double-click **`start.bat`**.
- **Linux:** run `./start.sh`.

The first start takes about a minute while it sets itself up. Then your browser opens **http://127.0.0.1:5005**. Leave the window that opened running while you use the app, and close it to stop.

**If double-clicking doesn't work on Mac**, open Terminal and type `bash `, drag `start.command` into the window, and press Return.

### First things to do in the app

1. **Settings:** set your own engagement. The easiest way is to drag the **Capture artist** button to your bookmarks bar, open your own Instagram Reels tab or TikTok, click the bookmark, and press **This is me**.
2. **Discover:** type who you're looking for, e.g. *"female punk rock band"* or *"R&B singers in Atlanta"*.
3. Star the artists you like. **For you** then starts suggesting more like them, and the **Galaxy** shows everyone at a glance.

### Optional: API keys for better results

The app works with no keys at all, using MusicBrainz and the artists' own websites. Adding free keys gives it more to work with: Spotify, Last.fm, YouTube, Brave Search, and Instagram's official API for reel engagement. Open the file named `.env` in the `artist-email-finder` folder; it's created on first run. Paste the keys in, then restart the app.

The full details are in [artist-email-finder/README.md](artist-email-finder/README.md).
