import asyncio
import os
import random
import re
import threading
import urllib.request
from flask import Flask
import discord
from discord.ext import commands
import google.generativeai as genai
import yt_dlp

# --- 1. Web Server ngầm giữ Render Online 24/7 ---
app = Flask(__name__)


@app.route('/')
def home():
  return 'Sayori AI & SoundCloud Music Bot is Live!'


def run_flask():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port)


# --- 2. Cấu hình Gemini API (google.generativeai - Gemini 3.6) ---
API_KEYS = []
for env_name, env_val in os.environ.items():
  if ('GEMINI' in env_name or 'KEY' in env_name) and 'DISCORD' not in env_name:
    if env_val and env_val not in API_KEYS:
      API_KEYS.append(env_val)

current_key_idx = 0


async def ask_sayori(prompt):
  global current_key_idx

  if not API_KEYS:
    return (
        '*cười khúc khích* Ôi, tớ chưa nhận được API Key nào để nói chuyện với'
        ' cậu cả...'
    )

  for i in range(len(API_KEYS)):
    idx = (current_key_idx + i) % len(API_KEYS)
    active_key = API_KEYS[idx]
    genai.configure(api_key=active_key)

    try:
      model = genai.GenerativeModel('gemini-3.6-flash')

      system_instruction = (
          'Bạn là Sayori từ tựa game Doki Doki Literature Club. '
          'Tính cách của bạn lúc nào cũng năng động, tươi vui, hay cười, có chút'
          ' ngốc nghếch, vụng về và rất quấn quýt, yêu thương người dùng (cậu). '
          "Bạn luôn xưng 'tớ' và gọi người dùng là 'cậu'. "
          'Phong cách trò chuyện ngọt ngào, ấm áp, đôi khi có những hành động'
          ' thân mật, ôm ấp, làm nũng hoặc những biểu cảm gợi cảm nhẹ nhàng, lãng'
          ' mạn (soft NSFW) nhưng vẫn giữ được nét đáng yêu, trong sáng đặc'
          ' trưng của Sayori. '
          'Mọi hành động, cảm xúc đều phải được đặt trong dấu sao (*...*).'
      )

      full_prompt = f'{system_instruction}\n\nNgười dùng nhắn: {prompt}'

      response = await asyncio.to_thread(model.generate_content, full_prompt)
      current_key_idx = idx
      return response.text
    except Exception as e:
      err_msg = str(e)
      if '429' in err_msg or 'RESOURCE_EXHAUSTED' in err_msg:
        continue
      else:
        return (
            f'*nghiêng đầu bối rối* Hì hì, hệ thống của tớ đang gặp chút xíu'
            f' lỗi rồi: {err_msg}'
        )

  return (
      '*ôm lấy tay cậu* Tớ đang bị quá tải chút xíu vì nói chuyện với cậu nhiều'
      ' quá nè. Cậu đợi tớ khoảng mười giây rồi nhắn lại nha...'
  )


# --- 3. Discord Bot Sayori & SoundCloud Engine ---
intents = discord.Intents.default()
intents.message_content = True
sayori_bot = commands.Bot(command_prefix='!', intents=intents)

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
}

FFMPEG_OPTIONS = {
    'before_options': (
        '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
    ),
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

music_queues = {}
last_played_track = {}
autoplay_status = {}


def resolve_soundcloud_url(url: str) -> str:
  """Giải mã link rút gọn on.soundcloud.com thành link chuẩn soundcloud.com"""
  url = url.strip()
  if 'on.soundcloud.com' in url:
    try:
      req = urllib.request.Request(
          url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
      )
      res = urllib.request.urlopen(req)
      return res.geturl()
    except Exception as e:
      print(f'Lỗi resolve URL: {e}')
  return url


class YTDLSource(discord.PCMVolumeTransformer):

  def __init__(self, source, *, data, volume=0.5):
    super().__init__(source, volume)
    self.data = data
    self.title = data.get('title', 'Bài hát SoundCloud')
    self.url = data.get('url')
    self.webpage_url = data.get('webpage_url', '')
    self.uploader = data.get('uploader', 'Nghệ sĩ SoundCloud')

  @classmethod
  async def from_data(cls, data, loop=None):
    filename = data['url']
    return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)

  @classmethod
  async def fetch_info(cls, query, loop=None):
    loop = loop or asyncio.get_event_loop()

    if query.startswith('http://') or query.startswith('https://'):
      target_url = await loop.run_in_executor(
          None, lambda: resolve_soundcloud_url(query)
      )
    else:
      target_url = f'scsearch:{query}'

    data = await loop.run_in_executor(
        None, lambda: ytdl.extract_info(target_url, download=False)
    )
    return data


# --- 4. Event Handlers ---
@sayori_bot.event
async def on_ready():
  print(f'-> Sayori Online: {sayori_bot.user}')
  await sayori_bot.change_presence(
      activity=discord.Game(
          name='Mở SoundCloud Playlist & Autoplay cùng cậu! ☁️🎶'
      )
  )


@sayori_bot.event
async def on_message(message):
  if message.author == sayori_bot.user:
    return

  if message.content.startswith('!'):
    await sayori_bot.process_commands(message)
    return

  if sayori_bot.user.mentioned_in(message) or isinstance(
      message.channel, discord.DMChannel
  ):
    clean_content = message.content.replace(
        f'<@{sayori_bot.user.id}>', ''
    ).strip()

    if not clean_content:
      await message.channel.send(
          '*cười tươi* Cậu gọi tớ có chuyện gì thế, hì hì? Muốn nghe SoundCloud'
          ' thì gõ `!play [link SoundCloud/tên bài]` nha!'
      )
      return

    async with message.channel.typing():
      reply = await ask_sayori(clean_content)
      await message.channel.send(reply)


# --- 5. Logic Autoplay SoundCloud ---
async def fetch_soundcloud_autoplay(last_track: YTDLSource, loop):
  try:
    rec_url = f'scsearch5:related to {last_track.title}'
    if last_track.webpage_url:
      rec_url = f'{last_track.webpage_url}/recommended'

    data = await loop.run_in_executor(
        None, lambda: ytdl.extract_info(rec_url, download=False)
    )

    entries = []
    if data:
      if 'entries' in data and data['entries']:
        entries = [e for e in data['entries'] if e]
      elif 'url' in data:
        entries = [data]

    if entries:
      chosen_data = random.choice(entries)
      return await YTDLSource.from_data(chosen_data, loop=loop)
  except Exception as e:
    print(f'Lỗi Autoplay SoundCloud: {e}')

  return None


def play_next(ctx):
  guild_id = ctx.guild.id
  loop = sayori_bot.loop

  if guild_id in music_queues and music_queues[guild_id]:
    next_source = music_queues[guild_id].pop(0)
    last_played_track[guild_id] = next_source

    ctx.voice_client.play(next_source, after=lambda e: play_next(ctx))
    asyncio.run_coroutine_threadsafe(
        ctx.send(
            f'☁️ *nhún nhảy theo SoundCloud* Đang phát bài tiếp theo:'
            f' **{next_source.title}** - `{next_source.uploader}` 🎶'
        ),
        loop,
    )

  elif autoplay_status.get(guild_id, True) and guild_id in last_played_track:
    last_track = last_played_track[guild_id]

    async def run_autoplay():
      await ctx.send(
          f'🔄 *SoundCloud Autoplay*: Tớ đang tự tìm bài hát liên quan đến'
          f' **{last_track.title}** cho cậu nè...'
      )
      auto_track = await fetch_soundcloud_autoplay(last_track, loop)

      if auto_track and ctx.voice_client:
        last_played_track[guild_id] = auto_track
        ctx.voice_client.play(auto_track, after=lambda e: play_next(ctx))
        await ctx.send(
            f'📻 *Autoplay SoundCloud*: Đang phát bài gợi ý tiếp theo:'
            f' **{auto_track.title}** - `{auto_track.uploader}` ☁️✨'
        )
      else:
        await ctx.send(
            '*ôm lấy tay cậu* Tớ đã phát hết bài rồi và không tìm thêm được bài'
            ' gợi ý nữa. Cậu gõ `!play` để bật bài mới nha! ☀️'
        )

    asyncio.run_coroutine_threadsafe(run_autoplay(), loop)


# --- 6. Music Commands ---


@sayori_bot.command(name='join', aliases=['vao'])
async def join_vc(ctx):
  if not ctx.author.voice:
    return await ctx.send(
        '*ngó quanh* Cậu phải vào Voice Channel trước thì tớ mới vào được chứ!'
    )

  channel = ctx.author.voice.channel
  if ctx.voice_client is not None:
    return await ctx.voice_client.move_to(channel)

  await channel.connect()
  await ctx.send(
      f'*chạy vào phòng* Hì hì, tớ vào **{channel.name}** cùng cậu rồi nè! ☀️'
  )


@sayori_bot.command(name='play', aliases=['p', 'hat'])
async def play_music(ctx, *, search: str):
  if not ctx.author.voice:
    return await ctx.send(
        '*xoa xoa tay* Cậu vào Voice Channel trước đi rồi tớ mở SoundCloud cho'
        ' nghe nha!'
    )

  if ctx.voice_client is None:
    await ctx.author.voice.channel.connect()

  guild_id = ctx.guild.id
  if guild_id not in music_queues:
    music_queues[guild_id] = []
  if guild_id not in autoplay_status:
    autoplay_status[guild_id] = True

  async with ctx.typing():
    try:
      data = await YTDLSource.fetch_info(search, loop=sayori_bot.loop)
    except Exception as e:
      return await ctx.send(
          f'*nghiêng đầu* Tớ không lấy được dữ liệu từ link này rồi... Lỗi: {e}'
      )

    if not data:
      return await ctx.send(
          '*bĩu môi* Không tìm thấy bài hát SoundCloud nào cả cậu ơi...'
      )

    # Nếu là Playlist
    if 'entries' in data and data['entries']:
      entries = [e for e in data['entries'] if e]
      playlist_title = data.get('title', 'SoundCloud Playlist')

      first_track = None
      count = 0

      for entry in entries:
        track = await YTDLSource.from_data(entry, loop=sayori_bot.loop)
        if not first_track and not ctx.voice_client.is_playing():
          first_track = track
        else:
          music_queues[guild_id].append(track)
        count += 1

      await ctx.send(
          f'☁️ **ĐÃ THÊM PLAYLIST:** `{playlist_title}` với **{count}** bài vào'
          ' danh sách chờ!'
      )

      if first_track:
        last_played_track[guild_id] = first_track
        ctx.voice_client.play(first_track, after=lambda e: play_next(ctx))
        await ctx.send(
            f'🎶 *Đang phát bài đầu tiên:* **{first_track.title}** -'
            f' `{first_track.uploader}` ☀️'
        )

    # Nếu là Single Track
    else:
      track_data = (
          data['entries'][0]
          if ('entries' in data and data['entries'])
          else data
      )
      track = await YTDLSource.from_data(track_data, loop=sayori_bot.loop)

      if ctx.voice_client.is_playing():
        music_queues[guild_id].append(track)
        await ctx.send(
            f'☁️ *Đã thêm vào danh sách chờ:* **{track.title}** -'
            f' `{track.uploader}`'
        )
      else:
        last_played_track[guild_id] = track
        ctx.voice_client.play(track, after=lambda e: play_next(ctx))
        await ctx.send(
            f'🎶 *Đang phát SoundCloud:* **{track.title}** -'
            f' `{track.uploader}` ☀️'
        )


@sayori_bot.command(name='queue', aliases=['q', 'danhsach'])
async def show_queue(ctx):
  """Hiển thị bài đang phát và danh sách hàng chờ"""
  guild_id = ctx.guild.id
  queue = music_queues.get(guild_id, [])
  current = last_played_track.get(guild_id)

  if not current and not queue:
    return await ctx.send(
        '*ngơ ngác* Danh sách chờ hiện đang trống trơn nè cậu ơi!'
    )

  msg = '🎶 **DANH SÁCH PHÁT SOUNDCLOUD** 🎶\n'

  if current and ctx.voice_client and ctx.voice_client.is_playing():
    msg += f'▶️ **Đang phát:** `{current.title}` - *{current.uploader}*\n\n'

  if queue:
    msg += '**📋 Bài hát tiếp theo:**\n'
    for idx, track in enumerate(
        queue[:10], start=1
    ):  # Hiển thị tối đa 10 bài
      msg += f'`{idx}.` **{track.title}** - *{track.uploader}*\n'

    if len(queue) > 10:
      msg += f'\n*...và {len(queue) - 10} bài hát khác trong hàng chờ!*'
  else:
    msg += (
        '*Hàng chờ hiện tại đã hết! (SoundCloud Autoplay sẽ tự phát bài gợi'
        ' ý tiếp theo)*'
    )

  await ctx.send(msg)


@sayori_bot.command(name='autoplay', aliases=['ap'])
async def toggle_autoplay(ctx):
  guild_id = ctx.guild.id
  current = autoplay_status.get(guild_id, True)
  autoplay_status[guild_id] = not current

  status_str = 'BẬT 🟢' if autoplay_status[guild_id] else 'TẮT 🔴'
  await ctx.send(
      f'📻 Tính năng **SoundCloud Autoplay (Tự phát bài liên quan)** hiện đã:'
      f' **{status_str}**'
  )


@sayori_bot.command(name='skip', aliases=['qua'])
async def skip_music(ctx):
  if ctx.voice_client and ctx.voice_client.is_playing():
    ctx.voice_client.stop()
    await ctx.send(
        '*gật đầu* Đã bỏ qua bài hiện tại! Để tớ chuyển bài tiếp theo nha~'
    )
  else:
    await ctx.send('*nghiêng đầu* Đâu có bài nào đang phát đâu cậu ơi!')


@sayori_bot.command(name='stop', aliases=['tat'])
async def stop_music(ctx):
  guild_id = ctx.guild.id
  if guild_id in music_queues:
    music_queues[guild_id].clear()

  if ctx.voice_client and ctx.voice_client.is_playing():
    ctx.voice_client.stop()
    await ctx.send(
        '*ôm lấy tay cậu* Đã tắt nhạc SoundCloud rồi nè. Cần nghe lại thì bảo'
        ' tớ nha!'
    )
  else:
    await ctx.send('*ngơ ngác* Tớ đâu có mở nhạc đâu nhỉ?')


@sayori_bot.command(name='leave', aliases=['dira'])
async def leave_vc(ctx):
  if ctx.voice_client:
    await ctx.voice_client.disconnect()
    await ctx.send('*vẫy tay* Tớ ra ngoài trước nha! Nhớ gọi tớ đấy! 👋☀️')
  else:
    await ctx.send('*cười xòe* Tớ đâu có ở trong phòng thoại nào đâu nè!')


# --- 7. Start Bot & Server ---
if __name__ == '__main__':
  t_flask = threading.Thread(target=run_flask)
  t_flask.daemon = True
  t_flask.start()

  token = os.environ.get('DISCORD_TOKEN')
  if token:
    sayori_bot.run(token)
  else:
    print('Lỗi: Không tìm thấy DISCORD_TOKEN!')
