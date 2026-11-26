import os
import yt_dlp
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

# Print yt-dlp version on startup so you can verify it in Render logs
print(f"DEBUG: yt-dlp version: {yt_dlp.version.__version__}")

app = FastAPI()

# Setup folders
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory="templates")


# ---------- Helper: Get video info ----------
def get_video_info(url: str):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
        'noplaylist': True,
        # CRITICAL FIX: Tell YouTube we are an Android/iOS/TV app to bypass cloud IP blocks
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'tv', 'web_safari'],
            }
        },
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"ERROR during extraction: {str(e)}")
        return {"error": str(e)}


# ---------- Helper: Download file ----------
def download_media(url: str, format_id: str, is_audio: bool = False):
    outtmpl = str(DOWNLOAD_DIR / "%(title).150B [%(id)s].%(ext)s")

    # Base options to apply to both audio and video downloads
    base_opts = {
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'tv', 'web_safari'],
            }
        },
    }

    if is_audio:
        ydl_opts = {
            **base_opts,
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }],
        }
    else:
        if format_id:
            fmt = f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio/{format_id}"
        else:
            fmt = "bestvideo+bestaudio/best"
            
        ydl_opts = {
            **base_opts,
            'format': fmt,
            'merge_output_format': 'mp4',
            'format_sort': ['ext:mp4:m4a', 'res', 'fps', 'vcodec:h264'],
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        
        if is_audio:
            filename = os.path.splitext(filename)[0] + '.mp3'
        else:
            base = os.path.splitext(filename)[0]
            if os.path.exists(base + '.mp4'):
                filename = base + '.mp4'
                
        return filename


# ---------- Routes ----------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/analyze")
async def analyze(url: str = Form(...)):
    info = get_video_info(url)
    if "error" in info:
        raise HTTPException(status_code=400, detail=info["error"])

    formats = []
    for f in info.get('formats', []):
        # Skip audio-only streams here; we show MP3 separately
        if f.get('vcodec') in (None, 'none'):
            continue
        
        height = f.get('height')
        if not height:
            continue

        formats.append({
            'format_id': f.get('format_id'),
            'ext': f.get('ext'),
            'height': height,
            'fps': f.get('fps'),
            'vcodec': f.get('vcodec'),
            'filesize': f.get('filesize') or f.get('filesize_approx'),
            'label': quality_label(height, f.get('fps'), f.get('ext')),
            'size_human': human_size(f.get('filesize') or f.get('filesize_approx')),
        })

    # Deduplicate: keep the BEST format per (height, ext, fps bucket)
    best = {}
    for f in formats:
        fps_bucket = 60 if (f['fps'] or 0) >= 50 else 30
        key = (f['height'], f['ext'], fps_bucket)
        prev = best.get(key)
        if prev is None or (f['filesize'] or 0) > (prev['filesize'] or 0):
            best[key] = f

    # Sort by height desc, then fps desc
    ordered = sorted(
        best.values(),
        key=lambda x: (x['height'], x['fps'] or 0),
        reverse=True,
    )

    return {
        "title": info.get('title', 'Unknown'),
        "thumbnail": info.get('thumbnail', ''),
        "duration": info.get('duration', 0),
        "uploader": info.get('uploader', 'Unknown'),
        "formats": ordered[:15],
        "webpage_url": info.get('webpage_url', url),
    }


@app.post("/download")
async def download(
    url: str = Form(...),
    format_id: str = Form(None),
    audio_only: bool = Form(False),
):
    try:
        filepath = download_media(url, format_id, audio_only)
        if not os.path.exists(filepath):
            raise HTTPException(status_code=500, detail="File not found after download.")

        filename = os.path.basename(filepath)
        return FileResponse(
            path=filepath,
            filename=filename,
            media_type='application/octet-stream',
        )
    except Exception as e:
        print(f"ERROR during download: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------- Helper functions for labels ----------
def quality_label(height, fps=None, ext=None):
    if not height:
        return "Audio"
    labels = {
        4320: "8K",
        2160: "4K",
        1440: "2K",
        1080: "1080p",
        720: "720p",
        480: "480p",
        360: "360p",
        240: "240p",
        144: "144p",
    }
    label = labels.get(height, f"{height}p")
    if fps and fps >= 50:
        label += f"{int(fps)}"
    if ext:
        label += f" · {ext.upper()}"
    return label


def human_size(num):
    if not num:
        return "?"
    for unit in ["B", "KB", "MB", "GB"]:
        if num < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
