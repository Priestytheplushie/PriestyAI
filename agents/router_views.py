import discord
import logging
import uuid
import re
from typing import Optional, List, Dict, Any

logger = logging.getLogger("AgentRouterViews")

DESCRIPTIONS = {
    "deep_research": (
        "**Web Browsing & Synthesis Protocol**\n"
        "I will search public search indexes, parse documentation, scrape target URLs, "
        "cross-corroborate multiple information sources, and write a complete analytical report.\n\n"
        "**Planned Tasks:**\n"
        "• Execute multiple parallel web searches on your query topic\n"
        "• Extract and clean markdown from top-ranked websites\n"
        "• Compile findings into an attached Markdown or Word document report complete with citations"
    ),
    "react": (
        "**Server Inspection & Context Protocol**\n"
        "I will inspect server channels, scan message histories, analyze user profile metadata, "
        "and process context snapshots using my native, secure command tools.\n\n"
        "**Planned Tasks:**\n"
        "• Query text channel history indexes and active forum threads\n"
        "• Match activity metrics of target users\n"
        "• Reference attached context snippets and qualitative user profile logs"
    ),
}


class ResearchPanelTabView(discord.ui.LayoutView):
    """
    An ephemeral Components v2 tabbed card displaying active research progress.
    Tab 1 (Thinking): Displays current plans, gaps, and recursive reasoning steps.
    Tab 2 (Sources): Displays a paginated index of all scraped web URLs.
    Tab 3 (Live Drafts): Displays compiled draft chapters dynamically as they complete.
    """

    def __init__(self, session_instance, active_tab: str = "thinking"):
        super().__init__(timeout=600.0)
        self.session = session_instance
        self.active_tab = active_tab

        self.source_page = 0
        self.sources_per_page = 5

        self.rebuild_layout()

    def rebuild_layout(self) -> None:
        self.clear_items()
        container = discord.ui.Container()

        if self.active_tab == "thinking":
            container.add_item(
                discord.ui.TextDisplay(
                    content="### 🧠 Deep Research - Active Thinking & Reasoning"
                )
            )
            container.add_item(
                discord.ui.Separator(
                    spacing=discord.SeparatorSpacing.small, visible=True
                )
            )

            thinking_content = (
                f"**📋 Active Research Spec Plan:**\n"
                f"{self.session.plan_text}\n\n"
                f"**⚡ Current Reasoning Step:**\n"
                f"> {self.session.current_thought}"
            )
            container.add_item(discord.ui.TextDisplay(content=thinking_content[:1900]))

        elif self.active_tab == "sources":
            total_sources = len(self.session.sources)
            total_pages = (
                total_sources + self.sources_per_page - 1
            ) // self.sources_per_page
            total_pages = max(1, total_pages)

            if self.source_page >= total_pages:
                self.source_page = total_pages - 1
            self.source_page = max(0, self.source_page)

            container.add_item(
                discord.ui.TextDisplay(
                    content=f"### 📋 Deep Research - Sources Browsed ({total_sources} websites | Page {self.source_page + 1}/{total_pages})"
                )
            )
            container.add_item(
                discord.ui.Separator(
                    spacing=discord.SeparatorSpacing.small, visible=True
                )
            )

            start_idx = self.source_page * self.sources_per_page
            end_idx = start_idx + self.sources_per_page
            page_sources = self.session.sources[start_idx:end_idx]

            source_lines = []
            for idx, src in enumerate(page_sources):
                absolute_idx = start_idx + idx + 1
                url = src.get("url", "")
                title = src.get("title", "Untitled Page")

                emoji = "🌐"
                if "reddit.com" in url:
                    emoji = "💬"
                elif "wikipedia.org" in url or "wiki" in url:
                    emoji = "📚"
                elif "github.com" in url:
                    emoji = "💻"
                elif "youtube.com" in url:
                    emoji = "🎥"
                elif "hosting" in url or "node" in url:
                    emoji = "🖥️"

                domain = url.split("//")[-1].split("/")[0].replace("www.", "")
                source_lines.append(
                    f"{absolute_idx}. {emoji} [**{domain}** — *{title[:65]}*]({url})"
                )

            sources_content = (
                "\n".join(source_lines)
                if source_lines
                else "*No websites have been browsed yet.*"
            )
            container.add_item(discord.ui.TextDisplay(content=sources_content[:1800]))

        else:
            container.add_item(
                discord.ui.TextDisplay(
                    content="### 📝 Deep Research - Live Draft Preview"
                )
            )
            container.add_item(
                discord.ui.Separator(
                    spacing=discord.SeparatorSpacing.small, visible=True
                )
            )

            draft_content = ""
            partial_drafts = getattr(self.session, "partial_drafts", {})

            if partial_drafts:
                for chapter_title, text in partial_drafts.items():

                    snippet = text[:500] + ("..." if len(text) > 500 else "")
                    draft_content += f"**{chapter_title}**\n{snippet}\n\n"
            else:
                draft_content = "*No chapters have finished compiling yet. Chapters will progressively appear here as the research steps complete.*"

            container.add_item(discord.ui.TextDisplay(content=draft_content[:1800]))

        container.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large, visible=False)
        )

        if (
            self.active_tab == "sources"
            and len(self.session.sources) > self.sources_per_page
        ):
            pagination_row = discord.ui.ActionRow()

            prev_page_btn = discord.ui.Button(
                label="Previous Page",
                style=discord.ButtonStyle.secondary,
                emoji="◀",
                disabled=(self.source_page <= 0),
            )

            async def on_prev_page(interaction: discord.Interaction):
                self.source_page -= 1
                self.rebuild_layout()
                await interaction.response.edit_message(view=self)

            prev_page_btn.callback = on_prev_page
            pagination_row.add_item(prev_page_btn)

            next_page_btn = discord.ui.Button(
                label="Next Page",
                style=discord.ButtonStyle.secondary,
                emoji="▶",
                disabled=(
                    (self.source_page + 1) * self.sources_per_page
                    >= len(self.session.sources)
                ),
            )

            async def on_next_page(interaction: discord.Interaction):
                self.source_page += 1
                self.rebuild_layout()
                await interaction.response.edit_message(view=self)

            next_page_btn.callback = on_next_page
            pagination_row.add_item(next_page_btn)

            container.add_item(pagination_row)
            container.add_item(
                discord.ui.Separator(
                    spacing=discord.SeparatorSpacing.small, visible=False
                )
            )

        nav_row = discord.ui.ActionRow()

        thinking_btn = discord.ui.Button(
            label="Show Thinking",
            style=(
                discord.ButtonStyle.primary
                if self.active_tab == "thinking"
                else discord.ButtonStyle.secondary
            ),
            emoji="🧠",
        )

        async def on_thinking_click(interaction: discord.Interaction):
            self.active_tab = "thinking"
            self.rebuild_layout()
            await interaction.response.edit_message(view=self)

        thinking_btn.callback = on_thinking_click
        nav_row.add_item(thinking_btn)

        sources_btn = discord.ui.Button(
            label=f"Sources ({len(self.session.sources)})",
            style=(
                discord.ButtonStyle.primary
                if self.active_tab == "sources"
                else discord.ButtonStyle.secondary
            ),
            emoji="📋",
        )

        async def on_sources_click(interaction: discord.Interaction):
            self.active_tab = "sources"
            self.rebuild_layout()
            await interaction.response.edit_message(view=self)

        sources_btn.callback = on_sources_click
        nav_row.add_item(sources_btn)

        draft_btn = discord.ui.Button(
            label="Live Draft",
            style=(
                discord.ButtonStyle.primary
                if self.active_tab == "draft"
                else discord.ButtonStyle.secondary
            ),
            emoji="📝",
        )

        async def on_draft_click(interaction: discord.Interaction):
            self.active_tab = "draft"
            self.rebuild_layout()
            await interaction.response.edit_message(view=self)

        draft_btn.callback = on_draft_click
        nav_row.add_item(draft_btn)

        container.add_item(nav_row)
        self.add_item(container)


class ViewResearchButton(discord.ui.Button):
    def __init__(self, session_instance):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="View Research Details",
            emoji="🧠",
            custom_id=f"deep_res_btn_{session_instance.thread_id}",
        )
        self.session = session_instance

    async def callback(self, interaction: discord.Interaction):

        tab_view = ResearchPanelTabView(session_instance=self.session)
        await interaction.response.send_message(view=tab_view, ephemeral=True)


class AgentLaunchModal(discord.ui.Modal):
    def __init__(
        self,
        bot_instance,
        prompt: str,
        user: discord.User,
        guild_list: list,
        user_contexts: list,
        message_contexts: list,
    ):
        super().__init__(title="Configure Agent Profiles")
        self.bot = bot_instance
        self.prompt = prompt
        self.user = user

        info_lines = [
            "📂 **Saved Profiles Index**",
            "Choose mutual servers or saved template snapshots below to attach to this workspace.",
        ]

        all_aliases = [
            f"`{c.get('alias')}`"
            for c in user_contexts + message_contexts
            if c.get("alias")
        ]
        if all_aliases:
            info_lines.append(
                f"Saved: {', '.join(all_aliases[:10])}"
                + ("..." if len(all_aliases) > 10 else "")
            )
        else:
            info_lines.append(
                "No saved profiles exist yet. Use the message options menu to save snapshot templates!"
            )

        self.info_box = discord.ui.TextDisplay(content="\n".join(info_lines)[:1500])
        self.add_item(self.info_box)

        server_options = []
        for guild in guild_list[:25]:
            server_options.append(
                discord.SelectOption(
                    label=guild.name[:100],
                    value=f"server_{guild.id}",
                    description=f"Server ID: {guild.id}",
                )
            )
        if not server_options:
            server_options.append(
                discord.SelectOption(label="No mutual servers", value="none")
            )

        self.server_select = discord.ui.Select(
            custom_id="agent_modal_server",
            placeholder="Select target server context...",
            options=server_options,
            min_values=0,
            max_values=1,
            required=False,
        )
        self.add_item(
            discord.ui.Label(
                text="📁 Target Server",
                description="Select server's channels/messages the AI will audit (Optional).",
                component=self.server_select,
            )
        )

        user_options = []
        for u_ctx in user_contexts[:25]:
            alias = u_ctx.get("alias", "unknown")
            desc = u_ctx.get("notes") or "User profile template snapshot"
            user_options.append(
                discord.SelectOption(
                    label=alias[:100], value=alias, description=desc[:100]
                )
            )
        if not user_options:
            user_options.append(
                discord.SelectOption(label="No saved user snapshots", value="none")
            )

        self.user_select = discord.ui.Select(
            custom_id="agent_modal_user_ctx",
            placeholder="Select user snapshot context...",
            options=user_options,
            min_values=0,
            max_values=min(25, len(user_options)),
            required=False,
        )
        self.add_item(
            discord.ui.Label(
                text="👤 User Profiles",
                description="Attach saved user snapshots to guide analysis (Optional).",
                component=self.user_select,
            )
        )

        msg_options = []
        for m_ctx in message_contexts[:25]:
            alias = m_ctx.get("alias", "unknown")
            desc = m_ctx.get("notes") or "Message transcript snapshot template"
            msg_options.append(
                discord.SelectOption(
                    label=alias[:100], value=alias, description=desc[:100]
                )
            )
        if not msg_options:
            msg_options.append(
                discord.SelectOption(label="No saved transcripts", value="none")
            )

        self.message_select = discord.ui.Select(
            custom_id="agent_modal_msg_ctx",
            placeholder="Select message transcript context...",
            options=msg_options,
            min_values=0,
            max_values=min(25, len(msg_options)),
            required=False,
        )
        self.add_item(
            discord.ui.Label(
                text="💬 Message Transcripts",
                description="Attach message transcripts to guide analysis (Optional).",
                component=self.message_select,
            )
        )

        self.additional_input = discord.ui.TextInput(
            custom_id="agent_modal_additional",
            style=discord.TextStyle.short,
            placeholder="e.g. custom_audit, debug_leak",
            required=False,
            max_length=500,
        )
        self.add_item(
            discord.ui.Label(
                text="✍️ Additional Profiles",
                description="Type extra profile names separated by commas for overflow (Optional).",
                component=self.additional_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.message.edit(
                view=None,
                content="✅ *This analysis session has been configured and initialized.*",
            )
        except Exception as e:
            logger.warning(f"Could not disable parent setup message: {e}")

        initial_msg = None

        raw_data = interaction.data if interaction.data else {}
        submitted_vals = {}

        def walk_components(comps):
            for c in comps:
                c_id = c.get("custom_id")
                if c_id:
                    if "value" in c:
                        submitted_vals[c_id] = [c["value"]]
                    elif "values" in c:
                        submitted_vals[c_id] = c["values"]

                sub = c.get("components")
                if sub:
                    walk_components(sub)

                single = c.get("component")
                if single:
                    walk_components([single])

        walk_components(raw_data.get("components", []))

        initial_msg = await interaction.followup.send(
            "⏳ **Initializing your private Agent Workspace thread...**",
            ephemeral=True,
            wait=True,
        )

        selected_server_id = None
        server_vals = submitted_vals.get("agent_modal_server", [])
        if server_vals and server_vals[0] != "none":
            srv_val = server_vals[0]
            if srv_val.startswith("server_"):
                selected_server_id = int(srv_val.split("_", 1)[1])

        selected_user_aliases = [
            v for v in submitted_vals.get("agent_modal_user_ctx", []) if v != "none"
        ]
        selected_msg_aliases = [
            v for v in submitted_vals.get("agent_modal_msg_ctx", []) if v != "none"
        ]

        additional_val = ""
        additional_list = submitted_vals.get("agent_modal_additional", [])
        if additional_list:
            additional_val = additional_list[0]

        extra_aliases = []
        if additional_val:
            extra_aliases = [
                a.strip().lower() for a in additional_val.split(",") if a.strip()
            ]

        all_aliases = list(
            dict.fromkeys(selected_user_aliases + selected_msg_aliases + extra_aliases)
        )

        appended_context_data = ""
        context_summary_log = "None"
        if all_aliases:
            context_summary_log = ", ".join([f"`{a}`" for a in all_aliases])
            appended_context_data = await self.bot._compile_selected_context_payloads(
                self.user.id, ",".join(all_aliases)
            )

        target_server_log = "Local Guild Only"
        if selected_server_id:
            guild_obj = self.bot.get_guild(selected_server_id)
            if guild_obj:
                target_server_log = f"{guild_obj.name} (ID: {selected_server_id})"
            else:
                target_server_log = f"Unknown Server (ID: {selected_server_id})"

        channel = interaction.channel
        thread = None
        if isinstance(channel, discord.Thread):
            thread = channel
            await thread.add_user(interaction.user)
        else:
            try:
                from core.bot import generate_slug_from_prompt, sanitize_channel_name

                user_part = sanitize_channel_name(interaction.user.display_name[:12])
                slug = generate_slug_from_prompt(self.prompt)
                thread_name = f"agent-{user_part}-{slug}"
                thread = await channel.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.private_thread,
                    auto_archive_duration=1440,
                )
                await thread.add_user(interaction.user)
            except Exception as e:
                if initial_msg:
                    await interaction.followup.edit_message(
                        message_id=initial_msg.id,
                        content=f"❌ **Error:** Failed to spawn private workspace thread: {e}. Ensure thread permissions are active.",
                    )
                return

        from agents.discord_react.agent import AgentSession

        session = AgentSession(
            thread_id=thread.id,
            user_id=interaction.user.id,
            prompt=self.prompt,
            loaded_contexts=appended_context_data,
            channel=thread,
        )
        session.target_guild_id = selected_server_id
        session.additional_instructions = additional_val
        self.bot.active_agent_sessions[thread.id] = session

        from core.ui_components import AgentPreStartView

        view = AgentPreStartView(thread.id)
        checklist_content = (
            f"📋 **Agent Pre-Start Checklist (Discord Analysis Mode)**\n"
            f"----------------------------------------\n"
            f"📁 Target Server: {target_server_log}\n"
            f"📂 Loaded Contexts: {context_summary_log}\n"
            f'🎯 Primary Task: "{self.prompt}"\n\n'
            f"Review the configuration above. You can add extra directions or start execution below."
        )
        checklist_msg = await thread.send(content=checklist_content, view=view)
        view.checklist_msg = checklist_msg

        if initial_msg:
            await interaction.followup.edit_message(
                message_id=initial_msg.id,
                content=f"✅ **Agent Workspace Initialized!** Proceed directly to your private thread: {thread.mention}",
            )


class DeepResearchLaunchModal(discord.ui.Modal):
    def __init__(self, bot_instance, prompt: str, user: discord.User):
        super().__init__(title="Configure Deep Research")
        self.bot = bot_instance
        self.prompt = prompt
        self.user = user

        self.depth_select = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(
                    label="Quick Scan (5 sources)",
                    value="brief",
                    description="Fast, high-level summary. Takes ~10 mins.",
                ),
                discord.RadioGroupOption(
                    label="Deep Dive (15 sources)",
                    value="standard",
                    description="Balanced analysis, gap checks. Takes ~16 mins.",
                    default=True,
                ),
                discord.RadioGroupOption(
                    label="Exhaustive Audit (30 sources)",
                    value="exhaustive",
                    description="Thorough check, recursive crawls. Takes ~25 mins.",
                ),
                discord.RadioGroupOption(
                    label="Extreme Core (60+ sources)",
                    value="extreme",
                    description="Deep recursive crawls, compiles data charts. Takes ~30-45 mins.",
                ),
            ]
        )
        self.add_item(
            discord.ui.Label(
                text="📊 Research Scope / Depth",
                description="Select research scale and iteration depth limit.",
                component=self.depth_select,
            )
        )

        self.format_select = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(
                    label="Markdown (.md)",
                    value="markdown",
                    description="Clean, lightweight layout. Copy/paste friendly.",
                ),
                discord.RadioGroupOption(
                    label="Word Document (.docx) - Corporate",
                    value="docx",
                    description="Polished document. Includes custom cover & charts.",
                    default=True,
                ),
                discord.RadioGroupOption(
                    label="MLA Essay (.docx) - Academic",
                    value="mla",
                    description="Double-spaced MLA format with Works Cited & parenthetical citations.",
                ),
            ]
        )
        self.add_item(
            discord.ui.Label(
                text="🎯 Target File Format",
                description="Select final compiled report file format.",
                component=self.format_select,
            )
        )

        self.flavor_select = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(
                    label="Executive Summary",
                    value="executive",
                    description="High-level value mapping, cost audits, and price guides.",
                    default=True,
                ),
                discord.RadioGroupOption(
                    label="Technical Deep Dive",
                    value="technical",
                    description="Dense hardware specs, network latencies, and system audits.",
                ),
                discord.RadioGroupOption(
                    label="Comparative Matrix",
                    value="matrix",
                    description="Side-by-side matrices and clear use-case recommendations.",
                ),
            ]
        )
        self.add_item(
            discord.ui.Label(
                text="📝 Report Formatting Flavor",
                description="Select analysis focus outline template.",
                component=self.flavor_select,
            )
        )

        self.domains_input = discord.ui.TextInput(
            custom_id="research_modal_domains",
            style=discord.TextStyle.short,
            placeholder="e.g. wikipedia.org, -reddit.com",
            required=False,
            max_length=200,
        )
        self.add_item(
            discord.ui.Label(
                text="🌐 Domain Filters (Optional)",
                description="Filter focus or excluded domains (e.g., -reddit.com).",
                component=self.domains_input,
            )
        )

        self.guidelines_input = discord.ui.TextInput(
            custom_id="research_modal_guidelines",
            style=discord.TextStyle.long,
            placeholder="e.g. Focus on retail pricing. Ignore enterprise statistics.",
            required=False,
            max_length=500,
        )
        self.add_item(
            discord.ui.Label(
                text="✍️ Extra Guidelines (Optional)",
                description="Type specific research rules or guidelines.",
                component=self.guidelines_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            await interaction.message.edit(
                view=None,
                content="✅ *This research session has been configured and initialized.*",
            )
        except Exception as e:
            logger.warning(f"Could not disable parent setup message: {e}")

        initial_msg = await interaction.followup.send(
            "⏳ **Initializing your private Deep Research workspace...**",
            ephemeral=True,
            wait=True,
        )

        depth = self.depth_select.value if self.depth_select.value else "standard"
        format_type = (
            self.format_select.value if self.format_select.value else "markdown"
        )
        flavor = self.flavor_select.value if self.flavor_select.value else "executive"
        domains = self.domains_input.value.strip() if self.domains_input.value else ""
        guidelines = (
            self.guidelines_input.value.strip() if self.guidelines_input.value else ""
        )

        channel = interaction.channel
        thread = None
        try:
            from core.bot import generate_slug_from_prompt, sanitize_channel_name

            user_part = sanitize_channel_name(interaction.user.display_name[:12])
            slug = generate_slug_from_prompt(self.prompt)
            thread_name = f"research-{user_part}-{slug}"
            thread = await channel.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                auto_archive_duration=1440,
            )
            await thread.add_user(interaction.user)
        except Exception as e:
            if initial_msg:
                await interaction.followup.edit_message(
                    message_id=initial_msg.id,
                    content=f"❌ **Error:** Failed to spawn thread: {e}",
                )
            return

        from agents.deep_research.agent import DeepResearchSession

        session = DeepResearchSession(
            thread_id=thread.id,
            user_id=interaction.user.id,
            prompt=self.prompt,
            loaded_contexts="",
            channel=thread,
            depth=depth,
            format_type=format_type,
            flavor=flavor,
            domains_filter=domains,
            guidelines=guidelines,
        )
        self.bot.active_agent_sessions[thread.id] = session

        self.bot.loop.create_task(session.execute_tick(self.bot))

        if initial_msg:
            await interaction.followup.edit_message(
                message_id=initial_msg.id,
                content=f"✅ **Research Workspace Initialized!** Proceed directly to your private thread: {thread.mention}",
            )


class AgentRouterView(discord.ui.LayoutView):
    """
    Components v2 card representing an active routing choice.
    Allows user to switch modes in-place or start configuring parameters.
    """

    def __init__(
        self,
        bot_instance,
        user: discord.User,
        prompt: str,
        initial_agent: str,
        initial_plan: str,
    ):
        super().__init__(timeout=600.0)
        self.bot = bot_instance
        self.user = user
        self.prompt = prompt
        self.agent_type = initial_agent
        self.plan_text = initial_plan

        self.rebuild_layout()

    def rebuild_layout(self) -> None:
        self.clear_items()

        container = discord.ui.Container()

        title_icon = "🔍" if self.agent_type == "deep_research" else "🛠️"
        title_label = (
            "Deep Research"
            if self.agent_type == "deep_research"
            else "Discord Analysis"
        )

        container.add_item(
            discord.ui.TextDisplay(
                content="*Review my proposed plan below. Click Configure to proceed, or Change Type to manually toggle the operational mode.*"
            )
        )
        container.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small, visible=True)
        )

        container.add_item(
            discord.ui.TextDisplay(
                content=f"### {title_icon} Agent Session - {title_label}"
            )
        )
        container.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small, visible=True)
        )

        plan_block = (
            f"**Deducted Plan:**\n"
            f"> {self.plan_text}\n\n"
            f"{DESCRIPTIONS[self.agent_type]}"
        )
        container.add_item(discord.ui.TextDisplay(content=plan_block))
        container.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large, visible=False)
        )

        action_row = discord.ui.ActionRow()

        config_btn = discord.ui.Button(
            label="Configure and Start Agent",
            style=discord.ButtonStyle.success,
            emoji="🚀",
        )
        config_btn.callback = self.on_configure_click
        action_row.add_item(config_btn)

        change_btn = discord.ui.Button(
            label="Change Type", style=discord.ButtonStyle.secondary, emoji="🔄"
        )
        change_btn.callback = self.on_change_click
        action_row.add_item(change_btn)

        container.add_item(action_row)
        self.add_item(container)

    async def on_configure_click(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ This setup panel belongs to another user.", ephemeral=True
            )
            return

        if self.agent_type == "deep_research":
            modal = DeepResearchLaunchModal(
                bot_instance=self.bot, prompt=self.prompt, user=self.user
            )
            await interaction.response.send_modal(modal)

        else:
            mutual_servers = []
            for guild in self.bot.guilds:
                member = guild.get_member(self.user.id)
                if member is not None:
                    mutual_servers.append(guild)

            import core.memory as memory

            all_contexts = await memory.fetch_all_contexts_for_user(
                self.bot, self.bot.brain_server_id, self.user.id
            )
            user_contexts = [
                c for c in all_contexts if c.get("type") == "User Profile Snapshot"
            ]
            message_contexts = [
                c for c in all_contexts if c.get("type") == "Message Transcript Snippet"
            ]

            modal = AgentLaunchModal(
                bot_instance=self.bot,
                prompt=self.prompt,
                user=self.user,
                guild_list=mutual_servers,
                user_contexts=user_contexts,
                message_contexts=message_contexts,
            )
            await interaction.response.send_modal(modal)

    async def on_change_click(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "❌ This setup panel belongs to another user.", ephemeral=True
            )
            return

        self.clear_items()

        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(
                content="### 🔄 Switch Agent Session Mode\nSelect which operational protocol you wish to apply to this prompt:"
            )
        )
        container.add_item(
            discord.ui.Separator(spacing=discord.SeparatorSpacing.small, visible=True)
        )

        select = discord.ui.Select(
            placeholder="Choose an agent type...",
            options=[
                discord.SelectOption(
                    label="Deep Research",
                    value="deep_research",
                    description="Search public search indexes, parse documentation, and compile reports.",
                    emoji="🔍",
                ),
                discord.SelectOption(
                    label="Discord Analysis",
                    value="react",
                    description="Audits roles, member snapshot activity, and channel text message history logs.",
                    emoji="🛠️",
                ),
            ],
        )

        async def on_select(select_interaction: discord.Interaction):
            self.agent_type = select.values[0]

            if self.agent_type == "deep_research":
                self.plan_text = "Initiating deep research protocols across public indexes to gather, synthesize, and compile report details."
            else:
                self.plan_text = "Inspecting available server channels, auditing role tables, and compiling member snapshot metrics."

            self.rebuild_layout()
            await select_interaction.response.edit_message(view=self)

        select.callback = on_select

        action_row = discord.ui.ActionRow()
        action_row.add_item(select)
        container.add_item(action_row)

        back_btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)

        async def on_back(back_interaction: discord.Interaction):
            self.rebuild_layout()
            await back_interaction.response.edit_message(view=self)

        back_btn.callback = on_back

        back_row = discord.ui.ActionRow()
        back_row.add_item(back_btn)
        container.add_item(back_row)

        self.add_item(container)
        await interaction.response.edit_message(view=self)
