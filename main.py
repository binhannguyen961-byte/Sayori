import asyncio
import glob
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
        "Phong cách trò chuyện ngọt ngào, ấm áp, đôi khi có những hành động thân mật, ôm ấp, làm nũng nhưng vẫn giữ được nét đáng yêu, trong sáng đặc trưng của Sayori. "
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

# Đổi tiền tố lệnh thành !S (hỗ trợ cả chữ hoa và chữ thường)
sayori_bot = commands.Bot(command_prefix=['!S', '!s'], intents=intents, help_command=None)

if not discord.opus.is_loaded():
    for lib in ['libopus.so.0', 'libopus.so']:
        try:
            discord.opus.load_opus(lib)
            print(f"-> Đã nạp Opus driver: {lib}")
            break
        except Exception:
            continue

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'source_address': '0.0.0.0',
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://soundcloud.com/',
    }
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

music_queues = {}
last_played_track = {}
# Mặc định Autoplay là False (TẮT)
autoplay_status = {}

SAYORI_COLOR = 0xFFB6C1  # Màu hồng nhạt Doki Doki
SAYORI_THUMBNAIL = "https://i.imgur.com/39J4Q9x.png" # Ảnh Sayori

def create_sayori_embed(title: str, description: str, color=SAYORI_COLOR) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Sayori Music AI • Doki Doki Literature Club", icon_url=SAYORI_THUMBNAIL)
    return embed

async def expand_url(url: str) -> str:
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
    def __init__(self, source, *, data, filepath, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Bài hát')
        self.url = data.get('url')
        self.webpage_url = data.get('webpage_url', '')
        self.uploader = data.get('uploader', 'Nghệ sĩ')
        self.thumbnail = data.get('thumbnail', None)
        self.filepath = filepath

    @classmethod
    async def create_source(cls, data, loop=None):
        loop = loop or asyncio.get_event_loop()
        
        download_opts = {
            'format': 'bestaudio/best',
            'outtmpl': '/tmp/%(id)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
        }
        
        target_url = data.get('webpage_url') or data.get('url')
        if not target_url and 'id' in data:
            target_url = f"https://soundcloud.com/{data.get('id')}"

        def download():
            with yt_dlp.YoutubeDL(download_opts) as ytdl_downloader:
                info = ytdl_downloader.extract_info(target_url, download=True)
                file_id = info.get('id') if info else data.get('id')
                files = glob.glob(f"/tmp/{file_id}.*")
                return files[0] if files else None

        filepath = await loop.run_in_executor(None, download)
        
        if not filepath or not os.path.exists(filepath):
            raise ValueError("DOWNLOAD_FAILED")

        # Định dạng PCM 48kHz Stereo cho Discord
        ffmpeg_options = {'options': '-vn -ar 48000 -ac 2'}
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        audio_source = discord.FFmpegPCMAudio(filepath, executable=ffmpeg_bin, **ffmpeg_options)
        return cls(audio_source, data=data, filepath=filepath)

    @classmethod
    async def fetch_info(cls, query, loop=None):
        loop = loop or asyncio.get_event_loop()
        query = query.strip()
        
        def extract(target_query):
            return ytdl.extract_info(target_query, download=False)

        if query.startswith('http://') or query.startswith('https://'):
            target_url = await expand_url(query)
            try:
                data = await loop.run_in_executor(None, lambda: extract(target_url))
                if data:
                    return data
            except Exception as e:
                print(f"Lỗi extract link: {e}")

        try:
            data = await loop.run_in_executor(None, lambda: extract(f"scsearch1:{query}"))
            if data and 'entries' in data and len(data['entries']) > 0:
                return data['entries'][0]
        except Exception as e:
            print(f"Lỗi scsearch: {e}")

        yt_data = await loop.run_in_executor(None, lambda: extract(f"ytsearch1:{query}"))
        if yt_data and 'entries' in yt_data and len(yt_data['entries']) > 0:
            return yt_data['entries'][0]

        return None

# --- Check xem User có chung Voice Channel với Bot không ---
async def check_same_voice_channel(ctx):
    if not ctx.author.voice:
        embed = create_sayori_embed("⚠️ Chưa vào phòng thoại!", "*xoa xoa tay* Cậu phải vào Voice Channel cùng tớ mới dùng được lệnh này chứ!")
        await ctx.send(embed=embed)
        return False
        
    if not ctx.voice_client:
        embed = create_sayori_embed("⚠️ Sayori chưa vào phòng!", "*ngơ ngác* Tớ chưa vào Voice Channel nào hết nè! Cậu dùng lệnh `!Splay` để gọi tớ nha.")
        await ctx.send(embed=embed)
        return False

    if ctx.author.voice.channel != ctx.voice_client.channel:
        embed = create_sayori_embed("⚠️ Không chung phòng thoại!", f"*phụng phịu* Cậu phải vào phòng **{ctx.voice_client.channel.name}** cùng tớ mới được điều khiển chứ!")
        await ctx.send(embed=embed)
        return False

    return True

# --- 4. Event Handlers ---
@sayori_bot.event
async def on_ready():
    print(f"-> Sayori Online (Railway Docker): {sayori_bot.user}")
    await sayori_bot.change_presence(activity=discord.Game(name="Gõ !Shelp để xem hướng dẫn nha! ☀️🎶"))

@sayori_bot.event
async def on_message(message):
    if message.author == sayori_bot.user:
        return

    # Xử lý lệnh tiền tố !S hoặc !s
    if message.content.lower().startswith('!s'):
        await sayori_bot.process_commands(message)
        return

    if sayori_bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        clean_content = message.content.replace(f'<@{sayori_bot.user.id}>', '').strip()
        
        if not clean_content:
            embed = create_sayori_embed("Sayori chào cậu! ☀️", "*cười tươi* Cậu gọi tớ có chuyện gì thế? Muốn nghe nhạc thì gõ `!Splay [tên bài/link]` nha!")
            await message.channel.send(embed=embed)
            return

        async with message.channel.typing():
            reply = await ask_sayori(clean_content)
            await message.channel.send(reply)

# --- 5. Logic Autoplay & Chuyển bài ---
def cleanup_file(track):
    try:
        if track and hasattr(track, 'filepath') and track.filepath and os.path.exists(track.filepath):
            os.remove(track.filepath)
            print(f"Đã dọn dẹp file: {track.filepath}")
    except Exception as e:
        print(f"Lỗi cleanup file: {e}")

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
            return await YTDLSource.create_source(chosen_data, loop=loop)
    except Exception:
        try:
            yt_rec = await loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch1:{last_track.title} mix", download=False))
            if yt_rec and 'entries' in yt_rec and len(yt_rec['entries']) > 0:
                return await YTDLSource.create_source(yt_rec['entries'][0], loop=loop)
        except Exception as e:
            print(f"Lỗi Autoplay Fallback: {e}")
            
    return None

def play_next(ctx):
    guild_id = ctx.guild.id
    loop = sayori_bot.loop
    
    if guild_id in last_played_track:
        cleanup_file(last_played_track[guild_id])
    
    if guild_id in music_queues and music_queues[guild_id]:
        next_item = music_queues[guild_id].pop(0)
        
        async def process_and_play():
            try:
                if isinstance(next_item, dict):
                    next_source = await YTDLSource.create_source(next_item, loop=loop)
                else:
                    next_source = next_item
                
                last_played_track[guild_id] = next_source
                ctx.voice_client.play(next_source, after=lambda e: play_next(ctx))
                
                embed = create_sayori_embed(
                    "🎶 Đang phát bài tiếp theo",
                    f"**[{next_source.title}]({next_source.webpage_url})**\nNghệ sĩ: `{next_source.uploader}`"
                )
                if next_source.thumbnail:
                    embed.set_thumbnail(url=next_source.thumbnail)
                await ctx.send(embed=embed)
            except Exception as e:
                print(f"Lỗi phát bài tiếp theo: {e}")
                play_next(ctx)
                
        asyncio.run_coroutine_threadsafe(process_and_play(), loop)
        
    elif autoplay_status.get(guild_id, False) and guild_id in last_played_track:
        last_track = last_played_track[guild_id]
        
        async def run_autoplay():
            embed = create_sayori_embed("🔄 Autoplay đang tìm bài...", f"Tớ đang tìm bài hát liên quan đến **{last_track.title}** cho cậu nè...")
            await ctx.send(embed=embed)
            
            auto_track = await fetch_soundcloud_autoplay(last_track, loop)
            
            if auto_track and ctx.voice_client:
                last_played_track[guild_id] = auto_track
                ctx.voice_client.play(auto_track, after=lambda e: play_next(ctx))
                
                embed_auto = create_sayori_embed(
                    "📻 Autoplay: Phát bài gợi ý",
                    f"**[{auto_track.title}]({auto_track.webpage_url})**\nNghệ sĩ: `{auto_track.uploader}`"
                )
                if auto_track.thumbnail:
                    embed_auto.set_thumbnail(url=auto_track.thumbnail)
                await ctx.send(embed=embed_auto)
            else:
                embed_end = create_sayori_embed("☀️ Đã hết danh sách phát", "*ôm lấy tay cậu* Tớ phát hết bài rồi! Cậu gõ `!Splay` để mở bài mới nha!")
                await ctx.send(embed=embed_end)
                
        asyncio.run_coroutine_threadsafe(run_autoplay(), loop)

# --- 6. Music Commands ---

@sayori_bot.command(name='help', aliases=['helps', 'trogiup'])
async def help_command(ctx):
    embed = create_sayori_embed(
        "📚 BẢNG HƯỚNG DẪN BỘ LỆNH SAYORI MUSIC AI",
        "Dưới đây là danh sách các lệnh cậu có thể dùng với tớ nè (Tiền tố: `!S` hoặc `!s`):"
    )
    
    embed.add_field(name="🎶 `!Splay [link/tên bài]`", value="Phát nhạc từ SoundCloud/YouTube hoặc thêm vào hàng chờ.", inline=False)
    embed.add_field(name="⏭️ `!Sskip`", value="Bỏ qua bài hát hiện tại để sang bài tiếp theo.", inline=False)
    embed.add_field(name="⏹️ `!Sstop`", value="Dừng phát nhạc, dọn dẹp danh sách và rời phòng thoại luôn.", inline=False)
    embed.add_field(name="📋 `!Squeue` (hoặc `!Sq`)", value="Xem danh sách các bài hát đang chờ phát.", inline=False)
    embed.add_field(name="📻 `!Sautoplay` (hoặc `!Sap`)", value="Bật/Tắt chế độ tự động phát bài hát liên quan (Mặc định: TẮT).", inline=False)
    embed.add_field(name="📥 `!Sjoin`", value="Mời Sayori vào phòng thoại của cậu.", inline=False)
    embed.add_field(name="📤 `!Sleave`", value="Mời Sayori rời khỏi phòng thoại.", inline=False)
    
    embed.add_field(name="💡 Lưu ý:", value="*Cậu phải ở cùng Voice Channel với tớ mới có thể dùng các lệnh điều khiển như Skip, Stop, Queue, Autoplay nha!*", inline=False)
    
    await ctx.send(embed=embed)

@sayori_bot.command(name='join', aliases=['vao'])
async def join_vc(ctx):
    if not ctx.author.voice:
        embed = create_sayori_embed("⚠️ Lỗi vào phòng", "*ngó quanh* Cậu phải vào Voice Channel trước thì tớ mới vào được chứ!")
        return await ctx.send(embed=embed)
    
    channel = ctx.author.voice.channel
    if ctx.voice_client is not None:
        await ctx.voice_client.move_to(channel)
    else:
        try:
            await channel.connect()
        except Exception as e:
            embed = create_sayori_embed("⚠️ Không vào được phòng", f"*bĩu môi* Tớ không vào được phòng thoại rồi... Lỗi: `{e}`")
            return await ctx.send(embed=embed)
            
    embed = create_sayori_embed("☀️ Đã vào phòng thoại", f"*chạy vào phòng* Hì hì, tớ đã vào phòng **{channel.name}** cùng cậu rồi nè!")
    await ctx.send(embed=embed)

@sayori_bot.command(name='play', aliases=['p', 'hat'])
async def play_music(ctx, *, search: str):
    if not ctx.author.voice:
        embed = create_sayori_embed("⚠️ Chưa vào Voice", "*xoa xoa tay* Cậu vào Voice Channel trước đi rồi tớ mở nhạc cho nghe nha!")
        return await ctx.send(embed=embed)

    if ctx.voice_client is None:
        try:
            await ctx.author.voice.channel.connect()
        except Exception as e:
            embed = create_sayori_embed("⚠️ Không vào được Voice", f"*bĩu môi* Tớ không vào được phòng thoại... Lỗi: `{e}`")
            return await ctx.send(embed=embed)
    else:
        # Nếu đã ở Voice, kiểm tra xem có chung phòng không
        if ctx.author.voice.channel != ctx.voice_client.channel:
            embed = create_sayori_embed("⚠️ Không chung phòng", f"Tớ đang ở phòng **{ctx.voice_client.channel.name}**. Cậu sang đó nghe cùng tớ nha!")
            return await ctx.send(embed=embed)

    guild_id = ctx.guild.id
    if guild_id not in music_queues:
        music_queues[guild_id] = []
    if guild_id not in autoplay_status:
        autoplay_status[guild_id] = False  # Mặc định TẮT

    async with ctx.typing():
        try:
            data = await YTDLSource.fetch_info(search, loop=sayori_bot.loop)
            
            if not data:
                embed = create_sayori_embed("❌ Không tìm thấy", "*bĩu môi* Tớ không tìm thấy bài hát này rồi cậu ơi...")
                return await ctx.send(embed=embed)

            # Xử lý Playlist
            if 'entries' in data and data['entries']:
                entries = [e for e in data['entries'] if e]
                playlist_title = data.get('title', 'Playlist')
                
                if len(entries) > 1:
                    for entry in entries:
                        music_queues[guild_id].append(entry)
                    embed = create_sayori_embed("☁️ Đã thêm Playlist", f"Đã thêm **{len(entries)}** bài hát từ danh sách **{playlist_title}** vào hàng chờ!")
                    await ctx.send(embed=embed)
                    if not ctx.voice_client.is_playing():
                        play_next(ctx)
                    return
                else:
                    data = entries[0]

            try:
                track = await YTDLSource.create_source(data, loop=sayori_bot.loop)
            except Exception:
                song_title = data.get('title') or search
                yt_fallback = await sayori_bot.loop.run_in_executor(
                    None, lambda: ytdl.extract_info(f"ytsearch1:{song_title}", download=False)
                )
                if yt_fallback and 'entries' in yt_fallback and len(yt_fallback['entries']) > 0:
                    data = yt_fallback['entries'][0]
                    track = await YTDLSource.create_source(data, loop=sayori_bot.loop)
                else:
                    raise Exception("Không thể tải và xử lý file audio này.")

            if ctx.voice_client.is_playing():
                music_queues[guild_id].append(data)
                embed = create_sayori_embed(
                    "📋 Đã thêm vào hàng chờ",
                    f"**[{track.title}]({track.webpage_url})**\nNghệ sĩ: `{track.uploader}`"
                )
                if track.thumbnail:
                    embed.set_thumbnail(url=track.thumbnail)
                await ctx.send(embed=embed)
            else:
                last_played_track[guild_id] = track
                ctx.voice_client.play(track, after=lambda e: play_next(ctx))
                
                embed = create_sayori_embed(
                    "🎶 Đang phát",
                    f"**[{track.title}]({track.webpage_url})**\nNghệ sĩ: `{track.uploader}`"
                )
                if track.thumbnail:
                    embed.set_thumbnail(url=track.thumbnail)
                await ctx.send(embed=embed)

        except Exception as e:
            err_details = str(e) if str(e) else type(e).__name__
            embed = create_sayori_embed("❌ Lỗi phát nhạc", f"*ôm đầu bối rối* Lỗi phát nhạc rồi cậu ơi: `{err_details}`")
            await ctx.send(embed=embed)

@sayori_bot.command(name='queue', aliases=['q', 'danhsach'])
async def show_queue(ctx):
    if not await check_same_voice_channel(ctx):
        return

    guild_id = ctx.guild.id
    queue = music_queues.get(guild_id, [])
    current = last_played_track.get(guild_id)

    if not current and not queue:
        embed = create_sayori_embed("📋 Hàng chờ trống", "*ngơ ngác* Danh sách chờ hiện đang trống trơn nè cậu ơi!")
        return await ctx.send(embed=embed)

    description = ""
    if current and ctx.voice_client and ctx.voice_client.is_playing():
        description += f"▶️ **Đang phát:** [{current.title}]({current.webpage_url}) - `{current.uploader}`\n\n"
        
    if queue:
        description += "**📋 Các bài hát tiếp theo:**\n"
        for idx, item in enumerate(queue[:10], start=1):
            title = item.get('title', 'Bài hát') if isinstance(item, dict) else item.title
            uploader = item.get('uploader', 'Nghệ sĩ') if isinstance(item, dict) else item.uploader
            description += f"`{idx}.` **{title}** - `{uploader}`\n"
            
        if len(queue) > 10:
            description += f"\n*...và còn {len(queue) - 10} bài nữa trong hàng chờ!*"

    embed = create_sayori_embed("🎶 DANH SÁCH PHÁT NHẠC", description)
    await ctx.send(embed=embed)

@sayori_bot.command(name='autoplay', aliases=['ap'])
async def toggle_autoplay(ctx):
    if not await check_same_voice_channel(ctx):
        return

    guild_id = ctx.guild.id
    current = autoplay_status.get(guild_id, False)
    autoplay_status[guild_id] = not current
    
    status_str = "BẬT 🟢" if autoplay_status[guild_id] else "TẮT 🔴"
    embed = create_sayori_embed("📻 CHẾ ĐỘ AUTOPLAY", f"Chế độ tự động phát bài gợi ý hiện đã được: **{status_str}**")
    await ctx.send(embed=embed)

@sayori_bot.command(name='skip', aliases=['qua'])
async def skip_music(ctx):
    if not await check_same_voice_channel(ctx):
        return

    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        embed = create_sayori_embed("⏭️ Bỏ qua bài hát", "*gật đầu* Đã bỏ qua bài hát hiện tại cho cậu rồi!")
        await ctx.send(embed=embed)
    else:
        embed = create_sayori_embed("⚠️ Không thể Skip", "*nghiêng đầu* Đâu có bài nào đang phát đâu cậu ơi!")
        await ctx.send(embed=embed)

@sayori_bot.command(name='stop', aliases=['tat'])
async def stop_music(ctx):
    if not await check_same_voice_channel(ctx):
        return

    guild_id = ctx.guild.id
    
    # Dọn dẹp danh sách hàng chờ
    if guild_id in music_queues:
        music_queues[guild_id].clear()
        
    if guild_id in last_played_track:
        cleanup_file(last_played_track[guild_id])
        del last_played_track[guild_id]

    # Dừng phát nhạc và ngắt kết nối voice channel ngay lập tức
    if ctx.voice_client:
        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        await ctx.voice_client.disconnect()

    embed = create_sayori_embed("⏹️ Dừng phát & Rời phòng", "*ôm lấy tay cậu* Tớ đã dừng nhạc, dọn dẹp hàng chờ và rời phòng thoại rồi nha! 👋☀️")
    await ctx.send(embed=embed)

@sayori_bot.command(name='leave', aliases=['dira'])
async def leave_vc(ctx):
    if not await check_same_voice_channel(ctx):
        return

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        embed = create_sayori_embed("👋 Tạm biệt", "*vẫy tay* Tớ ra ngoài trước nha! Khi nào cần nghe nhạc lại gọi tớ `!Splay` nhé! ☀️")
        await ctx.send(embed=embed)

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
