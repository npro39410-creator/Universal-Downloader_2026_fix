import os
import shutil
import tempfile
import yt_dlp
from pathlib import Path
from fastapi import FastAPI, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory="templates")

RENDER_COOKIE_PATH = Path("/etc/secrets/cookies.txt")
LOCAL_COOKIE_PATH = BASE_DIR / "cookies.txt"


# ---------- Helper: Prepare writable cookies ----------
def get_writable_cookies() -> str | None:
    """
    Render mounts /etc/secrets/cookies.txt as read-only.
    yt-dlp tries to write back to the cookie file, so we copy it to a temp path.
    """
    source = None
    if RENDER_COOKIE_PATH.exists():
        source = RENDER_COOKIE_PATH
    elif LOCAL_COOKIE_PATH.exists():
        source = LOCAL_COOKIE_PATH

    if not source:
        return None

    tmp_cookie = Path(tempfile.gettempdir()) / "yt_cookies_runtime.txt"
    shutil.copyfile(source, tmp_cookie)
    return str(tmp_cookie)


def is_youtube_url(url: str) -> bool:
    lowered = url.lower()
    return "youtube.com" in lowered or "youtu.be" in lowered


def build_base_ydl_opts(url: str, cookie_path: str | None) -> dict:
    opts = {
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "noplaylist": True,
        "nocheckcertificate": True,
        "geo_bypass": True,
    }

    if is_youtube_url(url):
        po_token = os.getenv("MlNTpK65TX7lR1OYYks2VW3sPUmpPgxvwsN-ih4ySMvz6TTLq_bvhPZWf5tyvNLSPzlBnWZW7AgSwaEbEra36OnfV5iaLTxxdEH7KcAGNdu_6BRGOQ==", "").strip()
        visitor_data = os.getenv("CgtUMEVJcDFjUElJMCiinqDYBjIKCgJLSBIEGgAgLWLDAgrAAkN1MEJDQVVTbndFQkRCNlBER21TZzY0TXFwME5KekUyQk1RLTV4T2Q2SmlJY2tiZEtIdHUxRU0xSGNoTDB1QXlxTHFqZUZZb2xPNno3UWpPTUhtMXJiQkV4MVlzVXBXbFhQQmlIdGlUQ19iVF9xUmItOHVnQTZNQi1CcEQyTElUVmVDeWZYTVo0eXVUeHlIYm03bDl6ODJxakh3WHVtS1o1aHNnQkxIa1I0T1VyMWVWaldYM3VDZ0xyMDE0X3JTVVJsSnJzWkFQZGtPQkk5ZW45NFlaUkJwQmZ0c2ZoTU1vQlRKRkFSeFk5V3JxYW9aOEwyTm4ydWNqQUpJT0xqVXAySktqLXRVLTdNSHZEbkFjU01CQWNKVnBQamJQYlc1Z1NONUNILXc1V3l6a3lqQWVWVjY2NjBCZVRjZjV5aGdH", "").strip()

        yt_args = {
            "player_client": ["web", "mweb"],
        }

        if po_token:
            # Support both raw token or already-prefixed token
            token_val = po_token if "+" in po_token else f"web.gvs+{po_token}"
            yt_args["po_token"] = [token_val]

        # If visitor_data is set (logged-out PO token session), use it without cookies
        # Otherwise fall back to writable cookies.txt if available
        if visitor_data:
            yt_args["visitor_data"] = [visitor_data]
        elif cookie_path:
            opts["cookiefile"] = cookie_path

        opts["extractor_args"] = {"youtube": yt_args}

    return opts


# ---------- Helper: Quality label ----------
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


# ---------- Helper: Get video info ----------
def get_video_info(url: str):
    cookie_path = get_writable_cookies()
    ydl_opts = build_base_ydl_opts(url, cookie_path)
    ydl_opts.update({
        "skip_download": True,
        "extract_flat": False,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        return {"error": str(e)}


# ---------- Helper: Download file ----------
def download_media(url: str, format_id: str | None, is_audio: bool = False) -> str:
    cookie_path = get_writable_cookies()
    ydl_opts = build_base_ydl_opts(url, cookie_path)
    outtmpl = str(DOWNLOAD_DIR / "%(title).120B [%(id)s].%(ext)s")

    if is_audio:
        ydl_opts.update({
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }],
        })
    else:
        if format_id:
            fmt = f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio/{format_id}"
        else:
            fmt = "bestvideo+bestaudio/best"

        ydl_opts.update({
            "format": fmt,
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "format_sort": ["ext:mp4:m4a", "res", "fps", "vcodec:h264"],
        })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)

        if is_audio:
            final_path = base + ".mp3"
        elif os.path.exists(base + ".mp4"):
            final_path = base + ".mp4"
        else:
            final_path = filename

        return final_path


def cleanup_file(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ---------- Routes ----------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/analyze")
async def analyze(url: str = Form(...)):
    url = url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Please enter a valid URL.")

    info = get_video_info(url)
    if not info or "error" in info:
        err_msg = info.get("error", "Failed to extract video info.") if info else "Failed to extract video info."
        raise HTTPException(status_code=400, detail=err_msg)

    formats = []
    for f in info.get("formats", []):
        if f.get("vcodec") in (None, "none"):
            continue
        height = f.get("height")
        if not height:
            continue

        size = f.get("filesize") or f.get("filesize_approx")
        formats.append({
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "height": height,
            "fps": f.get("fps"),
            "vcodec": f.get("vcodec"),
            "filesize": size,
            "label": quality_label(height, f.get("fps"), f.get("ext")),
            "size_human": human_size(size),
        })

    best = {}
    for f in formats:
        fps_bucket = 60 if (f["fps"] or 0) >= 50 else 30
        key = (f["height"], f["ext"], fps_bucket)
        prev = best.get(key)
        if prev is None or (f["filesize"] or 0) > (prev["filesize"] or 0):
            best[key] = f

    ordered = sorted(
        best.values(),
        key=lambda x: (x["height"], x["fps"] or 0),
        reverse=True,
    )

    return {
        "title": info.get("title", "Unknown"),
        "thumbnail": info.get("thumbnail", ""),
        "duration": info.get("duration", 0),
        "uploader": info.get("uploader", "Unknown"),
        "formats": ordered[:15],
        "webpage_url": info.get("webpage_url", url),
    }


@app.post("/download")
async def download(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    format_id: str = Form(None),
    audio_only: bool = Form(False),
):
    try:
        filepath = download_media(url.strip(), format_id, audio_only)
        if not os.path.exists(filepath):
            raise HTTPException(status_code=500, detail="File not found after download.")

        filename = os.path.basename(filepath)
        background_tasks.add_task(cleanup_file, filepath)

        return FileResponse(
            path=filepath,
            filename=filename,
            media_type="application/octet-stream",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
