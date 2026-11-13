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

# ===================== ORTAK AYARLAR =====================
RTMP_URL = "rtmp://rtmp.zemmedya.com/zem"

GITHUB_STEP_SUMMARY = os.getenv("GITHUB_STEP_SUMMARY")

STREAM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Overlay ayarları
LOGO_OPACITY = float(os.getenv("LOGO_OPACITY", "1.0"))
TEXT_OPACITY = float(os.getenv("TEXT_OPACITY", "1.0"))
BOLD_FONT_PATH = os.getenv("BOLD_FONT_PATH", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

# Decoder thread sayısı
DECODER_THREADS = os.getenv("DECODER_THREADS", "1")

# Watchdog: bu süre boyunca FFmpeg'den ilerleme (time=) gelmezse donmuş sayılır
WATCHDOG_TIMEOUT_SECONDS = int(os.getenv("WATCHDOG_TIMEOUT_SECONDS", "45"))

# Yeniden bağlanma gecikmesi (sabit)
RECONNECT_DELAY_SECONDS = int(os.getenv("RECONNECT_DELAY_SECONDS", "5"))


# ===================== 5 YAYIN KONFİGÜRASYONU =====================
# Her yayın bağımsız. İstediğin kadar ekleyip çıkarabilirsin.
STREAMS = [
    {
        "id": 1,
        "name": "Yayın 1",
        "m3u8": os.getenv("M3U8_URL_1") or "https://v2.catcast.tv/content/50732/seg-27708703-v1-a1/index.m3u8",
        "logo": os.getenv("LOGO_URL_1") or "https://i.hizliresim.com/5uwdmtke.png",
        "referer": os.getenv("STREAM_REFERER_1", "https://Google.com/"),
        "stream_key": os.getenv("STREAM_KEY_1") or "ozgurluk?key=Zemmedya-9397554259",
        "title": os.getenv("STREAM_TITLE_1", "Canlı Yayın 1"),
    },
    {
        "id": 2,
        "name": "Yayın 2",
        "m3u8": os.getenv("M3U8_URL_2") or "https://v2.catcast.tv/content/50733/seg-27708703-v1-a1/index.m3u8",
        "logo": os.getenv("LOGO_URL_2") or "https://i.hizliresim.com/nfphas7u.png",
        "referer": os.getenv("STREAM_REFERER_2", "https://Google.com/"),
        "stream_key": os.getenv("STREAM_KEY_2") or "zmedya?key=Zemmedya-1149358454",
        "title": os.getenv("STREAM_TITLE_2", "Canlı Yayın 2"),
    },
    {
        "id": 3,
        "name": "Yayın 3",
        "m3u8": os.getenv("M3U8_URL_3") or "https://141.94.142.156:3205/stream/play.m3u8",
        "logo": os.getenv("LOGO_URL_3") or "https://i.hizliresim.com/on1bgkjg.png",
        "referer": os.getenv("STREAM_REFERER_3", "https://Google.com/"),
        "stream_key": os.getenv("STREAM_KEY_3") or "z1medya?key=Zemmedya-2873560829",
        "title": os.getenv("STREAM_TITLE_3", "Canlı Yayın 3"),
    },
    {
        "id": 4,
        "name": "Yayın 4",
        "m3u8": os.getenv("M3U8_URL_4") or "https://141.94.142.156:3205/stream/play.m3u8",
        "logo": os.getenv("LOGO_URL_4") or "https://i.hizliresim.com/w0617e2g.png",
        "referer": os.getenv("STREAM_REFERER_4", "https://Google.com/"),
        "stream_key": os.getenv("STREAM_KEY_4") or "Mutlumedya?key=Zemmedya-29797",
        "title": os.getenv("STREAM_TITLE_4", "Canlı Yayın 4"),
    },
    {
        "id": 5,
        "name": "Yayın 5",
        "m3u8": os.getenv("M3U8_URL_5") or "https://141.94.142.156:3205/stream/play.m3u8",
        "logo": os.getenv("LOGO_URL_5") or "https://i.hizliresim.com/w0617e2g.png",
        "referer": os.getenv("STREAM_REFERER_5", "https://Google.com/"),
        "stream_key": os.getenv("STREAM_KEY_5") or "Mutlumedya?key=Zemmedya-292797",
        "title": os.getenv("STREAM_TITLE_5", "Canlı Yayın 5"),
    },
]


# ===================== YARDIMCILAR =====================
def format_hms(total_seconds):
    total_seconds = int(total_seconds)
    hrs = total_seconds // 3600
    mins = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def download_file(url, filename, label, stream_id):
    headers = {'User-Agent': STREAM_USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code == 200 and len(response.content) > 0:
            with open(filename, 'wb') as f:
                f.write(response.content)
            print(f"✅ [{stream_id}] {label} başarıyla indirildi.")
            return True
        else:
            print(f"⚠️ [{stream_id}] {label} indirilemedi (HTTP {response.status_code}).")
    except Exception as e:
        print(f"⚠️ [{stream_id}] {label} indirme hatası: {e}")
    return False


def write_title_file(title, filename):
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(title)
    except Exception as e:
        print(f"⚠️ Başlık dosyası yazma hatası: {e}")


def print_dashboard(stream_id, title, seconds, status="🟢 Yayında"):
    print("┌" + "─" * 58 + "┐")
    print(f"│ 🆔 Yayın No       : {stream_id:<36} │")
    print(f"│ 🎬 İçerik         : {title[:36]:<36} │")
    print(f"│ ⏱️  Geçen Süre     : {format_hms(seconds):<36} │")
    print(f"│ 📡 Durum          : {status:<36} │")
    print("└" + "─" * 58 + "┘")


def write_step_summary(streams_state):
    """
    streams_state: {stream_id: {"title":..., "seconds":..., "status":..., "name":...}}
    GitHub Actions Step Summary'yi tüm yayınlar için tek tablo olarak yazar.
    """
    if not GITHUB_STEP_SUMMARY:
        return
    try:
        lines = [
            "## 📺 Canlı Yayın Durumu (5 Bağımsız Yayın)\n",
            "| # | Yayın | ⏱️ Süre | 📡 Durum |",
            "|---|---|---|---|",
        ]
        for sid in sorted(streams_state.keys()):
            s = streams_state[sid]
            lines.append(
                f"| {sid} | {s.get('name', '-')} | {format_hms(s.get('seconds', 0))} | {s.get('status', '-')} |"
            )
        lines.append(f"\n🕒 Son güncelleme: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        with open(GITHUB_STEP_SUMMARY, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        print(f"⚠️ Step summary yazma hatası: {e}")


# ===================== FFMPEG KOMUTU =====================
def build_ffmpeg_command(cfg, has_logo, logo_file, title_file):
    rtmp_server = f"{RTMP_URL}/{cfg['stream_key']}"

    headers_arg = (
        f"User-Agent: {STREAM_USER_AGENT}\r\n"
        f"Referer: {cfg['referer']}\r\n"
        f"Origin: {cfg['referer'].rstrip('/')}\r\n"
    )

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

    input_args = input_options + ['-i', cfg['m3u8']]

    title_drawtext = (
        f"drawtext=textfile='{title_file}':reload=1:fontfile='{BOLD_FONT_PATH}':"
        f"fontcolor=white@{TEXT_OPACITY}:fontsize=25:"
        f"x=80:y=main_h-th-55"
    )

    if has_logo:
        logo_inputs = ['-i', logo_file]
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
            rtmp_server
        ]
    )
    return command


# ===================== TEK YAYIN ÇALIŞTIRICI =====================
def run_single_stream(cfg, state, state_lock):
    """
    Tek bir yayını sonsuza kadar çalıştırır. Kendi içinde retry yapar.
    Diğer yayınlardan tamamen bağımsızdır.
    """
    sid = cfg['id']
    logo_file = f"logo_{sid}.png"
    title_file = f"title_{sid}.txt"

    print("=" * 60)
    print(f"📺 [{sid}] {cfg['name']} başlatılıyor")
    print(f"🔧 [{sid}] M3U8 : {cfg['m3u8']}")
    print(f"🔧 [{sid}] RTMP : {RTMP_URL}/{cfg['stream_key']}")
    print("=" * 60)

    # Logo ve başlık dosyalarını hazırla
    download_file(cfg['logo'], logo_file, 'Logo', sid)
    write_title_file(cfg['title'], title_file)

    while True:
        # Durumu güncelle
        with state_lock:
            state[sid] = {
                "name": cfg['name'],
                "title": cfg['title'],
                "seconds": 0,
                "status": "🟡 Bağlanılıyor",
            }

        has_logo = os.path.exists(logo_file) and os.path.getsize(logo_file) > 0
        command = build_ffmpeg_command(cfg, has_logo, logo_file, title_file)

        print(f"▶ [{sid}] FFmpeg başlatıldı.")
        try:
            process = subprocess.Popen(
                command,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
        except Exception as e:
            print(f"❌ [{sid}] FFmpeg başlatma hatası: {e}")
            time.sleep(RECONNECT_DELAY_SECONDS)
            continue

        last_dashboard_time = time.time()
        current_stream_seconds = 0.0
        stderr_tail = deque(maxlen=30)
        last_progress_time = [time.time()]

        # Watchdog (bu yayına özel)
        def _watchdog(proc=process, progress_ref=last_progress_time):
            while proc.poll() is None:
                time.sleep(5)
                if time.time() - progress_ref[0] > WATCHDOG_TIMEOUT_SECONDS:
                    print(f"🚨 [{sid}] Watchdog: {WATCHDOG_TIMEOUT_SECONDS}s ilerleme yok, sonlandırılıyor.")
                    try:
                        proc.kill()
                    except Exception as e:
                        print(f"⚠️ [{sid}] Watchdog kill hatası: {e}")
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

                    # Durum güncelle
                    with state_lock:
                        state[sid] = {
                            "name": cfg['name'],
                            "title": cfg['title'],
                            "seconds": current_stream_seconds,
                            "status": "🟢 Yayında",
                        }

                    if now - last_dashboard_time > 30:
                        print_dashboard(sid, cfg['title'], current_stream_seconds)
                        last_dashboard_time = now

        retcode = process.returncode

        if retcode == 0:
            print(f"ℹ️ [{sid}] FFmpeg normal sonlandı, yeniden bağlanılıyor...")
        elif retcode == -6:
            print(f"⚠️ [{sid}] FFmpeg SIGABRT.")
        elif retcode == -9:
            print(f"⚠️ [{sid}] FFmpeg watchdog tarafından sonlandırıldı (donma).")
        else:
            print(f"⚠️ [{sid}] FFmpeg çöktü (Return Code: {retcode}).")

        if stderr_tail:
            print(f"🧾 [{sid}] Son FFmpeg logları:")
            for t in list(stderr_tail)[-5:]:
                print(f"   [{sid}] {t}")

        with state_lock:
            state[sid] = {
                "name": cfg['name'],
                "title": cfg['title'],
                "seconds": current_stream_seconds,
                "status": "🔴 Bağlantı koptu",
            }

        print(f"⚠️ [{sid}] {RECONNECT_DELAY_SECONDS} saniye sonra yeniden bağlanılıyor...")
        time.sleep(RECONNECT_DELAY_SECONDS)


# ===================== ANA GİRİŞ =====================
def main():
    print("=" * 60)
    print(f"📺 {len(STREAMS)} Bağımsız Canlı Yayın Başlatılıyor")
    print("=" * 60)

    # Global durum (thread'ler arası paylaşımlı)
    state = {}
    state_lock = threading.Lock()

    # Her yayın için bağımsız thread
    threads = []
    for cfg in STREAMS:
        t = threading.Thread(
            target=run_single_stream,
            args=(cfg, state, state_lock),
            daemon=True,
            name=f"stream-{cfg['id']}"
        )
        t.start()
        threads.append(t)
        time.sleep(1)  # Başlangıçta küçük bir staggered start

    # Ana thread: periyodik olarak step summary yaz + genel durum logla
    try:
        while True:
            time.sleep(30)
            with state_lock:
                snapshot = dict(state)
            write_step_summary(snapshot)

            # Genel durum satırı
            alive = sum(1 for t in threads if t.is_alive())
            print(f"📊 Genel Durum: {alive}/{len(STREAMS)} yayın thread'i aktif.")
            for sid in sorted(snapshot.keys()):
                s = snapshot[sid]
                print(
                    f"   [{sid}] {s.get('status','-'):<20} "
                    f"süre={format_hms(s.get('seconds', 0))}"
                )
    except KeyboardInterrupt:
        print("🛑 Kullanıcı tarafından durduruldu.")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("🛑 Kullanıcı tarafından durduruldu.")
        sys.exit(0)
