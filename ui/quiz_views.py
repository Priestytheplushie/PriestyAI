import io
import time
import json
import html
import re
import asyncio
import logging
from typing import Any, Callable
import discord
from discord.ui import (
    LayoutView,
    Container,
    Section,
    TextDisplay,
    Separator,
    ActionRow,
    Button,
    Select
)
from config.settings import LOADING_EMOJI, WORKHORSE_DENSE_MODEL
from ui.modals import DynamicModalV2
from ui.artifact_views import build_artifact_components_for_message, get_file_icon
from core.client_manager import client_manager
from core.branch_manager import branch_manager
from parsers.markdown_parser import DFM_EMOJI_MAP
from google.genai import types

logger = logging.getLogger("PriestyAI.QuizUI")

CHOICE_LETTERS = ["A", "B", "C", "D", "E", "F"]

def is_boolean_question(options: list[dict[str, Any]]) -> bool:
    if len(options) != 2:
        return False
    opt_texts = {html.unescape(o.get("text", "")).strip().lower() for o in options}
    return opt_texts == {"true", "false"}

def format_progress_bar(questions: list[dict[str, Any]], answers: dict[int, int | None]) -> str:
    indicators = []
    for idx, q in enumerate(questions):
        if idx not in answers:
            indicators.append("○")
        elif answers[idx] is None or answers[idx] == -1:
            indicators.append("-")
        else:
            chosen_idx = answers[idx]
            opts = q.get("options", [])
            is_correct = (0 <= chosen_idx < len(opts)) and bool(opts[chosen_idx].get("correct", False))
            indicators.append("✓" if is_correct else "✗")
    return "  ".join([f"[ {ind} ]" for ind in indicators])

async def generate_quiz_diagnostics_llm(
    quiz_data: dict[str, Any],
    answers: dict[int, int | None]
) -> tuple[str, list[str], list[str]]:
    questions = quiz_data.get("questions", [])
    topic = quiz_data.get("topic", "General Knowledge")
    title = quiz_data.get("title", "Quiz")

    audit_lines = []
    score = 0
    skipped = 0
    total = len(questions)

    for idx, q in enumerate(questions):
        q_text = q.get("text", "")
        cat = q.get("category", topic)
        opts = q.get("options", [])
        chosen_idx = answers.get(idx)

        if chosen_idx is None or chosen_idx == -1:
            skipped += 1
            audit_lines.append(f"Q{idx+1} ({cat}): '{q_text}' -> USER SKIPPED")
        else:
            is_correct = (0 <= chosen_idx < len(opts)) and bool(opts[chosen_idx].get("correct", False))
            chosen_text = opts[chosen_idx].get("text", "") if 0 <= chosen_idx < len(opts) else "Unknown"
            correct_opt = next((o for o in opts if o.get("correct")), None)
            correct_text = correct_opt.get("text", "") if correct_opt else ""
            explanation = correct_opt.get("explanation", "") if correct_opt else ""

            if is_correct:
                score += 1
                audit_lines.append(f"Q{idx+1} ({cat}): '{q_text}' -> CORRECT (Chose: '{chosen_text}')")
            else:
                audit_lines.append(
                    f"Q{idx+1} ({cat}): '{q_text}' -> INCORRECT (Chose '{chosen_text}', Correct: '{correct_text}'). Key rule: {explanation}"
                )

    percent = int((score / max(1, total)) * 100)
    prompt = (
        f"Analyze this completed user quiz performance on '{title}' (Topic: {topic}).\n"
        f"Score: {score}/{total} ({percent}%), Skipped: {skipped}, Incorrect: {total - score - skipped}\n\n"
        f"Performance Breakdown:\n" + "\n".join(audit_lines) + "\n\n"
        f"Generate a diagnostic assessment with:\n"
        f"1. headline: 1 natural sentence assessing overall performance (e.g. 'Solid progress! Keep up the good work.').\n"
        f"2. strengths: Exactly 1 to 3 concise bullet points summarizing concepts/categories the user mastered.\n"
        f"3. focus_areas: Exactly 1 to 3 concise bullet points explaining specific rules/mechanics the user should review based on questions they missed or skipped. If the user scored 100%, write 1 celebratory bullet highlighting mastery and next-step challenges. Speak directly and naturally. Do NOT start focus points with 'Correct. While...'.\n\n"
        f"Output strict JSON:\n"
        f'{{"headline": "...", "strengths": ["...", "..."], "focus_areas": ["...", "..."]}}'
    )

    client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", fallback=True)
    if client:
        try:
            res = await client.aio.models.generate_content(
                model=active_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            if res.text:
                data = json.loads(res.text.strip())
                headline = data.get("headline", "Solid progress! Keep up the good work.")
                strengths = data.get("strengths", [])
                focus_areas = data.get("focus_areas", [])
                if strengths and focus_areas:
                    return headline, strengths, focus_areas
        except Exception as e:
            logger.debug(f"LLM quiz diagnostics fallback: {e}")

    headline = "Outstanding! Mastered all concepts." if percent == 100 else ("Solid progress! Keep up the good work." if percent >= 70 else "Good effort! A few key areas to review.")
    fallback_strengths = [f"**{topic}**: Answered {score} question(s) correctly."]
    fallback_focus = ["**Mastery Achieved**: You aced all questions in this set!"] if percent == 100 else [f"**{topic}**: Review mechanics for the {total - score} missed/skipped question(s)."]
    return headline, fallback_strengths, fallback_focus

def build_quiz_components_for_message(
    quiz_data: dict[str, Any],
    message_id: str | int = "temp",
    is_live_stream: bool = False
) -> list[Any]:
    title = html.unescape(quiz_data.get("title", "Quiz"))
    quiz_id = quiz_data.get("quiz_id", "quiz_0")
    topic = html.unescape(quiz_data.get("topic", "Knowledge Check"))
    difficulty = quiz_data.get("difficulty", "Medium")
    questions = quiz_data.get("questions", [])
    num_questions = len(questions) or quiz_data.get("question_count", 10)
    status = quiz_data.get("status", "ready")

    container = Container()

    if status == "generating" or quiz_data.get("is_generating"):
        start_t = quiz_data.get("start_time")
        if start_t is None or start_t <= 0:
            start_t = time.time()
            quiz_data["start_time"] = start_t
        elapsed = max(0, int(time.time() - start_t))

        display_text = f"🧩 **{title}**\n-# {LOADING_EMOJI} Generating your Quiz... ({elapsed}s)"
        open_btn = Button(label="Open", style=discord.ButtonStyle.secondary, disabled=True)
        container.add_item(Section(TextDisplay(display_text), accessory=open_btn))
        return [container]

    display_text = f"🧩 **{title}**\n-# {num_questions} Questions • {difficulty.capitalize()} • {topic}"
    open_btn = Button(
        label="Open",
        style=discord.ButtonStyle.secondary,
        custom_id=f"quizopen:{message_id}:{quiz_id}",
        disabled=is_live_stream
    )
    container.add_item(Section(TextDisplay(display_text), accessory=open_btn))
    return [container]

def build_quiz_spoiler_warning_view(message_id: str | int, version_idx: int) -> LayoutView:
    view = LayoutView(timeout=300)
    warning_text = (
        f"{DFM_EMOJI_MAP['gfm_warning']} **Quiz Spoilers**\n"
        "This thought process contains the AI's internal reasoning and answer key for the quiz.\n"
        "Opening it before finishing may spoil the questions and answers."
    )
    view.add_item(TextDisplay(warning_text))
    view.add_item(Separator(visible=True))

    show_anyway_btn = Button(
        label="Show Anyway",
        style=discord.ButtonStyle.danger,
        custom_id=f"gen_thought_force_{message_id}_{version_idx}"
    )
    view.add_item(ActionRow(show_anyway_btn))
    return view

def build_new_quiz_modal(
    current_topic: str,
    missed_categories: list[str],
    on_submit: Callable[[discord.Interaction, dict[str, Any]], Any]
) -> DynamicModalV2:
    clean_topic = html.unescape(current_topic)
    
    if missed_categories:
        missed_str = ", ".join(missed_categories[:3])
        second_option = {
            "label": "Growth areas",
            "value": "growth",
            "description": f"Focus on missed areas: {missed_str}"[:100]
        }
    else:
        second_option = {
            "label": "Advanced mastery",
            "value": "advanced",
            "description": f"Deeper edge cases and harder questions on {clean_topic}"[:100]
        }

    focus_options = [
        {"label": "All topics", "value": "all", "description": f"Cover all areas of {clean_topic}"[:100], "default": True},
        second_option
    ]

    fields = [
        {
            "type": "text_display",
            "content": f"# New Quiz Configuration\nCustomize a new follow-up quiz on **{clean_topic}**."
        },
        {
            "type": "radio_group",
            "custom_id": "focus",
            "label": "Focus",
            "description": "Choose whether to cover all topics or target growth areas",
            "value": "all",
            "options": focus_options,
            "required": True
        },
        {
            "type": "string_select",
            "custom_id": "num_questions",
            "label": "Number of questions",
            "description": "How many questions to generate",
            "value": "10",
            "options": [
                {"label": "5 Questions", "value": "5", "description": "Quick 5-question check"},
                {"label": "10 Questions", "value": "10", "description": "Standard 10-question quiz", "default": True},
                {"label": "15 Questions", "value": "15", "description": "Comprehensive 15-question exam"}
            ],
            "required": True
        },
        {
            "type": "radio_group",
            "custom_id": "difficulty",
            "label": "Difficulty",
            "description": "Select challenge level",
            "value": "same",
            "options": [
                {"label": "Easier", "value": "easier", "description": "Foundational concepts and simpler rules"},
                {"label": "About the same", "value": "same", "description": "Keep current challenge level", "default": True},
                {"label": "Harder", "value": "harder", "description": "Advanced edge cases and intricate mechanics"}
            ],
            "required": True
        }
    ]

    return DynamicModalV2(
        title="Add More Questions",
        custom_id="modal_new_quiz_config",
        fields_schema=fields,
        on_submit_callback=on_submit
    )

class QuizActiveStepperView(LayoutView):
    def __init__(
        self,
        quiz_data: dict[str, Any],
        user: discord.User | discord.Member,
        message_id: str | int = "temp",
        saved_answers: dict[int, int | None] | None = None,
        initial_idx: int = 0,
        on_progress_callback: Callable | None = None,
        on_finish_callback: Callable | None = None
    ):
        super().__init__(timeout=1800)
        self.quiz_data = quiz_data
        self.user = user
        self.message_id = message_id
        self.on_progress_callback = on_progress_callback
        self.on_finish_callback = on_finish_callback

        self.questions = quiz_data.get("questions", [])
        self.total_questions = max(1, len(self.questions))
        self.current_idx = max(0, min(initial_idx, self.total_questions - 1))
        self.answers: dict[int, int | None] = dict(saved_answers or {})

        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        q_data = self.questions[self.current_idx]
        q_text = html.unescape(q_data.get("text", ""))
        options = q_data.get("options", [])
        cat = html.unescape(q_data.get("category", ""))
        is_bool = is_boolean_question(options)

        is_answered = self.current_idx in self.answers
        chosen_idx = self.answers.get(self.current_idx)

        progress_str = format_progress_bar(self.questions, self.answers)
        correct_count = sum(
            1 for idx, ans in self.answers.items()
            if ans is not None and ans != -1
            and 0 <= ans < len(self.questions[idx].get("options", []))
            and bool(self.questions[idx]["options"][ans].get("correct", False))
        )
        skipped_count = sum(1 for ans in self.answers.values() if ans is None or ans == -1)
        incorrect_count = len(self.answers) - correct_count - skipped_count

        cat_badge = f" • {cat}" if cat else ""
        header_text = (
            f"**{html.unescape(self.quiz_data.get('title', 'Quiz'))}**\n"
            f"{progress_str}  `{self.current_idx + 1}/{self.total_questions}`  (✓ {correct_count}  ✗ {incorrect_count})\n"
            f"-# Difficulty: {self.quiz_data.get('difficulty', 'Medium').capitalize()}{cat_badge}"
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        if not is_answered:
            opts_lines = []
            for opt_idx, opt in enumerate(options):
                letter = CHOICE_LETTERS[opt_idx] if opt_idx < len(CHOICE_LETTERS) else str(opt_idx + 1)
                opt_text = html.unescape(opt.get("text", ""))
                label_prefix = f"• **{letter}.**" if not is_bool else "•"
                opts_lines.append(f"{label_prefix} {opt_text}")

            full_body_text = f"### Question {self.current_idx + 1}\n{q_text}\n\n" + "\n".join(opts_lines)
            container.add_item(TextDisplay(full_body_text))
            container.add_item(Separator(visible=True))

            action_buttons = []
            for opt_idx, opt in enumerate(options):
                if is_bool:
                    btn_label = html.unescape(opt.get("text", "Option")).strip().title()
                else:
                    btn_label = CHOICE_LETTERS[opt_idx] if opt_idx < len(CHOICE_LETTERS) else str(opt_idx + 1)

                btn = Button(
                    label=btn_label,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"btn_q_opt_{self.current_idx}_{opt_idx}"
                )
                btn.callback = self._create_option_callback(opt_idx)
                action_buttons.append(btn)

            container.add_item(ActionRow(*action_buttons))

        else:
            container.add_item(TextDisplay(f"### Question {self.current_idx + 1}\n{q_text}"))

            for opt_idx, opt in enumerate(options):
                letter = CHOICE_LETTERS[opt_idx] if opt_idx < len(CHOICE_LETTERS) else str(opt_idx + 1)
                opt_text = html.unescape(opt.get("text", ""))
                is_correct = bool(opt.get("correct", False))
                is_selected = (opt_idx == chosen_idx)
                explanation = html.unescape(opt.get("explanation", "").strip())
                display_prefix = f"{letter}. " if not is_bool else ""

                if is_selected and is_correct:
                    acc_btn = Button(label="Correct", style=discord.ButtonStyle.success, disabled=True)
                    body_text = f"**{display_prefix}{opt_text}** (Your answer)\n{explanation}" if explanation else f"**{display_prefix}{opt_text}** (Your answer)"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                elif is_selected and not is_correct:
                    acc_btn = Button(label="Incorrect", style=discord.ButtonStyle.danger, disabled=True)
                    body_text = f"**{display_prefix}{opt_text}** (Your answer)\n{explanation}" if explanation else f"**{display_prefix}{opt_text}** (Your answer)"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                elif is_correct and not is_selected:
                    acc_btn = Button(label="Correct answer", style=discord.ButtonStyle.success, disabled=True)
                    body_text = f"**{display_prefix}{opt_text}**\n{explanation}" if explanation else f"**{display_prefix}{opt_text}**"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                else:
                    plain_text = f"• **{display_prefix}{opt_text}**" if not is_bool else f"• {opt_text}"
                    container.add_item(TextDisplay(plain_text))

        container.add_item(Separator(visible=True))

        nav_buttons = []
        prev_btn = Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_idx == 0),
            custom_id="btn_quiz_prev"
        )
        prev_btn.callback = self._on_prev_clicked
        nav_buttons.append(prev_btn)

        if not is_answered:
            skip_btn = Button(
                label="Skip",
                style=discord.ButtonStyle.secondary,
                custom_id="btn_quiz_skip"
            )
            skip_btn.callback = self._on_skip_clicked
            nav_buttons.append(skip_btn)

        is_last = (self.current_idx == self.total_questions - 1)
        next_label = "Finish" if is_last else "Next"
        next_btn = Button(
            label=next_label,
            style=discord.ButtonStyle.primary if is_last else discord.ButtonStyle.secondary,
            disabled=not is_answered,
            custom_id="btn_quiz_next"
        )
        next_btn.callback = self._on_next_clicked
        nav_buttons.append(next_btn)

        container.add_item(ActionRow(*nav_buttons))
        self.add_item(container)

    def _create_option_callback(self, chosen_opt_idx: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user.id:
                await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
                return

            self.answers[self.current_idx] = chosen_opt_idx
            self._build_layout()
            await interaction.response.edit_message(view=self)

            if self.on_progress_callback:
                asyncio.create_task(self.on_progress_callback(self.current_idx, self.answers))
        return callback

    async def _on_skip_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
            return

        if self.current_idx < self.total_questions - 1:
            self.current_idx += 1
            self._build_layout()
            await interaction.response.edit_message(view=self)
            if self.on_progress_callback:
                asyncio.create_task(self.on_progress_callback(self.current_idx, self.answers))
        else:
            await self._complete_quiz_flow(interaction)

    async def _on_prev_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
            return

        if self.current_idx > 0:
            self.current_idx -= 1
            self._build_layout()
            await interaction.response.edit_message(view=self)
            if self.on_progress_callback:
                asyncio.create_task(self.on_progress_callback(self.current_idx, self.answers))
        else:
            await interaction.response.defer()

    async def _on_next_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
            return

        if self.current_idx < self.total_questions - 1:
            self.current_idx += 1
            self._build_layout()
            await interaction.response.edit_message(view=self)
            if self.on_progress_callback:
                asyncio.create_task(self.on_progress_callback(self.current_idx, self.answers))
        else:
            await self._complete_quiz_flow(interaction)

    async def _complete_quiz_flow(self, interaction: discord.Interaction):
        await interaction.response.defer()

        for i in range(self.total_questions):
            if i not in self.answers:
                self.answers[i] = -1

        score = sum(
            1 for idx, ans in self.answers.items()
            if ans is not None and ans != -1
            and 0 <= ans < len(self.questions[idx].get("options", []))
            and bool(self.questions[idx]["options"][ans].get("correct", False))
        )
        skipped = sum(1 for ans in self.answers.values() if ans is None or ans == -1)

        headline, strengths, focus_areas = await generate_quiz_diagnostics_llm(self.quiz_data, self.answers)

        summary_view = QuizScoreSummaryView(
            quiz_data=self.quiz_data,
            user=self.user,
            score=score,
            total_questions=self.total_questions,
            skipped=skipped,
            answers=self.answers,
            headline=headline,
            strengths=strengths,
            focus_areas=focus_areas,
            message_id=self.message_id
        )

        if self.on_finish_callback:
            try:
                await self.on_finish_callback(score, self.total_questions, skipped, headline, self.answers, strengths, focus_areas)
            except Exception as ex:
                logger.debug(f"Quiz finish callback error: {ex}")

        await interaction.edit_original_response(view=summary_view)

class QuizScoreSummaryView(LayoutView):
    def __init__(
        self,
        quiz_data: dict[str, Any],
        user: discord.User | discord.Member,
        score: int,
        total_questions: int,
        skipped: int,
        answers: dict[int, int | None],
        headline: str,
        strengths: list[str],
        focus_areas: list[str],
        message_id: str | int = "temp"
    ):
        super().__init__(timeout=1800)
        self.quiz_data = quiz_data
        self.user = user
        self.score = score
        self.total_questions = total_questions
        self.skipped = skipped
        self.answers = answers
        self.headline = headline
        self.strengths = strengths
        self.focus_areas = focus_areas
        self.message_id = message_id

        self.study_guide_artifact: dict[str, Any] | None = None
        self.is_generating_guide: bool = False
        self.guide_start_time: float = 0.0

        self._check_existing_study_guide()
        self._build_layout()

    def _check_existing_study_guide(self):
        q_id = self.quiz_data.get("quiz_id", "quiz_0")
        guide_scope_id = f"quiz_guide_{q_id}"
        guide_record = branch_manager.get_artifact_by_channel_and_file(guide_scope_id, "study_guide.md")
        if guide_record:
            versions = guide_record.get("versions", [])
            content = versions[-1].get("content", "") if versions else ""
            self.study_guide_artifact = {
                "artifact_id": guide_record["artifact_id"],
                "type": "single_file",
                "title": guide_record.get("title", "Study Guide"),
                "filename": "study_guide.md",
                "description": f"v{guide_record.get('active_version', 1)} • study_guide.md",
                "file_count": 1,
                "total_lines": max(1, len(content.splitlines())),
                "size_bytes": len(content.encode("utf-8")),
                "active_version": guide_record.get("active_version", 1),
                "total_versions": len(versions),
                "versions": versions,
                "content": content,
                "status": "ready"
            }

    def _build_layout(self):
        self.clear_items()

        results_container = Container()

        percent = int((self.score / max(1, self.total_questions)) * 100)
        incorrect_count = self.total_questions - self.score - self.skipped
        is_perfect_score = (self.score == self.total_questions)

        header_block = (
            f"# {html.unescape(self.quiz_data.get('title', 'Quiz'))} — Results\n"
            f"**Score: {self.score}/{self.total_questions} Correct** ({percent}%)\n"
            f"-# {self.skipped} skipped • {incorrect_count} incorrect\n\n"
            f"### {self.headline}"
        )
        results_container.add_item(TextDisplay(header_block))
        results_container.add_item(Separator(visible=True))

        str_lines = ["### Strengths"]
        for s in self.strengths:
            str_lines.append(f"• {html.unescape(s)}")
        results_container.add_item(TextDisplay("\n".join(str_lines)))

        focus_lines = ["\n### Focus areas"]
        for f in self.focus_areas:
            focus_lines.append(f"• {html.unescape(f)}")
        results_container.add_item(TextDisplay("\n".join(focus_lines)))

        self.add_item(results_container)
        self.add_item(Separator(visible=True))

        if self.is_generating_guide or (self.study_guide_artifact and self.study_guide_artifact.get("status") == "generating"):
            self.add_item(TextDisplay("### Keep Learning"))
            art_items = build_artifact_components_for_message(
                self.study_guide_artifact or {
                    "filename": "study_guide.md",
                    "title": f"Study Guide: {self.quiz_data.get('topic', 'Topic')}",
                    "status": "generating",
                    "is_generating": True,
                    "start_time": self.guide_start_time
                },
                message_id=self.message_id,
                is_live_stream=False
            )
            for item in art_items:
                self.add_item(item)

        elif self.study_guide_artifact:
            self.add_item(TextDisplay("### Keep Learning"))
            art_items = build_artifact_components_for_message(
                self.study_guide_artifact,
                message_id=self.message_id,
                is_live_stream=False
            )
            for item in art_items:
                self.add_item(item)

        else:
            study_btn = Button(
                label="Generate",
                style=discord.ButtonStyle.secondary,
                custom_id=f"btn_quiz_study_{self.message_id}"
            )
            study_btn.callback = self._on_generate_study_guide_clicked

            if is_perfect_score:
                guide_title = "**Mastery Reference Guide**"
                guide_desc = "-# Generate a comprehensive advanced cheat sheet and edge-case manual on this topic."
            else:
                guide_title = "**Study Guide**"
                guide_desc = "-# Generate a targeted reference guide focusing on your growth areas."

            study_text = f"### Keep Learning\n{guide_title}\n{guide_desc}"
            self.add_item(Section(TextDisplay(study_text), accessory=study_btn))

        self.add_item(Separator(visible=True))

        review_btn = Button(
            label="Review Answers",
            style=discord.ButtonStyle.secondary,
            custom_id=f"quiz_review_{self.message_id}"
        )
        review_btn.callback = self._on_review_clicked

        retake_btn = Button(
            label="Retake Quiz",
            style=discord.ButtonStyle.secondary,
            custom_id=f"quiz_retake_{self.message_id}"
        )
        retake_btn.callback = self._on_retake_clicked

        new_quiz_btn = Button(
            label="New Quiz",
            style=discord.ButtonStyle.primary,
            custom_id=f"quiz_new_{self.message_id}"
        )
        new_quiz_btn.callback = self._on_new_quiz_clicked

        self.add_item(ActionRow(review_btn, retake_btn, new_quiz_btn))

    async def _on_generate_study_guide_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This action is private to the quiz taker.", ephemeral=True)
            return

        await interaction.response.defer()

        self.is_generating_guide = True
        self.guide_start_time = time.time()
        self.study_guide_artifact = {
            "artifact_id": f"art_guide_{int(time.time() * 1000)}",
            "filename": "study_guide.md",
            "title": f"Study Guide: {html.unescape(self.quiz_data.get('topic', 'Topic'))}",
            "status": "generating",
            "is_generating": True,
            "start_time": self.guide_start_time
        }
        self._build_layout()
        await interaction.edit_original_response(view=self)

        current_topic = html.unescape(self.quiz_data.get("topic", "General Knowledge"))
        q_id = self.quiz_data.get("quiz_id", "quiz_0")
        guide_scope_id = f"quiz_guide_{q_id}"
        is_perfect = (self.score == self.total_questions)

        if is_perfect:
            instruction = (
                f"You are PriestyAI. The user scored 100% (Mastery) on the '{current_topic}' quiz.\n"
                f"Generate a comprehensive, advanced Markdown Document Canvas reference manual and cheat sheet for '{current_topic}'.\n"
                f"Structure with clear H1/H2 headings, technical deep dives, edge cases, best practices, and pro tips.\n"
                f"Emit `<artifact identifier=\"study_guide.md\" title=\"Mastery Guide: {current_topic}\">` containing the complete markdown guide."
            )
        else:
            focus_str = "\n".join([f"- {f}" for f in self.focus_areas])
            instruction = (
                f"You are PriestyAI. The user completed a quiz on '{current_topic}'.\n"
                f"Generate a targeted Markdown Document Canvas study guide for '{current_topic}' specifically addressing these growth areas:\n{focus_str}\n\n"
                f"Structure with clear H1/H2 headings, key rules, memory mnemonics, concept breakdowns, and examples.\n"
                f"Emit `<artifact identifier=\"study_guide.md\" title=\"Study Guide: {current_topic}\">` containing the complete markdown guide."
            )

        async def run_guide_pipeline():
            stop_timer = asyncio.Event()

            async def timer_loop():
                while not stop_timer.is_set():
                    try:
                        await asyncio.sleep(1.0)
                        if stop_timer.is_set():
                            break
                        self._build_layout()
                        await interaction.edit_original_response(view=self)
                    except Exception:
                        break

            timer_task = asyncio.create_task(timer_loop())

            guide_content = ""
            client, key_idx, active_model = client_manager.get_client_for_model(
                WORKHORSE_DENSE_MODEL,
                fallback=True
            )

            if client:
                try:
                    config = types.GenerateContentConfig(
                        system_instruction=instruction,
                        thinking_config=types.ThinkingConfig(
                            thinking_level="MEDIUM",
                            include_thoughts=True
                        ),
                        temperature=0.7
                    )
                    res = await client.aio.models.generate_content(
                        model=active_model,
                        contents=f"Generate the comprehensive study guide artifact for {current_topic}.",
                        config=config
                    )
                    if res and res.text:
                        raw_text = res.text.strip()
                        art_m = re.search(r'<artifact[^>]*>(.*?)</artifact>', raw_text, re.DOTALL | re.IGNORECASE)
                        if art_m:
                            guide_content = art_m.group(1).strip()
                        else:
                            guide_content = raw_text

                        if guide_content.startswith("```"):
                            guide_content = re.sub(r'^```[a-zA-Z]*\n|\n```$', '', guide_content).strip()

                except Exception as ex:
                    logger.warning(f"Failed to generate study guide on Gemma 4: {ex}")

            if not guide_content:
                guide_content = f"# Study Guide: {current_topic}\n\n## Core Concepts\nReview the fundamentals and mechanics covered in this quiz session."

            stop_timer.set()
            if not timer_task.done():
                timer_task.cancel()

            title_text = f"Mastery Guide: {current_topic}" if is_perfect else f"Study Guide: {current_topic}"
            record = branch_manager.save_or_update_artifact(
                channel_id=guide_scope_id,
                filename="study_guide.md",
                title=title_text,
                content=guide_content,
                change_summary=f"Generated {title_text}"
            )

            self.is_generating_guide = False
            self.study_guide_artifact = {
                "artifact_id": record["artifact_id"],
                "type": "single_file",
                "title": title_text,
                "filename": "study_guide.md",
                "description": f"v{record['active_version']} • study_guide.md",
                "file_count": 1,
                "total_lines": record["latest_version_data"]["lines"],
                "size_bytes": record["latest_version_data"]["size_bytes"],
                "active_version": record["active_version"],
                "total_versions": record["total_versions"],
                "versions": record["versions"],
                "content": guide_content,
                "status": "ready"
            }

            self._build_layout()
            try:
                await interaction.edit_original_response(view=self)
            except Exception as ex:
                logger.debug(f"Failed to edit study guide final layout: {ex}")

        asyncio.create_task(run_guide_pipeline())

    async def _on_review_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This quiz summary is private to the invoking user.", ephemeral=True)
            return

        review_view = QuizReviewView(
            quiz_data=self.quiz_data,
            user=self.user,
            answers=self.answers,
            score=self.score,
            total_questions=self.total_questions,
            skipped=self.skipped,
            headline=self.headline,
            strengths=self.strengths,
            focus_areas=self.focus_areas,
            message_id=self.message_id
        )
        await interaction.response.edit_message(view=review_view)

    async def _on_retake_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
            return

        q_id = self.quiz_data.get("quiz_id", "")
        if q_id:
            branch_manager.delete_quiz_attempts_for_user(q_id, self.user.id)

        async def save_attempt_cb(sc, tot, sk, head, ans, st, foc):
            branch_manager.finalize_quiz_attempt(
                quiz_id=q_id,
                user_id=self.user.id,
                score=sc,
                total_questions=tot,
                skipped=sk,
                headline=head,
                answers=ans,
                strengths=st,
                focus_areas=foc
            )

        async def save_progress_cb(cur_idx, ans):
            branch_manager.save_quiz_attempt_progress(
                quiz_id=q_id,
                user_id=self.user.id,
                current_idx=cur_idx,
                answers=ans,
                total_questions=len(self.quiz_data.get("questions", []))
            )

        fresh_stepper = QuizActiveStepperView(
            quiz_data=self.quiz_data,
            user=self.user,
            message_id=self.message_id,
            on_progress_callback=save_progress_cb,
            on_finish_callback=save_attempt_cb
        )
        await interaction.response.edit_message(view=fresh_stepper)

    async def _on_new_quiz_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
            return

        current_topic = self.quiz_data.get("topic", "General Knowledge")
        missed_cats = []
        questions = self.quiz_data.get("questions", [])
        for idx, q in enumerate(questions):
            ans = self.answers.get(idx)
            opts = q.get("options", [])
            is_correct = (ans is not None and ans != -1 and 0 <= ans < len(opts)) and bool(opts[ans].get("correct", False))
            if not is_correct:
                c = q.get("category", "")
                if c and c not in missed_cats:
                    missed_cats.append(c)

        async def on_new_quiz_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            focus_choice = data.get("focus", "all")
            num_q = data.get("num_questions", "10")
            diff = data.get("difficulty", "same")

            diff_str = "about the same difficulty" if diff == "same" else f"{diff} difficulty"
            focus_str = f"focusing specifically on growth areas ({', '.join(missed_cats)})" if (focus_choice == "growth" and missed_cats) else f"covering all topics of {current_topic}"

            followup_prompt = f"Please generate a new {num_q}-question quiz on {current_topic} ({diff_str}), {focus_str}."

            await sub_inter.response.send_message(
                content=f"🧩 **Generating New Quiz:** Starting your {num_q}-question quiz on **{current_topic}**...",
                ephemeral=True
            )
            from commands.chat import execute_chat_turn
            await execute_chat_turn(
                interaction=sub_inter,
                prompt_text=followup_prompt,
                is_ephemeral=False
            )

        modal = build_new_quiz_modal(
            current_topic=current_topic,
            missed_categories=missed_cats,
            on_submit=on_new_quiz_submit
        )
        await interaction.response.send_modal(modal)

class QuizReviewView(LayoutView):
    def __init__(
        self,
        quiz_data: dict[str, Any],
        user: discord.User | discord.Member,
        answers: dict[int, int | None],
        score: int,
        total_questions: int,
        skipped: int,
        headline: str,
        strengths: list[str],
        focus_areas: list[str],
        message_id: str | int = "temp"
    ):
        super().__init__(timeout=1800)
        self.quiz_data = quiz_data
        self.user = user
        self.answers = answers
        self.score = score
        self.total_questions = total_questions
        self.skipped = skipped
        self.headline = headline
        self.strengths = strengths
        self.focus_areas = focus_areas
        self.message_id = message_id
        self.current_idx = 0
        self.questions = quiz_data.get("questions", [])
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        q_data = self.questions[self.current_idx]
        q_text = html.unescape(q_data.get("text", ""))
        options = q_data.get("options", [])
        cat = html.unescape(q_data.get("category", ""))
        chosen_idx = self.answers.get(self.current_idx)
        is_skipped = (chosen_idx is None or chosen_idx == -1)
        is_bool = is_boolean_question(options)

        progress_str = format_progress_bar(self.questions, self.answers)
        cat_badge = f" • {cat}" if cat else ""

        header_text = (
            f"**Reviewing: {html.unescape(self.quiz_data.get('title', 'Quiz'))}**\n"
            f"{progress_str}  `{self.current_idx + 1}/{self.total_questions}`\n"
            f"-# Score: {self.score}/{self.total_questions} ({self.skipped} skipped){cat_badge}"
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        question_display = f"### Question {self.current_idx + 1}\n{q_text}"
        container.add_item(TextDisplay(question_display))

        if is_skipped:
            container.add_item(TextDisplay("-# *Question Skipped*"))
            correct_opt = next((o for o in options if o.get("correct")), None)
            if correct_opt:
                c_letter = CHOICE_LETTERS[options.index(correct_opt)] if correct_opt in options else "A"
                c_text = html.unescape(correct_opt.get("text", ""))
                c_exp = html.unescape(correct_opt.get("explanation", "").strip())
                display_prefix = f"{c_letter}. " if not is_bool else ""
                acc_btn = Button(label="Correct answer", style=discord.ButtonStyle.success, disabled=True)
                body_text = f"**{display_prefix}{c_text}**\n{c_exp}" if c_exp else f"**{display_prefix}{c_text}**"
                container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))
        else:
            for opt_idx, opt in enumerate(options):
                letter = CHOICE_LETTERS[opt_idx] if opt_idx < len(CHOICE_LETTERS) else str(opt_idx + 1)
                opt_text = html.unescape(opt.get("text", ""))
                is_correct = bool(opt.get("correct", False))
                is_selected = (opt_idx == chosen_idx)
                explanation = html.unescape(opt.get("explanation", "").strip())
                display_prefix = f"{letter}. " if not is_bool else ""

                if is_selected and is_correct:
                    acc_btn = Button(label="Correct answer", style=discord.ButtonStyle.success, disabled=True)
                    body_text = f"**{display_prefix}{opt_text}** (Your answer)\n{explanation}" if explanation else f"**{display_prefix}{opt_text}** (Your answer)"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                elif is_selected and not is_correct:
                    acc_btn = Button(label="Incorrect", style=discord.ButtonStyle.danger, disabled=True)
                    body_text = f"**{display_prefix}{opt_text}** (Your answer)\n{explanation}" if explanation else f"**{display_prefix}{opt_text}** (Your answer)"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                elif is_correct and not is_selected:
                    acc_btn = Button(label="Correct answer", style=discord.ButtonStyle.success, disabled=True)
                    body_text = f"**{display_prefix}{opt_text}**\n{explanation}" if explanation else f"**{display_prefix}{opt_text}**"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                else:
                    plain_text = f"• **{display_prefix}{opt_text}**" if not is_bool else f"• {opt_text}"
                    container.add_item(TextDisplay(plain_text))

        container.add_item(Separator(visible=True))

        prev_btn = Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_idx == 0),
            custom_id="btn_review_prev"
        )
        prev_btn.callback = self._on_prev_clicked

        results_btn = Button(
            label="Summary",
            style=discord.ButtonStyle.primary,
            custom_id="btn_review_summary"
        )
        results_btn.callback = self._on_summary_clicked

        next_btn = Button(
            label="Next",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_idx == self.total_questions - 1),
            custom_id="btn_review_next"
        )
        next_btn.callback = self._on_next_clicked

        container.add_item(ActionRow(prev_btn, results_btn, next_btn))
        self.add_item(container)

    async def _on_prev_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This review is private to the invoking user.", ephemeral=True)
            return

        if self.current_idx > 0:
            self.current_idx -= 1
            self._build_layout()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    async def _on_next_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This review is private to the invoking user.", ephemeral=True)
            return

        if self.current_idx < self.total_questions - 1:
            self.current_idx += 1
            self._build_layout()
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.defer()

    async def _on_summary_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This summary is private to the invoking user.", ephemeral=True)
            return

        summary_view = QuizScoreSummaryView(
            quiz_data=self.quiz_data,
            user=self.user,
            score=self.score,
            total_questions=self.total_questions,
            skipped=self.skipped,
            answers=self.answers,
            headline=self.headline,
            strengths=self.strengths,
            focus_areas=self.focus_areas,
            message_id=self.message_id
        )
        await interaction.response.edit_message(view=summary_view)