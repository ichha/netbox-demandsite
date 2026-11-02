import logging
from extras.scripts import Script
from .views import sync_all_demand_sites

logger = logging.getLogger('netbox.plugins.netbox_demandsite')


class DemandsiteDailySyncScript(Script):
    class Meta:
        name = "Demandsite 24-Hour Auto Sync"
        description = "Automated daily synchronization of Demand Site API data into NetBox"
        scheduling_enabled = True

    def run(self, data, commit):
        self.log_info("Starting Demandsite 24-Hour Synchronization...")
        result = sync_all_demand_sites(logger_func=self.log_info)
        self.log_success(
            f"Demandsite sync completed: Total={result['total']}, "
            f"Created={result['created']}, Updated={result['updated']}, "
            f"Unchanged={result['unchanged']}, Errors={len(result['errors'])}"
        )
        if result['errors']:
            for err in result['errors'][:10]:
                self.log_failure(err)
        return result
