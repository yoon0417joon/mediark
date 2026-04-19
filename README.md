# mediark

[한국어 README](README.ko.md)

A local media search engine that indexes images, GIFs, and videos using **OCR / tagging / speech recognition**, then lets you search across all four dimensions at once.

- **OCR search** — text inside images (Korean + English)
- **WD14 tag search** — anime/illustration style tags (WD EVA-02 Large v3)
- **RAM++ tag search** — natural language tags (Recognize Anything Plus)
- **STT search** — spoken words in videos (Whisper)

All processing runs **locally** — no cloud API required. Retrieval uses FTS5 keyword filtering followed by vector similarity ranking (sentence-transformers).

---

## Supported Platforms

| OS | Python | OCR backend |
|----|--------|-------------|
| Ubuntu 22.04+ | 3.10+ | PaddleOCR 2.7.3 |
| macOS (Apple Silicon / Intel) | 3.10+ | EasyOCR |
| Windows 10 / 11 | 3.10+ | PaddleOCR 2.7.3 |

> **ffmpeg** must be installed separately for video processing and STT extraction.

---

## Quick Start

### 1. Install ffmpeg

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows — download from https://ffmpeg.org/download.html and add to PATH
```

### 2. Clone and install

```bash
git clone https://github.com/yoon0417joon/mediark.git
cd imgsearchengine
```

**Linux / macOS:**
```bash
bash setup.sh
```

**Windows:**
```bat
setup.bat
```

The script creates a virtual environment, installs OS-specific dependencies, installs RAM++, and initialises the `.env` file.

### 3. Set your gallery path

Open `.env` and set `GALLERY_ROOT` to your image folder:

```dotenv
GALLERY_ROOT=/path/to/your/images
```

### 4. Start the server

**Linux / macOS:**
```bash
source .venv/bin/activate
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

**Windows:**
```bat
.venv\Scripts\activate
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000` in your browser.

### 5. Index your media

```bash
python -m server.ingest.pipeline full
```

Only new files are processed. Search is available immediately after.

---

## Indexing

| Method | Description |
|--------|-------------|
| `pipeline full` | Index all new files (OCR + tagging + embedding) |
| `pipeline ocr` | OCR / tags / thumbnails only |
| `pipeline embed` | Embedding + Qdrant storage only |
| `POST /ingest` | Trigger background indexing via API |
| Watchdog | Auto-index when files are added to `GALLERY_ROOT` |

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/search` | Search (`ocr_q`, `wd14_q`, `ram_q`, `stt_q`) |
| `GET` | `/random` | Random media |
| `GET` | `/media/{id}` | Download original file |
| `GET` | `/thumb/{id}` | Thumbnail image |
| `GET` | `/info/{id}` | Media metadata (OCR / tags) |
| `POST` | `/upload` | Upload a file |
| `POST` | `/ingest` | Trigger indexing pipeline |
| `GET` | `/status` | Indexing progress |
| `GET` | `/tags/suggest` | Tag autocomplete |
| `GET` | `/watchdog/status` | File watcher status |

---

## Key Environment Variables

See `.env.example` for the full list. Commonly used:

| Variable | Default | Description |
|----------|---------|-------------|
| `GALLERY_ROOT` | `./images_sample` | Path to your media folder |
| `API_KEY` | *(none)* | Set to enable API key authentication |
| `QDRANT_URL` | *(none)* | External Qdrant server URL |
| `OCR_BACKEND` | auto by OS | `paddleocr` or `easyocr` |
| `STT_MODEL` | `base` | Whisper model size |

---

## Project Structure

```
imgsearchengine/
├── server/
│   ├── main.py          # FastAPI app
│   ├── config.py        # All configuration
│   ├── ingest/          # Pipeline (OCR, tagger, STT, thumbnail)
│   ├── search/          # Vector search + re-ranking
│   └── db/              # SQLite + Qdrant wrappers
├── client/
│   └── index.html       # Web UI (single file)
├── tests/
├── .env.example
├── requirements-base.txt
├── requirements-linux.txt
├── requirements-mac.txt
├── requirements-windows.txt
├── setup.sh             # Linux/macOS installer
└── setup.bat            # Windows installer
```

---

## Troubleshooting

**Qdrant / SQLite consistency mismatch:**
```bash
python -c "from server.db.sqlite import init_db; from server.ingest.pipeline import repair_qdrant_consistency; init_db(); repair_qdrant_consistency()"
```

**Tag autocomplete returns nothing:**
```bash
python -c "from server.db.sqlite import init_db, rebuild_tag_stats; init_db(); rebuild_tag_stats()"
```

**RAM++ tags missing:**
```bash
python -m server.ingest.repair_ram_tags
```

---

## Roadmap

- [ ] Duplicate detection — SHA-256 exact-match upload rejection; bulk backfill for existing gallery
- [ ] Multi-user auth — JWT login, viewer / uploader / moderator / admin roles, invite-code signup
- [ ] Per-user search settings — vector vs. exact match, partial vs. exact tag, AND / OR keyword logic
- [ ] User & server management — admin panel, server profile (visibility, allowed formats), multi-server client presets
- [ ] Content moderation — report queue, hide / delete actions, granular moderator permissions
- [ ] Storage quota — LRU auto-eviction weighted by access score; Pin to protect files
- [ ] Extended formats — audio (mp3 / wav / flac / m4a), PDF, EPUB ingestion
- [ ] Plugin pipeline — per-stage ON/OFF toggle (OCR / WD14 / RAM++ / STT), ingest scheduling, selective reprocess
- [ ] Tag management — aliases, parent-child hierarchy, per-media editing, range-slider numeric search
- [ ] Reverse image search, similarity dedup (pHash + vector), and smart collections
- [ ] Full plugin ecosystem (11 categories) + admin dashboard
- [ ] Themes, federated search, and large-scale performance optimisation *(long-term)*

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT License](LICENSE)
