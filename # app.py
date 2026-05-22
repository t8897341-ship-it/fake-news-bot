import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 環境變數
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SERP_API_KEY = os.environ.get("SERP_API_KEY")

line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)


def search_sources(query):
    """使用 SerpAPI 搜尋相關來源"""

    url = "https://serpapi.com/search"

    params = {
        "q": query[:100],
        "api_key": SERP_API_KEY,
        "num": 5,
        "hl": "zh-tw",
        "gl": "tw"
    }

    try:
        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        results = data.get("organic_results", [])

        sources = []

        for r in results[:5]:
            sources.append({
                "title": r.get("title", ""),
                "link": r.get("link", ""),
                "snippet": r.get("snippet", ""),
                "displayed_link": r.get("displayed_link", "")
            })

        return sources

    except Exception:
        return []


def analyze_with_openai(message, sources):
    """使用 OpenAI 分析訊息可信度"""

    sources_text = ""

    for i, s in enumerate(sources, 1):
        sources_text += (
            f"{i}. {s['title']}\n"
            f"網址：{s['link']}\n"
            f"摘要：{s['snippet']}\n\n"
        )

    prompt = f"""
你是一個假訊息偵測專家，請分析以下訊息的可信度。

【待查訊息】
{message}

【搜尋到的相關來源】
{sources_text if sources_text else "未找到相關來源"}

請用繁體中文分析：

1. 整體判斷（可信 / 需要查證 / 高度可疑）
2. 來源類型分析
3. 是否有斷章取義跡象
4. 簡短建議（如何查證）

限制：
- 回覆簡潔
- 適合 LINE 閱讀
- 不超過 300 字
"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": "你是專業的繁體中文假訊息查核助手。"
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 500,
        "temperature": 0.3
    }

    try:
        res = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=body,
            timeout=30
        )

        data = res.json()

        return data["choices"][0]["message"]["content"]

    except Exception as e:
        return f"分析時發生錯誤：{str(e)}"


def format_reply(message, sources, analysis):
    """組合 LINE 回覆"""

    reply = "🔍 假訊息偵測結果\n"
    reply += "─" * 20 + "\n\n"

    reply += analysis

    reply += "\n\n"
    reply += "─" * 20 + "\n"
    reply += "📌 相關來源：\n"

    if sources:
        for i, s in enumerate(sources[:3], 1):
            reply += f"{i}. {s['title'][:30]}...\n"
            reply += f"   {s['link']}\n\n"
    else:
        reply += "未找到相關來源，請特別小心此訊息。\n"

    return reply


@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)

    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    user_msg = event.message.text.strip()

    if len(user_msg) < 5:

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="請貼上想查證的訊息內容（至少5個字）。"
            )
        )

        return

    # 先回覆分析中
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🔄 正在分析中，請稍候..."
        )
    )

    # 搜尋來源
    sources = search_sources(user_msg)

    # OpenAI 分析
    analysis = analyze_with_openai(user_msg, sources)

    # 組合回覆
    reply = format_reply(user_msg, sources, analysis)

    # push 結果給使用者
    line_bot_api.push_message(
        event.source.user_id,
        TextSendMessage(text=reply)
    )


@app.route("/", methods=["GET"])
def index():
    return "LINE Bot 假訊息偵測系統運作中 ✅"


if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
