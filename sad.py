
cat > sad.py <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import time
import signal
import queue
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

# ============================================================
# AYARLAR
# ============================================================

M3U_URL = os.getenv(
    "M3U_URL",
    "https://raw.githubusercontent.com/zirvebaba/t/refs/heads/main/sad.m3u"
)

LOGO_URL = os.getenv(
    "LOGO_URL",
    "https://i.hizliresim.com/w0617e2g.png"
)

RTMP_URL = os.getenv(
    "RTMP_URL",
    "rtmp://ssh101.bozztv.com:1935/ssh101/sadtv"
)

STATE_FILE = Path(os.getenv("STATE_FILE", "state_sadtv.json"))
LOGO_FILE = Path("sad_logo.png")
TITLE_FILE = Path("sad_title.txt")

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 25
VIDEO_BITRATE = "2500k"
AUDIO_BITRATE = "128k"

MAX_FAILURES = 3
FAST_FAIL_SECONDS = 25
WATCHDOG_SECONDS = 90
RETRY_DELAY = 5
REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

stop_event = threading.Event()


# ============================================================
# YARDIMCI FONKSİYONLAR
# ============================================================

def log(message):
    print(
        time.strftime("[%Y-%m-%d %H:%M:%S]"),
        message,
        flush=True
    )


def safe_log(text):
    """Token ve hassas sorgu parametrelerini loglardan gizle."""
    text = str(text)
    text = re.sub(
        r"(?i)(token|password|pass|username)=([^&\s]+)",
        r"\1=***",
        text
    )
    return text


def load_state():
    default = {
        "index": 0,
        "failures": {},
        "last_url": "",
        "last_title": "",
        "total_played": 0,
        "total_failed": 0
    }

    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                default.update(data)
    except Exception as e:
        log(f"Durum dosyası okunamadı: {e}")

    return default


def save_state(state):
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        tmp.replace(STATE_FILE)
    except Exception as e:
        log(f"Durum kaydedilemedi: {e}")


def download_file(url, destination):
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        if not response.content:
            raise RuntimeError("İndirilen dosya boş.")

        Path(destination).write_bytes(response.content)
        return True

    except Exception as e:
        log(f"Dosya indirilemedi: {e}")
        return False


# ============================================================
# M3U OKUMA
# ============================================================

def parse_m3u(content):
    items = []
    current_title = "İsimsiz Film"
    current_group = ""

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF:"):
            current_title = line.split(",", 1)[-1].strip()

            group_match = re.search(
                r'group-title="([^"]*)"',
                line,
                re.IGNORECASE
            )

            current_group = (
                group_match.group(1).strip()
                if group_match else ""
            )

            continue

        if line.startswith("#"):
            continue

        if line.startswith(("http://", "https://")):
            items.append({
                "title": current_title or "İsimsiz Film",
                "group": current_group,
                "url": line
            })

            current_title = "İsimsiz Film"
            current_group = ""

    return items


def get_playlist():
    log("M3U listesi indiriliyor...")

    response = session.get(
        M3U_URL,
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()

    items = parse_m3u(response.text)

    if not items:
        raise RuntimeError("M3U içerisinde yayın bulunamadı.")

    log(f"Toplam {len(items)} yayın bulundu.")
    return items


# ============================================================
# KAYNAK BAŞLIKLARI
# ============================================================

def make_headers(source_url):
    parsed = urlparse(source_url)

    origin = f"{parsed.scheme}://{parsed.netloc}"
    referer = origin + "/"

    return (
        f"User-Agent: {USER_AGENT}\r\n"
        f"Referer: {referer}\r\n"
        f"Origin: {origin}\r\n"
        "Accept: */*\r\n"
        "Connection: keep-alive\r\n"
    )


# ============================================================
# FFMPEG KOMUTU
# ============================================================

def build_ffmpeg_command(item):
    source_url = item["url"]

    headers = make_headers(source_url)

    # Kaynak HLS girdisi.
    #
    # extension_picky=0:
    # stream.php?token=... gibi uzantısız HLS segmentlerine
    # izin verir.
    #
    # allowed_extensions=ALL:
    # HLS segment uzantısı kontrolünü genişletir.
    #
    # Bu seçenekler -i kaynak adresinden ÖNCE olmalıdır.

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-nostats",

        "-re",

        "-user_agent", USER_AGENT,
        "-headers", headers,

        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto,data",

        "-extension_picky", "0",
        "-allowed_extensions", "ALL",

        "-rw_timeout", "30000000",

        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",

        "-fflags", "+genpts+discardcorrupt",
        "-err_detect", "ignore_err",

        "-i", source_url,
    ]

    # Logo varsa ikinci girdi olarak ekle.
    logo_enabled = LOGO_FILE.exists()

    if logo_enabled:
        command += [
            "-loop", "1",
            "-framerate", str(VIDEO_FPS),
            "-i", str(LOGO_FILE)
        ]

    # Film adını FFmpeg drawtext için dosyadan okut.
    title = item.get("title", "MutluAPK")

    try:
        TITLE_FILE.write_text(
            title.replace("\n", " ").replace("\r", " "),
            encoding="utf-8"
        )
    except Exception:
        pass

    font_file = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    )

    if logo_enabled:
        filter_complex = (
            f"[0:v:0]"
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            f"(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1[base];"
            f"[1:v:0]scale=180:-1[logo];"
            f"[base][logo]overlay="
            f"W-w-35:35:shortest=1[withlogo];"
            f"[withlogo]drawtext="
            f"fontfile={font_file}:"
            f"textfile={TITLE_FILE}:"
            f"reload=1:"
            f"fontcolor=white:"
            f"fontsize=28:"
            f"borderw=2:"
            f"bordercolor=black:"
            f"x=35:y=H-th-35[vout]"
        )
    else:
        filter_complex = (
            f"[0:v:0]"
            f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:"
            f"(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,"
            f"drawtext="
            f"fontfile={font_file}:"
            f"textfile={TITLE_FILE}:"
            f"reload=1:"
            f"fontcolor=white:"
            f"fontsize=28:"
            f"borderw=2:"
            f"bordercolor=black:"
            f"x=35:y=H-th-35[vout]"
        )

    command += [
        "-filter_complex", filter_complex,

        "-map", "[vout]",
        "-map", "0:a:0?",

        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-r", str(VIDEO_FPS),
        "-g", str(VIDEO_FPS * 2),
        "-b:v", VIDEO_BITRATE,
        "-maxrate", VIDEO_BITRATE,
        "-bufsize", "5000k",

        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-ar", "44100",
        "-ac", "2",

        "-flvflags", "no_duration_filesize",

        "-progress", "pipe:1",
        "-nostats",

        "-f", "flv",
        RTMP_URL
    ]

    return command


# ============================================================
# FFMPEG ÇALIŞTIRMA / WATCHDOG
# ============================================================

def run_ffmpeg(item, state):
    title = item["title"]
    source_url = item["url"]

    log(f"Yayın başlatılıyor: {title}")

    state["last_url"] = source_url
    state["last_title"] = title
    save_state(state)

    command = build_ffmpeg_command(item)

    started_at = time.time()
    last_progress = time.time()
    last_progress_text = ""
    output_queue = queue.Queue()

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

    except Exception as e:
        log(f"FFmpeg başlatılamadı: {e}")
        return 1, 0

    def reader(pipe, prefix):
        try:
            for line in iter(pipe.readline, ""):
                output_queue.put((prefix, line.rstrip()))
        except Exception:
            pass
        finally:
            output_queue.put((prefix, None))

    stdout_thread = threading.Thread(
        target=reader,
        args=(process.stdout, "OUT"),
        daemon=True
    )

    stderr_thread = threading.Thread(
        target=reader,
        args=(process.stderr, "ERR"),
        daemon=True
    )

    stdout_thread.start()
    stderr_thread.start()

    try:
        while not stop_event.is_set():
            try:
                prefix, line = output_queue.get(timeout=1)
            except queue.Empty:
                line = None
                prefix = None

            if line is not None:
                if prefix == "OUT":
                    if "=" in line:
                        key, value = line.split("=", 1)

                        if key in (
                            "out_time",
                            "out_time_ms",
                            "progress"
                        ):
                            last_progress = time.time()
                            last_progress_text = line

                        if key == "progress" and value == "continue":
                            pass

                elif prefix == "ERR":
                    cleaned = safe_log(line)

                    if cleaned:
                        log("FFmpeg: " + cleaned)

            return_code = process.poll()

            if return_code is not None:
                elapsed = time.time() - started_at
                return return_code, elapsed

            if time.time() - last_progress > WATCHDOG_SECONDS:
                log(
                    f"Watchdog: {WATCHDOG_SECONDS} saniyedir "
                    "FFmpeg ilerlemesi yok. İşlem yeniden başlatılıyor."
                )

                process.kill()
                process.wait(timeout=10)

                return -2, time.time() - started_at

        if process.poll() is None:
            process.terminate()

            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        return -3, time.time() - started_at

    finally:
        if process.poll() is None:
            process.kill()


# ============================================================
# ANA DÖNGÜ
# ============================================================

def main():
    log("SADTV yayın sistemi başlatılıyor.")

    state = load_state()

    if not download_file(LOGO_URL, LOGO_FILE):
        log("Logo indirilemedi. Logo olmadan yayın devam edecek.")

    try:
        items = get_playlist()
    except Exception as e:
        log(f"M3U hatası: {e}")
        return 1

    index = int(state.get("index", 0))

    if index < 0 or index >= len(items):
        index = 0

    failures = state.get("failures", {})

    while not stop_event.is_set():
        if not items:
            log("Yayın listesi boş.")
            time.sleep(10)
            continue

        if index >= len(items):
            index = 0

        item = items[index]
        title = item["title"]
        source_url = item["url"]

        failure_key = str(index)
        current_failures = int(failures.get(failure_key, 0))

        if current_failures >= MAX_FAILURES:
            log(
                f"Yayın {MAX_FAILURES} kez başarısız oldu, "
                f"sonraki filme geçiliyor: {title}"
            )

            index += 1
            state["index"] = index
            save_state(state)
            continue

        return_code, elapsed = run_ffmpeg(item, state)

        if stop_event.is_set():
            break

        if elapsed >= 60:
            failures[failure_key] = 0
            state["total_played"] = (
                int(state.get("total_played", 0)) + 1
            )

            log(f"Yayın tamamlandı veya sona erdi: {title}")

            index += 1

        else:
            failures[failure_key] = current_failures + 1
            state["total_failed"] = (
                int(state.get("total_failed", 0)) + 1
            )

            log(
                f"Yayın başarısız. Kod={return_code}, "
                f"Süre={int(elapsed)} saniye, "
                f"Deneme={failures[failure_key]}/{MAX_FAILURES}"
            )

            if failures[failure_key] >= MAX_FAILURES:
                index += 1
            else:
                log(f"{RETRY_DELAY} saniye sonra tekrar denenecek.")
                time.sleep(RETRY_DELAY)

        state["index"] = index
        state["failures"] = failures
        save_state(state)

    save_state(state)
    log("SADTV yayın sistemi durduruldu.")
    return 0


# ============================================================
# SİNYAL YÖNETİMİ
# ============================================================

def handle_signal(signum, frame):
    log(f"Durdurma sinyali alındı: {signum}")
    stop_event.set()


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        stop_event.set()
        sys.exit(0)
    except Exception as e:
        log(f"KRİTİK HATA: {e}")
        sys.exit(1)
PY

python3 -m py_compile sad.py && echo "sad.py syntax OK"
