from django.core.management.base import BaseCommand
from django.db.models import Q
from stammbaum.models import Elternschaft, Ehe


class Command(BaseCommand):
    help = 'Erstellt Ehe-Einträge für Elternpaare mit gemeinsamen Kindern'

    def handle(self, *args, **options):
        paare = set()
        for e in Elternschaft.objects.filter(vater__isnull=False, mutter__isnull=False):
            key = (min(e.vater_id, e.mutter_id), max(e.vater_id, e.mutter_id))
            paare.add((key[0], key[1]))

        created = 0
        for a_id, b_id in paare:
            exists = Ehe.objects.filter(
                Q(partner1_id=a_id, partner2_id=b_id) |
                Q(partner1_id=b_id, partner2_id=a_id)
            ).exists()
            if not exists:
                Ehe.objects.create(partner1_id=a_id, partner2_id=b_id)
                created += 1

        self.stdout.write(self.style.SUCCESS(
            f'{created} Ehe-Einträge erstellt (aus {len(paare)} Elternpaaren)'
        ))
