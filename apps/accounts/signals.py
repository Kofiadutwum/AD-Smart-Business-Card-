from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User


@receiver(post_save, sender=User)
def give_staff_a_card(sender, instance, raw=False, **kwargs):
    """Every administrator gets a personal card the moment they become staff."""
    if raw or not instance.is_staff:
        return
    from apps.cards.services import ensure_staff_card

    ensure_staff_card(instance)
