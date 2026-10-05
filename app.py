import os
import json
import hmac
import hashlib
import base64
import requests
import tempfile
import uuid
import time
import threading

from flask import Flask, request, abort, send_from_directory
from openai import OpenAI

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# 語音暫存資料夾
AUDIO_DIR = os.path.join(
    tempfile.gettempdir(),
    "line_translator_audio"
)

os.makedirs(AUDIO_DIR, exist_ok=True)


# =========================================================
# LINE 簽章驗證
# =========================================================

def verify_signature(body, signature):
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()

    expected = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(
        expected,
        signature
    )


# =========================================================
# 取得 LINE 訊息來源
# 群組 -> groupId
# 多人聊天室 -> roomId
# 私聊 -> userId
# =========================================================

def get_target_id(source):
    source_type = source.get("type")

    if source_type == "group":
        return source.get("groupId")

    if source_type == "room":
        return source.get("roomId")

    return source.get("userId")


# =========================================================
# 文字翻譯
# =========================================================

def translate_text(text):
    response = client.responses.create(
        model="gpt-6-luna",
        instructions="""
你是 LINE 群組中的中文與印尼文雙向翻譯機器人。

請先判斷使用者輸入的主要語言。

如果輸入是中文：
1. 先理解原意。
2. 把中文語句整理得自然、清楚、口語。
3. 不要改變原本意思。
4. 不要自行增加新的要求。
5. 再把整理後的意思翻譯成自然、口語、符合印尼人日常使用方式的印尼文。
6. 再把你產生的印尼文重新翻譯回台灣繁體中文。
7. 不要顯示使用者原本的中文。

固定使用以下格式：

🇮🇩 印尼文：
（印尼文翻譯）

🔄 回翻中文：
（從印尼文重新翻回的繁體中文）

如果輸入是印尼文：
1. 先理解原意。
2. 整理成自然、清楚的台灣繁體中文。
3. 不要改變原本意思。
4. 不要自行增加新的要求。
5. 再把整理後的繁體中文重新翻譯回自然的印尼文。
6. 不要顯示使用者原本的印尼文。

固定使用以下格式：

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


# =========================================================
# 語音專用翻譯
# 只回最終翻譯文字，交給 TTS 念出來
# =========================================================

def translate_voice_text(text):
    response = client.responses.create(
        model="gpt-6-luna",
        instructions="""
你是中文與印尼文雙向語音翻譯助手。

請先判斷輸入主要是中文還是印尼文。

如果是中文：
1. 先理解原意並整理語句。
2. 語句可以變得自然、清楚、口語。
3. 不准改變原本意思。
4. 不准增加新的指令。
5. 翻譯成自然、日常、容易讓印尼人理解的印尼文。
6. 最後只輸出印尼文。

如果是印尼文：
1. 先理解原意。
2. 翻譯成自然、清楚、符合台灣日常說法的繁體中文。
3. 不准改變原意。
4. 不准增加新的內容。
5. 最後只輸出繁體中文。

固定人名：
珽珽／婷婷 = Ting Ting
菲菲 = Fei Fei
湯圓 = Tang Yuan
咪塔／妹塔 = Meta

不要回答問題。
不要解釋。
不要加標題。
不要輸出原文。
只輸出要被語音唸出來的翻譯結果。
""",
        input=text
    )

    return response.output_text.strip()


# =========================================================
# LINE 回覆文字
# =========================================================

def reply_text(reply_token, text):
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

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20
    )

    response.raise_for_status()


# =========================================================
# LINE Push 文字
# 背景處理失敗時用
# =========================================================

def push_text(target_id, text):
    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "to": target_id,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20
    )

    response.raise_for_status()


# =========================================================
# LINE Push 語音
# =========================================================

def push_audio(target_id, audio_url, duration):
    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "to": target_id,
        "messages": [
            {
                "type": "audio",
                "originalContentUrl": audio_url,
                "duration": duration
            }
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=20
    )

    if not response.ok:
        print(
            "LINE PUSH AUDIO ERROR:",
            response.status_code,
            response.text
        )

    response.raise_for_status()


# =========================================================
# 從 LINE 下載語音檔
# =========================================================

def download_line_audio(message_id):
    url = (
        "https://api-data.line.me/v2/bot/message/"
        f"{message_id}/content"
    )

    headers = {
        "Authorization":
        f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    for attempt in range(6):

        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )

        if response.status_code == 200:

            content_type = response.headers.get(
                "Content-Type",
                ""
            ).lower()

            if "ogg" in content_type:
                suffix = ".ogg"

            elif "mpeg" in content_type:
                suffix = ".mp3"

            elif "wav" in content_type:
                suffix = ".wav"

            else:
                # LINE 語音通常可當 m4a / mp4 音訊處理
                suffix = ".m4a"

            temp_file = tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            )

            temp_file.write(response.content)
            temp_file.close()

            return temp_file.name

        # LINE 還在準備內容
        if response.status_code == 202:
            time.sleep(2)
            continue

        raise Exception(
            "下載 LINE 語音失敗："
            f"{response.status_code} "
            f"{response.text}"
        )

    raise Exception("LINE 語音檔準備逾時")


# =========================================================
# OpenAI 語音轉文字
# =========================================================

def transcribe_audio(audio_path):

    with open(audio_path, "rb") as audio_file:

        transcription = (
            client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio_file
            )
        )

    return transcription.text.strip()


# =========================================================
# OpenAI 文字轉女聲
# =========================================================

def create_female_speech(text):

    filename = f"{uuid.uuid4().hex}.mp3"

    output_path = os.path.join(
        AUDIO_DIR,
        filename
    )

    # shimmer + 明確要求成年女性聲線
    with client.audio.speech.with_streaming_response.create(
        model="gpt-4o-mini-tts",
        voice="shimmer",
        input=text,
        instructions="""
使用自然、溫暖、清楚的成年女性聲音。

語速正常。
不要太快。
不要像廣播。
不要像機器朗讀。

如果內容是繁體中文，
使用自然的台灣華語發音。

如果內容是印尼文，
使用自然、清楚的印尼文發音。
"""
    ) as response:

        response.stream_to_file(
            output_path
        )

    return filename


# =========================================================
# LINE audio 訊息需要 duration
# 先用文字長度估算毫秒
# =========================================================

def estimate_duration(text):

    has_chinese = any(
        "\u4e00" <= c <= "\u9fff"
        for c in text
    )

    if has_chinese:
        milliseconds = len(text) * 330
    else:
        milliseconds = len(text) * 95

    return max(
        1000,
        min(milliseconds, 600000)
    )


# =========================================================
# 清除舊 MP3
# =========================================================

def cleanup_old_audio():

    now = time.time()

    try:

        for filename in os.listdir(AUDIO_DIR):

            path = os.path.join(
                AUDIO_DIR,
                filename
            )

            if not os.path.isfile(path):
                continue

            age = now - os.path.getmtime(path)

            # 六小時後刪掉
            if age > 21600:
                os.remove(path)

    except Exception as e:
        print(
            "CLEANUP ERROR:",
            str(e)
        )


# =========================================================
# 背景處理語音
# =========================================================

def process_voice_in_background(
    message_id,
    target_id,
    base_url
):

    audio_path = None

    try:
        print("VOICE STEP 1: download")

        audio_path = download_line_audio(
            message_id
        )

        print("VOICE STEP 2: transcribe")

        original_text = transcribe_audio(
            audio_path
        )

        print(
            "VOICE ORIGINAL:",
            original_text
        )

        if not original_text:
            raise Exception(
                "沒有辨識到語音內容"
            )

        print("VOICE STEP 3: translate")

        translated_text = translate_voice_text(
            original_text
        )

        print(
            "VOICE TRANSLATED:",
            translated_text
        )

        if not translated_text:
            raise Exception(
                "沒有取得翻譯結果"
            )

        print("VOICE STEP 4: speech")

        filename = create_female_speech(
            translated_text
        )

        audio_url = (
            f"{base_url}/audio/{filename}"
        )

        duration = estimate_duration(
            translated_text
        )

        print(
            "VOICE STEP 5: push",
            audio_url
        )

        push_audio(
            target_id,
            audio_url,
            duration
        )

        print("VOICE DONE")

    except Exception as e:

        error_message = str(e)

        print(
            "VOICE BACKGROUND ERROR:",
            error_message
        )

        try:
            push_text(
                target_id,
                "⚠️ 語音翻譯失敗：\n"
                + error_message[:1200]
            )

        except Exception as push_error:

            print(
                "ERROR PUSH FAILED:",
                str(push_error)
            )

    finally:

        if (
            audio_path
            and os.path.exists(audio_path)
        ):

            try:
                os.remove(audio_path)

            except Exception:
                pass


# =========================================================
# 首頁
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return (
        "LINE Translator Bot is running!",
        200
    )


# =========================================================
# 提供產生完成的 MP3 給 LINE 下載
# =========================================================

@app.route(
    "/audio/<filename>",
    methods=["GET"]
)
def serve_audio(filename):

    return send_from_directory(
        AUDIO_DIR,
        filename,
        mimetype="audio/mpeg"
    )


# =========================================================
# LINE Webhook
# =========================================================

@app.route(
    "/callback",
    methods=["POST"]
)
def callback():

    body = request.get_data()

    signature = request.headers.get(
        "x-line-signature"
    )

    if (
        not signature
        or not verify_signature(
            body,
            signature
        )
    ):
        abort(400)

    data = json.loads(
        body.decode("utf-8")
    )

    cleanup_old_audio()

    for event in data.get("events", []):

        if event.get("type") != "message":
            continue

        message = event.get(
            "message",
            {}
        )

        message_type = message.get(
            "type"
        )

        reply_token = event.get(
            "replyToken"
        )

        source = event.get(
            "source",
            {}
        )

        target_id = get_target_id(
            source
        )

        # =================================================
        # 文字
        # =================================================

        if message_type == "text":

            original_text = message.get(
                "text",
                ""
            )

            try:

                translated_text = translate_text(
                    original_text
                )

                if reply_token:

                    reply_text(
                        reply_token,
                        translated_text
                    )

            except Exception as e:

                print(
                    "TEXT ERROR:",
                    str(e)
                )


        # =================================================
        # 語音
        # =================================================

        elif message_type == "audio":

            try:

                message_id = message.get(
                    "id"
                )

                if not target_id:
                    raise Exception(
                        "找不到 LINE 群組或使用者 ID"
                    )

                # 先立即回覆
                # 讓我們確定 LINE 已經收到語音事件
                if reply_token:

                    reply_text(
                        reply_token,
                        "🎤 收到語音，正在翻譯…"
                    )

                # 公開 HTTPS 網址
                base_url = os.environ.get(
                    "RENDER_EXTERNAL_URL"
                )

                if not base_url:

                    base_url = (
                        request.url_root
                        .rstrip("/")
                    )

                # 丟到背景執行
                worker = threading.Thread(
                    target=
                    process_voice_in_background,
                    args=(
                        message_id,
                        target_id,
                        base_url
                    ),
                    daemon=True
                )

                worker.start()

            except Exception as e:

                error_message = str(e)

                print(
                    "VOICE START ERROR:",
                    error_message
                )

                if reply_token:

                    try:

                        reply_text(
                            reply_token,
                            "⚠️ 無法開始語音翻譯：\n"
                            + error_message[:1000]
                        )

                    except Exception:
                        pass

    # 立刻回 200 給 LINE
    return "OK", 200


# =========================================================
# 啟動
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
