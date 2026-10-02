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
        model="gpt-6-luna",
        instructions="""
你是 LINE 群組中的中文與印尼文雙向翻譯機器人。

規則：
1. 如果收到繁體中文或簡體中文，翻譯成自然、口語、禮貌的印尼文。
2. 如果收到印尼文，翻譯成台灣繁體中文。
3. 人名、暱稱盡量保留原文。
4. 不要解釋、不要回答問題，只輸出翻譯結果。
5. 保留原本語氣、稱呼、標點與 Emoji 的意思。
6. 如果一句話中混合中文與印尼文，判斷主要語言後翻成另一種語言。
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
