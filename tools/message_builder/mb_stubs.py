
from typing import Union, List, Optional

class Component:
    pass

class Container(Component):
    def __init__(self, *children: Component, accent_colour: Optional[str] = None) -> None:
        pass

class Section(Component):
    def __init__(self, children: List[Component], accessory: Component, id: Optional[str] = None) -> None:
        pass

class TextDisplay(Component):
    def __init__(self, content: str, id: Optional[str] = None) -> None:
        pass

class Separator(Component):
    def __init__(self, spacing: str = "small", visible: bool = True) -> None:
        pass

class Button(Component):
    def __init__(self, label: str, style: str = "secondary", url: Optional[str] = None, on_click: Optional[Union[str, List[str]]] = None, id: Optional[str] = None) -> None:
        pass

class SelectOption:
    def __init__(self, label: str, value: str, description: Optional[str] = None, emoji: Optional[str] = None) -> None:
        pass

class UserSelect(Component):
    def __init__(self, placeholder: str, min_values: int = 1, max_values: int = 25, on_select: Optional[Union[str, List[str]]] = None, id: Optional[str] = None) -> None:
        pass

class RoleSelect(Component):
    def __init__(self, placeholder: str, min_values: int = 1, max_values: int = 25, on_select: Optional[Union[str, List[str]]] = None, id: Optional[str] = None) -> None:
        pass

class ChannelSelect(Component):
    def __init__(self, placeholder: str, min_values: int = 1, max_values: int = 25, on_select: Optional[Union[str, List[str]]] = None, id: Optional[str] = None) -> None:
        pass

class MentionableSelect(Component):
    def __init__(self, placeholder: str, min_values: int = 1, max_values: int = 25, on_select: Optional[Union[str, List[str]]] = None, id: Optional[str] = None) -> None:
        pass

class StringSelect(Component):
    def __init__(self, placeholder: str, options: List[SelectOption], min_values: int = 1, max_values: int = 25, on_select: Optional[Union[str, List[str]]] = None, id: Optional[str] = None) -> None:
        pass

class ActionRow(Component):
    def __init__(self, *children: Component) -> None:
        pass

class ModalField(Component):
    pass

class Label(ModalField):
    def __init__(self, text: str, component: Component, description: Optional[str] = None) -> None:
        pass

class TextInput(ModalField):
    def __init__(self, label: str = "Input", style: str = "short", custom_id: Optional[str] = None) -> None:
        pass

class Checkbox(ModalField):
    def __init__(self, label: str, default: bool = False, id: Optional[str] = None) -> None:
        pass

class CheckboxGroup(ModalField):
    def __init__(self, options: List[SelectOption], id: Optional[str] = None) -> None:
        pass

class RadioGroup(ModalField):
    def __init__(self, options: List[SelectOption], id: Optional[str] = None) -> None:
        pass

class FileUpload(ModalField):
    def __init__(self, min_values: int = 1, max_values: int = 1, id: Optional[str] = None) -> None:
        pass

class Modal:
    def __init__(self, title: str, *children: ModalField, on_submit: Optional[Union[str, List[str]]] = None) -> None:
        pass

class Action:
    
    @staticmethod
    def trigger_ai(instruction_payload: str) -> str:
        return f"ai:{instruction_payload}"

    @staticmethod
    def trigger_image_generation(prompt: str) -> str:
        return f"trigger_image_generation:{prompt}"

    @staticmethod
    def reply_private(text_content: str) -> str:
        return f"reply_private:{text_content}"

    @staticmethod
    def reply_public(text_content: str) -> str:
        return f"reply_public:{text_content}"

    @staticmethod
    def delete_message() -> str:
        return "delete_message"

    @staticmethod
    def disable_components() -> str:
        return "disable_components"

    @staticmethod
    def pass_input() -> str:
        return "pass"

    @staticmethod
    def open_modal(modal: Modal) -> str:
        return "open_modal"