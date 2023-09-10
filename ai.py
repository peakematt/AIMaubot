# See https://github.com/maubot/maubot/blob/master/maubot/matrix.py if you want to see the options
# for responding to messages

import subprocess
import aiohttp
import uuid
import json

import datetime as dt

from typing import Type
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from mautrix.util.async_db import UpgradeTable, Connection
from mautrix.types import RoomID, ImageInfo

upgrade_table = UpgradeTable()

class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("text_command_aliases")
        helper.copy("image_command_aliases")
        helper.copy("allowlist")
        helper.copy("openai-api-key")
        helper.copy("images_to_generate")
        helper.copy("image_output_size")
        helper.copy("text_ai_model")
        helper.copy("text_ai_model_temperature")
        helper.copy("text_ai_model_max_tokens")
        helper.copy("text_ai_model_top_p")
        helper.copy("text_ai_model_frequency_penalty")
        helper.copy("text_ai_model_presence_penalty")
        helper.copy("text_ai_debug")
        helper.copy("text_ai_use_chat_endpoint")
        helper.copy("text_ai_base_url")
        helper.copy("image_ai_base_url")
        helper.copy("verify_ssl")


class AIBot(Plugin):
    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    def get_text_command_aliases(self, command: str) -> str:
        return command == "txtai" or command in self.config["text_command_aliases"]

    def get_picture_command_aliases(self, command: str) -> str:
        return command == "picai" or command in self.config["image_command_aliases"]

    async def start(self) -> None:
        output = subprocess.run(
            ["python3", "-m", "pip", "install", "openai"], capture_output=True
        )
        self.config.load_and_update()

    @classmethod
    def get_db_upgrade_table(cls) -> UpgradeTable | None:
        return upgrade_table
    
    async def get_chat_history(self, channel) -> list[dict]:
        q = """
            SELECT role, message FROM ai_chat_history WHERE channel=$1 AND role IS DISTINCT FROM 'system' ORDER BY timestamp ASC
        """
        rows = await self.database.fetch(q, channel)
        if len(rows) == 0:
            return []
        return [{"role": row["role"], "content": row["message"]} for row in rows]

    async def put_chat_history(self, message_id, channel, role, message, timestamp) -> bool:
        q = """
            INSERT INTO ai_chat_history (id, channel, role, message, timestamp) VALUES ($1, $2, $3, $4, $5)
        """
        await self.database.execute(q, message_id, channel, role, message, timestamp)
        return True
    
    async def clear_chat_history(self, channel) -> bool:
        q = """
            DELETE FROM ai_chat_history WHERE channel=$1 AND role IS DISTINCT FROM 'system'
        """
        await self.database.execute(q, channel)
        return True

    async def get_channel_prompt(self, channel) -> str:
        q = """
            SELECT message FROM ai_chat_history WHERE channel=$1 AND role='system'
        """
        row = await self.database.fetchrow(q, channel)
        if row:
            return row["message"]
        return ""

    async def put_channel_prompt(self, message_id, channel, prompt, timestamp) -> bool:
        existing_prompt = self.get_channel_prompt(channel)
        
        if existing_prompt != "":
            self.clear_channel_prompt(channel,)

        q = """
            INSERT INTO ai_chat_history (id, channel, role, message, timestamp) VALUES ($1, $2, 'system', $3, $4)
        """
        await self.database.execute(q, message_id, channel, prompt, timestamp)

    async def clear_channel_prompt(self, channel) -> bool:
        q = """
            DELETE FROM ai_chat_history WHERE channel=$1 AND role='system'
        """
        await self.database.execute(q, channel)
        return True

    async def txtai_legacy_completion(self, evt, prompt):
        base_url = self.config["text_ai_base_url"]
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/v1/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config['openai-api-key']}",
                },
                json={
                    "model": self.config["text_ai_model"],
                    "prompt": prompt,
                    "temperature": self.config["text_ai_model_temperature"],
                    "max_tokens": self.config["text_ai_model_max_tokens"],
                    "top_p": self.config["text_ai_model_top_p"],
                    "frequency_penalty": self.config[
                        "text_ai_model_frequency_penalty"
                    ],
                    "presence_penalty": self.config[
                        "text_ai_model_presence_penalty"
                    ]
                },
                ssl=self.config["verify_ssl"]
            ) as resp:
                response = await resp.json()
                if response.get("choices", None) is not None:
                    await self.client.send_markdown(
                        room_id = evt.room_id,
                        markdown = response["choices"][0]["text"]
                    )
                elif response.get("error", None) is not None:
                    await evt.reply(
                        f"Sorry there's been an error: {response.get('error', {}).get('message', 'unknown error')}"
                    )
                else:
                    await evt.reply(
                        f"Something very confusing has happened. Ask Matt to check the logs!!"
                    )

                if self.config["text_ai_debug"]:
                    self.log.info(json.dumps(response))

    async def txtai_chat_completion(self, evt, prompt):
        base_url = self.config["text_ai_base_url"]
        response = None
        channel_id = evt.room_id
        event_id = evt.event_id
        timestamp = dt.datetime.fromtimestamp(evt.timestamp/1e3)

        messages = []

        # Retrieve channel-level prompt
        channel_prompt = await self.get_channel_prompt(channel_id)
        if channel_prompt != "":
            messages.append({"role": "system", "content": f"{channel_prompt}"})

        # Insert this message to channel-level history
        await self.put_chat_history(event_id, channel_id, "user", prompt, timestamp)

        # Retrieve channel-level chat history
        messages += await self.get_chat_history(channel_id)

        if self.config["text_ai_debug"]:
            self.log.info(json.dumps(messages))

        # make API request to ChatGPT
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config['openai-api-key']}",
                },
                json={
                    "model": self.config["text_ai_model"],
                    "messages": messages,
                },
                ssl=self.config["verify_ssl"],
            ) as resp:
                response = await resp.json()

            # Log response from ChatGPT if desired
            if self.config["text_ai_debug"]:
                self.log.info(json.dumps(response))
            
            # Post response from ChatGPT to channel
            if response.get("choices", None) is not None:
                # Happy path. We received a response from ChatGPT API
                # Get it from the response object, store it in the database
                # and post it to Matrix
                gpt_response = response["choices"][0]["message"]["content"]
                await self.put_chat_history(f"{uuid.uuid4()}", channel_id, "assistant", gpt_response, dt.datetime.utcnow())
                await evt.respond(gpt_response)
            
            elif response.get("error", None) is not None:
                await evt.reply(
                    f"Sorry there's been an error: {response.get('error', {}).get('message', 'unknown error')}"
                )
            
            else:
                await evt.reply(
                    f"Something very confusing has happened. Ask Matt to check the logs!!"
                )

    @command.new(name="txtai", aliases=get_text_command_aliases, require_subcommand=True)
    @command.argument(name="prompt", pass_raw=True, required=False)
    async def command_text_chat(self, evt: MessageEvent, prompt: str) -> None:
        if not evt.sender in self.config["allowlist"]:
            return

        if not prompt:
            await evt.reply("Usage: `!txtai [prompt for AI]`\nAlso see `!manage_txtai` for management options")
            return

        if not self.config["text_ai_use_chat_endpoint"]:
            return await self.txtai_legacy_completion(evt, prompt)
             
        return await self.txtai_chat_completion(evt, prompt)

    @command.new(name="manage_txtai", require_subcommand=True)
    async def command_manage_text(self, evt: MessageEvent) -> None:
        # Intentionally empty
        pass

    @command.new(name="picai", aliases=get_picture_command_aliases)
    @command.argument(name="prompt", pass_raw=True, required=False)
    async def command_picture(self, evt: MessageEvent, prompt: str) -> None:
        if not evt.sender in self.config["allowlist"]:
            return

        if not prompt:
            await evt.reply("Usage: !picai [prompt for AI]")
            return

        base_url = base_url = self.config["image_ai_base_url"]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/v1/images/generations",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config['openai-api-key']}",
                },
                json={
                    "prompt": prompt,
                    "n": self.config["images_to_generate"],
                    "size": self.config["image_output_size"],
                },
                ssl=self.config["verify_ssl"],
            ) as resp:
                response = await resp.json()

        try:
            picture_links = [item["url"] for item in response["data"]]
            for link in picture_links:
                resp = await self.http.get(link)
                data = await resp.read()
                mime = "image/png"
                filename = f"{uuid.uuid4().hex}.png"
                width = self.config.get("image_output_size", "1024x1024").split("x")[0]
                height = self.config.get("image_output_size", "1024x1024").split("x")[1]
                mxc_uri = await self.client.upload_media(
                    data, mime_type=mime, filename=filename
                )

                await self.client.send_image(
                    room_id=evt.room_id,
                    url=mxc_uri,
                    file_name=filename,
                    info=ImageInfo(mimetype=mime, width=width, height=height),
                )

        except Exception as e:
            self.log.warning(e)
            await evt.reply(
                "Sorry. There was an error returning the requested image(s)"
            )

    @command_manage_text.subcommand(name="history", help="Options to manage chat history")
    async def text_history(self, evt: MessageEvent) -> None:
        # Intentionally empty 
        pass

    @text_history.subcommand(name="show", help="Shows history for this channel")
    async def text_history_show(self, evt: MessageEvent) -> None:
        channel_id = evt.room_id
        messages = []
        counter = 1

        response_text = "### Message History\n"

        # Retrieve channel-level chat history
        messages += await self.get_chat_history(channel_id)

        if len(messages) == 0:
            await evt.reply("No messages in history for this channel")
            return

        for message in messages:
            response_text += f"#### {counter}: {message['role']}\n"
            response_text += f"{message['content']}\n"
            counter += 1

        await evt.reply(response_text)

    @text_history.subcommand(name="clear", help="Clears all history for this channel")
    async def text_history_clear(self, evt: MessageEvent) -> None:
        channel_id = evt.room_id
        await self.clear_chat_history(channel_id)
        await evt.reply("Chat history cleared!")

    @command_manage_text.subcommand(name="system_prompt", help="Options to manage a persistent prompt that influences the AI's behavior")
    async def text_system_prompt(self, evt: MessageEvent) -> None:
        # Intentionally empty
        pass

    @text_system_prompt.subcommand(name="set", help="Sets persistent prompt that influences the AI's behavior")
    @command.argument(name="prompt", pass_raw=True, required=False)
    async def text_system_prompt_set(self, evt: MessageEvent, prompt: str) -> None:
        channel_id = evt.room_id
        event_id = evt.event_id
        timestamp = dt.datetime.fromtimestamp(evt.timestamp/1e3)

        await self.put_channel_prompt(event_id, channel_id, prompt, timestamp)

        await evt.reply("System Prompt text set!")

    @text_system_prompt.subcommand(name="clear", help="Clear's this channel's persistent prompt that influences the AI's behavior")
    async def text_system_prompt_clear(self, evt: MessageEvent):
        channel_id = evt.room_id
        await self.clear_channel_prompt(channel_id)
        await evt.reply("System Prompt cleared!")

    @text_system_prompt.subcommand(name="show", help="Shows this channel's persistent prompt that influences the AI's behavior")
    async def text_system_prompt_show(self, evt: MessageEvent):
        channel_id = evt.room_id

        channel_prompt = await self.get_channel_prompt(channel_id)

        await evt.reply(channel_prompt)

@upgrade_table.register(description="Initial schema")
async def upgrade_v1(conn: Connection) -> None:
    await conn.execute(
        """
            CREATE TABLE ai_chat_history (
                id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TIMESTAMP NOT NULL
            )
        """
    )