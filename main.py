from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import PeerChannel
import asyncio
import requests
from datetime import datetime
import logging
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open("config.json", "r") as config_file:
    config = json.load(config_file)

bearer_oauth_token = config["clould_bearer_oauth_token"]
folder_id = config["cloud_folder_id"]

client = TelegramClient('session_name', config["tg_api_id"], config["tg_api_hash"])
chats = [-1001466120158, 1511414765, 1178238337]


SYSTEM_PROMPT_SUMMARIZE_CHANNEL = """Суммируй следующие посты в телеграм-канале на русском языке.
Выпиши количество постов, которые ты найдешь.
Сделай краткий обзор основных моментов и ключевой информации.
Используй только факты, упомянутые в сообщениях, не придумывай ничего нового.
Если какое-то сообщение повторяется несколько раз, то оставь только одно упоминание.
Верни короткое саммари в двух предложениях, написанное на русском языке."""

SYSTEM_PROMPT_SUMMARIZE_TOTAL = """Ты получаешь на вход несколько коротких саммари о последних постах в нескольких телеграм каналах. Сами посты уже были суммаризированы на предыдущем шаге. Тебе нужно составить короткое описание того, что обсуждалось в этих постах. Если какая-то тема обсуждалась несколько раз, то подними ее выше в своем тексте и укажи, кто о ней писал. Используй только факты, упомянутые в сообщениях, не придумывай ничего нового. Верни короткое саммари для каждого канала в одном абзаце, написанное на русском языке. Должны быть упомянуты все каналы, которые поданы на вход, с названием и логином и количеством новых постов. Короткие описания постов в каждом канале должны быть выдержаны в едином стиле."""


def request_ya_gpt(msg, system_prompt):
    folder_id = config["cloud_folder_id"]
    data = {
        "modelUri": f"gpt://{folder_id}/yandexgpt",
        "messages": [
            {
                "text": system_prompt,
                "role": "system"
            },
            {
                "text": msg,
                "role": "user"
            }
        ],
        "completionOptions": {
            "stream": False,
            "maxTokens": 500,
            "temperature": 0.1
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Api-Key " + config["cloud_api_key_llm"],
        "x-folder-id": folder_id
    }

    r = requests.post("https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
                      data=json.dumps(data), headers=headers)
    r.raise_for_status()
    response = r.json()

    return response["result"]["alternatives"][0]["message"]["text"]

async def start_client(config):
    phone_number = config["tg_phone_number"]
    await client.start(phone=phone_number)
    if not await client.is_user_authorized():
        await client.sign_in(phone_number, password=config["tg_password"])

@client.on(events.NewMessage(pattern='/start|/help'))
async def send_welcome(event):
    await event.reply("Welcome! Use /addchat <chat_link> to add a chat, /removechat <chat_id> to remove a chat, /listchats to list all chats, and /summarize to get a summary.")

@client.on(events.NewMessage(pattern='/addchat'))
async def add_chat(event):
    try:
        chat_link = event.message.text.split()[1]
        chat_id = await get_chat_id_from_link(chat_link)
        if chat_id and chat_id not in chats:
            chats.append(chat_id)
            await event.reply(f'Chat added with ID {chat_id}')
        else:
            await event.reply('Chat already added or invalid link.')
    except Exception as e:
        await event.reply(f'Error: {str(e)}')

@client.on(events.NewMessage(pattern='/removechat'))
async def remove_chat(event):
    try:
        chat_id = int(event.message.text.split()[1])
        if chat_id in chats:
            chats.remove(chat_id)
            await event.reply(f'Chat with ID {chat_id} removed.')
        else:
            await event.reply('Chat ID not found.')
    except Exception as e:
        await event.reply(f'Error: {str(e)}')

@client.on(events.NewMessage(pattern='/listchats'))
async def list_chats(event):
    if chats:
        chat_list = "\n".join([str(chat_id) for chat_id in chats])
        await event.reply(f'Current chat IDs:\n{chat_list}')
    else:
        await event.reply('No chats added.')

@client.on(events.NewMessage(pattern='/summarize'))
async def summarize(event):
    try:
        summaries = await fetch_and_summarize()
        logger.info(f"Got summary: {len(summaries)}")
        if summaries:
            summary_text = ""
            for header, text in summaries:
                if text:
                    logger.info("Summarizing channel... header " + header)
                    summary_channel = request_ya_gpt(header + text, SYSTEM_PROMPT_SUMMARIZE_CHANNEL) + "\n\n"
                    # print(summary_channel)
                    await client.send_message(event.chat_id, header + "\n" + summary_channel)
                    summary_text += summary_channel
            logger.info("Got summarized text: " + summary_text)

            summarized_yapgt = request_ya_gpt(summary_text, SYSTEM_PROMPT_SUMMARIZE_TOTAL)
            logger.info("Got summarized YAPGT text: " + summarized_yapgt)

            if summarized_yapgt:
                # print(event)
                await client.send_message(event.chat_id, summarized_yapgt)
                logger.info(f"Sent summary: {summary_text}")
            else:
                await event.reply("No summaries to send.")
                logger.info("No summaries to send")
        else:
            await event.reply("No chats to summarize.")
            logger.info("No chats to summarize")
    except Exception as e:
        await event.reply(f'Error: {str(e)}')
        logger.error(f"Error during summarization: {str(e)}")

async def get_chat_id_from_link(chat_link):
    try:
        chat = await client.get_entity(chat_link)
        logger.info(f"Fetched chat ID {chat.id} from link {chat_link}")
        return chat.id if chat else None
    except Exception as e:
        logger.error(f"Error fetching chat ID from link {chat_link}: {str(e)}")
        return None

def summarize_messages(channel, messages):
    if not messages:
        return ""
    header = f"# Посты в канале \"{channel.title}\" @{channel.username}\n\n"
    text = header
    for msg in messages:
        if msg.message:
            text += f"## Пост {msg.date} \n{msg.message}\n"
    if not text.strip():
        return ""
    return header.strip(), text

async def fetch_messages_for_chat(chat_id):
    try:
        channel = await client.get_entity(PeerChannel(chat_id))
        history = await client(GetHistoryRequest(
            peer=channel,
            limit=10,
            offset_date=None,
            offset_id=0,
            max_id=0,
            min_id=0,
            add_offset=0,
            hash=0
        ))
        messages = history.messages
        if messages:
            return summarize_messages(channel, messages)
        else:
            logger.info(f"Chat {chat_id} has no messages")
            return "", ""
    except Exception as e:
        logger.error(f"Failed to fetch messages for chat {chat_id}: {str(e)}")
        return "", ""

async def fetch_and_summarize():
    tasks = [fetch_messages_for_chat(chat_id) for chat_id in chats]
    summaries = await asyncio.gather(*tasks)
    logger.info("Fetched and summarized all")
    return summaries

async def main(config):
    await start_client(config)
    logger.info("Bot is up and running...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main(config))
