from django.core.management.base import BaseCommand
from netbox_demandsite.views import sync_all_demand_sites


class Command(BaseCommand):
    help = "Synchronize sites and BTS devices from Demand Site API into NetBox (every 24 hours or on-demand)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--siteid',
            type=str,
            help='Optionally synchronize only a single site ID (e.g. --siteid CDR807)',
            default=None
        )

    def handle(self, *args, **options):
        single_site = options.get('siteid')
        if single_site:
            self.stdout.write(f"Starting Demand Site synchronization for single site: {single_site}...")
        else:
            self.stdout.write("Starting Demand Site 24-Hour full synchronization...")

        result = sync_all_demand_sites(
            logger_func=self.stdout.write,
            target_siteid=single_site
        )

        msg = (
            f"Demand Site sync finished: Total={result['total']}, "
            f"Created={result['created']}, Updated={result['updated']}, "
            f"Unchanged={result['unchanged']}, Errors={len(result['errors'])}"
        )
        if result['errors']:
            self.stdout.write(self.style.WARNING(msg))
            for err in result['errors']:
                self.stderr.write(self.style.ERROR(f" - {err}"))
        else:
            self.stdout.write(self.style.SUCCESS(msg))
