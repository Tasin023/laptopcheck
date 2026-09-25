# Building LaptopCheck.exe from Kali

PyInstaller only builds for the OS it's *running on*. On Kali it produces a
Linux binary — never a Windows `.exe`, no matter what flags you pass. You
have two real options:

---

## Option A (recommended): GitHub Actions — build it on a real Windows box, free

No Wine, no cross-compile headaches. GitHub spins up an actual Windows
machine, builds it, you download the finished `.exe`.

1. Create a new repo on your GitHub (`github.com/tasin023`), e.g. `laptopcheck`.
2. Push these files to it, in the repo root:
   - `LaptopCheckGUI.py`
   - `logo.ico`
   - `logo_512.png`
   - `.github/workflows/build.yml`  <- create this exact folder path, put `build.yml` inside it

   ```bash
   git init
   git add LaptopCheckGUI.py logo.ico logo_512.png
   mkdir -p .github/workflows
   cp build.yml .github/workflows/build.yml
   git add .github/workflows/build.yml
   git commit -m "add app + build workflow"
   git branch -M main
   git remote add origin https://github.com/tasin023/laptopcheck.git
   git push -u origin main
   ```

3. On GitHub, go to your repo → **Actions** tab. The workflow runs
   automatically on push (or click **Run workflow** to trigger it manually).
4. Wait ~1-2 minutes for the build to finish (green checkmark).
5. Click into the finished run → under **Artifacts**, download
   `LaptopCheck-exe` → unzip it → that's your real Windows `.exe`.

This is the same method real Windows apps get cross-built with — it's not a
workaround, it's the standard way.

---

## Option B: Wine on Kali (if you don't want to use GitHub)

More fragile, but works if set up right.

```bash
sudo apt update
sudo apt install wine winetricks -y

# Download the Windows Python installer (do this in a browser, or wget it)
wget https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe

# Install Python INSIDE Wine
wine python-3.11.8-amd64.exe
# -> in the installer window: CHECK "Add python.exe to PATH", then Install Now

# Verify it installed
wine python --version

# Install build deps inside the Wine Python environment
wine python -m pip install --upgrade pip
wine python -m pip install pyinstaller pillow

# Build -- this now produces a REAL .exe because it's running Windows Python
wine python -m PyInstaller --onefile --windowed --name LaptopCheck ^
  --icon=logo.ico --add-data "logo.ico;." --add-data "logo_512.png;." ^
  LaptopCheckGUI.py
```

The output lands in `dist/LaptopCheck.exe` -- that file is a real Windows
binary and works fine on actual Windows even though it was built under Wine.

**Common Wine failure points**, if it's erroring:
- `wine python` not found -> the PATH checkbox wasn't ticked during install;
  rerun the installer.
- Tkinter GUI crashes on launch when tested under Wine itself (not a real
  problem -- Wine's own Tk support is shaky; the exe still runs fine on real
  Windows, so this isn't worth debugging, just test the actual exe on real
  Windows or in a VM).
- Pillow build errors -> run `winetricks vcrun2019` first, then retry the
  pip install.

---

## If you want help debugging Option B further

Paste me the exact error text you're getting on Kali and I'll walk through
the fix instead of guessing blind.
