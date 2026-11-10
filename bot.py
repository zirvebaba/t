#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import time
import os
import re
import requests
import threading
from collections import deque

# ===================== AYARLAR =====================
# RTMP hedefi
RTMP_URL = "rtmp://s3.catcast.tv/live"
STREAM_KEY = os.getenv("STREAM_KEY") or "twst42?key=27429_50878_140ab6913463d66a"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

# Doğrudan canlı HLS (.m3u8) akış URL'si — M3U playlist DEĞİL
HLS_URL = os.getenv("HLS_URL") or (
    "http://145.239.37.125:20458/foramuzik/foramuzik/playlist.m3u8"
)

# Logo
LOGO_URL = os.getenv("LOGO_URL") or "https://i.hizliresim.com/gns4rf0d.png"

# GitHub Actions step summary (opsiyonel)
GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
STREAM_REFERER = "https://ha.vixolity.com/"
STREAM_ORIGIN = "https://ha.vixolity.com"

# Logo ve yazı opaklık ayarları (0.0 - 1.0)
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv(
    "BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
)

# Decoder thread sayısı (H.264 "non-existing PPS" hatalarını azaltır)
DECODER_THREADS = os.getenv("DECODER_THREADS", "1")

# Watchdog: bu kadar saniye ilerleme (time=) gelmezse FFmpeg donmuş kabul edilir
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "60"))

# Yeniden bağlanma parametreleri
MAX_RETRY_DELAY_SECONDS = int(os.getenv("MAX_RETRY_DELAY_SECONDS", "60"))
BASE_RETRY_DELAY_SECONDS = int(os.getenv("BASE_RETRY_DELAY_SECONDS", "5"))

# Yayın başlığı (drawtext'te gösterilecek)
STREAM_TITLE = os.getenv("STREAM_TITLE", "Canlı Yayın")


# ===================== YARDIMCI FONKSİYONLAR =====================
def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def download_logo():
    headers = {"User-Agent": STREAM_USER_AGENT}
    try:
        response = requests.get(LOGO_URL, headers=headers, timeout=15)
        if response.status_code == 200 and len(response.content) > 0:
            with open("logo.png", "wb") as f:
                f.write(response.content)
            print("✅ Logo başarıyla indirildi.")
        else:
            print(f"⚠️ Logo indirilemedi (HTTP {response.status_code}).")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")


def write_title_file(title):
    try:
        with open("title.txt", "w", encoding="utf-8") as f:
            f.write(title)
    except Exception as e:
        print(f"⚠️ Başlık dosyası yazma hatası: {e}")


def print_dashboard(title, seconds, status="🟢 Yayında"):
    print("┌" + "─" * 58 + "┐")
    print(f"│ 🎬 İçerik         : {title[:36]:<36} │")
    print(f"│ ⏱️  Geçen Süre     : {format_hms(seconds):<36} │")
    print(f"│ 📡 Durum          : {status:<36} │")
    print("└" + "─" * 58 + "┘")


def write_step_summary(title, seconds, status="🟢 Yayında"):
    if not GITHUB_STEP_SUMMARY:
        return
    try:
        content = (
            "## 📺 Canlı Yayın Durumu\n\n"
            "| Alan | Değer |\n"
            "|---|---|\n"
            f"| 🎬 İçerik | {title} |\n"
            f"| ⏱️ Geçen süre | {format_hms(seconds)} (sa:dk:sn) |\n"
            f"| 📡 Durum | {status} |\n"
            f"| 🕒 Son güncelleme | {time.strftime('%Y-%m-%d %H:%M:%S')} |\n"
        )
        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️ Step summary yazma hatası: {e}")


def build_ffmpeg_command(has_logo):
    """Canlı HLS akışını RTMP'ye aktaran FFmpeg komutunu oluşturur."""

    headers_arg = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {STREAM_REFERER}\r\n"
        f"Origin: {STREAM_ORIGIN}\r\n"
    )

    # Canlı HLS için input seçenekleri — -ss YOK (canlı yayında geri sarma yok)
    input_options = [
        "-re",  # Gerçek zamanlı okuma
        "-headers", headers_arg,
        "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
        "-err_detect", "ignore_err",
        "-fflags", "+genpts+discardcorrupt",
        "-thread_queue_size", "1024",
        "-analyzeduration", "20000000",  # Anahtar kare analizi için artırıldı
        "-probesize", "20000000",
        "-live_start_index", "-1",       # Canlı kenardan başla
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
        "-rw_timeout", "15000000",
        "-threads", DECODER_THREADS,
        "-i", HLS_URL,
    ]

    # Filtre zinciri
    title_drawtext = (
        f"drawtext=textfile='title.txt':reload=1:"
        f"fontfile='{BOLD_FONT_PATH}':"
        f"fontcolor=white@{TEXT_OPACITY}:fontsize=25:"
        f"x=80:y=main_h-th-55"
    )

    logo_inputs = []
    logo_input_index = 1
    if has_logo:
        logo_inputs = ["-i", "logo.png"]
        filter_str = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];"
            f"[{logo_input_index}:v]scale=-2:130,format=rgba,"
            f"colorchannelmixer=aa={LOGO_OPACITY}[logo1];"
            "[main][logo1]overlay=75:55[tmp];"
            f"[tmp]{title_drawtext}[v]"
        )
    else:
        filter_str = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];"
            f"[main]{title_drawtext}[v]"
        )

    command = (
        ["ffmpeg"]
        + input_options
        + logo_inputs
        + [
            "-dn",  # timed_id3 gibi veri akışlarını çıktıya dahil etme
            "-filter_complex", filter_str,
            "-map", "[v]",
            "-map", "0:a:0?",   # Ses akışı opsiyonel
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-r", "25",
            "-b:v", "2500k",
            "-maxrate", "2500k",
            "-bufsize", "3000k",
            "-g", "50",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",
            "-ar", "44100",
            "-max_interleave_delta", "1000000",  # A/V senkron güvenliği
            "-f", "flv",
            RTMP_SERVER,
        ]
    )
    return command


# ===================== ANA DÖNGÜ =====================
def start_stream():
    print(f"🔧 HLS kaynağı      : {HLS_URL}")
    print(f"🔧 Logo             : {LOGO_URL}")
    print(f"🔧 RTMP hedefi      : {RTMP_SERVER}")
    print(f"🔧 Decoder thread   : {DECODER_THREADS}")
    print(f"🔧 Watchdog timeout : {WATCHDOG_TIMEOUT_SECONDS}s")

    download_logo()
    write_title_file(STREAM_TITLE)

    has_logo = os.path.exists("logo.png") and os.path.getsize("logo.png") > 0
    if has_logo:
        print("✅ Logo hazır, yayına eklenecek.")
    else:
        print("ℹ️ Logo yok, yayın logosuz devam edecek.")

    consecutive_failures = 0

    while True:
        print("=" * 60)
        print("📺 Canlı HLS → RTMP Aktarımı (1080p 25fps @ 2500k)")
        print(f"🎬 İçerik: {STREAM_TITLE}")
        print("=" * 60)

        print_dashboard(STREAM_TITLE, 0, status="🟡 Başlatılıyor")
        write_step_summary(STREAM_TITLE, 0, status="🟡 Başlatılıyor")

        command = build_ffmpeg_command(has_logo)
        print("▶ FFmpeg başlatıldı...")

        process = subprocess.Popen(
            command, stderr=subprocess.PIPE, universal_newlines=True
        )

        stderr_tail = deque(maxlen=50)
        last_progress_time = [time.time()]
        stream_start_time = time.time()

        def _watchdog(proc=process, progress_ref=last_progress_time):
            while proc.poll() is None:
                time.sleep(5)
                if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                    print(
                        f"🚨 Watchdog: {WATCHDOG_TIMEOUT_SECONDS} saniyedir "
                        f"ilerleme yok, FFmpeg donmuş. Süreç öldürülüyor."
                    )
                    try:
                        proc.kill()
                    except Exception as e:
                        print(f"⚠️ Watchdog kill hatası: {e}")
                    break

        watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        watchdog_thread.start()

        last_dashboard_time = time.time()

        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break

            if line:
                stderr_tail.append(line.rstrip())

            if "time=" in line:
                time_match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                if time_match:
                    hrs, mins, secs = time_match.groups()
                    played_seconds = (
                        int(hrs) * 3600 + int(mins) * 60 + float(secs)
                    )
                    now = time.time()
                    last_progress_time[0] = now

                    if now - last_dashboard_time > 30:
                        print_dashboard(STREAM_TITLE, played_seconds)
                        write_step_summary(STREAM_TITLE, played_seconds)
                        last_dashboard_time = now

        # Süreç sona erdi
        total_seconds = int(time.time() - stream_start_time)
        returncode = process.returncode

        if returncode == 0:
            print("✅ FFmpeg normal şekilde kapandı (kaynak bitti veya durduruldu).")
            write_step_summary(
                STREAM_TITLE, total_seconds, status="✅ Bitti"
            )
            # Canlı yayında kaynak "bitmez" ama normal kapanma olduysa kısa bekle
            time.sleep(5)
            consecutive_failures = 0
            continue

        # Hata durumu
        if returncode == -6:
            print("⚠️ FFmpeg SIGABRT (decoder assertion) ile çöktü.")
        elif returncode == -9:
            print("⚠️ FFmpeg watchdog tarafından öldürüldü (donma).")

        print(f"⚠️ Yayın koptu (Return Code: {returncode}).")

        if stderr_tail:
            print("🧾 FFmpeg son log satırları:")
            for tail_line in stderr_tail:
                print(f"   {tail_line}")

        write_step_summary(
            STREAM_TITLE, total_seconds, status="🔴 Bağlantı koptu, tekrar denenecek"
        )

        # Hızlı çökme tespiti
        if total_seconds < 20:
            consecutive_failures += 1
        else:
            consecutive_failures = 0

        # Exponential backoff
        if consecutive_failures > 0:
            retry_delay = min(
                BASE_RETRY_DELAY_SECONDS * (2 ** consecutive_failures),
                MAX_RETRY_DELAY_SECONDS,
            )
        else:
            retry_delay = BASE_RETRY_DELAY_SECONDS

        print(f"⚠️ {retry_delay} saniye sonra tekrar bağlanılıyor...")
        time.sleep(retry_delay)


if __name__ == "__main__":
    start_stream()
