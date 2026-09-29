from django.contrib import admin
from django import forms

from .models import COLOR_CHOICES, AdAsset, Campaign, Generation, Product, ProductPhoto


class ProductForm(forms.ModelForm):
    colors = forms.MultipleChoiceField(choices=COLOR_CHOICES, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Product
        fields = "__all__"


class ProductPhotoInline(admin.TabularInline):
    model = ProductPhoto
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "pattern_type", "colors", "created_at")
    inlines = [ProductPhotoInline]
    form = ProductForm


class GenerationInline(admin.TabularInline):
    model = Generation
    extra = 0
    can_delete = False
    fields = ("attempt", "status", "qc_score", "qc_notes", "provider_used", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("product", "occasion", "status", "created_at")
    list_filter = ("status",)
    inlines = [GenerationInline]
    actions = ["approve_campaigns"]

    @admin.action(description="Approve selected campaigns")
    def approve_campaigns(self, request, queryset):
        campaigns = queryset.filter(status=Campaign.Status.REVIEW)
        campaign_ids = list(campaigns.values_list("id", flat=True))
        updated = campaigns.update(status=Campaign.Status.DONE)
        Generation.objects.filter(campaign_id__in=campaign_ids, status=Generation.Status.PASSED_QC).update(
            status=Generation.Status.APPROVED
        )
        self.message_user(request, f"{updated} campaign(s) approved.")

@admin.register(Generation)
class GenerationAdmin(admin.ModelAdmin):
    list_display = ("campaign", "attempt", "status", "qc_score", "created_at")
    list_filter = ("status",)
    readonly_fields = ("prompt", "image", "attempt", "status", "qc_score", "qc_notes", "provider_used", "created_at")


admin.site.register(AdAsset)