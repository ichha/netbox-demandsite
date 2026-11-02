from django.core.management.base import BaseCommand
from netbox_demandsite.views import auto_sync_missing_demand_sites, sync_all_demand_sites


class Command(BaseCommand):
    help = "Demand WSD Auto-Sync: Checks for Demand sites missing in NetBox and imports them (every 24 hours or on-demand)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--siteid',
            type=str,
            help='Optionally synchronize only a single site ID (e.g. --siteid CDR807)',
            default=None
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Force sync of all sites (default only imports missing sites)',
            default=False
        )

    def handle(self, *args, **options):
        single_site = options.get('siteid')
        sync_all = options.get('all')

        if single_site:
            self.stdout.write(f"Starting Demand Site synchronization for single site: {single_site}...")
            result = sync_all_demand_sites(logger_func=self.stdout.write, target_siteid=single_site)
        elif sync_all:
            self.stdout.write("Starting FULL Demand Site synchronization for ALL sites...")
            result = sync_all_demand_sites(logger_func=self.stdout.write)
        else:
            self.stdout.write("Starting 24-Hour Auto-Sync (Importing Demand sites missing in NetBox)...")
            result = auto_sync_missing_demand_sites(source='CLI manage.py sync_demandsite', logger_func=self.stdout.write)

        if 'created' in result:
            msg = f"Done: {result['created']} created, {result.get('skipped', 0)} skipped, {len(result.get('errors', []))} errors."
        else:
            msg = f"Done: {result['created']} created, {result['updated']} updated, {len(result.get('errors', []))} errors."

        if result.get('errors'):
            self.stdout.write(self.style.WARNING(msg))
            for err in result['errors']:
                self.stderr.write(self.style.ERROR(f" - {err}"))
        else:
            self.stdout.write(self.style.SUCCESS(msg))
