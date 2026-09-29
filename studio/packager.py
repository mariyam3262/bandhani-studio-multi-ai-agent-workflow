import os

from django.core.files.base import ContentFile
from PIL import Image

from .models import AdAsset

ASPECT_RATIOS = {
    "1:1": (1, 1),
    "4:5": (4, 5),
    "9:16": (9, 16),
}


def center_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    img_ratio = img.width / img.height
    target_ratio = target_w / target_h

    if img_ratio > target_ratio:
        new_width = int(img.height * target_ratio)
        left = (img.width - new_width) // 2
        box = (left, 0, left + new_width, img.height)
    else:
        new_height = int(img.width / target_ratio)
        top = (img.height - new_height) // 2
        box = (0, top, img.width, top + new_height)

    return img.crop(box)


def build_caption(campaign) -> str:
    p = campaign.product
    return (
        f"Handcrafted {p.pattern_type.title()} Bandhani, made for {campaign.occasion}. "
        f"Traditional tie-dye artistry in {', '.join(p.colors)}. "
        f"#Bandhani #{p.pattern_type.title()} #{campaign.occasion.replace(' ', '')} "
        f"#HandmadeIndia #TraditionalTextile"
    )


def package_generation(generation):
    if generation.status != generation.Status.PASSED_QC:
        raise ValueError("Only PASSED_QC generations can be packaged")

    source = Image.open(generation.image.path).convert("RGB")
    caption = build_caption(generation.campaign)
    assets = []

    for label, (w, h) in ASPECT_RATIOS.items():
        cropped = center_crop(source, w, h)
        cropped = cropped.resize((1080, int(1080 * h / w)))

        from io import BytesIO
        buf = BytesIO()
        cropped.save(buf, format="JPEG", quality=90)

        asset, created = AdAsset.objects.get_or_create(
            generation=generation,
            aspect_ratio=label,
            defaults={"caption": caption},
        )
        if created or not asset.image:
            asset.caption = caption
            asset.image.save(f"ad_{generation.id}_{label.replace(':', 'x')}.jpg", ContentFile(buf.getvalue()), save=True)
        assets.append(asset)

    campaign = generation.campaign
    if campaign.status != campaign.Status.DONE:
        campaign.status = campaign.Status.REVIEW
        campaign.save(update_fields=["status"])
    return assets