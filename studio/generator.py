import io
import logging
import os

from PIL import Image, ImageDraw

from .models import Generation

logger = logging.getLogger("studio")


class ImageProvider:
    name = "interface"

    def generate(self, prompt: str, reference_image_path: str) -> bytes:
        raise NotImplementedError


class MockProvider(ImageProvider):
    name = "mock"

    def generate(self, prompt: str, reference_image_path: str) -> bytes:
        image = Image.open(reference_image_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        label = "MOCK GENERATION"
        bounds = draw.textbbox((0, 0), label)
        width = bounds[2] - bounds[0]
        draw.rectangle((0, image.height - 30, image.width, image.height), fill=(20, 20, 20))
        draw.text(((image.width - width) // 2, image.height - 24), label, fill=(255, 255, 255))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=95)
        return output.getvalue()


class GeminiProvider(ImageProvider):
    def __init__(self):
        self.model = os.environ.get("GEMINI_IMAGE_MODEL", "")
        self.api_key = os.environ.get("GEMINI_API_KEY", "")

    @property
    def name(self):
        return f"gemini:{self.model}"

    def generate(self, prompt: str, reference_image_path: str) -> bytes:
        if not self.model or not self.api_key:
            raise RuntimeError("GEMINI_IMAGE_MODEL and GEMINI_API_KEY are required for Gemini generation")
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        with open(reference_image_path, "rb") as image_file:
            image_part = types.Part.from_bytes(data=image_file.read(), mime_type="image/jpeg")
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=[prompt, image_part],
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
            )
        except Exception as exc:
            message = str(exc)
            if "RESOURCE_EXHAUSTED" in message or "quota" in message.lower():
                raise QuotaExceededError(message) from exc
            raise
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if part.inline_data and part.inline_data.data:
                    return part.inline_data.data
        raise RuntimeError("Gemini returned no image data")


class QuotaExceededError(RuntimeError):
    pass


def _configured_provider():
    provider_name = os.environ.get("IMAGE_PROVIDER", "mock").lower()
    if provider_name == "mock":
        return MockProvider()
    if provider_name == "gemini":
        return GeminiProvider()
    raise ValueError(f"Unsupported IMAGE_PROVIDER: {provider_name}")


IMAGE_PROVIDER = _configured_provider()


def build_prompt(campaign, corrective_feedback=""):
    p = campaign.product
    return (
        f"Create a professional, ad-ready fashion photograph for the occasion: {campaign.occasion}. "
        "The attached photo is the real garment. Show a model wearing this exact garment. "
        "Keep the garment IDENTICAL to the photo: same Bandhani (tie-dye) dot pattern, "
        f"same {p.pattern_type} motif layout, same colors ({p.colors}), same fabric look. "
        "Do not invent, simplify, or restyle the pattern. "
        "Natural skin and hands, realistic lighting, elegant Indian festive setting, "
        "no text, no watermark, no logos. "
        f"{campaign.brief} {corrective_feedback}"
    )


def prepare_generation(campaign):
    photo = campaign.product.photos.filter(kind="flatlay").first()
    if photo is None:
        raise ValueError("This product has no flat-lay photo")

    prompt = build_prompt(campaign)
    generation = Generation.objects.create(campaign=campaign, prompt=prompt)
    return generation, photo