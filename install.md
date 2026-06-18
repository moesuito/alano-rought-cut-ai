# alano-rought-cut-ai install (Rough Cut Specialist)

Use this file only for first-time install or reconnect. For daily editing, read `SKILL.md`. Always read `helpers/` — that's where the scripts live.

## What you're doing

You're setting up a conversation-driven rough cut video editor for the user. After install, the user drops raw footage into any folder, runs their agent (`claude`, `codex`, etc.) there, and says "edit these clips into a rough cut." The agent does the rest by reading `SKILL.md` and exporting a Final Cut Pro 7 XML timeline for Adobe Premiere Pro.

Three things must exist on this machine:

1. The `alano-rought-cut-ai` repo cloned somewhere stable.
2. `ffmpeg` and `ffprobe` on `$PATH` (plus optional `yt-dlp` for online sources).
3. An ElevenLabs API key in `.env` at the repo root (for Scribe transcription).

And one thing must be true about the current agent:

4. It can discover `SKILL.md` — either via a global skills directory or via system-prompt imports.

## Steps

### 1. Clone to a stable path

```bash
test -d ~/Developer/alano-rought-cut-ai || git clone https://github.com/Alano/alano-rought-cut-ai ~/Developer/alano-rought-cut-ai
cd ~/Developer/alano-rought-cut-ai
```

If the repo is already there, `git pull --ff-only` and continue.

### 2. Install Python deps

```bash
# Prefer uv if available; fall back to pip.
command -v uv >/dev/null && uv sync || pip install -e .
```

`pyproject.toml` lists `requests`, `pillow`, `numpy`.

### 3. Install ffmpeg

`ffmpeg` and `ffprobe` are hard requirements.

```bash
# macOS
command -v ffmpeg >/dev/null || brew install ffmpeg

# Windows (Powershell)
# winget install FFmpeg
```

### 4. Register the skill with the current agent

Symlink the whole repo directory to your agent's skills directory under the name `video-use`.

- **Claude Code** (`~/.claude/` present):
    ```bash
    mkdir -p ~/.claude/skills
    ln -sfn ~/Developer/alano-rought-cut-ai ~/.claude/skills/video-use
    ```

- **Antigravity / Gemini / other agent with a skills directory**: Symlink the path to the skills folder.

### 5. ElevenLabs API key

1. Check existing state:
    ```bash
    [ -n "$ELEVENLABS_API_KEY" ] && echo "env"
    grep -q '^ELEVENLABS_API_KEY=..' ~/Developer/alano-rought-cut-ai/.env 2>/dev/null && echo "dotenv"
    ```

2. If neither is set, write it to `.env` at the repo root:
    ```bash
    printf 'ELEVENLABS_API_KEY=%s\n' "$KEY" > ~/Developer/alano-rought-cut-ai/.env
    chmod 600 ~/Developer/alano-rought-cut-ai/.env
    ```

### 6. Verify end-to-end

Run one real command to verify:
```bash
python ~/Developer/alano-rought-cut-ai/helpers/timeline_view.py --help >/dev/null && echo "helpers OK"
ffprobe -version | head -1
```

### 7. Hand off

Tell the user:
- Where the skill is installed.
- To `cd` into their footage folder and start editing.
- All outputs land in `<videos_dir>/edit/` — the repo stays clean.
