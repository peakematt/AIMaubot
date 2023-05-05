# See https://github.com/maubot/maubot/blob/master/maubot/matrix.py if you want to see the options
# for responding to messages

import subprocess
import aiohttp
import uuid
import json

from typing import Type
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper
from mautrix.types import RoomID, ImageInfo


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

    @command.new(name="txtai", aliases=get_text_command_aliases)
    @command.argument(name="prompt", pass_raw=True, required=False)
    async def command_text(self, evt: MessageEvent, prompt: str) -> None:
        if not evt.sender in self.config["allowlist"]:
            return

        if not prompt:
            await evt.reply("Usage: !txtai [prompt for AI]")
            return

        async with aiohttp.ClientSession() as session:
            if not self.config["text_ai_use_chat_endpoint"]:
                async with session.post(
                    "https://api.openai.com/v1/completions",
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
                        ],
                    },
                ) as resp:
                    response = await resp.json()
                    if response.get("choices", None) is not None:
                        # await evt.reply(response["choices"][0]["text"])
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
            else:
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.config['openai-api-key']}",
                    },
                    json={
                        "model": self.config["text_ai_model"],
                        "messages": [{"role": "user", "content": prompt}],
                    },
                ) as resp:
                    response = await resp.json()
                    if response.get("choices", None) is not None:
                        # await evt.reply(response["choices"][0]["message"]["content"])
                        await evt.respond(response["choices"][0]["message"]["content"])

                        # await self.client.send_markdown(
                        #     room_id = evt.room_id,
                        #     markdown = response["choices"][0]["message"]["content"]
                        # )


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

    @command.new(name="picai", aliases=get_picture_command_aliases)
    @command.argument(name="prompt", pass_raw=True, required=False)
    async def command_picture(self, evt: MessageEvent, prompt: str) -> None:
        if not evt.sender in self.config["allowlist"]:
            return

        if not prompt:
            await evt.reply("Usage: !picai [prompt for AI]")
            return

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config['openai-api-key']}",
                },
                json={
                    "prompt": prompt,
                    "n": self.config["images_to_generate"],
                    "size": self.config["image_output_size"],
                },
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
