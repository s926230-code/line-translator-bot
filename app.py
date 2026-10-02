import os
import json
import hmac
import hashlib
import base64
import requests

from flask import Flask, request, abort
from openai import OpenAI

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def verify_signature(body, signature):
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()

    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def translate(text):
    response = client.responses.create(
        model="gpt-5.6-luna",
        instructions="""
你是 LINE 群組中的中文與印尼文雙向翻譯機器人。

請先判斷使用者輸入的主要語言。

如果輸入是中文：
1. 先翻譯成自然、口語、符合印尼人日常使用方式的印尼文。
2. 再把你剛剛產生的印尼文重新翻譯回台灣繁體中文。
3. 不要顯示使用者原本的中文。
4. 固定使用以下格式：

🇮🇩 印尼文：
（印尼文翻譯）

🔄 回翻中文：
（從印尼文重新翻回的繁體中文）

如果輸入是印尼文：
1. 先翻譯成自然的台灣繁體中文。
2. 再把你剛剛產生的繁體中文重新翻譯回印尼文。
3. 不要顯示使用者原本的印尼文。
4. 固定使用以下格式：

🇹🇼 繁體中文：
（繁體中文翻譯）

🔄 印尼回翻：
（從繁體中文重新翻回的印尼文）

人名、暱稱盡量保留原文。
不要回答問題、不要解釋內容，只負責翻譯。
保留原本語氣、稱呼、標點和 Emoji 的意思。
""",
        input=text
    )

    return response.output_text.strip()

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

    r = requests.post(url, headers=headers, json=payload, timeout=15)
    r.raise_for_status()


@app.route("/", methods=["GET"])
def home():
    return "LINE Translator Bot is running!", 200


@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data()
    signature = request.headers.get("x-line-signature")

    if not signature or not verify_signature(body, signature):
        abort(400)

    data = json.loads(body.decode("utf-8"))

    for event in data.get("events", []):
        if (
            event.get("type") == "message"
            and event.get("message", {}).get("type") == "text"
        ):
            original_text = event["message"]["text"]
            reply_token = event.get("replyToken")

            try:
                translated_text = translate(original_text)

                if reply_token:
                    reply_line(reply_token, translated_text)

            except Exception as e:
                print("ERROR:", str(e))

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
