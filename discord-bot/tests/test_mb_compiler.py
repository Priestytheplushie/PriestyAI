import pytest
from tools.message_builder.mb_compiler import (
    compile_dsl_payload,
    ASTValidationError,
)


def test_compile_valid_container_and_text():
    code = """
Container(
    TextDisplay("Hello Discord!"),
    accent_colour="0x5865F2"
)
"""
    result = compile_dsl_payload(code)
    assert result["type"] == "Container"
    assert result["kwargs"]["accent_colour"] == "0x5865F2"
    assert len(result["args"]) == 1
    assert result["args"][0]["type"] == "TextDisplay"
    assert result["args"][0]["args"] == ["Hello Discord!"]


def test_compile_code_block_markdown_stripping():
    code = """```python
Container(
    TextDisplay("Cleaned from markdown fences")
)
```"""
    result = compile_dsl_payload(code)
    assert result["type"] == "Container"
    assert result["args"][0]["args"] == ["Cleaned from markdown fences"]


def test_compile_section_with_mandatory_accessory():
    code = """
Section(
    children=[TextDisplay("Left column info")],
    accessory=Button("Click Me", style="primary")
)
"""
    result = compile_dsl_payload(code)
    assert result["type"] == "Section"
    assert result["kwargs"]["accessory"]["type"] == "Button"
    assert result["kwargs"]["children"][0]["type"] == "TextDisplay"


def test_compile_section_missing_accessory_raises_error():
    code = """
Section(
    children=[TextDisplay("Missing accessory button")]
)
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "Section component must contain a valid 'accessory'" in str(exc_info.value)


def test_compile_action_row_button_limit():

    valid_code = """
ActionRow(
    Button("1"), Button("2"), Button("3"), Button("4"), Button("5")
)
"""
    result = compile_dsl_payload(valid_code)
    assert result["type"] == "ActionRow"
    assert len(result["args"]) == 5

    invalid_code = """
ActionRow(
    Button("1"), Button("2"), Button("3"), Button("4"), Button("5"), Button("6")
)
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(invalid_code)
    assert "cannot hold more than 5 Button components" in str(exc_info.value)


def test_compile_action_row_single_dropdown_limit():
    code = """
ActionRow(
    StringSelect(placeholder="First"),
    StringSelect(placeholder="Second")
)
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "cannot hold more than exactly 1 Select Dropdown" in str(exc_info.value)


def test_compile_action_row_cannot_mix_buttons_and_dropdowns():
    code = """
ActionRow(
    Button("Click"),
    StringSelect(placeholder="Select...")
)
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "cannot mix Buttons and Select Dropdowns" in str(exc_info.value)


def test_compile_max_action_rows_limit():
    code = """
Container(
    ActionRow(Button("1")),
    ActionRow(Button("2")),
    ActionRow(Button("3")),
    ActionRow(Button("4")),
    ActionRow(Button("5")),
    ActionRow(Button("6"))
)
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "cannot contain more than 5 ActionRow components" in str(exc_info.value)


def test_security_blocks_import_statements():
    code = """
import os
Container(TextDisplay("Unsafe"))
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "Imports are strictly forbidden" in str(exc_info.value)


def test_security_blocks_function_definitions():
    code = """
def hack():
    pass
Container(TextDisplay("Unsafe"))
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "Defining functions is forbidden" in str(exc_info.value)


def test_security_blocks_loops():
    code = """
for i in range(5):
    Button(str(i))
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "Control loops are forbidden" in str(exc_info.value)


def test_security_blocks_unapproved_functions():
    code = """
eval("print('pwned')")
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "Call to unapproved component or function" in str(exc_info.value)


def test_no_chained_modals_rule():
    code = """
Button(
    label="Open",
    on_click=[Action.open_modal(Modal(title="Test")), Action.reply_public("Chained")]
)
"""
    with pytest.raises(ASTValidationError) as exc_info:
        compile_dsl_payload(code)
    assert "open_modal() cannot be chained" in str(exc_info.value)
