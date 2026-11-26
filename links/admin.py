from django.contrib import admin

from links.models import LinkDetails, Links


@admin.register(Links)
class LinksAdmin(admin.ModelAdmin):
    list_display = ["id", "content_id", "link"]
    search_fields = ["content_id", "link"]


@admin.register(LinkDetails)
class LinkDetailsAdmin(admin.ModelAdmin):
    list_display = ["id", "content_id", "status", "status_code", "brand_exist", "brand_name", "link_detail", "resolved_at"]
    list_filter = ["status", "brand_exist"]
    search_fields = ["link__content_id", "link__link", "link_detail", "brand_name"]
    list_select_related = ["link"]  # content_id comes from Links: one query, not one per row
    raw_id_fields = ["link"]  # a dropdown of every link would not scale

    @admin.display(description="content id", ordering="link__content_id")
    def content_id(self, detail: LinkDetails) -> int:
        return detail.link.content_id
