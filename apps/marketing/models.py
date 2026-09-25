from django.db import models

from apps.core.utils import keep_name


class GalleryImage(models.Model):
    """A slide in the homepage gallery, managed by staff."""

    FOCUS = [("center", "Centre"), ("top", "Top"), ("bottom", "Bottom")]

    image = models.ImageField(upload_to=keep_name)
    title = models.CharField(
        max_length=120, help_text="The dark opening line under the slides, e.g. the client or project."
    )
    description = models.CharField(
        max_length=240, blank=True, help_text="The grey sentence that runs on from the title."
    )
    alt_text = models.CharField(
        max_length=200, blank=True, help_text="Describe the picture for people who cannot see it."
    )
    caption = models.CharField(
        max_length=40, blank=True, help_text="Short label shown on the open slide, e.g. 'Flyer design'."
    )
    action_label = models.CharField(max_length=40, blank=True, default="View design")
    action_url = models.URLField(
        blank=True, help_text="Optional. Leave empty to open the full picture instead."
    )
    focus = models.CharField(
        max_length=8, choices=FOCUS, default="center",
        help_text="Which part of the picture stays in view when a slide is narrow.",
    )
    display_order = models.IntegerField(default=0)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    legacy_id = models.IntegerField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self):
        return self.title
