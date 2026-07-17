# 🎬 Ruxxys Shorts Bot

YouTube video link → AI-powered 9:16 Short (Telegram Bot).

**Stack:** Python + yt-dlp + ffmpeg + Groq Whisper + Groq LLaMA 3.3

---

## 🚀 Deploy on Render (FREE)

### Step 1: Push to GitHub
1. GitHub pe new repo banao (private/public — koi bhi)
2. Iss folder ka saara code repo me push kar do

### Step 2: Render pe deploy
1. https://render.com pe signup (GitHub se login)
2. Dashboard → **New +** → **Blueprint**
3. Apna GitHub repo select karo
4. Render `render.yaml` auto-detect karega
5. **Environment Variables** me ye 2 dalo:
   - `TELEGRAM_BOT_TOKEN` = (BotFather se mila token)
   - `GROQ_API_KEY` = (https://console.groq.com/keys se)
6. **Apply** dabao → 2-3 min me deploy ho jayega ✅

### Step 3: Test
Telegram me apne bot ko `/start` bhejo → koi YouTube link bhejo → Short milega!

---

## ⚠️ Free Tier Notes

- Render Free Worker service **sleep nahi hota** (Web Service sleep hota hai, Worker nahi)
- Agar Web Service use kar rahe ho toh **UptimeRobot** se `/health` ping karwao har 5 min
- RAM: 512MB (Groq Whisper cloud me chalta hai, local nahi — isliye kaafi hai)

## 💰 Cost = ₹0

- Render Free Worker: Free
- Groq API (Whisper + LLaMA): Free tier — 14,400 requests/day
- Telegram Bot: Free
- yt-dlp + ffmpeg: Open source

## 🔧 Local Test

```bash
pip install -r requirements.txt
sudo apt install ffmpeg   # ya `brew install ffmpeg` Mac pe
cp .env.example .env      # values fill karo
export $(cat .env | xargs)
python bot.py
```

## 📢 Channel

Force-join channel: [@Ruxxys](https://t.me/Ruxxys)

Channel/branding change karna ho toh `render.yaml` me `FORCE_JOIN_CHANNEL` aur `CHANNEL_URL` update kar do.
