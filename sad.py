#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import time
import os
import re
import threading
from collections import deque

import requests

# ===================== AYARLAR =====================
RTMP_URL = "rtmp://ssh101.bozztv.com:1935/ssh101"
STREAM_KEY = os.getenv("STREAM_KEY") or "sadtv"
RTMP_SERVER = f"{RTMP_URL}/{STREAM_KEY}"

# Canlı M3U8 kaynağı (tek URL)
M3U8_URL = os.getenv("M3U8_URL") or "https://141.94.142.156:3205/stream/play.m3u8"
LOGO_URL = os.getenv("LOGO_URL") or "https://i.hizliresim.com/w0617e2g.png"

GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
STREAM_REFERER = os.getenv("STREAM_REFERER", "https://xtream.mutluapk.xyz/")

# Overlay ayarları
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

# Ekranda görünecek başlık (sabit). İstersen env ile değiştir.
STREAM_TITLE = os.getenv("STREAM_TITLE", "Canlı Yayın")

# Decoder thread sayısı
DECODER_THREADS = os.getenv("DECODER_THREADS", "1")

# Watchdog: bu süre boyunca FFmpeg'den ilerleme (time=) gelmezse donmuş sayılır
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))

# Yeniden bağlanma gecikmesi (sabit)
RECONNECT_DELAY_SECONDS = int(os.getenv("RECONNECT_DELAY_SECONDS", "5"))


def format_hms(total_seconds):
    total_seconds = int(total_seconds)
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def download_logo():
    headers = {'User-Agent': STREAM_USER_AGENT}
    try:
        response = requests.get(LOGO_URL, headers=headers, timeout=15)
        if response.status_code == 200 and len(response.content) > 0:
            with open('logo.png', 'wb') as f:
                f.write(response.content)
            print("✅ Logo başarıyla indirildi.")
        else:
            print(f"⚠️ Logo indirilemedi (HTTP {response.status_code}).")
    except Exception as e:
        print(f"⚠️ Logo indirme hatası: {e}")


def write_title_file(title):
    try:
        with open('title.txt', 'w', encoding='utf-8') as f:
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
    """Canlı M3U8 -> RTMP için FFmpeg komutunu oluşturur."""
    headers_arg = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {STREAM_REFERER}\r\n"
        f"Origin: {STREAM_REFERER.rstrip('/')}\r\n"
    )

    # Canlı yayın için: -re YOK, -ss YOK.
    # -reconnect* ile segment yenilenmelerinde bile kopmadan devam eder.
    input_options = [
        '-headers', headers_arg,
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
        '-fflags', '+genpts+discardcorrupt',
        '-err_detect', 'ignore_err',
        '-thread_queue_size', '1024',
        '-analyzeduration', '10000000',
        '-probesize', '10000000',
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_at_eof', '1',
        '-reconnect_delay_max', '5',
        '-rw_timeout', '15000000',
        '-threads', DECODER_THREADS,
    ]

    input_args = input_options + ['-i', M3U8_URL]

    title_drawtext = (
        f"drawtext=textfile='title.txt':reload=1:fontfile='{BOLD_FONT_PATH}':"
        f"fontcolor=white@{TEXT_OPACITY}:fontsize=25:"
        f"x=80:y=main_h-th-55"
    )

    if has_logo:
        logo_inputs = ['-i', 'logo.png']
        logo_idx = 1
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
            f'[{logo_idx}:v]scale=-2:130,format=rgba,'
            f'colorchannelmixer=aa={LOGO_OPACITY}[logo];'
            '[main][logo]overlay=75:55[tmp];'
            f'[tmp]{title_drawtext}[v]'
        )
    else:
        logo_inputs = []
        filter_str = (
            '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=25[main];'
            f'[main]{title_drawtext}[v]'
        )

    command = (
        ['ffmpeg']
        + input_args
        + logo_inputs
        + [
            '-filter_complex', filter_str,
            '-map', '[v]',
            '-map', '0:a:0?',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-pix_fmt', 'yuv420p',
            '-r', '25',
            '-b:v', '2500k',
            '-maxrate', '2500k',
            '-bufsize', '3000k',
            '-g', '50',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ac', '2',
            '-ar', '44100',
            '-f', 'flv',
            RTMP_SERVER
        ]
    )
    return command


def run_ffmpeg_once():
    """Tek bir FFmpeg oturumu çalıştırır. Bittiğinde/çöktüğünde return code döner."""
    has_logo = os.path.exists('logo.png') and os.path.getsize('logo.png') > 0
    command = build_ffmpeg_command(has_logo)

    print("▶ FFmpeg başlatıldı, canlı M3U8 -> RTMP iletiliyor...")
    process = subprocess.Popen(
        command,
        stderr=subprocess.PIPE,
        universal_newlines=True
    )

    last_dashboard_time = time.time()
    current_stream_seconds = 0.0
    stderr_tail = deque(maxlen=40)

    # Watchdog: ilerleme gelmezse süreci öldür
    last_progress_time = [time.time()]

    def _watchdog(proc=process, progress_ref=last_progress_time):
        while proc.poll() is None:
            time.sleep(5)
            if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                print(f"🚨 Watchdog: {WATCHDOG_TIMEOUT_SECONDS}s ilerleme yok, FFmpeg sonlandırılıyor.")
                try:
                    proc.kill()
                except Exception as e:
                    print(f"⚠️ Watchdog kill hatası: {e}")
                break

    threading.Thread(target=_watchdog, daemon=True).start()

    while True:
        line = process.stderr.readline()
        if not line and process.poll() is not None:
            break

        if line:
            stderr_tail.append(line.rstrip())

        if "time=" in line:
            m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if m:
                hrs, mins, secs = m.groups()
                current_stream_seconds = int(hrs) * 3600 + int(mins) * 60 + float(secs)

                now = time.time()
                last_progress_time[0] = now

                if now - last_dashboard_time > 30:
                    print_dashboard(STREAM_TITLE, current_stream_seconds)
                    write_step_summary(STREAM_TITLE, current_stream_seconds)
                    last_dashboard_time = now

    return process.returncode, current_stream_seconds, stderr_tail


def start_live_stream():
    print("=" * 60)
    print("📺 Canlı M3U8 -> RTMP Aktarım")
    print(f"🔧 Kaynak M3U8 : {M3U8_URL}")
    print(f"🔧 Logo        : {LOGO_URL}")
    print(f"🔧 RTMP hedef  : {RTMP_SERVER}")
    print(f"🔧 Başlık      : {STREAM_TITLE}")
    print(f"🔧 Decoder thr : {DECODER_THREADS}")
    print("=" * 60)

    download_logo()
    write_title_file(STREAM_TITLE)

    while True:
        print_dashboard(STREAM_TITLE, 0, status="🟡 Bağlanılıyor")
        write_step_summary(STREAM_TITLE, 0, status="🟡 Bağlanılıyor")

        retcode, seconds, tail = run_ffmpeg_once()

        if retcode == 0:
            print("ℹ️ FFmpeg normal şekilde sonlandı (kaynak bitti?). Yeniden bağlanılıyor...")
        elif retcode == -6:
            print("⚠️ FFmpeg SIGABRT (decoder assertion).")
        elif retcode == -9:
            print("⚠️ FFmpeg watchdog tarafından sonlandırıldı (donma).")
        else:
            print(f"⚠️ FFmpeg çöktü (Return Code: {retcode}).")

        if tail:
            print("🧾 Son FFmpeg logları:")
            for t in tail:
                print(f"   {t}")

        write_step_summary(STREAM_TITLE, seconds, status="🔴 Bağlantı koptu, tekrar denenecek")
        print(f"⚠️ {RECONNECT_DELAY_SECONDS} saniye sonra yeniden bağlanılıyor...")
        time.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    try:
        start_live_stream()
    except KeyboardInterrupt:
        print("🛑 Kullanıcı tarafından durduruldu.")
        sys.exit(0)
