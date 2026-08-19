import pytest
from unittest.mock import AsyncMock, patch
from core.image_gen import (
    ImageGenerator,
    STYLE_MODIFIERS,
    RateLimitError,
)


@pytest.fixture
def image_gen():
    return ImageGenerator()


def test_style_modifiers_dictionary():
    assert "photorealistic" in STYLE_MODIFIERS
    assert "anime" in STYLE_MODIFIERS
    assert "cyberpunk" in STYLE_MODIFIERS
    assert "pixel" in STYLE_MODIFIERS
    assert len(STYLE_MODIFIERS) >= 10


@pytest.mark.asyncio
async def test_generate_pollinations_success_attempt_1(image_gen):
    with patch.object(
        image_gen,
        "_generate_pollinations",
        new=AsyncMock(return_value=b"fake-image-bytes"),
    ) as mock_poll:
        result = await image_gen.generate("A cute cat", style_key="anime")

        assert result == b"fake-image-bytes"
        mock_poll.assert_called_once()

        call_args = mock_poll.call_args[0]
        assert "A cute cat," in call_args[0]
        assert STYLE_MODIFIERS["anime"] in call_args[0]


@pytest.mark.asyncio
async def test_generate_fallback_to_pixazo_when_pollinations_fails(image_gen):
    image_gen.pixazo_api_key = "test-pixazo-key"

    with patch.object(
        image_gen,
        "_generate_pollinations",
        new=AsyncMock(side_effect=ValueError("500 Server Error")),
    ), patch.object(
        image_gen, "_generate_pixazo", new=AsyncMock(return_value=b"pixazo-image-bytes")
    ) as mock_pixazo:
        result = await image_gen.generate("A futuristic city", style_key="cyberpunk")

        assert result == b"pixazo-image-bytes"
        mock_pixazo.assert_called_once()


@pytest.mark.asyncio
async def test_generate_fallback_to_aihorde_when_all_prior_fail(image_gen):
    image_gen.pixazo_api_key = "test-key"

    with patch.object(
        image_gen,
        "_generate_pollinations",
        new=AsyncMock(side_effect=ValueError("Pollinations Down")),
    ), patch.object(
        image_gen,
        "_generate_pixazo",
        new=AsyncMock(side_effect=ValueError("Pixazo Quota Error")),
    ), patch.object(
        image_gen,
        "_generate_aihorde",
        new=AsyncMock(return_value=b"aihorde-image-bytes"),
    ) as mock_horde:
        result = await image_gen.generate("Fantasy castle", style_key="fantasy")

        assert result == b"aihorde-image-bytes"
        mock_horde.assert_called_once()


@pytest.mark.asyncio
async def test_generate_all_fallbacks_fail_raises_value_error(image_gen):
    image_gen.pixazo_api_key = None

    with patch.object(
        image_gen,
        "_generate_pollinations",
        new=AsyncMock(side_effect=ValueError("Service Unavailable")),
    ), patch.object(
        image_gen,
        "_generate_aihorde",
        new=AsyncMock(side_effect=TimeoutError("Queue Timeout")),
    ):
        with pytest.raises(ValueError) as exc_info:
            await image_gen.generate("A red apple")

        assert "Image generation failed across all fallback backends" in str(
            exc_info.value
        )


@pytest.mark.asyncio
async def test_pollinations_active_cooldown_raises_rate_limit_error(image_gen):

    image_gen.pollinations_cooldown_until = 9999999999.0

    with pytest.raises(RateLimitError) as exc_info:
        await image_gen._generate_pollinations("Prompt", 1024, 1024, 123)

    assert "active rate-limit cooldown" in str(exc_info.value)
