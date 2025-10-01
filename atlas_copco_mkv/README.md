# Atlas Copco MKV Parser (Example Home Assistant Add-on)

This zip contains a minimal, working add-on skeleton with a corrected `.dockerignore`
so that `requirements.txt` and `atlas_copco_parser.py` are included in the build context.

## Install (Local Repo)
1. Unzip into your add-ons repo under a folder like `atlas_copco_mkv_addon_fixed/`.
2. In Home Assistant: **Settings → Add-ons → Add-on Store → (⋮) → Repositories** and add your repo if needed.
3. Open the add-on and click **Build** (or **Install** if Supervisor already built it).

## Notes
- No `image:` field in `config.yaml` to force a local build from the Dockerfile.
- `.dockerignore` now explicitly un-ignores the needed files.
