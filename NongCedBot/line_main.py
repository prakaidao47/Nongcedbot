import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv

from nongced_service import answer_with_nongced

load_dotenv()

CHANNEL_SECRET = os.getenv("CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN", "")
PORT = int(os.getenv("PORT", 5000))

if not CHANNEL_SECRET or not CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ต้องตั้ง CHANNEL_SECRET และ CHANNEL_ACCESS_TOKEN ใน .env")

app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.get("/")
def health():
    return "OK", 200

def _handle_line_request():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400, "Invalid signature")
    return "OK", 200

@app.post("/callback")
def callback():
    return _handle_line_request()

@app.post("/webhook")
def webhook():
    return _handle_line_request()

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event: MessageEvent):
    user_id = event.source.user_id
    text = (event.message.text or "").strip()
    reply = answer_with_nongced(user_id, text)
    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply[:4900]))
    except Exception as e:
        print("Reply Error:", e)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
