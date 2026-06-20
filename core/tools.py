
import logging
import asyncio
import urllib.parse
import aiohttp
import io
import random
import re
import numpy as np
import discord
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any, List

import sympy
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches

logger = logging.getLogger("LocalTools")

RATIO_MAP = {
    "1:1": (1024, 1024),
    "4:5": (800, 1000),
    "9:16": (720, 1280),
    "3:2": (1200, 800),
    "16:9": (1280, 720)
}

def latex_to_python(s: str) -> str:
    s = s.replace("$$", "").replace("$", "").replace("\\(", "").replace("\\)", "").strip()
    
    s = s.replace("\\infty", "oo")
    s = s.replace("\\pi", "pi")
    s = s.replace("\\theta", "theta")
    s = s.replace("\\cdot", "*")
    s = s.replace("\\,", " ")
    s = s.replace("\\left", "")
    s = s.replace("\\right", "")
    s = s.replace("\\ ", " ")
    
    def find_matching_brace(text: str, start: int) -> int:
        count = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                count += 1
            elif text[i] == '}':
                count -= 1
                if count == 0:
                    return i
        return -1

    def convert_fractions(text: str) -> str:
        while True:
            match = re.search(r'\\frac\s*\{', text)
            if not match:
                break
            start_idx = match.start()
            num_start = match.end() - 1
            num_end = find_matching_brace(text, num_start)
            if num_end == -1:
                break
            numerator = text[num_start+1:num_end]
            
            denom_part = text[num_end+1:].strip()
            if not denom_part.startswith("{"):
                break
            denom_start = num_end + 1 + text[num_end+1:].find("{")
            denom_end = find_matching_brace(text, denom_start)
            if denom_end == -1:
                break
            denominator = text[denom_start+1:denom_end]
            
            old_block = text[start_idx:denom_end+1]
            new_block = f"(({numerator})/({denominator}))"
            text = text.replace(old_block, new_block, 1)
        return text

    s = convert_fractions(s)
    
    while True:
        match = re.search(r'e\^\{', s)
        if not match:
            break
        start_idx = match.start()
        expr_start = match.end() - 1
        expr_end = find_matching_brace(s, expr_start)
        if expr_end == -1:
            break
        inner_expr = s[expr_start+1:expr_end]
        old_block = s[start_idx:expr_end+1]
        new_block = f"exp({inner_expr})"
        s = s.replace(old_block, new_block, 1)
        
    s = re.sub(r'e\^([a-zA-Z0-9])', r'exp(\1)', s)
    
    s = s.replace("\\ln", "log")
    s = s.replace("\\log", "log")
    s = s.replace("\\sin", "sin")
    s = s.replace("\\cos", "cos")
    s = s.replace("\\tan", "tan")
    s = s.replace("\\exp", "exp")
    
    s = re.sub(r'\\int_([0-9a-zA-Z])\^\\([a-zA-Z]+)', r'\\int_{\1}^{\\\2}', s)
    s = re.sub(r'\\int_([0-9a-zA-Z])\^([0-9a-zA-Z])', r'\\int_{\1}^{\2}', s)
    
    while True:
        match = re.search(r'\\int\s*_\{([^}]+)\}\s*\^\{([^}]+)\}(.*?)\s*d([a-zA-Z])', s, flags=re.DOTALL)
        if not match:
            match = re.search(r'\\int\s*_([a-zA-Z0-9]+)\s*\^([a-zA-Z0-9]+)(.*?)\s*d([a-zA-Z])', s, flags=re.DOTALL)
            if not match:
                break
        start_idx = match.start()
        end_idx = match.end()
        lower, upper, integrand, var = match.groups()
        
        integrand_clean = integrand.strip().rstrip("\\").strip()
        
        old_block = s[start_idx:end_idx]
        new_block = f"integrate({integrand_clean}, ({var}, {lower}, {upper}))"
        s = s.replace(old_block, new_block, 1)

    s = re.sub(r'\s+', ' ', s).strip()
    return s


class ToolSuite:
    def __init__(self, bot_instance=None, context_channel=None, context_author=None, context_message=None):
        self.bot = bot_instance
        self.channel = context_channel
        self.author = context_author
        self.message = context_message

    def __deepcopy__(self, memo):
        return self

    async def execute_math_evaluation(self, query: str) -> str:
        if not self.channel:
            return "Error: Cannot evaluate math outside of active channel contexts."
            
        if self.message:
            try:
                bot_name = self.bot.get_bot_name(self.channel)
                await self.message.edit(content=f"🔢 *{bot_name} is compiling mathematics...*")
            except Exception as e:
                logger.warning(f"Could not edit active placeholder message: {e}")
        
        cleaned_query = re.sub(r'\bsolve\b', '', query, flags=re.IGNORECASE).strip()
        cleaned_query = cleaned_query.replace("?", "").strip()
        
        if "\\" in cleaned_query or "^" in cleaned_query or "_" in cleaned_query or "frac" in cleaned_query:
            try:
                cleaned_query = latex_to_python(cleaned_query)
                logger.info(f"Math Tool: Translated LaTeX query into: {cleaned_query}")
            except Exception as latex_err:
                logger.warning(f"LaTeX-to-Python translation failed: {latex_err}")

        cleaned_query = re.sub(r'^(sp\.)?solve\((.*)\)$', r'\2', cleaned_query, flags=re.IGNORECASE)
        cleaned_query = re.sub(r'^(sp\.)?integrate\((.*)\)$', r'integrate \2', cleaned_query, flags=re.IGNORECASE)
        cleaned_query = re.sub(r'^(sp\.)?diff\((.*)\)$', r'diff \2', cleaned_query, flags=re.IGNORECASE)

        try:
            transformations = standard_transformations + (implicit_multiplication_application,)
            expr = parse_expr(cleaned_query, transformations=transformations)
            
            if isinstance(expr, (list, tuple)):
                expr = expr[0] if len(expr) > 0 else sympy.Integer(0)
            
            is_unit_circle = any(t in cleaned_query.lower() for t in ["sin", "cos", "tan"]) and any(a in cleaned_query.lower() for a in ["pi", "deg", "degree"])
            is_geometry = any(g in cleaned_query.lower() for g in ["triangle", "rectangle", "hypotenuse"])
            
            steps_latex = []
            domain = "algebra"
            python_code = f"import sympy as sp\n\n# Solve expression\nexpr = sp.parse_expr('{cleaned_query}')"
            
            steps_latex.append(r"\text{Original: } " + sympy.latex(expr))
            
            if "diff" in cleaned_query or "derivative" in cleaned_query:
                domain = "calculus"
                free_vars = list(expr.free_symbols)
                var = free_vars[0] if free_vars else sympy.Symbol('x')
                deriv = sympy.diff(expr, var)
                steps_latex.append(r"\frac{d}{d" + sympy.latex(var) + r"} \left[" + sympy.latex(expr) + r"\right]")
                steps_latex.append(r"\text{Result: } " + sympy.latex(deriv))
                final_sol = deriv
                python_code += f"\nvar = sp.Symbol('{var}')\nderiv = sp.diff(expr, var)"
                
            elif "integrate" in cleaned_query or "integral" in cleaned_query:
                domain = "calculus"
                free_vars = list(expr.free_symbols)
                var = free_vars[0] if free_vars else sympy.Symbol('x')
                integral = sympy.integrate(expr, var)
                
                steps_latex.append(r"\int " + sympy.latex(expr) + r" \, d" + sympy.latex(var))
                steps_latex.append(r"\text{Result: } " + sympy.latex(integral) + r" + C")
                final_sol = integral
                python_code += f"\nvar = sp.Symbol('{var}')\nintegral = sp.integrate(expr, var)"
                
            else:
                free_vars = list(expr.free_symbols)
                if free_vars:
                    try:
                        roots = sympy.solve(expr, free_vars[0])
                        steps_latex.append(r"\text{Set equation to 0: } " + sympy.latex(expr) + r" = 0")
                        steps_latex.append(r"\text{Factored: } " + sympy.latex(expr.factor()))
                        steps_latex.append(r"\text{Roots: } " + sympy.latex(roots))
                        final_sol = roots
                        python_code += f"\nvar = sp.Symbol('{free_vars[0]}')\nroots = sp.solve(expr, var)"
                    except Exception:
                        simplified = expr.simplify()
                        steps_latex.append(r"\text{Simplified: } " + sympy.latex(simplified))
                        final_sol = simplified
                        python_code += "\nsimplified = expr.simplify()"
                else:
                    evaluated = expr.evalf()
                    steps_latex.append(r"\text{Evaluated Value: } " + sympy.latex(evaluated))
                    final_sol = evaluated
                    domain = "arithmetic"
                    python_code += "\nevaluated = expr.evalf()"
                    
            if is_unit_circle:
                domain = "trig"
            elif is_geometry:
                domain = "geometry"

            latex_canvas_bytes = self._render_latex_canvas(steps_latex)
            
            if self.message:
                placeholder_id = self.message.id
            else:
                placeholder_id = random.randint(100000, 999999)

            self.bot.math_cache[placeholder_id] = {
                "query": query,
                "expression": expr,
                "final_solution": final_sol,
                "steps_latex": steps_latex,
                "code": python_code,
                "domain": domain,
                "free_symbols": [str(s) for s in expr.free_symbols],
                "image_bytes": latex_canvas_bytes
            }
            
            return f"Success: Mathematics compiled. Math Session ID: {placeholder_id}."
            
        except Exception as e:
            logger.error(f"Math solver tool execution crashed: {e}")
            return f"Error: Math compilation failed: {str(e)}"

    def _render_latex_canvas(self, latex_lines: List[str]) -> bytes:
        fig_height = max(2.5, len(latex_lines) * 0.6 + 0.8)
        fig, ax = plt.subplots(figsize=(7, fig_height), dpi=150)
        
        fig.patch.set_facecolor('#2f3136')
        ax.set_facecolor('#2f3136')
        
        y_pos = 0.85
        y_step = 0.8 / len(latex_lines) if len(latex_lines) > 1 else 0.4
        
        for line in latex_lines:
            math_line_str = f"${line}$"
            ax.text(
                0.5, y_pos, math_line_str, 
                horizontalalignment='center', 
                verticalalignment='center', 
                color='white', 
                fontsize=11
            )
            y_pos -= y_step
            
        ax.axis('off')
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.1, facecolor=fig.get_facecolor(), edgecolor='none')
        buf.seek(0)
        plt.close(fig)
        return buf.read()

    async def generate_image(self, prompt: str, style: str = "photorealistic", ratio: str = "1:1", strength: str = "0.6", is_edit: bool = False) -> str:
        if not self.channel:
            return "Error: Cannot generate images outside of active channel contexts."

        placeholder = await self.channel.send(content="🎨 *Initializing drawing canvas...*")
        history = self.bot.history_tracker.get_formatted_history(self.channel.id)
        
        base_image_bytes = None
        if is_edit:
            for msg_id, versions in list(self.bot.image_versions.items()):
                if versions:
                    base_image_bytes = versions[-1].get("image_bytes")
                    break

        try:
            data, clean_summary, final_thoughts, elapsed_total = await self.bot._stream_artist_and_get_params(
                prompt, history, self.channel, placeholder, base_image_bytes, is_edit
            )
        except Exception as planning_exc:
            logger.warning(f"Artist planning pass failed: {planning_exc}. Falling back to inputs.")
            data = {
                "expanded_prompt": prompt,
                "selected_style": style,
                "selected_ratio": ratio,
                "selected_strength": strength
            }
            clean_summary = ""
            final_thoughts = "Artist planning failed. Bypassed to direct rendering."
            elapsed_total = 0

        expanded_prompt = data.get("expanded_prompt", prompt)
        selected_style = data.get("selected_style", style).strip().lower()
        selected_ratio = data.get("selected_ratio", ratio).strip()
        selected_strength = data.get("selected_strength", strength).strip()

        width, height = RATIO_MAP.get(selected_ratio, (1024, 1024))
        seed = random.randint(1, 10000000)
        
        try:
            img_bytes = await self.bot.image_generator.generate(
                prompt=expanded_prompt, width=width, height=height, seed=seed, style_key=selected_style,
                base_image_bytes=base_image_bytes, strength=selected_strength
            )
            
            file = discord.File(fp=io.BytesIO(img_bytes), filename="generated.png")
            
            self.bot.image_versions[placeholder.id] = [{
                "prompt": prompt, "expanded": expanded_prompt, "style": selected_style, "ratio": selected_ratio,
                "strength": selected_strength, "seed": seed, "image_bytes": img_bytes, "is_completed": True,
                "banter": clean_summary, "is_edit_flow": is_edit, "thoughts": final_thoughts, "thoughts_elapsed": elapsed_total,
                "user_app_session_id": None
            }]
            self.bot.image_version_indexes[placeholder.id] = 0
            
            from core.ui_components import DynamicView, ThoughtsButton
            view = DynamicView(self.bot, self.channel)
            if final_thoughts:
                view.add_item(ThoughtsButton(final_thoughts, elapsed=elapsed_total, thinking_active=False, message_id=placeholder.id, bot_instance=self.bot, thinking_level="HIGH"))
            view.add_image_controls(self.bot, placeholder.id, prompt, selected_style, selected_ratio, selected_strength, current_is_edit_flow=is_edit)
            view.finalize_layout()
            
            display_prompt = prompt
            if "\n" in display_prompt:
                display_prompt = display_prompt.split("\n")[0].strip()
            display_prompt = re.sub(r'//.*', '', display_prompt).strip()
            display_prompt = re.sub(r'#.*', '', display_prompt).strip()
            if not display_prompt:
                display_prompt = "Custom Drawing"

            await placeholder.edit(
                content=f"🎨 **Image Generated**\n*Prompt:* \"{display_prompt[:100]}\"" + (f"\n\n{clean_summary}" if clean_summary else ""),
                attachments=[file],
                view=view
            )
            
            img_url = placeholder.attachments[0].url if placeholder.attachments else "No attachment URL"
            return f"Success: Image successfully rendered. Attachment URL: {img_url}. Message ID: {placeholder.id}."
            
        except Exception as e:
            logger.error(f"Image generation failed during tool execution: {e}")
            try:
                await placeholder.edit(content=f"❌ *Artist rendering failed:* {e}", view=None, attachments=[])
            except Exception:
                pass
            return f"Error: Image generation failed due to: {str(e)}"

    async def create_thread(self, name: str, parent_channel_id: Optional[int] = None) -> str:
        parent = self.channel
        if parent_channel_id:
            parent = self.bot.get_channel(parent_channel_id) or await self.bot.fetch_channel(parent_channel_id)
            
        if not parent:
            return "Error: Could not resolve parent channel to spawn thread under."
            
        try:
            thread = await parent.create_thread(
                name=name[:100],
                auto_archive_duration=1440,
                type=discord.ChannelType.public_thread
            )
            self.bot.active_channels.add(thread.id)
            return f"Success: Public thread '{name}' successfully created. Thread ID: {thread.id}. Link: <#{thread.id}>."
        except Exception as e:
            return f"Error: Failed to create thread: {str(e)}"

    async def watch_channel(self, channel_id: int, action: str, duration_minutes: int = 5) -> str:
        chan_id = int(channel_id)
        act = action.strip().lower()
        dur = max(1, min(int(duration_minutes), 15))

        if act == "watch":
            self.bot.active_channels.add(chan_id)
            self.bot.loop.create_task(self._schedule_unwatch(chan_id, dur))
            logger.info(f"Tool: Watched channel {chan_id} for {dur} minutes.")
            return f"Success: Successfully registered watch status on channel <#{chan_id}> for {dur} minutes."
            
        elif act == "unwatch":
            if chan_id in self.bot.active_channels:
                self.bot.active_channels.remove(chan_id)
            logger.info(f"Tool: Unwatched channel {chan_id} immediately.")
            return f"Success: Successfully removed active watching status on channel <#{chan_id}>."
            
        return "Error: Invalid action. Choose 'watch' or 'unwatch'."

    async def _schedule_unwatch(self, channel_id: int, minutes: int):
        await asyncio.sleep(minutes * 60)
        if channel_id in self.bot.active_channels:
            self.bot.active_channels.remove(channel_id)
            logger.info(f"Scheduled unwatch triggered. Expired listening status on channel {channel_id}.")

    async def save_memory_fact(self, tier: str, fact: str) -> str:
        import core.memory as memory
        t_clean = tier.strip().lower()
        fact_clean = fact.strip()

        if not fact_clean:
            return "Error: Cannot save empty facts."

        if t_clean == "user":
            await memory.save_fact(self.bot, self.bot.brain_server_id, self.author if self.author else self.bot.user, fact_clean)
            return f"Success: Recorded user fact: '{fact_clean}'"
            
        elif t_clean == "server" and self.channel and self.channel.guild:
            await memory.save_server_fact(self.bot, self.bot.brain_server_id, self.channel.guild, fact_clean)
            return f"Success: Saved server fact under '{self.channel.guild.name}': '{fact_clean}'"
            
        elif t_clean == "global":
            await memory.save_global_fact(self.bot, self.bot.brain_server_id, fact_clean)
            return f"Success: Saved global database fact: '{fact_clean}'"
            
        return "Error: Invalid tier or server-lore context is missing."

    async def forget_memory_fact(self, tier: str, fact: str) -> str:
        import core.memory as memory
        t_clean = tier.strip().lower()
        fact_clean = fact.strip()

        if t_clean == "user" and self.author:
            user_chan = f"{self.author.name}-memory".lower().replace(" ", "-")
            success = await memory.forget_fact(self.bot, self.bot.brain_server_id, "🧠 User Memories", user_chan, fact_clean)
            return f"Success: Forgot fact matching '{fact_clean}'" if success else "Notice: Fact was not found in records."
            
        elif t_clean == "global":
            success = await memory.forget_fact(self.bot, self.bot.brain_server_id, "🌐 Global Database", "global-memory", fact_clean)
            return f"Success: Removed global fact matching '{fact_clean}'" if success else "Notice: Fact was not found in global database."
            
        return "Error: Invalid tier or context parameters are missing."

    async def web_search(self, query: str) -> str:
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
                async with session.get(url, timeout=12) as response:
                    if response.status != 200:
                        return f"[Error: Search scraper failed with HTTP status {response.status}]"
                    html_data = await response.text()
                    
            soup = BeautifulSoup(html_data, "html.parser")
            results = []
            
            for node in soup.find_all("a", class_="result__snippet")[:5]:
                title_node = node.find_previous("a", class_="result__url")
                title = title_node.get_text().strip() if title_node else "Result Title"
                link = title_node["href"] if title_node else "No link found"
                snippet = node.get_text().strip()
                results.append(f"Title: {title}\nLink: {link}\nSnippet: {snippet}\n")
                
            if not results:
                return f"DuckDuckGo search returned no active results for query: '{query}'."
            return "\n".join(results)
        except Exception as e:
            return f"[Failed to process local web search: {e}]"

    async def web_scrape(self, url: str) -> str:
        if not url:
            return "[Error: URL argument is empty]"
        try:
            scraped_markdown = await self.bot.link_reader.fetch_and_clean(url)
            return scraped_markdown
        except Exception as e:
            return f"[Failed to scrape webpage: {e}]"