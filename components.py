import discord

def get_style(style_str: str) -> discord.ButtonStyle:
    styles = {
        "primary": discord.ButtonStyle.primary,
        "secondary": discord.ButtonStyle.secondary,
        "success": discord.ButtonStyle.success,
        "danger": discord.ButtonStyle.danger
    }
    return styles.get(style_str.lower().strip(), discord.ButtonStyle.secondary)

class DynamicModal(discord.ui.Modal):
    def __init__(self, title: str, fields: list, submit_callback):
        super().__init__(title=title[:45])
        self.submit_callback = submit_callback
        self.text_inputs = []
        
        for field in fields[:5]:
            inp = discord.ui.TextInput(
                label=field[:45], 
                custom_id=field[:45],
                style=discord.TextStyle.paragraph, 
                required=False
            )
            self.add_item(inp)
            self.text_inputs.append(inp)

    async def on_submit(self, interaction: discord.Interaction):
        results = []
        for inp in self.text_inputs:
            if inp.value.strip():
                results.append(f"{inp.custom_id}: {inp.value}")
        
        result_str = ", ".join(results) if results else "User submitted an empty form."
        
        await interaction.response.defer()
        await self.submit_callback(interaction, result_str)

class DynamicAIView(discord.ui.View):
    def __init__(self, actions: dict, interaction_callback):
        super().__init__(timeout=None) 
        self.interaction_callback = interaction_callback
        self._build_view(actions)

    def disable_all(self):
        for child in self.children:
            child.disabled = True

    def _build_view(self, actions: dict):
        for label, color in actions.get("buttons", []):
            btn = discord.ui.Button(style=get_style(color), label=label[:80])
            
            async def btn_callback(interaction: discord.Interaction, btn_label=label):
                self.disable_all()
                await interaction.response.edit_message(view=self)
                await self.interaction_callback(interaction, f"User clicked button: '{btn_label}'")
                
            btn.callback = btn_callback
            self.add_item(btn)

        for label, fields in actions.get("modal_buttons", []):
            btn = discord.ui.Button(style=discord.ButtonStyle.primary, label=label[:80])
            
            async def modal_btn_callback(interaction: discord.Interaction, modal_title=label, modal_fields=fields):
                async def modal_submitted(modal_interaction: discord.Interaction, result_str: str):
                    self.disable_all()
                    await modal_interaction.message.edit(view=self)
                    await self.interaction_callback(modal_interaction, f"User submitted form '{modal_title}' with data: {result_str}")
                    
                await interaction.response.send_modal(DynamicModal(modal_title, modal_fields, modal_submitted))
                
            btn.callback = modal_btn_callback
            self.add_item(btn)

        for placeholder, options in actions.get("string_selects", []):
            select_options = [discord.SelectOption(label=opt[:100]) for opt in options[:25]]
            select = discord.ui.Select(placeholder=placeholder[:100], options=select_options)
            
            async def select_callback(interaction: discord.Interaction, sel=select):
                self.disable_all()
                await interaction.response.edit_message(view=self)
                selected_values = ", ".join(sel.values)
                await self.interaction_callback(interaction, f"User selected '{selected_values}' from dropdown '{sel.placeholder}'")
                
            select.callback = select_callback
            self.add_item(select)

        for placeholder in actions.get("user_selects", []):
            select = discord.ui.UserSelect(placeholder=placeholder[:100])
            
            async def user_sel_callback(interaction: discord.Interaction, sel=select):
                self.disable_all()
                await interaction.response.edit_message(view=self)
                selected_users = ", ".join([user.display_name for user in sel.values])
                await self.interaction_callback(interaction, f"User selected members '{selected_users}' from dropdown '{sel.placeholder}'")
                
            select.callback = user_sel_callback
            self.add_item(select)

        for placeholder in actions.get("role_selects", []):
            select = discord.ui.RoleSelect(placeholder=placeholder[:100])
            
            async def role_sel_callback(interaction: discord.Interaction, sel=select):
                self.disable_all()
                await interaction.response.edit_message(view=self)
                selected_roles = ", ".join([role.name for role in sel.values])
                await self.interaction_callback(interaction, f"User selected roles '{selected_roles}' from dropdown '{sel.placeholder}'")
                
            select.callback = role_sel_callback
            self.add_item(select)