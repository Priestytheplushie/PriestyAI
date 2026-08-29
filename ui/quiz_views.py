import io
import time
import json
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
from config.settings import LOADING_EMOJI
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.QuizUI")

CHOICE_LETTERS = ["A", "B", "C", "D", "E", "F"]

def format_progress_bar(questions: list[dict[str, Any]], answers: dict[int, int]) -> str:
    indicators = []
    for idx, q in enumerate(questions):
        if idx not in answers:
            indicators.append("⚪")
        else:
            chosen_idx = answers[idx]
            opts = q.get("options", [])
            is_correct = (0 <= chosen_idx < len(opts)) and bool(opts[chosen_idx].get("correct", False))
            indicators.append("✓" if is_correct else "✗")
    return "  ".join([f"[ {ind} ]" for ind in indicators])

def compute_diagnostic_summary(quiz_data: dict[str, Any], answers: dict[int, int]) -> tuple[list[str], list[str]]:
    questions = quiz_data.get("questions", [])
    strengths_by_cat: dict[str, int] = {}
    focus_by_cat: dict[str, list[str]] = {}

    for idx, q in enumerate(questions):
        cat = q.get("category") or quiz_data.get("topic", "General Knowledge")
        chosen_idx = answers.get(idx, -1)
        opts = q.get("options", [])
        is_correct = (0 <= chosen_idx < len(opts)) and bool(opts[chosen_idx].get("correct", False))

        if is_correct:
            strengths_by_cat[cat] = strengths_by_cat.get(cat, 0) + 1
        else:
            if cat not in focus_by_cat:
                focus_by_cat[cat] = []
            
            correct_opt = next((o for o in opts if o.get("correct")), None)
            exp = correct_opt.get("explanation", "") if correct_opt else ""
            if exp:
                focus_by_cat[cat].append(exp)
            else:
                q_text = q.get("text", "")
                focus_by_cat[cat].append(f"Review rules for: {q_text[:80]}")

    strengths_list = []
    for cat, count in strengths_by_cat.items():
        strengths_list.append(f"**{cat}**: Demonstrated solid understanding and answered {count} question(s) correctly.")

    focus_list = []
    for cat, notes in focus_by_cat.items():
        first_note = notes[0] if notes else "Review key concepts and mechanics in this category."
        focus_list.append(f"**{cat}**: {first_note}")

    if not strengths_list:
        strengths_list.append("Keep practicing to build your foundational knowledge across these topics.")

    if not focus_list:
        focus_list.append("Mastery achieved! No critical growth areas identified.")

    return strengths_list, focus_list

def build_quiz_components_for_message(
    quiz_data: dict[str, Any],
    message_id: str | int = "temp",
    is_live_stream: bool = False
) -> list[Any]:
    title = quiz_data.get("title", "Quiz")
    quiz_id = quiz_data.get("quiz_id", "quiz_0")
    topic = quiz_data.get("topic", "Knowledge Check")
    difficulty = quiz_data.get("difficulty", "Medium")
    questions = quiz_data.get("questions", [])
    num_questions = len(questions) or quiz_data.get("question_count", 5)
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

def build_new_quiz_modal(
    current_topic: str,
    missed_categories: list[str],
    on_submit: Callable[[discord.Interaction, dict[str, Any]], Any]
) -> DynamicModalV2:
    focus_options = [
        {"label": "All topics", "value": "all", "description": f"Cover all areas of {current_topic}", "default": True}
    ]
    if missed_categories:
        missed_str = ", ".join(missed_categories[:3])
        focus_options.append({
            "label": "Growth areas",
            "value": "growth",
            "description": f"Target missed topics: {missed_str}"[:100]
        })

    fields = [
        {
            "type": "text_display",
            "content": f"# New Quiz Configuration\nCustomize a new follow-up quiz on **{current_topic}**."
        },
        {
            "type": "radio_group",
            "custom_id": "focus",
            "label": "Focus",
            "description": "Choose whether to cover all topics or focus on growth areas",
            "value": "all",
            "options": focus_options,
            "required": True
        },
        {
            "type": "string_select",
            "custom_id": "num_questions",
            "label": "Number of questions",
            "description": "How many questions to generate",
            "value": "5",
            "options": [
                {"label": "5 Questions", "value": "5", "description": "Quick 5-question test", "default": True},
                {"label": "10 Questions", "value": "10", "description": "Standard 10-question quiz"},
                {"label": "15 Questions", "value": "15", "description": "Comprehensive 15-question exam"}
            ],
            "required": True
        },
        {
            "type": "radio_group",
            "custom_id": "difficulty",
            "label": "Difficulty",
            "description": "Select the target challenge level",
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
        on_finish_callback: Callable | None = None
    ):
        super().__init__(timeout=1800)
        self.quiz_data = quiz_data
        self.user = user
        self.message_id = message_id
        self.on_finish_callback = on_finish_callback

        self.questions = quiz_data.get("questions", [])
        self.total_questions = max(1, len(self.questions))
        self.current_idx = 0
        self.answers: dict[int, int] = {}

        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        q_data = self.questions[self.current_idx]
        q_text = q_data.get("text", "")
        options = q_data.get("options", [])
        cat = q_data.get("category", "")

        is_answered = self.current_idx in self.answers
        chosen_idx = self.answers.get(self.current_idx, -1)

        progress_str = format_progress_bar(self.questions, self.answers)
        correct_count = sum(
            1 for idx, ans in self.answers.items()
            if 0 <= ans < len(self.questions[idx].get("options", []))
            and bool(self.questions[idx]["options"][ans].get("correct", False))
        )
        incorrect_count = len(self.answers) - correct_count

        cat_badge = f" • {cat}" if cat else ""
        header_text = (
            f"**{self.quiz_data.get('title', 'Quiz')}**\n"
            f"{progress_str}  `{self.current_idx + 1}/{self.total_questions}`  (✓ {correct_count}  ✗ {incorrect_count})\n"
            f"-# Difficulty: {self.quiz_data.get('difficulty', 'Medium').capitalize()}{cat_badge}"
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        question_display = f"### Question {self.current_idx + 1}\n{q_text}"
        container.add_item(TextDisplay(question_display))

        if not is_answered:
            opt_buttons = []
            for opt_idx, opt in enumerate(options):
                letter = CHOICE_LETTERS[opt_idx] if opt_idx < len(CHOICE_LETTERS) else str(opt_idx + 1)
                opt_label = f"{letter}. {opt.get('text', '')}"[:80]
                
                btn = Button(
                    label=opt_label,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"btn_q_opt_{self.current_idx}_{opt_idx}"
                )
                btn.callback = self._create_option_callback(opt_idx)
                opt_buttons.append(btn)

            for b in opt_buttons:
                container.add_item(ActionRow(b))

        else:
            for opt_idx, opt in enumerate(options):
                letter = CHOICE_LETTERS[opt_idx] if opt_idx < len(CHOICE_LETTERS) else str(opt_idx + 1)
                opt_text = opt.get("text", "")
                is_correct = bool(opt.get("correct", False))
                is_selected = (opt_idx == chosen_idx)
                explanation = opt.get("explanation", "").strip()

                if is_selected and is_correct:
                    btn_label = f"{letter}. {opt_text} (Your answer — Correct)"[:80]
                    acc_btn = Button(label="Correct", style=discord.ButtonStyle.success, disabled=True)
                    body_text = f"**{letter}. {opt_text}**\n{explanation}" if explanation else f"**{letter}. {opt_text}**"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                elif is_selected and not is_correct:
                    btn_label = f"{letter}. {opt_text} (Your answer — Incorrect)"[:80]
                    acc_btn = Button(label="Incorrect", style=discord.ButtonStyle.danger, disabled=True)
                    body_text = f"**{letter}. {opt_text}**\n{explanation}" if explanation else f"**{letter}. {opt_text}**"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                elif is_correct and not is_selected:
                    acc_btn = Button(label="Correct answer", style=discord.ButtonStyle.success, disabled=True)
                    body_text = f"**{letter}. {opt_text}**\n{explanation}" if explanation else f"**{letter}. {opt_text}**"
                    container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

                else:
                    plain_text = f"{letter}. {opt_text}"
                    container.add_item(TextDisplay(plain_text))

        container.add_item(Separator(visible=True))

        prev_btn = Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_idx == 0),
            custom_id="btn_quiz_prev"
        )
        prev_btn.callback = self._on_prev_clicked

        is_last = (self.current_idx == self.total_questions - 1)
        next_label = "Finish" if (is_last and len(self.answers) == self.total_questions) else ("Finish" if is_last else "Next")
        next_btn = Button(
            label=next_label,
            style=discord.ButtonStyle.primary if is_last else discord.ButtonStyle.secondary,
            disabled=not is_answered,
            custom_id="btn_quiz_next"
        )
        next_btn.callback = self._on_next_clicked

        container.add_item(ActionRow(prev_btn, next_btn))
        self.add_item(container)

    def _create_option_callback(self, chosen_opt_idx: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user.id:
                await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
                return

            self.answers[self.current_idx] = chosen_opt_idx
            self._build_layout()
            await interaction.response.edit_message(view=self)
        return callback

    async def _on_prev_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
            return

        if self.current_idx > 0:
            self.current_idx -= 1
            self._build_layout()
            await interaction.response.edit_message(view=self)
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
        else:
            score = sum(
                1 for idx, ans in self.answers.items()
                if 0 <= ans < len(self.questions[idx].get("options", []))
                and bool(self.questions[idx]["options"][ans].get("correct", False))
            )
            strengths, focus = compute_diagnostic_summary(self.quiz_data, self.answers)

            summary_view = QuizScoreSummaryView(
                quiz_data=self.quiz_data,
                user=self.user,
                score=score,
                total_questions=self.total_questions,
                answers=self.answers,
                strengths=strengths,
                focus_areas=focus,
                message_id=self.message_id
            )

            if self.on_finish_callback:
                try:
                    await self.on_finish_callback(score, self.total_questions, self.answers, strengths, focus)
                except Exception as ex:
                    logger.debug(f"Quiz finish callback error: {ex}")

            await interaction.response.edit_message(view=summary_view)

class QuizScoreSummaryView(LayoutView):
    def __init__(
        self,
        quiz_data: dict[str, Any],
        user: discord.User | discord.Member,
        score: int,
        total_questions: int,
        answers: dict[int, int],
        strengths: list[str],
        focus_areas: list[str],
        message_id: str | int = "temp"
    ):
        super().__init__(timeout=1800)
        self.quiz_data = quiz_data
        self.user = user
        self.score = score
        self.total_questions = total_questions
        self.answers = answers
        self.strengths = strengths
        self.focus_areas = focus_areas
        self.message_id = message_id
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        percent = int((self.score / max(1, self.total_questions)) * 100)
        incorrect_count = self.total_questions - self.score

        if percent == 100:
            headline = "Outstanding! You mastered all concepts in this quiz."
        elif percent >= 80:
            headline = "Solid progress! Keep up the good work."
        elif percent >= 50:
            headline = "Good effort! A few key areas to sharpen."
        else:
            headline = "Keep practicing! Review the focus areas below."

        header_block = (
            f"# {self.quiz_data.get('title', 'Quiz')} — Results\n"
            f"**Score: {self.score}/{self.total_questions} Correct** ({percent}%)\n"
            f"-# 0 skipped • {incorrect_count} incorrect\n\n"
            f"### {headline}"
        )
        container.add_item(TextDisplay(header_block))
        container.add_item(Separator(visible=True))

        str_lines = ["### Strengths"]
        for s in self.strengths:
            str_lines.append(f"• {s}")
        container.add_item(TextDisplay("\n".join(str_lines)))

        focus_lines = ["\n### Focus areas"]
        for f in self.focus_areas:
            focus_lines.append(f"• {f}")
        container.add_item(TextDisplay("\n".join(focus_lines)))

        container.add_item(Separator(visible=True))

        review_btn = Button(
            label="Review Answers",
            style=discord.ButtonStyle.secondary,
            custom_id=f"quiz_review_{self.message_id}"
        )
        review_btn.callback = self._on_review_clicked

        new_quiz_btn = Button(
            label="New Quiz",
            style=discord.ButtonStyle.primary,
            custom_id=f"quiz_new_{self.message_id}"
        )
        new_quiz_btn.callback = self._on_new_quiz_clicked

        container.add_item(ActionRow(review_btn, new_quiz_btn))
        self.add_item(container)

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
            strengths=self.strengths,
            focus_areas=self.focus_areas,
            message_id=self.message_id
        )
        await interaction.response.edit_message(view=review_view)

    async def _on_new_quiz_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(content="❌ This quiz session is private to the invoking user.", ephemeral=True)
            return

        current_topic = self.quiz_data.get("topic", "General Knowledge")
        missed_cats = []
        questions = self.quiz_data.get("questions", [])
        for idx, q in enumerate(questions):
            ans = self.answers.get(idx, -1)
            opts = q.get("options", [])
            is_correct = (0 <= ans < len(opts)) and bool(opts[ans].get("correct", False))
            if not is_correct:
                c = q.get("category", "")
                if c and c not in missed_cats:
                    missed_cats.append(c)

        async def on_new_quiz_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            focus_choice = data.get("focus", "all")
            num_q = data.get("num_questions", "5")
            diff = data.get("difficulty", "same")

            diff_str = "about the same difficulty" if diff == "same" else f"{diff} difficulty"
            focus_str = f"focusing on growth areas ({', '.join(missed_cats)})" if (focus_choice == "growth" and missed_cats) else f"covering all topics of {current_topic}"

            followup_prompt = f"Please generate a new {num_q}-question quiz on {current_topic} ({diff_str}), {focus_str}."

            await sub_inter.response.defer(ephemeral=False)
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
        answers: dict[int, int],
        score: int,
        total_questions: int,
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
        q_text = q_data.get("text", "")
        options = q_data.get("options", [])
        cat = q_data.get("category", "")
        chosen_idx = self.answers.get(self.current_idx, -1)

        progress_str = format_progress_bar(self.questions, self.answers)
        cat_badge = f" • {cat}" if cat else ""

        header_text = (
            f"**Reviewing: {self.quiz_data.get('title', 'Quiz')}**\n"
            f"{progress_str}  `{self.current_idx + 1}/{self.total_questions}`\n"
            f"-# Score: {self.score}/{self.total_questions}{cat_badge}"
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        question_display = f"### Question {self.current_idx + 1}\n{q_text}"
        container.add_item(TextDisplay(question_display))

        for opt_idx, opt in enumerate(options):
            letter = CHOICE_LETTERS[opt_idx] if opt_idx < len(CHOICE_LETTERS) else str(opt_idx + 1)
            opt_text = opt.get("text", "")
            is_correct = bool(opt.get("correct", False))
            is_selected = (opt_idx == chosen_idx)
            explanation = opt.get("explanation", "").strip()

            if is_selected and is_correct:
                acc_btn = Button(label="Correct answer", style=discord.ButtonStyle.success, disabled=True)
                body_text = f"**{letter}. {opt_text}** (Your answer)\n{explanation}" if explanation else f"**{letter}. {opt_text}** (Your answer)"
                container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

            elif is_selected and not is_correct:
                acc_btn = Button(label="Incorrect", style=discord.ButtonStyle.danger, disabled=True)
                body_text = f"**{letter}. {opt_text}** (Your answer)\n{explanation}" if explanation else f"**{letter}. {opt_text}** (Your answer)"
                container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

            elif is_correct and not is_selected:
                acc_btn = Button(label="Correct answer", style=discord.ButtonStyle.success, disabled=True)
                body_text = f"**{letter}. {opt_text}**\n{explanation}" if explanation else f"**{letter}. {opt_text}**"
                container.add_item(Section(TextDisplay(body_text), accessory=acc_btn))

            else:
                plain_text = f"{letter}. {opt_text}"
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
            answers=self.answers,
            strengths=self.strengths,
            focus_areas=self.focus_areas,
            message_id=self.message_id
        )
        await interaction.response.edit_message(view=summary_view)