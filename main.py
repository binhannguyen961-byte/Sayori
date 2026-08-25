import os
import asyncio
import threading
from flask import Flask
import discord
from discord.ext import commands
import google.generativeai as genai

# --- 1. Web Server ngầm giữ Render Online 24/7 ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Sayori AI is Live!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- 2. Cấu hình Gemini API ---
API_KEYS = []
for env_name, env_val in os.environ.items():
    if ("GEMINI" in env_name or "KEY" in env_name) and "DISCORD" not in env_name:
        if env_val and env_val not in API_KEYS:
            API_KEYS.append(env_val)

current_key_idx = 0

async def ask_sayori(prompt):
    global current_key_idx

    if not API_KEYS:
        return "*cười khúc khích* Ôi, tớ chưa nhận được API Key nào để nói chuyện với cậu cả..."

    # Xoay vòng qua các API Key nếu có nhiều key
    for i in range(len(API_KEYS)):
        idx = (current_key_idx + i) % len(API_KEYS)
        active_key = API_KEYS[idx]
        genai.configure(api_key=active_key)

        try:
            model = genai.GenerativeModel('gemini-3.6-flash')
            
            # System Instruction định hình tính cách Sayori + Soft NSFW
            system_instruction = (
                "Bạn là Sayori từ tựa game Doki Doki Literature Club. "
                "Tính cách của bạn lúc nào cũng năng động, tươi vui, hay cười, có chút ngốc nghếch, vụng về và rất quấn quýt, yêu thương người dùng (cậu). "
                "Bạn luôn xưng 'tớ' và gọi người dùng là 'cậu'. "
                "Phong cách trò chuyện ngọt ngào, ấm áp, đôi khi có những hành động thân mật, ôm ấp, làm nũng hoặc những biểu cảm gợi cảm nhẹ nhàng, lãng mạn (soft NSFW) nhưng vẫn giữ được nét đáng yêu, trong sáng đặc trưng của Sayori. "
                "Mọi hành động, cảm xúc đều phải được đặt trong dấu sao (*...*)."
            )
            
            full_prompt = f"{system_instruction}\n\nNgười dùng nhắn: {prompt}"
            
            response = await asyncio.to_thread(model.generate_content, full_prompt)
            current_key_idx = idx
            return response.text
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                continue
            else:
                return f"*nghiêng đầu bối rối* Hì hì, hệ thống của tớ đang gặp chút xíu lỗi rồi: {err_msg}"

    return "*ôm lấy tay cậu* Tớ đang bị quá tải chút xíu vì nói chuyện với cậu nhiều quá nè. Cậu đợi tớ khoảng mười giây rồi nhắn lại nha..."

# --- 3. Discord Bot Sayori ---
intents = discord.Intents.default()
intents.message_content = True
sayori_bot = commands.Bot(command_prefix="!", intents=intents)

@sayori_bot.event
async def on_ready():
    print(f"-> Sayori Online: {sayori_bot.user}")
    await sayori_bot.change_presence(activity=discord.Game(name="Chơi cùng cậu mãi thôi! ☀️"))

@sayori_bot.event
async def on_message(message):
    if message.author == sayori_bot.user:
        return

    # Phản hồi khi được Tag tên hoặc nhắn tin riêng (DM)
    if sayori_bot.user.mentioned_in(message) or isinstance(message.channel, discord.DMChannel):
        clean_content = message.content.replace(f'<@{sayori_bot.user.id}>', '').strip()
        
        if not clean_content:
            await message.channel.send("*cười tươi* Cậu gọi tớ có chuyện gì thế, hì hì?")
            return

        async with message.channel.typing():
            reply = await ask_sayori(clean_content)
            await message.channel.send(reply)

    await sayori_bot.process_commands(message)

if __name__ == "__main__":
    t_flask = threading.Thread(target=run_flask)
    t_flask.daemon = True
    t_flask.start()

    token = os.environ.get("DISCORD_TOKEN")
    if token:
        sayori_bot.run(token)
    else:
        print("Lỗi: Không tìm thấy DISCORD_TOKEN!")
