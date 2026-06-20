
import logging
import asyncio
from typing import Dict, Any, Optional, Tuple
import discord
from tools.message_builder.mb_compiler import compile_dsl_payload, ASTValidationError
from tools.message_builder.mb_views import DSLRuntimeView

logger = logging.getLogger("MBTool")

def build_layout_generator_prompt(initial_prompt: str, ai_banter_reply: str, extracted_instructions: str) -> str:
    return (
        f"[System Sandbox Turn: You are now compiling the V2 Layout code. "
        f"Generate ONLY the Python DSL code block representing the interface. "
        f"Do NOT generate any conversational banter, intros, or outros. "
        f"Conform strictly to mb_stubs.py.\n\n"
        f"=== CONTEXTUAL INPUTS ===\n"
        f"User's Original Prompt: \"{initial_prompt}\"\n"
        f"AI's Conversational Banter: \"{ai_banter_reply}\"\n"
        f"Extracted Layout Instructions: \"{extracted_instructions}\"\n\n"
        f"=== COMPILATION CONSTRAINTS (MANDATORY) ===\n"
        f"1. Every Section MUST contain a valid accessory component passed as the keyword-only "
        f"argument 'accessory' (e.g. Button). This parameter is strictly mandatory. If you do not want to display "
        f"an accessory, use a standard Container(TextDisplay(...)) instead of a Section!\n"
        f"2. To make a container look integrated natively into Discord, omit the accent_colour parameter.\n"
        f"3. In event callbacks (like 'on_click' or 'on_select'), you can chain multiple actions "
        f"using lists, but Action.open_modal() CANNOT be combined in lists or triggered inside Modal submits.]"
    )

async def build_message_layout(bot_instance, channel, dsl_script_code: str, initial_prompt: str, user_app_session_id: Optional[int] = None) -> Tuple[Optional[discord.ui.LayoutView], Optional[str]]:
    try:
        layout_cfg = compile_dsl_payload(dsl_script_code)
        
        rendered_view = DSLRuntimeView(
            bot_instance=bot_instance, 
            channel=channel, 
            dsl_view_config=layout_cfg, 
            initial_prompt=initial_prompt, 
            user_app_session_id=user_app_session_id
        )
        return rendered_view, None
    except ASTValidationError as ast_err:
        err_msg = str(ast_err)
        if "int() can't convert" in err_msg:
            err_msg = "Syntax Error: accent_colour must be passed as an integer (e.g. 0x5865F2) or a hex string (e.g. '0x5865F2')."
        elif "cannot mix both url" in err_msg:
            err_msg = "Syntax Error: A Button with style='link' and a 'url' cannot have an 'id' or click events. Remove 'id' or 'on_click' from link buttons."
        logger.warning(f"DSL Compilation Blocked: {err_msg}")
        return None, err_msg
    except Exception as exc:
        err_msg = str(exc)
        if "int() can't convert" in err_msg:
            err_msg = "Syntax Error: accent_colour must be passed as an integer (e.g. 0x5865F2) or a hex string (e.g. '0x5865F2')."
        elif "cannot mix both url" in err_msg:
            err_msg = "Syntax Error: A Button with style='link' and a 'url' cannot have an 'id' or trigger click events. Remove 'id' or 'on_click' from link buttons."
        logger.error(f"Uncaught DSL Parsing Crash: {exc}")
        return None, f"Runtime Compiler Error: {err_msg}"



def inject_message_builder_hook(bot_class_instance):
    
    async def trigger_message_builder_ai_turn(self, interaction: Optional[discord.Interaction], instruction_payload: str):
        author = interaction.user if interaction else self.user
        channel = interaction.channel if interaction else None
        if not channel:
            return
            
        async with channel.typing():
            history = self.history_tracker.get_formatted_history(channel.id)
            context = self._compile_server_context(channel.guild, author) if hasattr(channel, 'guild') else ""
            
            is_dm = isinstance(channel, discord.DMChannel)
            target_id = author.id if is_dm else channel.id
            config_state = await self.get_config(target_id, is_dm)
            
            if "Memory Journals" in config_state.get("system_tools", []):
                memories = await self._compile_memories_for_ai(author, channel, query_text=instruction_payload)
            else:
                memories = {"user_memories": "", "server_lore": "", "global_database": ""}
            
            custom_prompt = config_state.get("system_prompt", "").strip()
            base_sys_prompt = custom_prompt if custom_prompt else self.chat_handler.system_instruction
            
            prompt = (
                f"{base_sys_prompt}\n\n"
                f"--- CALLBACK CONTEXT ---\n"
                f"The user interacted with your active V2 component. "
                f"They triggered the callback payload: '{instruction_payload}'.\n"
                f"Analyze this selection context and generate your response. If they want to perform "
                f"another layout change, you may output another `[BUILD_MESSAGE]` payload. "
                f"Otherwise, reply with natural chat banter."
            )
            
            await self._execute_ai_with_retries(
                prompt=prompt, history=history, attachments=[], display_name=author.display_name,
                memory_dict=memories, context=context, channel=channel, author=author,
                is_dm=is_dm, original_message=None, config=config_state
            )

    bot_class_instance.trigger_message_builder_ai_turn = trigger_message_builder_ai_turn.__get__(bot_class_instance)