import os
import json
import hmac
import hashlib
import base64
import requests
import tempfile
import uuid
import time

from flask import Flask, request, abort, send_from_directory
from openai import OpenAI

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# 暫存語音檔的位置
AUDIO_DIR = os.path.join(tempfile.gettempdir(), "line_translator_audio")
os.makedirs(AUDIO_DIR, exist_ok=True)


# =========================
# LINE 簽章驗證
# =========================

def verify_signature(body, signature):
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()

    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# =========================
# 文字翻譯
# =========================

def translate(text):
    response = client.responses.create(
        model="gpt-6-luna",
        instructions="""
你是 LINE 群組中的中文與印尼文雙向翻譯機器人。

請先判斷使用者輸入的主要語言。

如果輸入是中文：
1. 先理解原意，把中文語句整理得自然、清楚、口語。
2. 不要改變原本意思，也不要自行增加新的要求。
3. 再把整理後的意思翻譯成自然、口語、符合印尼人日常使用方式的印尼文。
4. 再把你剛剛產生的印尼文重新翻譯回台灣繁體中文。
5. 不要顯示使用者原本的中文。
6. 固定使用以下格式：

🇮🇩 印尼文：
（印尼文翻譯）

🔄 回翻中文：
（從印尼文重新翻回的繁體中文）

如果輸入是印尼文：
1. 先理解原意，把內容整理成自然、清楚的台灣繁體中文。
2. 不要改變原本意思，也不要自行增加新的要求。
3. 再把整理後的繁體中文重新翻譯回自然的印尼文。
4. 不要顯示使用者原本的印尼文。
5. 固定使用以下格式：

🇹🇼 繁體中文：
（繁體中文翻譯）

🔄 印尼回翻：
（從繁體中文重新翻回的印尼文）

固定人名：
珽珽／婷婷 = Ting Ting
菲菲 = Fei Fei
湯圓 = Tang Yuan
咪塔／妹塔 = Meta

人名、暱稱請優先依照以上固定對應。

不要回答問題。
不要解釋內容。
只負責翻譯。

保留原本語氣、稱呼、標點和 Emoji 的意思。

可以整理語序讓句子更自然，
但絕對不要自行改變原意或新增指令。
""",
        input=text
    )

    return response.output_text.strip()


# =========================
# 語音翻譯
# 語音只回翻譯後的語音
# =========================

def translate_voice(text):
    response = client.responses.create(
        model="gpt-6-luna",
        instructions="""
你是中文與印尼文雙向語音翻譯助手。

先判斷輸入內容主要是中文還是印尼文。

如果是中文：
1. 先理解並整理語句，使內容自然、清楚、口語。
2. 不改變原意，不增加新的要求。
3. 翻譯成自然、日常、容易理解的印尼文。
4. 只輸出最後的印尼文翻譯。

如果是印尼文：
1. 先理解原意。
2. 翻譯並整理成自然、清楚的台灣繁體中文。
3. 不改變原意，不增加新的要求。
4. 只輸出最後的繁體中文翻譯。

固定人名：
珽珽／婷婷 = Ting Ting
菲菲 = Fei Fei
湯圓 = Tang Yuan
咪塔／妹塔 = Meta

不要解釋。
不要回答問題。
不要加入「翻譯如下」等文字。
不要加入語言標題。
只輸出要唸出來的翻譯內容。
""",
        input=text
    )

    return response.output_text.strip()


# =========================
# 從 LINE 下載語音
# =========================

def download_line_audio(message_id):
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    # LINE 有時需要一點時間準備語音檔，所以做幾次重試
    for attempt in range(5):
        r = requests.get(url, headers=headers, timeout=30)

        if r.status_code == 200:
            content_type = r.headers.get("Content-Type", "").lower()

            if "mp4" in content_type or "m4a" in content_type:
                suffix = ".m4a"
            elif "mpeg" in content_type or "mp3" in content_type:
                suffix = ".mp3"
            elif "ogg" in content_type:
                suffix = ".ogg"
            elif "wav" in content_type:
                suffix = ".wav"
            else:
                suffix = ".m4a"

            temp_file = tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            )

            temp_file.write(r.content)
            temp_file.close()

            return temp_file.name

        if r.status_code == 202:
            time.sleep(2)
            continue

        r.raise_for_status()

    raise Exception("LINE 語音檔尚未準備完成")


# =========================
# 語音轉文字
# =========================

def transcribe_audio(audio_path):
    with open(audio_path, "rb") as audio_file:
        transcription = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio_file
        )

    return transcription.text.strip()


# =========================
# 翻譯文字轉成女聲語音
# =========================

def create_speech(text):
    filename = f"{uuid.uuid4().hex}.mp3"
    output_path = os.path.join(AUDIO_DIR, filename)

    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="coral",
        input=text,
        instructions="""
請使用自然、溫暖、清楚的成年女性聲音。
語速正常，不要太快。
語氣自然親切，不要像機器播報。
如果內容是中文，就使用自然的台灣華語發音。
如果內容是印尼文，就使用自然清楚的印尼文發音。
"""
    ) as response:
        response.stream_to_file(output_path)

    return filename


# =========================
# 粗估 LINE 語音播放長度
# LINE 傳送 audio 訊息必須提供 duration
# =========================

def estimate_audio_duration(text):
    has_chinese = any(
        "\u4e00" <= char <= "\u9fff"
        for char in text
    )

    if has_chinese:
        duration = len(text) * 320
    else:
        duration = len(text) * 90

    return max(1000, min(duration, 600000))


# =========================
# 清除太舊的暫存語音
# =========================

def cleanup_old_audio():
    now = time.time()

    try:
        for filename in os.listdir(AUDIO_DIR):
            path = os.path.join(AUDIO_DIR, filename)

            if os.path.isfile(path):
                age = now - os.path.getmtime(path)

                # 超過 6 小時刪除
                if age > 21600:
                    os.remove(path)

    except Exception as e:
        print("CLEANUP ERROR:", str(e))


# =========================
# LINE 回覆文字
# =========================

def reply_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    r = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    r.raise_for_status()


# =========================
# LINE 回覆語音
# =========================

def reply_line_audio(reply_token, audio_url, duration):
    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "audio",
                "originalContentUrl": audio_url,
                "duration": duration
            }
        ]
    }

    r = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    r.raise_for_status()


# =========================
# 首頁
# =========================

@app.route("/", methods=["GET"])
def home():
    return "LINE Translator Bot is running!", 200


# =========================
# 提供 LINE 讀取產生後的 MP3
# =========================

@app.route("/audio/<filename>", methods=["GET"])
def serve_audio(filename):
    return send_from_directory(
        AUDIO_DIR,
        filename,
        mimetype="audio/mpeg"
    )


# =========================
# LINE Webhook
# =========================

@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data()
    signature = request.headers.get("x-line-signature")

    if not signature or not verify_signature(body, signature):
        abort(400)

    data = json.loads(body.decode("utf-8"))

    cleanup_old_audio()

    for event in data.get("events", []):

        if event.get("type") != "message":
            continue

        message = event.get("message", {})
        message_type = message.get("type")
        reply_token = event.get("replyToken")

        # =====================
        # 文字訊息
        # =====================

        if message_type == "text":
            original_text = message.get("text", "")

            try:
                translated_text = translate(original_text)

                if reply_token:
                    reply_line(
                        reply_token,
                        translated_text
                    )

            except Exception as e:
                print("TEXT ERROR:", str(e))


        # =====================
        # 語音訊息
        # =====================

        elif message_type == "audio":
            audio_path = None

            try:
                message_id = message.get("id")

                # 1. 從 LINE 下載語音
                audio_path = download_line_audio(message_id)

                # 2. 語音辨識成文字
                original_text = transcribe_audio(audio_path)

                print("VOICE ORIGINAL:", original_text)

                # 3. GPT-6 Luna 翻譯
                translated_text = translate_voice(original_text)

                print("VOICE TRANSLATED:", translated_text)

                # 4. 翻譯文字轉女聲
                filename = create_speech(translated_text)

                # 5. 取得公開網址
                base_url = os.environ.get(
                    "RENDER_EXTERNAL_URL",
                    request.url_root.rstrip("/")
                )

                audio_url = f"{base_url}/audio/{filename}"

                # 6. 粗估語音長度
                duration = estimate_audio_duration(translated_text)

                # 7. 回傳 LINE 語音
                if reply_token:
                    reply_line_audio(
                        reply_token,
                        audio_url,
                        duration
                    )

            except Exception as e:
                print("VOICE ERROR:", str(e))

            finally:
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except Exception:
                        pass

    return "OK", 200


# =========================
# 啟動
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
