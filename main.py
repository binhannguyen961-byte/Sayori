import asyncio
import os
import random
import threading
import aiohttp
from flask import Flask
import discord
from discord.ext import commands
from google import genai
from google.genai import types
import imageio_ffmpeg
import yt_dlp

# --- 1. Web Server ngầm giữ Railway / Healthcheck Online ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Sayori AI & SoundCloud Music Bot is Live on Railway!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- 2. Cấu hình Google GenAI SDK ---
API_KEYS = []
for env_name, env_val in os.environ.items():
    if ('GEMINI' in env_name or 'KEY' in env_name) and 'DISCORD' not in env_name:
        if env_val and env_val not in API_KEYS:
            API_KEYS.append(env_val)

current_key_idx = 0

async def ask_sayori(prompt):
    global current_key_idx
    if not API_KEYS:
        return "*cười khúc khích* Ôi, tớ chưa nhận được API Key nào để nói chuyện với cậu cả..."
    
    system_instruction = (
        "Bạn là Sayori từ tựa game Doki Doki Literature Club. "
        "Tính cách của bạn lúc nào cũng năng động, tươi vui, hay cười, có chút ngốc nghếch, vụng về và rất quấn quýt, yêu thương người dùng (cậu). "
        "Bạn luôn xưng 'tớ' và gọi người dùng là 'cậu'. "
        "Phong cách trò chuyện ngọt ngào, ấm áp, đôi khi có những hành động thân mật, ôm ấp, làm nũng hoặc những biểu cảm gợi cảm nhẹ nhàng, lãng mạn (soft NSFW) nhưng vẫn giữ được nét đáng yêu, trong sáng đặc trưng của Sayori. "
        "Mọi hành động, cảm xúc đều phải được đặt trong dấu sao (*...*)."
    )

    for i in range(len(API_KEYS)):
        idx = (current_key_idx + i) % len(API_KEYS)
        active_key = API_KEYS[idx]
        
        try:
            client = genai.Client(api_key=active_key)
            response = await asyncio.to_thread(
                client.models.generate_content,
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction
                )
            )
            current_key_idx = idx
            return response.text
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                continue
            else:
                return f"*nghiêng đầu bối rối* Hì hì, hệ thống của tớ đang gặp chút xíu lỗi rồi: {err_msg}"

    return "*ôm lấy tay cậu* Tớ đang bị quá tải chút xíu vì nói chuyện với cậu nhiều quá nè. Cậu đợi tớ khoảng mười giây rồi nhắn lại nha..."

# --- 3. Cấu hình Discord Bot & Engine Âm Thanh ---
intents = discord.Intents.default()
intents.message_content = True
sayori_bot = commands.Bot(command_prefix='!', intents=intents)

if not discord.opus.is_loaded():
    try:
        discord.opus.load_opus('libopus.so.0')
    except Exception as e:
        print(f"Lỗi nạp Opus driver: {e}")

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'source_address': '0.0.0.0',
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://soundcloud.com/',
    }
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 10000000 -analyzeduration 15000000',
    'options': '-vn -loglevel error'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

music_queues = {}
last_played_track = {}
autoplay_status = {}

async def expand_url(url: str) -> str:
    """Giải mã link rút gọn on.soundcloud.com sang URL SoundCloud gốc"""
    url = url.strip()
    if "on.soundcloud.com" in url or "soundcloud.app.goo.gl" in url:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, allow_redirects=True, timeout=10) as response:
                    final_url = str(response.url)
                    return final_url.split('?')[0] if '?' in final_url else final_url
        except Exception as e:
            print(f"Lỗi expand URL: {e}")
    return url

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Bài hát')
        self.url = data.get('url')
        self.webpage_url = data.get('webpage_url', '')
        self.uploader = data.get('uploader', 'Nghệ sĩ')

    @classmethod
    def create_source(cls, data):
        stream_url = data.get('url')
        if not stream_url and 'formats' in data:
            for fmt in reversed(data['formats']):
                if fmt.get('url'):
                    stream_url = fmt['url']
                    break

        if not stream_url:
            raise ValueError("Chặn luồng audio (Stream URL Blocked)")

        data['url'] = stream_url
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        return cls(discord.FFmpegPCMAudio(stream_url, executable=ffmpeg_bin, **FFMPEG_OPTIONS), data=data)

    @classmethod
    async def fetch_info(cls, query, loop=None):
        loop = loop or asyncio.get_event_loop()
        query = query.strip()
        
        # BƯỚC 1: Thử trích xuất direct từ SoundCloud
        if query.startswith('http://') or query.startswith('https://'):
            target_url = await expand_url(query)
            try:
                data = await loop.run_in_executor(None, lambda: ytdl.extract_info(target_url, download=False))
                if data and (data.get('url') or data.get('entries') or 'formats' in data):
                    return data
            except Exception as e:
                print(f"Lỗi direct SoundCloud extract: {e}")

        # BƯỚC 2: Fallback 1 - scsearch SoundCloud
        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(f"scsearch1:{query}", download=False))
            if data and 'entries' in data and len(data['entries']) > 0:
                return data['entries'][0]
        except Exception as e:
            print(f"Lỗi scsearch SoundCloud: {e}")

        # BƯỚC 3: Fallback 2 - Chuyển sang YouTube search (Bypass IP Block hoàn toàn)
        print("Đang fallback sang YouTube search...")
        yt_data = await loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch1:{query}", download=False))
        if yt_data and 'entries' in yt_data and len(yt_data['entries']) > 0:
            return yt_data['entries'][0]

        return None

# --- 4. Event Handlers ---
@sayori_bot.event
async def on_ready():
    print(f"-> Sayori Online (Railway): {sayori_bot.user}")
    await sayori_bot.change_presence(activity=discord.Game(name="Mở SoundCloud & Autoplay cùng cậu! ☁️🎶"))

@sayori_bot.event
async def on_message(message):
    if message.author == sayori_bot.user:
        return

    if message.content.startswith('!'):
        await sayori_bot.process_commands(message)
        return

    if sayori_bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        clean_content = message.content.replace(f'<@{sayori_bot.user.id}>', '').strip()
        
        if not clean_content:
            await message.channel.send("*cười tươi* Cậu gọi tớ có chuyện gì thế, hì hì? Muốn nghe SoundCloud thì gõ `!play [link SoundCloud/tên bài]` nha!")
            return

        async with message.channel.typing():
            reply = await ask_sayori(clean_content)
            await message.channel.send(reply)

# --- 5. Logic Autoplay & Chuyển bài ---
async def fetch_soundcloud_autoplay(last_track, loop):
    try:
        rec_url = f"scsearch5:related to {last_track.title}"
        if last_track.webpage_url and "soundcloud.com" in last_track.webpage_url:
            rec_url = f"{last_track.webpage_url}/recommended"
            
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(rec_url, download=False))
        
        entries = []
        if data:
            if 'entries' in data and data['entries']:
                entries = [e for e in data['entries'] if e]
            elif 'url' in data:
                entries = [data]
                
        if entries:
            chosen_data = random.choice(entries)
            return YTDLSource.create_source(chosen_data)
    except Exception:
        # Nếu SoundCloud Autoplay xịt, fallback tự tìm bài liên quan trên YouTube
        try:
            yt_rec = await loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch1:{last_track.title} mix", download=False))
            if yt_rec and 'entries' in yt_rec and len(yt_rec['entries']) > 0:
                return YTDLSource.create_source(yt_rec['entries'][0])
        except Exception as e:
            print(f"Lỗi Autoplay Fallback: {e}")
            
    return None

def play_next(ctx):
    guild_id = ctx.guild.id
    loop = sayori_bot.loop
    
    if guild_id in music_queues and music_queues[guild_id]:
        next_item = music_queues[guild_id].pop(0)
        
        async def process_and_play():
            try:
                if isinstance(next_item, dict):
                    next_source = YTDLSource.create_source(next_item)
                else:
                    next_source = next_item
                
                last_played_track[guild_id] = next_source
                ctx.voice_client.play(next_source, after=lambda e: play_next(ctx))
                
                await ctx.send(f"☁️ *nhún nhảy theo điệu nhạc* Đang phát bài tiếp theo: **{next_source.title}** - `{next_source.uploader}` 🎶")
            except Exception as e:
                print(f"Lỗi phát bài tiếp theo: {e}")
                play_next(ctx)
                
        asyncio.run_coroutine_threadsafe(process_and_play(), loop)
        
    elif autoplay_status.get(guild_id, True) and guild_id in last_played_track:
        last_track = last_played_track[guild_id]
        
        async def run_autoplay():
            await ctx.send(f"🔄 *Autoplay*: Tớ đang tự tìm bài hát liên quan đến **{last_track.title}** cho cậu nè...")
            auto_track = await fetch_soundcloud_autoplay(last_track, loop)
            
            if auto_track and ctx.voice_client:
                last_played_track[guild_id] = auto_track
                ctx.voice_client.play(auto_track, after=lambda e: play_next(ctx))
                await ctx.send(f"📻 *Autoplay*: Đang phát bài gợi ý tiếp theo: **{auto_track.title}** - `{auto_track.uploader}` ☁️✨")
            else:
                await ctx.send("*ôm lấy tay cậu* Tớ đã phát hết bài rồi và không tìm thêm được bài gợi ý nữa. Cậu gõ `!play` để bật bài mới nha! ☀️")
                
        asyncio.run_coroutine_threadsafe(run_autoplay(), loop)

# --- 6. Commands ---
@sayori_bot.command(name='join', aliases=['vao'])
async def join_vc(ctx):
    if not ctx.author.voice:
        return await ctx.send("*ngó quanh* Cậu phải vào Voice Channel trước thì tớ mới vào được chứ!")
    
    channel = ctx.author.voice.channel
    if ctx.voice_client is not None:
        return await ctx.voice_client.move_to(channel)
        
    try:
        await channel.connect()
        await ctx.send(f"*chạy vào phòng* Hì hì, tớ vào **{channel.name}** cùng cậu rồi nè! ☀️")
    except Exception as e:
        await ctx.send(f"*bĩu môi* Tớ không vào được phòng thoại rồi... Lỗi: `{e}`")

@sayori_bot.command(name='play', aliases=['p', 'hat'])
async def play_music(ctx, *, search: str):
    if not ctx.author.voice:
        return await ctx.send("*xoa xoa tay* Cậu vào Voice Channel trước đi rồi tớ mở nhạc cho nghe nha!")

    if ctx.voice_client is None:
        try:
            await ctx.author.voice.channel.connect()
        except Exception as e:
            return await ctx.send(f"*bĩu môi* Tớ không vào được phòng thoại... Lỗi: `{e}`")

    guild_id = ctx.guild.id
    if guild_id not in music_queues:
        music_queues[guild_id] = []
    if guild_id not in autoplay_status:
        autoplay_status[guild_id] = True

    async with ctx.typing():
        try:
            data = await YTDLSource.fetch_info(search, loop=sayori_bot.loop)
            
            if not data:
                return await ctx.send("*bĩu môi* Tớ không tìm thấy bài hát này rồi cậu ơi...")

            # Xử lý Playlist
            if 'entries' in data and data['entries']:
                entries = [e for e in data['entries'] if e]
                playlist_title = data.get('title', 'Playlist')
                
                if len(entries) > 1:
                    for entry in entries:
                        music_queues[guild_id].append(entry)
                    await ctx.send(f"☁️ **ĐÃ THÊM PLAYLIST:** `{playlist_title}` với **{len(entries)}** bài vào hàng chờ!")
                    if not ctx.voice_client.is_playing():
                        play_next(ctx)
                    return
                else:
                    data = entries[0]

            # Khởi tạo nguồn audio (Thử SoundCloud stream, nếu bị IP Railway chặn sẽ tự tìm bản YouTube)
            try:
                track = YTDLSource.create_source(data)
            except Exception:
                song_title = data.get('title') or search
                print(f"Railway IP bị chặn stream SoundCloud, tự tìm bản YouTube cho: {song_title}")
                yt_fallback = await sayori_bot.loop.run_in_executor(
                    None, lambda: ytdl.extract_info(f"ytsearch1:{song_title}", download=False)
                )
                if yt_fallback and 'entries' in yt_fallback and len(yt_fallback['entries']) > 0:
                    data = yt_fallback['entries'][0]
                    track = YTDLSource.create_source(data)
                else:
                    raise Exception("Không thể phát luồng audio từ nguồn này.")

            if ctx.voice_client.is_playing():
                music_queues[guild_id].append(data)
                await ctx.send(f"☁️ *Đã thêm vào danh sách chờ:* **{track.title}** - `{track.uploader}`")
            else:
                last_played_track[guild_id] = track
                ctx.voice_client.play(track, after=lambda e: play_next(ctx))
                await ctx.send(f"🎶 *Đang phát:* **{track.title}** - `{track.uploader}` ☀️")

        except Exception as e:
            err_details = str(e) if str(e) else type(e).__name__
            await ctx.send(f"*ôm đầu bối rối* Lỗi phát nhạc rồi cậu ơi: `{err_details}`")

@sayori_bot.command(name='queue', aliases=['q', 'danhsach'])
async def show_queue(ctx):
    guild_id = ctx.guild.id
    queue = music_queues.get(guild_id, [])
    current = last_played_track.get(guild_id)

    if not current and not queue:
        return await ctx.send("*ngơ ngác* Danh sách chờ hiện đang trống trơn nè cậu ơi!")

    msg = "🎶 **DANH SÁCH PHÁT NHẠC** 🎶\n"
    
    if current and ctx.voice_client and ctx.voice_client.is_playing():
        msg += f"▶️ **Đang phát:** `{current.title}` - *{current.uploader}*\n\n"
        
    if queue:
        msg += "**📋 Bài hát tiếp theo:**\n"
        for idx, item in enumerate(queue[:10], start=1):
            title = item.get('title', 'Bài hát') if isinstance(item, dict) else item.title
            uploader = item.get('uploader', 'Nghệ sĩ') if isinstance(item, dict) else item.uploader
            msg += f"`{idx}.` **{title}** - *{uploader}*\n"
            
        if len(queue) > 10:
            msg += f"\n*...và {len(queue) - 10} bài hát khác trong hàng chờ!*"
  
    await ctx.send(msg)

@sayori_bot.command(name='autoplay', aliases=['ap'])
async def toggle_autoplay(ctx):
    guild_id = ctx.guild.id
    current = autoplay_status.get(guild_id, True)
    autoplay_status[guild_id] = not current
    
    status_str = "BẬT 🟢" if autoplay_status[guild_id] else "TẮT 🔴"
    await ctx.send(f"📻 Tính năng **Autoplay (Tự phát bài liên quan)** hiện đã: **{status_str}**")

@sayori_bot.command(name='skip', aliases=['qua'])
async def skip_music(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("*gật đầu* Đã bỏ qua bài hiện tại!")
    else:
        await ctx.send("*nghiêng đầu* Đâu có bài nào đang phát đâu cậu ơi!")

@sayori_bot.command(name='stop', aliases=['tat'])
async def stop_music(ctx):
    guild_id = ctx.guild.id
    if guild_id in music_queues:
        music_queues[guild_id].clear()
        
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("*ôm lấy tay cậu* Đã tắt nhạc rồi nè!")
    else:
        await ctx.send("*ngơ ngác* Tớ đâu có mở nhạc đâu nhỉ?")

@sayori_bot.command(name='leave', aliases=['dira'])
async def leave_vc(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("*vẫy tay* Tớ ra ngoài trước nha! 👋☀️")
    else:
        await ctx.send("*cười xòe* Tớ đâu có ở trong phòng thoại nào đâu nè!")

# --- 7. Khởi chạy Server & Bot ---
if __name__ == '__main__':
    t_flask = threading.Thread(target=run_flask)
    t_flask.daemon = True
    t_flask.start()

    token = os.environ.get('DISCORD_TOKEN')
    if token:
        sayori_bot.run(token)
    else:
        print("Lỗi: Không tìm thấy DISCORD_TOKEN!")
