from typing import List, Dict, Any, Optional

class ModalV2Builder:

    @staticmethod
    def text_display(content: str) -> Dict[str, Any]:
        return {
            "type": 10,
            "content": content
        }

    @staticmethod
    def text_input(
        custom_id: str,
        label: str,
        style: str = "short",
        placeholder: Optional[str] = None,
        default: Optional[str] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        required: bool = True,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        inner = {
            "type": 4,
            "custom_id": custom_id,
            "style": 2 if style.lower() in ("paragraph", "long", "2") else 1,
            "required": required
        }
        if placeholder:
            inner["placeholder"] = placeholder
        if default:
            inner["value"] = default
        if min_length is not None:
            inner["min_length"] = min_length
        if max_length is not None:
            inner["max_length"] = max_length

        wrapper = {
            "type": 18,
            "label": label,
            "component": inner
        }
        if description:
            wrapper["description"] = description
        return wrapper

    @staticmethod
    def string_select(
        custom_id: str,
        label: str,
        options: List[Dict[str, str]],
        placeholder: Optional[str] = None,
        description: Optional[str] = None,
        required: bool = True
    ) -> Dict[str, Any]:
        formatted_options = []
        for opt in options:
            formatted_options.append({
                "label": opt.get("label", "Option")[:100],
                "value": opt.get("value", opt.get("label", "val"))[:100],
                "description": opt.get("description", "")[:100] if opt.get("description") else None,
                "default": opt.get("default", False)
            })

        wrapper = {
            "type": 18,
            "label": label,
            "component": {
                "type": 3,
                "custom_id": custom_id,
                "placeholder": placeholder or "Select an option...",
                "options": formatted_options,
                "required": required
            }
        }
        if description:
            wrapper["description"] = description
        return wrapper

    @staticmethod
    def channel_select(
        custom_id: str,
        label: str,
        placeholder: Optional[str] = None,
        description: Optional[str] = None,
        channel_types: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        inner = {
            "type": 8,
            "custom_id": custom_id,
            "placeholder": placeholder or "Choose a channel..."
        }
        if channel_types:
            inner["channel_types"] = channel_types

        wrapper = {
            "type": 18,
            "label": label,
            "component": inner
        }
        if description:
            wrapper["description"] = description
        return wrapper

    @staticmethod
    def radio_group(
        custom_id: str,
        label: str,
        options: List[Dict[str, str]],
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        formatted_options = []
        for opt in options:
            formatted_options.append({
                "label": opt.get("label", "Option"),
                "value": opt.get("value", opt.get("label", "val")),
                "description": opt.get("description"),
                "default": opt.get("default", False)
            })

        wrapper = {
            "type": 18,
            "label": label,
            "component": {
                "type": 21,
                "custom_id": custom_id,
                "options": formatted_options
            }
        }
        if description:
            wrapper["description"] = description
        return wrapper

    @staticmethod
    def checkbox_group(
        custom_id: str,
        label: str,
        options: List[Dict[str, str]],
        min_values: int = 0,
        max_values: int = 10,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        formatted_options = []
        for opt in options:
            formatted_options.append({
                "label": opt.get("label", "Option"),
                "value": opt.get("value", opt.get("label", "val")),
                "description": opt.get("description"),
                "default": opt.get("default", False)
            })

        wrapper = {
            "type": 18,
            "label": label,
            "component": {
                "type": 22,
                "custom_id": custom_id,
                "min_values": min_values,
                "max_values": max_values,
                "options": formatted_options
            }
        }
        if description:
            wrapper["description"] = description
        return wrapper

    @staticmethod
    def file_upload(
        custom_id: str,
        label: str,
        min_values: int = 1,
        max_values: int = 3,
        required: bool = False,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        wrapper = {
            "type": 18,
            "label": label,
            "component": {
                "type": 19,
                "custom_id": custom_id,
                "min_values": min_values,
                "max_values": max_values,
                "required": required
            }
        }
        if description:
            wrapper["description"] = description
        return wrapper