"""Form fields shared by every app."""

from django import forms

from .utils import PICTURE_ACCEPT


class PictureField(forms.ImageField):
    """A picture of any common type, including iPhone HEIC.

    Django's own check would refuse a type by its file extension. ``process_image`` opens the file and
    decides from what is inside it, so only the file picker is told what to offer.
    """

    default_validators = []

    def widget_attrs(self, widget):
        attrs = super().widget_attrs(widget)
        attrs["accept"] = PICTURE_ACCEPT
        return attrs
