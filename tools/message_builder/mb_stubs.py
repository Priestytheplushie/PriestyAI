"""
This is a mock declaration stub file defining the interface for modern Components V2 layouts.
It is used purely to instruct the AI on valid visual structures and scripting capabilities.
"""

from typing import Union, List, Optional


class Component:
    """Base interface for all modern Components V2 layouts."""

    pass


class Container(Component):
    """
    Renders a modern, bordered visual card bounding multiple nested layout elements.

    Args:
        *children: Nested components inside this card (TextDisplay, Section, Separator, ActionRow).
        accent_colour: Optional border hex string (e.g., "0x7289da", "0xff0000").
                       DESIGN RULE: If accent_colour is omitted or set to None, the container
                       will have no visible border, looking seamlessly integrated into the
                       native Discord dark/light UI theme.
    """

    def __init__(
        self, *children: Component, accent_colour: Optional[str] = None
    ) -> None:
        pass


class Section(Component):
    """
    A dual-column card element. Perfect for lists, directories, or catalog options.
    Houses a list of TextDisplays (up to 3) on the left column, paired with a single accessory on the right.

    Args:
        children: A list of TextDisplay components (1 to 3) to render on the left side.
        accessory: A Button component to display on the right side. This argument is STRICTLY REQUIRED.
                   DESIGN RULE: You MUST pass a Button component as the keyword argument 'accessory'.
                   It is mandatory. If you do not want an accessory, use a Container with a TextDisplay instead of a Section.
        id: Optional state identifier.
    """

    def __init__(
        self, children: List[Component], accessory: Component, id: Optional[str] = None
    ) -> None:
        pass


class TextDisplay(Component):
    """
    Renders cleanly formatted inline markdown text anywhere on the canvas grid.
    Supports bold, italics, headers, code blocks, spoilers, and lists (up to 4000 characters).
    """

    def __init__(self, content: str, id: Optional[str] = None) -> None:
        pass


class Separator(Component):
    """
    A horizontal visual divider line to cleanly separate components.

    Args:
        spacing: Set empty vertical gap size. Allowed: "small" or "large".
        visible: If True, draws a solid horizontal divider line. False leaves empty space.
    """

    def __init__(self, spacing: str = "small", visible: bool = True) -> None:
        pass


class Button(Component):
    """
    An interactive click trigger. Must sit inside an ActionRow.

    Args:
        label: Text written on the button (max 80 characters).
        style: Button color preset. Allowed: "primary", "secondary", "success", "danger", "link".
        url: Destination URL. Required if style is set to "link".
        on_click: Interactive action call or a list of chained action calls to execute when clicked.
                  Example: Action.reply_private("Confirmed!")
        id: Unique callback tracking identifier.
    """

    def __init__(
        self,
        label: str,
        style: str = "secondary",
        url: Optional[str] = None,
        on_click: Optional[Union[str, List[str]]] = None,
        id: Optional[str] = None,
    ) -> None:
        pass


class SelectOption:
    """Represents a text choice option inside a StringSelect component."""

    def __init__(
        self,
        label: str,
        value: str,
        description: Optional[str] = None,
        emoji: Optional[str] = None,
    ) -> None:
        pass


class UserSelect(Component):
    """
    A dropdown selector allowing the interacting user to pick members from the server.
    Must sit inside an ActionRow.

    Args:
        placeholder: Text displayed when nothing is selected.
        min_values: Minimum members that must be selected (default: 1, max: 25).
        max_values: Maximum members allowed to be selected (default: 1, max: 25).
        on_select: Event trigger script executing when selection is confirmed.
        id: Unique state tracking identifier.
    """

    def __init__(
        self,
        placeholder: str,
        min_values: int = 1,
        max_values: int = 25,
        on_select: Optional[Union[str, List[str]]] = None,
        id: Optional[str] = None,
    ) -> None:
        pass


class RoleSelect(Component):
    """
    A dropdown selector allowing users to pick roles from the server.
    Must sit inside an ActionRow.
    """

    def __init__(
        self,
        placeholder: str,
        min_values: int = 1,
        max_values: int = 25,
        on_select: Optional[Union[str, List[str]]] = None,
        id: Optional[str] = None,
    ) -> None:
        pass


class ChannelSelect(Component):
    """
    A dropdown selector allowing users to pick channels/threads from the server.
    Must sit inside an ActionRow.
    """

    def __init__(
        self,
        placeholder: str,
        min_values: int = 1,
        max_values: int = 25,
        on_select: Optional[Union[str, List[str]]] = None,
        id: Optional[str] = None,
    ) -> None:
        pass


class MentionableSelect(Component):
    """
    A dropdown selector allowing users to pick either members or roles.
    Must sit inside an ActionRow.
    """

    def __init__(
        self,
        placeholder: str,
        min_values: int = 1,
        max_values: int = 25,
        on_select: Optional[Union[str, List[str]]] = None,
        id: Optional[str] = None,
    ) -> None:
        pass


class StringSelect(Component):
    """
    A custom dropdown selector presenting a predefined set of text choices.
    Must sit inside an ActionRow.
    """

    def __init__(
        self,
        placeholder: str,
        options: List[SelectOption],
        min_values: int = 1,
        max_values: int = 25,
        on_select: Optional[Union[str, List[str]]] = None,
        id: Optional[str] = None,
    ) -> None:
        pass


class ActionRow(Component):
    """
    A horizontal row layout on the interface.

    API LIMITATION RULES (MANDATORY):
    1. An ActionRow can hold up to 5 Button components.
    2. An ActionRow can hold EXACTLY 1 dropdown selector (e.g. StringSelect, UserSelect).
    3. You can NEVER mix Buttons and Dropdowns in the same ActionRow.
    4. You can NEVER place multiple dropdown selectors inside a single ActionRow.
    """

    def __init__(self, *children: Component) -> None:
        pass


class ModalField(Component):
    """Base interface for custom interactive fields inside a Modal V2 pop-up."""

    pass


class Label(ModalField):
    """Pairs a static descriptive heading with a nested child input inside a Modal."""

    def __init__(
        self, text: str, component: Component, description: Optional[str] = None
    ) -> None:
        pass


class TextInput(ModalField):
    """
    A short or long text input field inside a Modal form.
    """

    def __init__(
        self,
        label: str = "Input",
        style: str = "short",
        custom_id: Optional[str] = None,
    ) -> None:
        pass


class Checkbox(ModalField):
    """An individual true/false binary toggle selection inside a Modal."""

    def __init__(
        self, label: str, default: bool = False, id: Optional[str] = None
    ) -> None:
        pass


class CheckboxGroup(ModalField):
    """A multi-select choice list of toggles inside a Modal."""

    def __init__(self, options: List[SelectOption], id: Optional[str] = None) -> None:
        pass


class RadioGroup(ModalField):
    """A single-select list of toggles inside a Modal where only one can be selected."""

    def __init__(self, options: List[SelectOption], id: Optional[str] = None) -> None:
        pass


class FileUpload(ModalField):
    """An interactive target slot allowing users to attach files inside a Modal pop-up."""

    def __init__(
        self, min_values: int = 1, max_values: int = 1, id: Optional[str] = None
    ) -> None:
        pass


class Modal:
    """
    Renders an interactive, pop-up input form Modal V2 to collect rich user data.

    Args:
        title: Title of the modal pop-up (max 45 characters).
        *children: Interactive ModalFields (e.g., Label, CheckboxGroup, RadioGroup, FileUpload).
        on_submit: Callback action or list of action scripts executed when submitted.

    API LIMITATION RULES (MANDATORY):
    1. Modals cannot be opened from within another modal submit (No Chained Modals).
    2. Modal callbacks cannot trigger another modal.
    """

    def __init__(
        self,
        title: str,
        *children: ModalField,
        on_submit: Optional[Union[str, List[str]]] = None,
    ) -> None:
        pass


class Action:
    """Declarative callback script constructor to register events on components."""

    @staticmethod
    def trigger_ai(instruction_payload: str) -> str:
        """
        Pauses normal execution and triggers a fresh AI model turn.
        Use this when you need to process inputs, update layouts, or decide the next state.
        """
        return f"ai:{instruction_payload}"

    @staticmethod
    def trigger_image_generation(prompt: str) -> str:
        """
        Instantly routes the user's prompt to the visual generation pipeline and spawns an image.
        """
        return f"trigger_image_generation:{prompt}"

    @staticmethod
    def reply_private(text_content: str) -> str:
        """Instantly replies to the active user with a private (ephemeral) message."""
        return f"reply_private:{text_content}"

    @staticmethod
    def reply_public(text_content: str) -> str:
        """Sends a standard public follow-up message in the current text channel."""
        return f"reply_public:{text_content}"

    @staticmethod
    def delete_message() -> str:
        """Deletes the active layout interface message immediately."""
        return "delete_message"

    @staticmethod
    def disable_components() -> str:
        """Disables all interactive buttons and select menus inside the parent layout."""
        return "disable_components"

    @staticmethod
    def pass_input() -> str:
        """
        Silently saves the user's selected choice/input to backend state memory without
        alerting the AI. Use this for passive surveys or deferred polling.
        """
        return "pass"

    @staticmethod
    def open_modal(modal: Modal) -> str:
        """
        Opens an interactive pop-up form.
        CRITICAL CONSTRAINT: You cannot chain modals or combine this action with other responses.
        """
        return "open_modal"
