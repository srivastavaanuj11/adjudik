"""``manage.py adjudik_bootstrap`` (SPEC §16.6).

Creates a workspace (with settings, chain head and keys) and an admin user whose
password comes from ``ADJUDIK_BOOTSTRAP_PASSWORD`` and must be changed at first
login. Idempotent, and works with ``DEBUG`` off.
"""

from __future__ import annotations

import datetime as _dt
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts import service
from apps.accounts.models import Membership, Role, UserPasswordState


class Command(BaseCommand):
    help = "Create a workspace and its first admin user (SPEC §16.6)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--workspace", required=True, help="workspace slug")
        parser.add_argument("--admin", required=True, help="admin username")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        slug = options["workspace"]
        username = options["admin"]
        password = os.environ.get("ADJUDIK_BOOTSTRAP_PASSWORD")
        if not password:
            raise CommandError("ADJUDIK_BOOTSTRAP_PASSWORD must be set (SPEC §16.6).")

        now = _dt.datetime.now(tz=_dt.UTC)
        ws = service.create_workspace(slug, slug, now=now)

        model = get_user_model()
        user, created = model.objects.get_or_create(username=username)
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
            UserPasswordState.objects.update_or_create(
                user=user,
                defaults={
                    "must_change": True,
                    "temp_expires_at": now + _dt.timedelta(hours=24),
                },
            )

        Membership.objects.get_or_create(
            workspace=ws, user=user, defaults={"role": Role.ADMIN, "created_at": now}
        )

        status = "created (must change password at first login)" if created else "already existed"
        self.stdout.write(
            self.style.SUCCESS(f"workspace '{slug}' ready; admin '{username}' {status}.")
        )
