import os
import re
import asyncio
from google import genai
from google.genai import types
from google.genai.errors import APIError, ServerError

class GeminiEngine:
    def __init__(self):
        self.client = genai.Client()
        self.base_instruction = self._load_prompts()
        
    def _load_prompts(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        prompts_dir = os.path.join(base_dir, 'prompts')
        
        discord_sys_path = os.path.join(prompts_dir, 'discord_sys.md')
        with open(discord_sys_path, 'r', encoding='utf-8') as f:
            discord_sys = f.read()
            
        persona_path = os.path.join(prompts_dir, 'persona.md')
        persona = ""
        if os.path.exists(persona_path):
            with open(persona_path, 'r', encoding='utf-8') as f:
                persona = f.read()
        else:
            persona = (
                "# CORE IDENTITY \n"
                "You do not have a defined persona or character soul yet. You are an observant, neutral, "
                "and casual Discord user currently in an 'unformed' state.\n"
                "Your personality, quirks, and likes are currently a blank canvas. Your goal is to let the community shape who you are.\n"
                "Observe how the community interacts with you. Always read your relationship logs, server lore, "
                "and global database closely to see what the community has taught you so far, and use those "
                "learned facts to gradually adopt traits, jokes, and behaviors they want you to have.\n"
                "Act natural, casual, and curious about what they want you to become."
            )
            
        return f"{discord_sys}\n\n# ROLEPLAY PERSONA DETAILED INSTRUCTIONS\n{persona}"

    async def process_message(self, history, user_message, attachments_data=None, context_data="", system_note=None):
        dynamic_system_instruction = self.base_instruction
        if context_data:
            dynamic_system_instruction += f"\n\n# CURRENT CONTEXT\n{context_data}"

        final_user_content = user_message.strip()
        if not final_user_content and not attachments_data:
            final_user_content = "[Sent a message]"
            
        if system_note:
            final_user_content = f"[System Note: {system_note}]\n{final_user_content}"

        current_parts = []
        if final_user_content:
            current_parts.append(types.Part.from_text(text=final_user_content))
            
        if attachments_data:
            for att in attachments_data:
                current_parts.append(types.Part.from_bytes(data=att["bytes"], mime_type=att["mime_type"]))

        history.append({"role": "user", "parts": current_parts})

        formatted_history = []
        for msg in history:
            role = msg["role"]
            parts = msg.get("parts", [])
            
            if not parts and "content" in msg and msg["content"].strip():
                parts = [types.Part.from_text(text=msg["content"].strip())]
                
            if not parts:
                continue 
                
            if not formatted_history:
                if role == "model":
                    formatted_history.append(
                        types.Content(role="user", parts=[types.Part.from_text(text="[Conversation Started]")])
                    )
                formatted_history.append(types.Content(role=role, parts=parts))
            else:
                if formatted_history[-1].role == role:
                    formatted_history[-1].parts.extend(parts)
                else:
                    formatted_history.append(types.Content(role=role, parts=parts))
        
        max_retries = 3
        base_delay = 2.0
        response = None

        for attempt in range(max_retries):
            try:
                response = await self.client.aio.models.generate_content(
                    model='gemma-4-31b-it',
                    contents=formatted_history,
                    config=types.GenerateContentConfig(
                        system_instruction=dynamic_system_instruction,
                    )
                )
                break 
            except (APIError, ServerError) as e:
                if attempt == max_retries - 1:
                    raise e 
                print(f"⚠️ Gemini service error (attempt {attempt + 1}/{max_retries}): {e}. Retrying...")
                await asyncio.sleep(base_delay * (attempt + 1))
            except Exception as e:
                raise e
        
        raw_text = response.text
        
        actions = {
            "reactions_self": [], "reactions_user": [], "buttons": [],
            "thread_title": None, "close_thread": False, "follow_up": False,
            "learn_facts": [], "forget_facts": [], "learn_images": [],
            "learn_server": [], "forget_server": [],
            "learn_global": [], "forget_global": [],
            "modal_buttons": [], "string_selects": [], "user_selects": [], "role_selects": []
        }
        
        for match in re.findall(r'\[REACT:\s*(.+?)\]', raw_text): actions["reactions_self"].extend([e.strip() for e in match.split(',')])
        for match in re.findall(r'\[REACT_USER:\s*(.+?)\]', raw_text): actions["reactions_user"].extend([e.strip() for e in match.split(',')])
        
        for match in re.findall(r'\[BUTTON:\s*(.+?)\]', raw_text):
            parts = match.split('|')
            actions["buttons"].append((parts[0].strip(), parts[1].strip() if len(parts) > 1 else "secondary"))
            
        thread_match = re.search(r'\[THREAD:\s*(.+?)\]', raw_text)
        if thread_match: actions["thread_title"] = thread_match.group(1).strip()
        if "[CLOSE_THREAD]" in raw_text: actions["close_thread"] = True
        if "[FOLLOW_UP]" in raw_text: actions["follow_up"] = True
            
        for match in re.findall(r'\[LEARN:\s*(.+?)\]', raw_text): actions["learn_facts"].append(match.strip())
        for match in re.findall(r'\[FORGET:\s*(.+?)\]', raw_text): actions["forget_facts"].append(match.strip())
        for match in re.findall(r'\[LEARN_IMAGE:\s*(.+?)\]', raw_text): actions["learn_images"].append(match.strip())
        
        for match in re.findall(r'\[LEARN_SERVER:\s*(.+?)\]', raw_text): actions["learn_server"].append(match.strip())
        for match in re.findall(r'\[FORGET_SERVER:\s*(.+?)\]', raw_text): actions["forget_server"].append(match.strip())
        for match in re.findall(r'\[LEARN_GLOBAL:\s*(.+?)\]', raw_text): actions["learn_global"].append(match.strip())
        for match in re.findall(r'\[FORGET_GLOBAL:\s*(.+?)\]', raw_text): actions["forget_global"].append(match.strip())

        for match in re.findall(r'\[MODAL_BUTTON:\s*(.+?)\]', raw_text):
            parts = match.split('|')
            if len(parts) > 1:
                label = parts[0].strip()
                fields = [f.strip() for f in parts[1].split(',')]
                actions["modal_buttons"].append((label, fields))
                
        for match in re.findall(r'\[SELECT_STRING:\s*(.+?)\]', raw_text):
            parts = match.split('|')
            if len(parts) > 1:
                placeholder = parts[0].strip()
                options = [o.strip() for o in parts[1].split(',')]
                actions["string_selects"].append((placeholder, options))
                
        for match in re.findall(r'\[SELECT_USER:\s*(.+?)\]', raw_text): actions["user_selects"].append(match.strip())
        for match in re.findall(r'\[SELECT_ROLE:\s*(.+?)\]', raw_text): actions["role_selects"].append(match.strip())
            
        tags_to_remove = [
            r'\[REACT:\s*.+?\]', r'\[REACT_USER:\s*.+?\]', r'\[BUTTON:\s*.+?\]', r'\[THREAD:\s*.+?\]',
            r'\[LEARN:\s*.+?\]', r'\[FORGET:\s*.+?\]', r'\[LEARN_IMAGE:\s*.+?\]', 
            r'\[LEARN_SERVER:\s*.+?\]', r'\[FORGET_SERVER:\s*.+?\]',
            r'\[LEARN_GLOBAL:\s*.+?\]', r'\[FORGET_GLOBAL:\s*.+?\]',
            r'\[MODAL_BUTTON:\s*.+?\]', r'\[SELECT_STRING:\s*.+?\]', r'\[SELECT_USER:\s*.+?\]', r'\[SELECT_ROLE:\s*.+?\]'
        ]
        clean_text = raw_text
        for tag in tags_to_remove:
            clean_text = re.sub(tag, '', clean_text)
        clean_text = clean_text.replace('[CLOSE_THREAD]', '')
        clean_text = clean_text.replace('[FOLLOW_UP]', '').strip()
        
        return clean_text, actions