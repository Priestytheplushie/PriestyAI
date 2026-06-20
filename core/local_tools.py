
import os
import re
import uuid
import logging
import urllib.parse
import aiohttp
from bs4 import BeautifulSoup
import sympy as sp
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from typing import Tuple, List, Dict, Any

logger = logging.getLogger("LocalTools")

ACTIVE_MATH_ASSETS: Dict[int, Dict[str, Any]] = {}


async def google_search(query: str) -> str:
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(url, timeout=12) as response:
                if response.status != 200:
                    return f"[Error: Web search index returned HTTP {response.status}]"
                html_data = await response.text()
                
        soup = BeautifulSoup(html_data, "html.parser")
        results = []
        
        for node in soup.find_all("a", class_="result__snippet")[:5]:
            title_node = node.find_previous("a", class_="result__url")
            title = title_node.get_text().strip() if title_node else "Result Title"
            link = title_node["href"] if title_node else "No link found"
            snippet = node.get_text().strip()
            
            if link.startswith("//"):
                link = "https:" + link
            if "duckduckgo.com/l/" in link:
                parsed = urllib.parse.urlparse(link)
                params = urllib.parse.parse_qs(parsed.query)
                if "uddg" in params:
                    link = params["uddg"][0]
                    
            results.append(f"Title: {title}\nURL: {link}\nSnippet: {snippet}\n")
            
        if not results:
            return f"DuckDuckGo search returned no active results for query: '{query}'."
            
        return "\n---\n".join(results)
    except Exception as e:
        logger.error(f"Search execution failed for query '{query}': {e}")
        return f"[Error: Web search failed: {e}]"



async def web_scrape(url: str) -> str:
    if not url:
        return "[Error: Web scraper URL argument is empty.]"
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    }
    
    if url.startswith("www."):
        url = "https://" + url

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as response:
                if response.status in (401, 403, 429):
                    return await _fetch_via_jina(url)
                    
                if response.status != 200:
                    return f"[Error: Scraping failed with HTTP Status {response.status}]"
                    
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" not in content_type:
                    if "text/" in content_type or "json" in content_type:
                        text_content = await response.text()
                        return text_content[:6000]
                    return f"[Error: Ignored non-text content type: {content_type}]"
                    
                html_content = await response.text()
                
        soup = BeautifulSoup(html_content, "html.parser")
        for element in soup(["script", "style", "noscript", "iframe", "header", "footer", "nav", "svg", "form"]):
            element.decompose()
            
        title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled Page"
        
        text_blocks = []
        for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'li', 'article']):
            text = element.get_text().strip()
            if text:
                text_blocks.append(text)
                
        cleaned_text = "\n\n".join(text_blocks)
        cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
        cleaned_text = re.sub(r' {2,}', ' ', cleaned_text)
        
        summary = f"Title: {title}\nURL: {url}\n\n{cleaned_text}"
        
        if len(cleaned_text.strip()) < 150:
            return await _fetch_via_jina(url)
            
        return summary[:6000]
        
    except Exception as e:
        logger.warning(f"Standard scraper failed for {url}: {e}. Trying Jina fallback...")
        return await _fetch_via_jina(url)

async def _fetch_via_jina(url: str) -> str:
    jina_url = f"https://r.jina.ai/{url}"
    headers = {
        "X-With-Links-Summary": "true",
        "X-With-Images-Summary": "false"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(jina_url, timeout=aiohttp.ClientTimeout(total=12)) as response:
                if response.status != 200:
                    return f"[Error: Web reader failed with HTTP status {response.status}]"
                text = await response.text()
                return text[:6000]
    except Exception as err:
        logger.error(f"Jina Reader fallback failed: {err}")
        return f"[Failed to scrape URL: {str(err)}]"



async def solve_equation(channel_id: int, equation: str, variable: str = "x") -> str:
    try:
        var = sp.Symbol(variable)
        expr = sp.sympify(equation)
        
        solutions = sp.solve(expr, var)
        
        if not solutions:
            return f"No symbolic solutions were found for equation: '{equation} = 0'."
            
        text_descriptions = []
        latex_lines = []
        
        for idx, sol in enumerate(solutions):
            sol_latex = sp.latex(sol)
            latex_lines.append(f"{variable}_{{{idx+1}}} &= {sol_latex}")
            
            try:
                approx = float(sol.evalf())
                text_descriptions.append(f"Root {idx+1}: {sol} (~{approx:.4f})")
            except Exception:
                text_descriptions.append(f"Root {idx+1}: {sol}")
                
        combined_latex = "\\begin{aligned}\n" + " \\\\\n".join(latex_lines) + "\n\\end{aligned}"
        
        filename = f"math_{uuid.uuid4().hex[:12]}.png"
        filepath = os.path.join(os.getcwd(), filename)
        
        success = _compile_latex_to_png(combined_latex, filepath)
        if success:
            ACTIVE_MATH_ASSETS[channel_id] = {
                "filepath": filepath,
                "caption": f"Algebraic roots solved for `{equation} = 0`",
                "type": "math"
            }
            logger.info(f"Math image successfully generated and cached at: {filepath}")
            
        return f"Solutions solved: {', '.join(text_descriptions)}"
    except Exception as e:
        logger.error(f"Solve Equation failed for '{equation}': {e}")
        return f"[Error: Math solver failed to solve equation: {e}]"

def _compile_latex_to_png(latex_expression: str, filepath: str) -> bool:
    try:
        fig, ax = plt.subplots(figsize=(6, 1.5))
        fig.patch.set_facecolor('none')
        ax.patch.set_facecolor('none')
        
        formatted_latex = f"${latex_expression}$"
        
        ax.text(
            0.5, 0.5, formatted_latex,
            fontsize=20,
            color='white',
            ha='center', va='center',
            transform=ax.transAxes
        )
        
        ax.axis('off')
        plt.savefig(filepath, bbox_inches='tight', pad_inches=0.1, dpi=220, transparent=True)
        plt.close(fig)
        return True
    except Exception as e:
        logger.error(f"Failed compiling LaTeX to PNG: {e}")
        return False



async def plot_graph(channel_id: int, equation: str, x_min: float = -10.0, x_max: float = 10.0, title: str = "") -> str:
    try:
        x_sym = sp.Symbol('x')
        expr = sp.sympify(equation)
        f = sp.lambdify(x_sym, expr, 'numpy')
        
        x_vals = np.linspace(x_min, x_max, 400)
        y_vals = f(x_vals)
        
        fig, ax = plt.subplots(figsize=(7, 4.5))
        fig.patch.set_facecolor('#1e1f22')
        ax.set_facecolor('#1e1f22')
        
        ax.plot(x_vals, y_vals, color='#5865f2', linewidth=2.5, label=f"y = {sp.latex(expr)}")
        
        ax.spines['bottom'].set_color('#4f545c')
        ax.spines['top'].set_color('#4f545c')
        ax.spines['left'].set_color('#4f545c')
        ax.spines['right'].set_color('#4f545c')
        ax.tick_params(colors='white')
        ax.grid(True, color='#2f3136', linestyle='--', linewidth=0.8)
        
        if title:
            ax.set_title(title, color='white', fontsize=12, pad=15)
            
        filename = f"plot_{uuid.uuid4().hex[:12]}.png"
        filepath = os.path.join(os.getcwd(), filename)
        
        plt.savefig(filepath, bbox_inches='tight', pad_inches=0.2, dpi=200)
        plt.close(fig)
        
        ACTIVE_MATH_ASSETS[channel_id] = {
            "filepath": filepath,
            "caption": title if title else f"Plotted function graph of `y = {equation}`",
            "type": "graph"
        }
        logger.info(f"Graph image successfully generated and cached at: {filepath}")
        
        return f"Graph plotted successfully for function: 'y = {equation}' over range [{x_min}, {x_max}]."
    except Exception as e:
        logger.error(f"Plot Graph failed for '{equation}': {e}")
        return f"[Error: Graph tool failed to plot function: {e}]"



async def calculate_expression(expression: str) -> str:
    try:
        expr = sp.sympify(expression)
        result = expr.evalf()
        
        simplified = sp.simplify(expr)
        
        if simplified != result:
            return f"Result: {simplified} (~{result})"
        return f"Result: {result}"
    except Exception as e:
        logger.error(f"Calculate Expression failed for '{expression}': {e}")
        return f"[Error: Calculator failed to compute expression: {e}]"