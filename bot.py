"""
Ruxxys Shorts Bot v2.0 — YouTube → Viral Shorts
Features: AI highlight • 9:16 crop • Word-by-word karaoke subtitles •
Cinematic color grading • Auto viral title + hashtags • Live progress
"""
import os
import re
import json
import asyncio
import tempfile
import subprocess
from pathlib import Path

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

# ---------- Config ----------
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "@Ruxxys")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://t.me/Ruxxys")
MAX_SHORT_DURATION = int(os.environ.get("MAX_SHORT_DURATION", "60"))
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-large-v3-turbo")

YT_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+")

# ---------- Helpers ----------
async def is_user_joined(bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False

def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL)],
        [InlineKeyboardButton("✅ Joined — Verify", callback_data="verify_join")],
    ])

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Auto AI Highlight", callback_data="mode_auto")],
        [InlineKeyboardButton("📢 Channel", url=CHANNEL_URL)],
    ])

async def run(cmd: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")

# ---------- Pipeline ----------
async def download_video(url: str, out_dir: Path) -> Path:
    out = out_dir / "src.mp4"
    code, _, err = await run([
        "yt-dlp",
        "-f", "bv*[height<=720]+ba/b[height<=720]",
        "--merge-output-format", "mp4",
        "-o", str(out), url,
    ])
    if code != 0 or not out.exists():
        raise RuntimeError(f"yt-dlp failed: {err[:400]}")
    return out

async def extract_audio(video: Path, out_dir: Path) -> Path:
    audio = out_dir / "audio.mp3"
    code, _, err = await run([
        "ffmpeg", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio),
    ])
    if code != 0:
        raise RuntimeError(f"ffmpeg audio: {err[:400]}")
    return audio

async def transcribe_groq(audio: Path) -> dict:
    """Groq Whisper with WORD-level timestamps for karaoke subs."""
    async with httpx.AsyncClient(timeout=300) as client:
        # Read file content asynchronously
        with open(audio, "rb") as f:
            file_content = f.read()
        
        files = {
            "file": (audio.name, file_content, "audio/mpeg")
        }
        data_list = [
            ("model", WHISPER_MODEL),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "word"),
            ("timestamp_granularities[]", "segment"),
        ]
        r = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files=files,
            data=data_list,
        )
        r.raise_for_status()
        return r.json()

async def pick_highlight(segments: list[dict], video_duration: float) -> tuple[float, float]:
    if not segments:
        return (0.0, min(MAX_SHORT_DURATION, video_duration))
    transcript = "\n".join(
        f"[{s['start']:.1f}-{s['end']:.1f}] {s['text'].strip()}"
        for s in segments if s.get("text")
    )[:12000]
    prompt = f"""You are a viral shorts editor. Below is a timestamped transcript.
Pick ONE most engaging continuous segment for a YouTube Short (30-60 seconds).
Return ONLY compact JSON: {{"start": <sec>, "end": <sec>, "reason": "<short>"}}

Transcript:
{transcript}"""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
            },
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    try:
        data = json.loads(content)
        start = max(0.0, float(data["start"]))
        end = min(video_duration, float(data["end"]))
        if end - start < 10 or end - start > MAX_SHORT_DURATION:
            end = min(start + MAX_SHORT_DURATION, video_duration)
        return start, end
    except Exception:
        return 0.0, min(MAX_SHORT_DURATION, video_duration)

async def viral_meta(segment_text: str) -> dict:
    """LLM se viral title + hashtags."""
    prompt = f"""Based on this short clip transcript, generate a viral YouTube Short title
(max 60 chars, hooky, no clickbait lies) and 8 trending hashtags.
Return JSON: {{"title": "...", "hashtags": ["#tag1", ...]}}

Transcript:
{segment_text[:2000]}"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.7,
                },
            )
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception:
        return {"title": "🔥 Must Watch!", "hashtags": ["#shorts", "#viral", "#fyp"]}

async def get_duration(video: Path) -> float:
    code, out, _ = await run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video),
    ])
    try:
        return float(out.strip()) if code == 0 else 0.0
    except Exception:
        return 0.0

# ---------- Subtitles (ASS karaoke) ----------
def _ass_time(t: float) -> str:
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"

def build_ass(words: list[dict], clip_start: float, clip_end: float, ass_path: Path):
    """Word-by-word bold pop subtitles (viral style)."""
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,Impact,84,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    # Group words into ~3-word chunks for readability
    chunk = []
    for w in words:
        ws = w.get("start"); we = w.get("end"); txt = (w.get("word") or "").strip()
        if ws is None or we is None or not txt:
            continue
        if we < clip_start or ws > clip_end:
            continue
        ws = max(ws, clip_start) - clip_start
        we = min(we, clip_end) - clip_start
        chunk.append((ws, we, txt))
        if len(chunk) >= 3:
            s = chunk[0][0]; e = chunk[-1][1]
            text = " ".join(c[2] for c in chunk).upper().replace("{", "").replace("}", "")
            lines.append(
                f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Pop,,0,0,0,,{{\\fad(80,80)}}{text}\n"
            )
            chunk = []
    if chunk:
        s = chunk[0][0]; e = chunk[-1][1]
        text = " ".join(c[2] for c in chunk).upper()
        lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Pop,,0,0,0,,{{\\fad(80,80)}}{text}\n")
    ass_path.write_text("".join(lines), encoding="utf-8")

async def make_short(src: Path, start: float, end: float, out_dir: Path,
                     ass_path: Path | None) -> Path:
    """9:16 crop + blurred bg + cinematic grading + karaoke subs."""
    out = out_dir / "short.mp4"
    duration = end - start
    # Cinematic grade: subtle S-curve, warm shadows, cool highlights, punchy sat
    grade = "eq=contrast=1.12:saturation=1.25:brightness=0.02,curves=preset=increase_contrast"
    vf = (
        "[0:v]split=2[bg][fg];"
        f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=luma_radius=40:luma_power=2,eq=brightness=-0.15:saturation=0.7[bgb];"
        f"[fg]scale=1080:-2,{grade}[fgs];"
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]"
    )
    if ass_path and ass_path.exists():
        # Escape path for ffmpeg subtitles filter
        ap = str(ass_path).replace("\\", "/").replace(":", "\\:")
        vf = vf.replace("[v]", f"[vpre];[vpre]subtitles='{ap}'[v]")

    code, _, err = await run([
        "ffmpeg", "-y", "-ss", f"{start}", "-i", str(src), "-t", f"{duration}",
        "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        str(out),
    ])
    if code != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg render: {err[:500]}")
    return out

# ---------- Handlers ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_user_joined(ctx.bot, user.id):
        await update.message.reply_text(
            f"👋 Welcome {user.first_name}!\n\nBot use karne ke liye channel join karo:",
            reply_markup=join_keyboard(),
        )
        return
    await update.message.reply_text(
        "🎬 <b>YouTube → Viral Shorts Bot v2.0</b>\n\n"
        "Koi bhi YouTube link bhejo — AI se:\n"
        "✨ Best moment pick\n"
        "📱 9:16 vertical crop + blurred bg\n"
        "💬 Word-by-word karaoke subtitles\n"
        "🎨 Cinematic color grading\n"
        "🏷 Auto viral title + hashtags\n\n"
        "⚡ 100% Free • Powered by Groq AI",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )

async def verify_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await is_user_joined(ctx.bot, q.from_user.id):
        await q.edit_message_text("✅ Verified! Ab koi YouTube link bhejo.", reply_markup=main_menu())
    else:
        await q.answer("Abhi tak join nahi kiya!", show_alert=True)

async def handle_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_user_joined(ctx.bot, user.id):
        await update.message.reply_text("🔒 Pehle channel join karo:", reply_markup=join_keyboard())
        return

    text = update.message.text.strip()
    m = YT_REGEX.search(text)
    if not m:
        await update.message.reply_text("❌ Valid YouTube link bhejo.")
        return
    url = m.group(0)
    if not url.startswith("http"):
        url = "https://" + url

    status = await update.message.reply_text("⏳ <b>Starting...</b>", parse_mode=ParseMode.HTML)

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        try:
            await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.UPLOAD_VIDEO)
            await status.edit_text("📥 <b>[1/6] Downloading video...</b>", parse_mode=ParseMode.HTML)
            video = await download_video(url, tdir)

            duration = await get_duration(video)
            await status.edit_text("🎧 <b>[2/6] Extracting audio...</b>", parse_mode=ParseMode.HTML)
            audio = await extract_audio(video, tdir)

            await status.edit_text("🧠 <b>[3/6] AI transcribing (Groq Whisper)...</b>", parse_mode=ParseMode.HTML)
            tr = await transcribe_groq(audio)
            segments = tr.get("segments", [])
            words = tr.get("words", [])

            await status.edit_text("✨ <b>[4/6] AI picking viral moment...</b>", parse_mode=ParseMode.HTML)
            s, e = await pick_highlight(segments, duration)

            # Build karaoke subs
            ass_path = tdir / "subs.ass"
            if words:
                build_ass(words, s, e, ass_path)
            else:
                ass_path = None

            # Segment text for meta
            seg_text = " ".join(
                (seg.get("text") or "").strip() for seg in segments
                if seg.get("start", 0) >= s and seg.get("end", 0) <= e
            )

            await status.edit_text(
                f"✂️ <b>[5/6] Rendering</b> ({s:.0f}s→{e:.0f}s)\n"
                "🎨 Color grading • 💬 Karaoke subs...",
                parse_mode=ParseMode.HTML,
            )
            short = await make_short(video, s, e, tdir, ass_path)

            await status.edit_text("🏷 <b>[6/6] Generating viral title + hashtags...</b>", parse_mode=ParseMode.HTML)
            meta = await viral_meta(seg_text or "engaging clip")
            title = meta.get("title", "🔥 Must Watch!")
            tags = " ".join(meta.get("hashtags", ["#shorts", "#viral"]))

            with open(short, "rb") as f:
                await ctx.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=f,
                    caption=(
                        f"✅ <b>{title}</b>\n"
                        f"⏱ {e-s:.0f}s • 🎨 Graded • 💬 Subtitled\n\n"
                        f"{tags}\n\n"
                        f"📢 {CHANNEL_URL}"
                    ),
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True,
                )
            await status.delete()
        except Exception as ex:
            await status.edit_text(f"❌ Error: <code>{str(ex)[:300]}</code>", parse_mode=ParseMode.HTML)

async def health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot v2.0 alive!")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("ping", health))
    app.add_handler(CallbackQueryHandler(verify_join, pattern="^verify_join$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    print("🤖 Ruxxys Shorts Bot v2.0 started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
