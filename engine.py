import os
import re
from google import genai
from google.genai import types

class GeminiEngine:
    def __init__(self):
        self.client = genai.Client()
        
        self.system_instruction = self._load_prompts()
        
    def _load_prompts(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        prompts_dir = os.path.join(base_dir, 'prompts')
        
        discord_sys_path = os.path.join(prompts_dir, 'discord_sys.md')
        
        with open(discord_sys_path, 'r', encoding='utf-8') as f:
            discord_sys = f.read()
            
        return discord_sys

    async def process_message(self, history, user_message):
        formatted_history = []
        for msg in history[:-1]:
            formatted_history.append(
                types.Content(role=msg["role"], parts=[types.Part.from_text(text=msg["parts"][0])])
            )
            
        formatted_history.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))
        
        response = await self.client.aio.models.generate_content(
            model='gemma-4-31b-it',
            contents=formatted_history,
            config=types.GenerateContentConfig(
                system_instruction=self.system_instruction,
            )
        )
        
        raw_text = response.text
        
        reactions = []
        react_matches = re.findall(r'\[REACT:\s*(.+?)\]', raw_text)
        for match in react_matches:
            emojis = [e.strip() for e in match.split(',')]
            reactions.extend(emojis)
            
        clean_text = re.sub(r'\[REACT:\s*.+?\]', '', raw_text).strip()
        
        components = None 
        
        return clean_text, reactions, components