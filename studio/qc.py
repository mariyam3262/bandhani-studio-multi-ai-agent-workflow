import json
import os
from collections import Counter

import numpy as np
from PIL import Image, ImageFilter


def color_similarity(reference_path: str, generated_path: str) -> float:
    """Real, local, free check: compares dominant color palettes.
    Returns 0.0 (very different) to 1.0 (near-identical)."""

    def dominant_colors(path, size=(50, 50), k=5):
        img = Image.open(path).convert("RGB").resize(size)
        pixels = np.array(img).reshape(-1, 3).astype(float)
        quantized = (pixels // 32).astype(int)
        counts = Counter(map(tuple, quantized))
        top = counts.most_common(k)
        total = sum(count for _, count in top)
        return [(np.array(color) * 32 + 16, count / total) for color, count in top]

    ref_colors = dominant_colors(reference_path)
    gen_colors = dominant_colors(generated_path)

    if not ref_colors or not gen_colors:
        return 0.0

    max_dist = np.linalg.norm([255, 255, 255])
    ref_weights = [weight for _, weight in ref_colors]
    gen_weights = [weight for _, weight in gen_colors]
    matches = sorted(
        (np.linalg.norm(ref_color - gen_color), ref_index, gen_index)
        for ref_index, (ref_color, _) in enumerate(ref_colors)
        for gen_index, (gen_color, _) in enumerate(gen_colors)
    )
    total_distance = 0.0
    for distance, ref_index, gen_index in matches:
        matched_weight = min(ref_weights[ref_index], gen_weights[gen_index])
        total_distance += distance * matched_weight
        ref_weights[ref_index] -= matched_weight
        gen_weights[gen_index] -= matched_weight
    return max(0.0, 1.0 - (total_distance / max_dist))


class QCProvider:
    """Scores pattern structure, border treatment, and hand-tied texture."""

    def evaluate(self, reference_path: str, generated_path: str) -> dict:
        raise NotImplementedError


class MockQCProvider(QCProvider):
    """Offline structural comparison for the visibly watermarked mock output."""

    def evaluate(self, reference_path: str, generated_path: str) -> dict:
        reference = Image.open(reference_path).convert("L").resize((128, 128)).filter(ImageFilter.FIND_EDGES)
        generated = Image.open(generated_path).convert("L").resize((128, 128)).filter(ImageFilter.FIND_EDGES)
        reference_edges = np.asarray(reference, dtype=np.float32)
        generated_edges = np.asarray(generated, dtype=np.float32)
        score = float(np.mean(np.abs(reference_edges - generated_edges)) / 255)
        similarity = max(0.0, 1.0 - score)
        passed = similarity >= MOCK_STRUCTURE_THRESHOLD
        return {
            "score": similarity,
            "passed": passed,
            "notes": f"Mock edge-structure similarity {similarity:.2f}; threshold {MOCK_STRUCTURE_THRESHOLD:.2f}.",
        }


class GeminiVisionQCProvider(QCProvider):
    def __init__(self):
        self.model = os.environ.get("GEMINI_QC_MODEL", "")
        self.api_key = os.environ.get("GEMINI_API_KEY", "")

    def evaluate(self, reference_path: str, generated_path: str) -> dict:
        if not self.model or not self.api_key:
            raise RuntimeError("GEMINI_QC_MODEL and GEMINI_API_KEY are required for Gemini QC")
        from google import genai
        from google.genai import types

        with open(reference_path, "rb") as image_file:
            reference = types.Part.from_bytes(data=image_file.read(), mime_type="image/jpeg")
        with open(generated_path, "rb") as image_file:
            generated = types.Part.from_bytes(data=image_file.read(), mime_type="image/jpeg")
        prompt = (
            "Compare the garment in these images. Return only JSON with numeric score from 0 to 1 and concise notes. "
            "Score whether the same Bandhani pattern structure is preserved, including diamond lattice, border "
            "treatment, and scatter-dot zones, and whether the tie-dye dots are organic, irregular, and hand-tied "
            "rather than uniform or printed. A score below 0.75 means the structure does not pass."
        )
        response = genai.Client(api_key=self.api_key).models.generate_content(
            model=self.model,
            contents=[prompt, reference, generated],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        result = json.loads(response.text)
        score = min(1.0, max(0.0, float(result["score"])))
        return {"score": score, "passed": score >= STRUCTURE_PASS_THRESHOLD, "notes": str(result.get("notes", ""))}


def _configured_qc_provider() -> QCProvider:
    name = os.environ.get("QC_PROVIDER", "mock").lower()
    if name == "gemini":
        return GeminiVisionQCProvider()
    if name == "mock":
        return MockQCProvider()
    raise ValueError(f"Unsupported QC_PROVIDER: {name}")


QC_PROVIDER = _configured_qc_provider()


COLOR_PASS_THRESHOLD = 0.75
STRUCTURE_PASS_THRESHOLD = 0.75
MOCK_STRUCTURE_THRESHOLD = 0.75


def run_qc(generation):
    reference_photo = generation.campaign.product.photos.filter(kind="flatlay").first()
    if reference_photo is None:
        raise ValueError("No reference flat-lay photo to compare against")

    color_score = color_similarity(reference_photo.image.path, generation.image.path)
    structural = QC_PROVIDER.evaluate(reference_photo.image.path, generation.image.path)

    passed = color_score >= COLOR_PASS_THRESHOLD and structural["passed"]

    generation.qc_score = round(min(color_score, structural["score"]), 3)
    generation.qc_notes = (
        f"Color similarity: {color_score:.2f} (threshold {COLOR_PASS_THRESHOLD}). "
        f"Structural similarity: {structural['score']:.2f} (threshold {STRUCTURE_PASS_THRESHOLD}). "
        f"{structural['notes']}"
    )
    generation.status = generation.Status.PASSED_QC if passed else generation.Status.FAILED_QC
    generation.save(update_fields=["qc_score", "qc_notes", "status"])
    return generation