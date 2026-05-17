"""Discord slash command entrypoint for user area registration."""

import os

import discord
from discord import app_commands

from register_area import register_user_area, validate_postal_code


DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]


class FlightNotifierBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()


client = FlightNotifierBot()


@client.tree.command(name="register", description="郵便番号から自宅周辺の検索地域を登録します")
@app_commands.describe(postal_code="7桁の郵便番号。例: 1130034")
async def register(interaction: discord.Interaction, postal_code: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        normalized_postal_code = validate_postal_code(postal_code)
        user = register_user_area(str(interaction.user.id), normalized_postal_code)
    except Exception as e:
        await interaction.followup.send(f"登録に失敗しました\n{e}", ephemeral=True)
        return

    await interaction.followup.send(
        "地域を登録しました\n"
        f"郵便番号: {user['postal_code']}\n"
        f"住所: {user['prefecture']} {user['city']} {user['town']}\n"
        f"検索キーワード: {', '.join(user['area_keywords'])}",
        ephemeral=True,
    )


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
