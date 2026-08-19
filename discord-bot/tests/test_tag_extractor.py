import pytest
from core.bot import (
    extract_build_message,
    split_outside_parentheses,
    generate_slug_from_prompt,
    sanitize_channel_name,
)


def test_extract_build_message_simple():
    content = "Here is your layout: [BUILD_MESSAGE: Container(TextDisplay('Hi'))] Have a great day!"
    clean_text, dsl_code = extract_build_message(content)
    assert clean_text == "Here is your layout:  Have a great day!"
    assert dsl_code == "Container(TextDisplay('Hi'))"


def test_extract_build_message_with_nested_brackets_and_quotes():
    content = (
        "Check this: [BUILD_MESSAGE: Section(children=[TextDisplay('Test [1]')], "
        "accessory=Button('OK'))] cool right?"
    )
    clean_text, dsl_code = extract_build_message(content)
    assert clean_text == "Check this:  cool right?"
    assert (
        dsl_code
        == "Section(children=[TextDisplay('Test [1]')], accessory=Button('OK'))"
    )


def test_extract_build_message_none():
    content = "Just a standard message with no layouts."
    clean_text, dsl_code = extract_build_message(content)
    assert clean_text == content
    assert dsl_code is None


def test_split_outside_parentheses():
    text = "Field1:short:Desc, Field2:select_string(Opt1:desc, Opt2:desc):Field Desc"
    parts = split_outside_parentheses(text, char=",")
    assert len(parts) == 2
    assert parts[0].strip() == "Field1:short:Desc"
    assert parts[1].strip() == "Field2:select_string(Opt1:desc, Opt2:desc):Field Desc"


def test_generate_slug_from_prompt():
    prompt = "Compare AWS and GCP latency"
    slug = generate_slug_from_prompt(prompt)
    assert slug == "aws-gcp"


def test_sanitize_channel_name():
    assert sanitize_channel_name("Dev Chat #1!") == "dev-chat-1"
