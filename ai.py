import subprocess
import aiohttp


from typing import Type
from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("text_command_aliases")
        helper.copy("allowlist")
        helper.copy("openai-api-key")


class AIBot(Plugin):
    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    def get_text_command_aliases(self, command: str) -> str:
        return command == "txtai" or command in self.config["text_command_aliases"]

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

        # await evt.reply("You did it!")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config['openai-api-key']}",
                },
                json={
                    "model": "text-davinci-003",
                    "prompt": prompt,
                    "temperature": 0.7,
                    "max_tokens": 256,
                    "top_p": 1,
                    "frequency_penalty": 0,
                    "presence_penalty": 0,
                },
            ) as resp:
                response = await resp.json()

        await evt.reply(response["choices"][0]["text"])
