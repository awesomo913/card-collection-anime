# Termux setup guide — card-collection-anime on Android

End-to-end walkthrough: install Termux, run the card collection app on your phone, get a one-tap launcher. No prior Linux/terminal experience needed.

---

## Step 1 — Install F-Droid (NOT the Play Store version of Termux)

The Play Store copy of Termux is years out of date and **will not work** with this guide. You must use F-Droid (a free open-source app store).

1. On your phone, open Chrome and go to **https://f-droid.org**
2. Tap the green **Download F-Droid** button. It downloads `F-Droid.apk`.
3. Chrome warns "this type of file can harm your device" — tap **Download anyway** / **OK**.
4. Open the downloaded APK (Chrome → Downloads → tap `F-Droid.apk`).
5. Android blocks the install: "For your security, your phone is not allowed to install unknown apps from this source."
   - Tap **Settings**.
   - Toggle **Allow from this source** ON.
   - Hit back, then **Install**.
6. Open F-Droid. It does a one-time index download (~30 sec). Wait for the bottom progress bar to finish.

---

## Step 2 — Install Termux + add-ons from F-Droid

In F-Droid, tap the search icon (top right) and install these three apps. Each one: search, tap the result, tap **Install**, accept the unknown-source prompt if it appears, tap **Install** again.

1. **Termux** — the terminal itself.
2. **Termux:Widget** — adds a home-screen tile that launches your script with one tap.
3. **Termux:API** — lets the script auto-open Chrome to the app when it starts.

(All three must come from F-Droid for them to talk to each other. Same-source signature requirement.)

---

## Step 3 — First launch of Termux

1. Open **Termux** (the icon is a black terminal).
2. Wait for the welcome message and the `~ $` prompt. First launch downloads a few small packages — about 20 seconds.
3. Grant storage permission so the app's database can be backed up if you ever want to:
   ```
   termux-setup-storage
   ```
   Tap **Allow** when Android prompts.

---

## Step 4 — Install the card collection app

Type these three lines into Termux, hit Enter after each (you can long-press inside Termux to paste):

```
pkg update -y && pkg install -y git
git clone https://github.com/awesomo913/card-collection-anime
bash card-collection-anime/deploy/termux-run.sh
```

The third line does everything: installs Python, Rust, Node.js, builds the React frontend, then starts the server. **First run takes 10–15 minutes** because it's compiling some Python packages from source. Subsequent runs of the same line take about 3 seconds — the script skips work that's already done.

When you see:
```
[termux-run] starting uvicorn on http://127.0.0.1:8000
INFO:     Uvicorn running on http://127.0.0.1:8000
```
the app is live. Termux:API will auto-open Chrome to the page. If it doesn't, open Chrome manually and type **127.0.0.1:8000** into the address bar.

---

## Step 5 — Add to home screen (real app feel)

In Chrome, with the page loaded:

1. Tap the **three-dot menu** (top right).
2. Tap **Add to Home screen** (or **Install app** on newer Chrome).
3. Name it whatever you want, tap **Add**.

Now there's an icon on your home screen. Tapping it opens the app fullscreen, no browser bar.

---

## Step 6 — One-tap launcher with Termux:Widget

The script needs to be re-run each time you want to use the app (Termux doesn't run in the background by Android design). Make it one tap:

In Termux:
```
mkdir -p ~/.shortcuts
ln -sf ~/card-collection-anime/deploy/termux-run.sh ~/.shortcuts/CardCollection.sh
chmod +x ~/.shortcuts/CardCollection.sh
```

Then long-press your home screen → **Widgets** → find **Termux:Widget** → drag a widget tile (1×1 or 2×2) onto your home screen → pick **CardCollection** from the list.

Now: tap that tile → Termux launches → script runs (3 sec since everything's installed) → Chrome opens to the app. Two taps total.

---

## Daily use

- Tap your **CardCollection** widget tile.
- Use the app in Chrome.
- When done, swipe Termux out of recents (or just lock the phone — Android will eventually kill it). Your data is saved to a SQLite file in `card-collection-anime/backend/`.

To stop it manually inside Termux: press `Ctrl+C` (volume-down + C on your keyboard).

---

## Updating the app (when you want new features)

```
cd ~/card-collection-anime
git pull
bash deploy/termux-run.sh
```

The script auto-rebuilds the frontend if `package.json` changed.

---

## Backing up your card data

Your collection lives in `~/card-collection-anime/backend/cards.db` (SQLite file). Copy it anywhere safe:

```
cp ~/card-collection-anime/backend/cards.db ~/storage/downloads/cards-backup-$(date +%F).db
```

That puts a dated copy in your phone's regular **Downloads** folder, where you can grab it via USB or upload to Drive/Dropbox.

---

## Troubleshooting

**"pkg: command not found"** — You're not in Termux. Open the actual Termux app, not a different terminal.

**"Could not resolve host: github.com"** — No internet. Check WiFi or cellular.

**Build hangs at `Building wheel for cryptography`** — normal, takes 3–5 minutes on phone CPUs. Be patient. If it fails: `pkg install -y rust openssl` then re-run.

**"Address already in use" on port 8000** — Previous run didn't shut down cleanly. In Termux: `pkill -f uvicorn` then re-run the launcher.

**Chrome shows "This site can't be reached"** — uvicorn isn't running. Look at Termux for an error message. Most common cause: storage full (need ~500MB free for first install).

**Want a different port** — Run `PORT=9000 bash card-collection-anime/deploy/termux-run.sh` and the app listens on 9000 instead.

---

## Why not just an APK file?

A real `.apk` would require porting the Python backend to either Pyodide (Python compiled to WASM) or a JS rewrite, plus an Android Studio build. Multi-hour project. Termux gives you the same end-result (icon on home screen, runs offline-capable app) with zero rewrite work.

If you ever want the actual `.apk` route, that path is documented separately — but try Termux first; most people stop here.
