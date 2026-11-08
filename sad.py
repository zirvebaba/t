
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import json
import requests
import threading
import queue
from collections import deque

# =========================================================
# AYARLAR
# =========================================================

RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY", "sadtv")
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

M3U_URL = os.getenv(
    "M3U_URL",
    "https://raw.githubusercontent.com/zirvebaba/t/refs/heads/main/sad.m3u"
)

LOGO_URL = os.getenv(
    "LOGO_URL",
    "https://i.hizliresim.com/w0617e2g.png"
)

STATE_FILE_NAME = os.getenv("STATE_FILE_NAME", "state_sadtv.json")

GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

STREAM_REFERER = "https://xtream.mutluapk.xyz/"
STREAM_ORIGIN = "https://xtream.mutluapk.xyz"

# Görüntü
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 25
VIDEO_BITRATE = "2500k"
AUDIO_BITRATE = "128k"

# Logo
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))

BOLD_FONT_PATH = os.getenv(
    "BOLD_FONT_PATH",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)

# FFmpeg
DECODER_THREADS = os.getenv("DECODER_THREADS", "1")
WATCHDOG_TIMEOUT_SECONDS = int(
    os.getenv("WATCHDOG_TIMEOUT_SECONDS", "90")
)

HTTP_TIMEOUT_SECONDS = 30
LINK_CHANGE_REWIND_SECONDS = 15

FAST_FAIL_THRESHOLD_SECONDS = 20
MAX_FAST_FAILURES = 3
MAX_RETRY_DELAY_SECONDS = 120

# =========================================================
# YARDIMCI FONKSİYONLAR
# =========================================================

def format_hms(seconds):
    seconds = max(0, int(seconds))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def get_local_state():

    if not os.path.exists(STATE_FILE_NAME):
        print("ℹ️ State dosyası bulunamadı. Baştan başlanıyor.")
        return 0, 0, "", ""

    try:
        if os.path.getsize(STATE_FILE_NAME) == 0:
            return 0, 0, "", ""

        with open(STATE_FILE_NAME, "r", encoding="utf-8") as f:
            data = json.load(f)

        index = max(0, int(data.get("last_index", 0)))
        seconds = max(0, int(data.get("last_seconds", 0)))

        url = data.get("last_url", "")
        title = data.get("last_title", "")

        print(
            f"✅ State okundu: {STATE_FILE_NAME} "
            f"=> İndeks: {index}, Saniye: {seconds}"
        )

        return index, seconds, url, title

    except Exception as e:
        print(f"⚠️ State okuma hatası: {e}")
        return 0, 0, "", ""


def update_local_state(index, seconds, url="", title=""):

    data = {
        "last_index": int(index),
        "last_seconds": max(0, int(seconds)),
        "last_url": url,
        "last_title": title
    }

    temp_file = STATE_FILE_NAME + ".tmp"

    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(temp_file, STATE_FILE_NAME)

        print(
            f"💾 State kaydedildi => "
            f"İndeks: {index}, Saniye: {int(seconds)}"
        )

    except Exception as e:
        print(f"⚠️ State yazma hatası: {e}")


# =========================================================
# M3U OKUMA
# =========================================================

def get_m3u_playlist(m3u_url):

    headers = {
        "User-Agent": STREAM_USER_AGENT,
        "Referer": STREAM_REFERER,
        "Origin": STREAM_ORIGIN
    }

    try:
        response = requests.get(
            m3u_url,
            headers=headers,
            timeout=30
        )

        response.raise_for_status()

        lines = response.text.lstrip("\ufeff").splitlines()

        playlist = []
        pending_title = None

        for raw_line in lines:

            line = raw_line.strip()

            if not line:
                continue

            if line.startswith("#EXTINF"):

                match = re.search(r",(.+)$", line)

                if match:
                    pending_title = match.group(1).strip()
                else:
                    pending_title = "İsimsiz içerik"

            elif not line.startswith("#"):

                if line.startswith(("http://", "https://")):

                    title = pending_title or os.path.basename(
                        line.split("?")[0]
                    )

                    playlist.append({
                        "url": line,
                        "title": title
                    })

                    pending_title = None

        print(f"📋 M3U başarıyla okundu: {len(playlist)} içerik")

        return playlist

    except Exception as e:

        print(f"❌ M3U okuma hatası: {e}")
        return []


# =========================================================
# LOGO
# =========================================================

def download_logo():

    try:

        response = requests.get(
            LOGO_URL,
            headers={"User-Agent": STREAM_USER_AGENT},
            timeout=20
        )

        response.raise_for_status()

        if len(response.content) < 100:
            raise ValueError("Logo dosyası çok küçük.")

        with open("logo.png", "wb") as f:
            f.write(response.content)

        print("✅ Logo başarıyla indirildi.")
        return True

    except Exception as e:

        print(f"⚠️ Logo indirilemedi: {e}")
        return False


# =========================================================
# BAŞLIK
# =========================================================

def write_title_file(title):

    try:

        title = title.replace("\n", " ").replace("\r", " ")

        with open("title.txt", "w", encoding="utf-8") as f:
            f.write(title)

    except Exception as e:
        print(f"⚠️ Başlık dosyası hatası: {e}")


# =========================================================
# GÖSTERGE
# =========================================================

def print_dashboard(
    title,
    index,
    playlist_len,
    seconds,
    status="🟢 Yayında"
):

    print("┌" + "─" * 58 + "┐")
    print(f"│ 🎬 İçerik         : {title[:36]:<36} │")
    print(f"│ 🔢 Sıra           : {index + 1}/{playlist_len:<32} │")
    print(f"│ ⏱️  Geçen Süre     : {format_hms(seconds):<36} │")
    print(f"│ 📡 Durum          : {status:<36} │")
    print("└" + "─" * 58 + "┘")


def write_step_summary(
    title,
    index,
    playlist_len,
    seconds,
    status="🟢 Yayında"
):

    if not GITHUB_STEP_SUMMARY:
        return

    try:

        content = (
            "## 📺 Canlı Yayın Durumu (Maxanimasyon)\n\n"
            "| Alan | Değer |\n"
            "|---|---|\n"
            f"| İçerik | {title} |\n"
            f"| Sıra | {index + 1}/{playlist_len} |\n"
            f"| Süre | {format_hms(seconds)} |\n"
            f"| Durum | {status} |\n"
            f"| Güncelleme | {time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )

        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write(content)

    except Exception as e:
        print(f"⚠️ Summary hatası: {e}")


# =========================================================
# HTTP / HLS GİRİŞ AYARLARI
# =========================================================

def get_input_options():

    headers = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {STREAM_REFERER}\r\n"
        f"Origin: {STREAM_ORIGIN}\r\n"
        "Accept: */*\r\n"
        "Connection: keep-alive\r\n"
    )

    return [
        "-user_agent", STREAM_USER_AGENT,
        "-referer", STREAM_REFERER,
        "-headers", headers,

        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto,data",

        "-allowed_extensions", "ALL",

        "-rw_timeout", str(HTTP_TIMEOUT_SECONDS * 1000000),

        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_on_http_error", "429,5xx",
        "-reconnect_delay_max", "5",

        "-fflags", "+genpts+discardcorrupt",

        "-analyzeduration", "5000000",
        "-probesize", "5000000",

        "-thread_queue_size", "2048",
        "-threads", DECODER_THREADS
    ]


# =========================================================
# FFMPEG KOMUTU
# =========================================================

def build_ffmpeg_command(target_url, last_seconds):

    dual_audio = ";" in target_url

    if dual_audio:

        video_url, audio_url = target_url.split(";", 1)

        video_url = video_url.strip()
        audio_url = audio_url.strip()

        print(f"🎥 Video: {video_url}")
        print(f"🔊 Ses  : {audio_url}")

        input_args = (
            get_input_options()
            + ["-i", video_url]
            + get_input_options()
            + ["-i", audio_url]
        )

        audio_map = ["-map", "1:a:0?"]
        logo_input_index = 2

    else:

        print(f"📡 Kaynak: {target_url}")

        input_args = get_input_options() + ["-i", target_url]

        audio_map = ["-map", "0:a:0?"]
        logo_input_index = 1

    # -ss girişten önce değil, girişten sonra uygulanır.
    # Böylece HLS seek işlemlerinde daha kontrollü davranılır.
    seek_args = (
        ["-ss", str(last_seconds)]
        if last_seconds > 0 else []
    )

    has_logo = (
        os.path.exists("logo.png")
        and os.path.getsize("logo.png") > 0
    )

    title_filter = (
        "drawtext="
        "textfile=title.txt:"
        f"fontfile={BOLD_FONT_PATH}:"
        f"fontcolor=white@{TEXT_OPACITY}:"
        "fontsize=25:"
        "x=80:"
        "y=h-th-55"
    )

    if has_logo:

        logo_args = ["-i", "logo.png"]

        filter_str = (
            "[0:v:0]"
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,"
            "fps=25[main];"

            f"[{logo_input_index}:v:0]"
            "scale=-2:130,"
            "format=rgba,"
            f"colorchannelmixer=aa={LOGO_OPACITY}[logo1];"

            "[main][logo1]"
            "overlay=75:55[tmp];"

            f"[tmp]{title_filter}[v]"
        )

    else:

        logo_args = []

        filter_str = (
            "[0:v:0]"
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,"
            "fps=25[main];"

            f"[main]{title_filter}[v]"
        )

    command = (
        ["ffmpeg", "-hide_banner", "-nostdin", "-y",
         "-loglevel", "info",
         "-stats_period", "5",
         "-progress", "pipe:1"]

        + input_args
        + seek_args
        + logo_args

        + [
            "-filter_complex", filter_str,

            "-map", "[v]"
        ]

        + audio_map

        + [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-r", "25",

            "-b:v", VIDEO_BITRATE,
            "-maxrate", VIDEO_BITRATE,
            "-bufsize", "5000k",

            "-g", "50",
            "-keyint_min", "50",
            "-sc_threshold", "0",

            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
            "-ac", "2",
            "-ar", "44100",

            "-max_muxing_queue_size", "2048",

            "-flvflags", "no_duration_filesize",
            "-f", "flv",

            RTMP_SERVER
        ]
    )

    return command


# =========================================================
# FFMPEG ÇIKTI OKUYUCULARI
# =========================================================

def reader_thread(pipe, output_queue, source):

    try:

        for line in iter(pipe.readline, ""):
            output_queue.put((source, line.rstrip()))

    except Exception as e:

        output_queue.put((source, f"READER_ERROR: {e}"))

    finally:

        output_queue.put((source, None))


def parse_progress_time(value):

    try:

        match = re.match(
            r"(\d+):(\d+):(\d+(?:\.\d+)?)",
            value
        )

        if not match:
            return None

        hours, minutes, seconds = match.groups()

        return (
            int(hours) * 3600
            + int(minutes) * 60
            + float(seconds)
        )

    except Exception:
        return None


# =========================================================
# FFMPEG ÇALIŞTIRMA
# =========================================================

def run_ffmpeg(command, base_seconds, index, title, total):

    print("▶ FFmpeg başlatılıyor...")

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1
    )

    output_queue = queue.Queue()

    stdout_thread = threading.Thread(
        target=reader_thread,
        args=(process.stdout, output_queue, "stdout"),
        daemon=True
    )

    stderr_thread = threading.Thread(
        target=reader_thread,
        args=(process.stderr, output_queue, "stderr"),
        daemon=True
    )

    stdout_thread.start()
    stderr_thread.start()

    stderr_tail = deque(maxlen=40)

    last_progress_time = time.time()
    last_save_time = time.time()
    last_dashboard_time = time.time()

    current_seconds = base_seconds

    stdout_closed = False
    stderr_closed = False

    while True:

        try:

            source, line = output_queue.get(timeout=1)

        except queue.Empty:

            source = None
            line = None

        if source and line is None:

            if source == "stdout":
                stdout_closed = True

            if source == "stderr":
                stderr_closed = True

        elif source and line is not None:

            if source == "stderr":

                stderr_tail.append(line)

                if line.strip():
                    print("FFmpeg:", line)

            elif source == "stdout":

                if line.startswith("out_time="):

                    played = parse_progress_time(
                        line.split("=", 1)[1]
                    )

                    if played is not None:

                        current_seconds = (
                            base_seconds + played
                        )

                        last_progress_time = time.time()

                        now = time.time()

                        if now - last_save_time >= 20:

                            update_local_state(
                                index,
                                current_seconds,
                                "",
                                title
                            )

                            last_save_time = now

                        if now - last_dashboard_time >= 30:

                            print_dashboard(
                                title,
                                index,
                                total,
                                current_seconds
                            )

                            write_step_summary(
                                title,
                                index,
                                total,
                                current_seconds
                            )

                            last_dashboard_time = now

        # FFmpeg normal olarak bitti mi?
        if process.poll() is not None:

            if stdout_closed and stderr_closed:
                break

            # Okuyucu thread'lerin son verileri boşaltmasına izin ver.
            if output_queue.empty():
                break

        # Watchdog
        if process.poll() is None:

            elapsed = time.time() - last_progress_time

            if elapsed > WATCHDOG_TIMEOUT_SECONDS:

                print(
                    f"🚨 Watchdog: {elapsed:.0f} saniyedir "
                    "FFmpeg ilerleme göstermiyor."
                )

                process.kill()
                break

    try:
        process.wait(timeout=10)

    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

    return process.returncode, current_seconds, stderr_tail


# =========================================================
# ANA YAYIN MOTORU
# =========================================================

def start_m3u_stream():

    print("=" * 60)
    print("📺 MAXANİMASYON CANLI YAYIN MOTORU")
    print("=" * 60)

    print(f"🔧 M3U      : {M3U_URL}")
    print(f"🔧 Logo     : {LOGO_URL}")
    print(f"🔧 State    : {STATE_FILE_NAME}")
    print(f"🔧 RTMP     : {RTMP_SERVER}")
    print(f"🔧 Threads  : {DECODER_THREADS}")
    print(f"🔧 Watchdog : {WATCHDOG_TIMEOUT_SECONDS}")

    download_logo()

    current_index, last_seconds, last_url, last_title = (
        get_local_state()
    )

    consecutive_fast_failures = 0

    while True:

        playlist = get_m3u_playlist(M3U_URL)

        if not playlist:

            print("⚠️ Playlist boş veya okunamadı. 15 saniye beklenecek.")
            time.sleep(15)
            continue

        if current_index >= len(playlist):

            print("🔄 Playlist tamamlandı. Baştan başlanıyor.")

            current_index = 0
            last_seconds = 0
            last_url = ""
            last_title = ""

            update_local_state(0, 0, "", "")

        current_item = playlist[current_index]

        target_url = current_item["url"]
        film_title = current_item["title"]

        # Kaynak linki değişti mi?
        if last_seconds > 0 and last_url:

            if target_url != last_url:

                if film_title == last_title:

                    old_seconds = last_seconds

                    last_seconds = max(
                        0,
                        last_seconds - LINK_CHANGE_REWIND_SECONDS
                    )

                    print(
                        f"🔄 Kaynak linki değişti. "
                        f"{old_seconds}s yerine "
                        f"{last_seconds}s'den devam edilecek."
                    )

                else:

                    print("🆕 İçerik değişmiş. Baştan başlanacak.")
                    last_seconds = 0

        last_url = target_url
        last_title = film_title

        write_title_file(film_title)

        print("\n" + "=" * 60)

        print("📺 Maxanimasyon Canlı Aktarım Yayını")
        print(f"🎬 İçerik   : {film_title}")
        print(f"🔢 Sıra     : {current_index + 1}/{len(playlist)}")
        print(f"⏱️ Başlangıç: {format_hms(last_seconds)}")
        print(f"🚀 RTMP     : {RTMP_SERVER}")
        print(f"📡 Kaynak   : {target_url}")

        print("=" * 60)

        print_dashboard(
            film_title,
            current_index,
            len(playlist),
            last_seconds,
            "🟡 Başlatılıyor"
        )

        write_step_summary(
            film_title,
            current_index,
            len(playlist),
            last_seconds,
            "🟡 Başlatılıyor"
        )

        command = build_ffmpeg_command(
            target_url,
            last_seconds
        )

        print(
            f"▶ Yayın: {VIDEO_WIDTH}x{VIDEO_HEIGHT} "
            f"{VIDEO_FPS} FPS / {VIDEO_BITRATE}"
        )

        return_code, current_stream_seconds, stderr_tail = (
            run_ffmpeg(
                command,
                last_seconds,
                current_index,
                film_title,
                len(playlist)
            )
        )

        print(f"⏹️ FFmpeg Return Code: {return_code}")

        if return_code == 0:

            print(f"✅ {film_title} tamamlandı.")

            write_step_summary(
                film_title,
                current_index,
                len(playlist),
                current_stream_seconds,
                "✅ Tamamlandı"
            )

            current_index += 1
            last_seconds = 0
            last_url = ""
            last_title = ""

            consecutive_fast_failures = 0

            update_local_state(
                current_index,
                0,
                "",
                ""
            )

        else:

            print("⚠️ FFmpeg bağlantısı kesildi.")

            if stderr_tail:

                print("\n🧾 SON FFMPEG LOGLARI:")

                for log_line in list(stderr_tail)[-20:]:
                    print("   " + log_line)

            write_step_summary(
                film_title,
                current_index,
                len(playlist),
                current_stream_seconds,
                "🔴 Bağlantı kesildi"
            )

            played_this_attempt = max(
                0,
                current_stream_seconds - last_seconds
            )

            if played_this_attempt < FAST_FAIL_THRESHOLD_SECONDS:

                consecutive_fast_failures += 1

            else:

                consecutive_fast_failures = 0

            if consecutive_fast_failures >= MAX_FAST_FAILURES:

                print(
                    f"❌ {film_title} "
                    f"{MAX_FAST_FAILURES} kez başarısız oldu."
                )

                print("⏭️ Sonraki içeriğe geçiliyor.")

                current_index += 1
                last_seconds = 0
                last_url = ""
                last_title = ""

                consecutive_fast_failures = 0

                update_local_state(
                    current_index,
                    0,
                    "",
                    ""
                )

            else:

                # Başarılı şekilde ilerlenen son konumu sakla.
                last_seconds = max(
                    last_seconds,
                    int(current_stream_seconds)
                )

                update_local_state(
                    current_index,
                    last_seconds,
                    target_url,
                    film_title
                )

        if consecutive_fast_failures:

            retry_delay = min(
                5 * (2 ** consecutive_fast_failures),
                MAX_RETRY_DELAY_SECONDS
            )

        else:

            retry_delay = 5

        print(
            f"⏳ {retry_delay} saniye sonra yeniden denenecek."
        )

        time.sleep(retry_delay)


# =========================================================
# BAŞLAT
# =========================================================

if __name__ == "__main__":

    try:

        start_m3u_stream()

    except KeyboardInterrupt:

        print("\n🛑 Yayın kullanıcı tarafından durduruldu.")
        sys.exit(0)

    except Exception as e:

        print(f"\n❌ KRİTİK HATA: {e}")

        import traceback
        traceback.print_exc()

        sys.exit(1)
