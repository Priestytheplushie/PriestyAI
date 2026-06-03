
import discord
from discord import app_commands
import asyncio
import logging

logger = logging.getLogger("CanvasTool")

class CanvasInviteView(discord.ui.LayoutView):
    text_display1 = discord.ui.TextDisplay(content="Sure, i'll put the code in the **Canvas**!")
    
    container1 = discord.ui.Container(
        discord.ui.Section(
            discord.ui.TextDisplay(content="# Test Canvas"),
            accessory=discord.ui.Button(
                url="https://discord.com/activities/1509364708476452894",
                style=discord.ButtonStyle.link,
                label="Open Canvas",
            ),
        ),
    )


@app_commands.command(name="canvas", description="Spawns a collaborative, live-syncing Monaco code workspace")
async def canvas_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    
    view = CanvasInviteView()
    sent_msg = await interaction.followup.send(content="Opening visual Canvas connection panel...", view=view)
    
    from core.web_server import CanvasWebServer
    server = CanvasWebServer.get_server()
    
    if server:
        await asyncio.sleep(4.0)
        
        test_html_code = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Canvas Test</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#0c0c0e] text-zinc-100 flex flex-col justify-center items-center h-screen font-sans">
    <div class="text-center p-12 bg-[#16161a] border border-[#24242b] rounded-2xl shadow-2xl max-w-md">
        <span class="px-3 py-1 text-xs font-semibold bg-indigo-500/20 text-indigo-300 rounded-full border border-indigo-500/30">
            Active Connection Verified
        </span>
        <h1 class="text-3xl font-black mt-4 text-white uppercase tracking-tight">Live WebSockets</h1>
        <p class="text-zinc-400 mt-2 text-sm">
            Your Python backend is successfully broadcasting raw document streams down to the Monaco Editor frontend!
        </p>
    </div>
</body>
</html>"""
        
        await server.broadcast_code_update(test_html_code)


def register_canvas_tool(bot):
    bot.tree.add_command(canvas_cmd)
    logger.info("Canvas Tool (/canvas) dynamically mounted to bot tree.")